"""평가 실행이 개발자 `.env` 에 흔들리지 않는지 본다.

`run_eval --dense` 의 결과는 *"이후 모든 비교의 기준선"* 이다. 같은 명령이 머신마다 다른
수치를 내면 그 목적이 성립하지 않는다 — 나중에 수치가 달라졌을 때 **검색이 변한 것인지
설정이 변한 것인지** 가릴 수 없다.

`ContentRoutingConfig` 등이 전부 `extra="ignore"` 라, `EVAL_SETTINGS` 에 오타가 나면
**아무 에러 없이 그 항목만 조용히 안 먹는다.** 그래서 필드 이름 대조를 따로 둔다.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from evaluation.config import (
    DEFAULT_EMBEDDING_PROFILE,
    DEFAULT_EVALUATOR_PROFILE,
    DEFAULT_GENERATOR_PROFILE,
    DEFAULT_JUDGE_PROFILE,
    DEFAULT_RERANK_PROFILE,
    DEFAULT_REWRITE_PROFILE,
    EVAL_EMBEDDINGS,
    EVAL_EVALUATORS,
    EVAL_GENERATORS,
    EVAL_JUDGES,
    EVAL_RERANKERS,
    EVAL_REWRITERS,
    EVAL_SETTINGS,
    embedding_profile,
    eval_config,
)
from rag_pipeline.config import (
    CleanerConfig,
    ContentRoutingConfig,
    EmbeddingConfig,
    EvaluatorConfig,
    GenerationConfig,
    IngestConfig,
    RerankConfig,
    RetrievalConfig,
    RewriteConfig,
    SplitterConfig,
)
from rag_pipeline.retrieval import get_reranker, get_rewriter

EVALUATION_DIR = Path(__file__).resolve().parents[2] / "evaluation"

# EVAL_SETTINGS 의 블록 이름 -> 그 값을 받는 설정 클래스
_MODELS = {
    "ingest": IngestConfig,
    "content": ContentRoutingConfig,
    "cleaner": CleanerConfig,
    "splitter": SplitterConfig,
    "embedding": EmbeddingConfig,
    "retrieval": RetrievalConfig,
    "rerank": RerankConfig,
    "rewrite": RewriteConfig,
    "evaluator": EvaluatorConfig,
    "generation": GenerationConfig,
}


# `.env` 가 무엇을 말하든 고정값이 이긴다. 이 저장소의 `.env` 는 실제로 이 값들을 갖고 있다
@pytest.mark.parametrize(
    ("env_key", "env_value", "attr"),
    [
        ("TABLE_PDF_HANDLER", "llamaparse", "table_pdf_handler"),
        ("IMAGE_HANDLER", "skip", "image_handler"),
        ("VISION_MODEL", "gpt-4o", "vision_model"),
        ("MAX_VISION_CALLS_PER_DOC", "1", "max_vision_calls_per_doc"),
        ("OCR_PSM", "3", "ocr_psm"),
    ],
)
def test_env_cannot_move_fixed_content_settings(monkeypatch, env_key, env_value, attr) -> None:
    monkeypatch.setenv(env_key, env_value)
    assert getattr(eval_config().content, attr) == EVAL_SETTINGS["content"][attr]


# 청킹·임베딩도 마찬가지다. 청크 상한이 흔들리면 정답 span 이 청크 경계에 걸리는지가 바뀐다
def test_env_cannot_move_splitter_and_embedding(monkeypatch) -> None:
    monkeypatch.setenv("MAX_CHUNK_CHARS", "400")
    monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-3-large")
    monkeypatch.setenv("DETECT_HEADINGS", "false")

    config = eval_config()

    assert config.splitter.max_chunk_chars == 1200
    assert config.embedding.embedding_model == "text-embedding-3-small"
    # 제목 승격이 꺼지면 splitter 가 경계를 못 얻어 청크가 통째로 달라진다
    assert config.detect_headings is True


# **키와 tesseract 경로는 일부러 고정하지 않는다** — 머신마다 달라야 하는 것들이다
def test_keys_and_tesseract_still_come_from_env(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    monkeypatch.setenv("TESSERACT_CMD", "C:/somewhere/tesseract.exe")

    config = eval_config()

    assert config.content.openai_api_key == "sk-from-env"
    assert config.embedding.openai_api_key == "sk-from-env"
    assert config.content.tesseract_cmd == "C:/somewhere/tesseract.exe"


# 설정 클래스들이 `extra="ignore"` 라 오타가 조용히 무시된다. 이름을 직접 대조한다
@pytest.mark.parametrize("block", sorted(_MODELS))
def test_every_fixed_key_is_a_real_field(block) -> None:
    unknown = set(EVAL_SETTINGS[block]) - set(_MODELS[block].model_fields)
    assert not unknown, f"{block} 에 실재하지 않는 설정 이름: {sorted(unknown)}"


# 그리고 실제로 그 값이 객체에 실렸는지까지 본다. 이름이 맞아도 못 실리면 뜻이 없다
def test_fixed_values_actually_land_on_the_config() -> None:
    config = eval_config()
    blocks = {
        "ingest": config,
        "content": config.content,
        "cleaner": config.cleaner,
        "splitter": config.splitter,
        "embedding": config.embedding,
        "evaluator": config.evaluator,
    }
    for name, target in blocks.items():
        for key, value in EVAL_SETTINGS[name].items():
            assert getattr(target, key) == value, f"{name}.{key}"


# 색인 경로는 넘긴 쪽이 정한다. qdrant 로컬 파일 모드가 배타 잠금을 걸어 실행마다 갈라야 한다
def test_qdrant_path_is_only_set_when_given(tmp_path) -> None:
    assert eval_config(qdrant_path=tmp_path / "store").indexing.qdrant_path == tmp_path / "store"


# **`--check` 의 파싱과 `--dense` 의 색인이 같은 설정을 써야 한다.** 한쪽만 `IngestConfig()`
# 를 쓰면 *"정답 span 이 청크 하나 안에 있다"* 는 검증이 실제로 색인된 청크와 갈린다.
# 산문으로 적어두면 다음 사람이 안 읽으므로 여기서 강제한다
@pytest.mark.parametrize("module", ["corpus.py", "run_eval.py"])
def test_evaluation_never_builds_its_own_ingest_config(module) -> None:
    tree = ast.parse((EVALUATION_DIR / module).read_text(encoding="utf-8"))
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "IngestConfig" not in called, f"{module} 는 eval_config() 를 써야 한다"
    assert "eval_config" in called


# ---------------------------------------------------------------------------
# 임베딩 프로필 축 — 고정 중에 이것 하나만 이름으로 갈아끼울 수 있다
# ---------------------------------------------------------------------------


# **두 곳에 안 적는다.** EVAL_SETTINGS 가 기본 프로필을 그대로 가리켜야, baseline.json 의
# settings 와 실제로 실린 설정이 갈릴 수 없다
def test_fixed_settings_point_at_the_default_profile() -> None:
    assert EVAL_SETTINGS["embedding"] is EVAL_EMBEDDINGS[DEFAULT_EMBEDDING_PROFILE]


# 프로필도 `extra="ignore"` 를 통과하므로 오타가 조용히 무시된다. 전부 대조한다 —
# 기본 프로필만 보면 나중에 더한 프로필의 오타가 그대로 산다
@pytest.mark.parametrize("profile", sorted(EVAL_EMBEDDINGS))
def test_every_profile_key_is_a_real_field(profile: str) -> None:
    unknown = set(EVAL_EMBEDDINGS[profile]) - set(EmbeddingConfig.model_fields)
    assert not unknown, f"프로필 '{profile}' 에 실재하지 않는 설정 이름: {sorted(unknown)}"


# **프로필은 자기 자신만으로 provider 를 결정해야 한다.** 안 박으면 코드 기본값을 타는데,
# 그 기본값은 움직인다 — 2026-08-25 에 `openai` -> `local` 로 뒤집히면서(`cba8e8d`)
# `baseline` 이 조용히 *로컬 text-embedding-3-small* 을 뜻하게 됐고, 그런 HF 리포는 없다.
# **어느 테스트도 그걸 못 잡았다.** 이름 대조(`_is_a_real_field`)는 있는 키만 보고,
# 기본값 fallback 은 키가 아예 없는 자리라 그 그물에 안 걸린다
@pytest.mark.parametrize("profile", sorted(EVAL_EMBEDDINGS))
def test_every_profile_pins_its_provider(profile: str) -> None:
    assert "embedding_provider" in EVAL_EMBEDDINGS[profile], (
        f"프로필 '{profile}' 이 embedding_provider 를 안 박았습니다 — "
        "코드 기본값이 바뀌면 이 프로필의 뜻이 아무 에러 없이 바뀝니다. "
        "실제로 2026-08-25 에 그렇게 됐습니다 (evaluation/config.py 의 baseline 주석)"
    )


# 아무것도 안 주면 지금까지와 같은 실행이어야 한다. 축이 산출을 바꾸면 축이 아니라 변경이다
def test_default_is_the_same_as_naming_the_default_profile() -> None:
    assert eval_config().embedding.model_dump() == (
        eval_config(embedding=DEFAULT_EMBEDDING_PROFILE).embedding.model_dump()
    )


# 고른 프로필의 값이 실제로 객체에 실려야 한다. 이름만 맞고 안 실리면 뜻이 없다
@pytest.mark.parametrize("profile", sorted(EVAL_EMBEDDINGS))
def test_named_profile_actually_lands(profile: str) -> None:
    config = eval_config(embedding=profile)
    for key, value in EVAL_EMBEDDINGS[profile].items():
        assert getattr(config.embedding, key) == value, f"{profile}.{key}"


# **없는 이름이면 조용히 기본값으로 안 돌아간다.** 돌아가면 대조군을 두 번 재고, 두 실행이
# 같은 수치를 내는 것이 "모델이 안 움직였다"로 읽힌다 — 물으려던 질문에 가짜 답이 나온다
def test_unknown_profile_stops() -> None:
    with pytest.raises(SystemExit) as err:
        eval_config(embedding="없는프로필")
    assert "없는프로필" in str(err.value)
    assert DEFAULT_EMBEDDING_PROFILE in str(err.value)


# 프로필을 골라도 `.env` 는 여전히 못 이긴다. 고정의 목적이 프로필 축으로 새면 안 된다
def test_env_cannot_move_a_named_profile(monkeypatch) -> None:
    monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-3-large")
    monkeypatch.setenv("EMBEDDING_BATCH_SIZE", "3")

    config = eval_config(embedding=DEFAULT_EMBEDDING_PROFILE)

    assert config.embedding.embedding_model == "text-embedding-3-small"
    assert config.embedding.embedding_batch_size == 100


# 프로필 조회는 사본이 아니라 그 딕셔너리를 준다. 호출부가 고치면 다음 실행이 오염되므로
# **읽기만 한다는 것**을 여기서 못 박아 둔다 (지금 호출부는 ** 로 펴서 넘긴다)
def test_lookup_returns_the_registered_block() -> None:
    assert embedding_profile(DEFAULT_EMBEDDING_PROFILE) == EVAL_EMBEDDINGS[DEFAULT_EMBEDDING_PROFILE]


# 리랭커 프로필 축 — 임베딩과 **같은 모양이라 같은 가드를 건다**
# ---------------------------------------------------------------------------


# 기본이 꺼짐이어야 한다. **`--rerank` 를 안 준 실행은 지금까지와 글자 그대로 같아야 한다** —
# 켜지는 순간 질의당 LLM 호출이 1회에서 2회가 되므로 기본값이 곧 과금 여부다
def test_rerank_default_profile_is_off() -> None:
    assert EVAL_SETTINGS["rerank"] is EVAL_RERANKERS[DEFAULT_RERANK_PROFILE]
    assert eval_config().rerank.rerank_model is None
    assert get_reranker(eval_config().rerank) is None


# 프로필도 `extra="ignore"` 를 통과하므로 오타가 조용히 무시된다. 전부 대조한다
@pytest.mark.parametrize("profile", sorted(EVAL_RERANKERS))
def test_every_rerank_profile_key_is_a_real_field(profile: str) -> None:
    unknown = set(EVAL_RERANKERS[profile]) - set(RerankConfig.model_fields)
    assert not unknown, f"리랭커 프로필 '{profile}' 에 실재하지 않는 설정 이름: {sorted(unknown)}"


# 고른 프로필의 값이 실제로 객체에 실려야 한다. 이름만 맞고 안 실리면 뜻이 없다
@pytest.mark.parametrize("profile", sorted(EVAL_RERANKERS))
def test_named_rerank_profile_actually_lands(profile: str) -> None:
    config = eval_config(rerank=profile)
    for key, value in EVAL_RERANKERS[profile].items():
        assert getattr(config.rerank, key) == value, f"{profile}.{key}"


# **로컬 갈래는 장치를 못 박아야 한다** — CPU 와 GPU 가 다른 점수를 내면 같은 명령이
# 머신마다 다른 순위를 만들고 비교표가 에러 없이 흔들린다. `kure-v1` 이 `embedding_device` 를
# 못 박은 것과 같은 함정이라, 새 로컬 프로필을 더할 때 빠뜨리는 것을 여기서 막는다
@pytest.mark.parametrize("profile", sorted(EVAL_RERANKERS))
def test_local_rerank_profiles_pin_the_device(profile: str) -> None:
    block = EVAL_RERANKERS[profile]
    if block.get("rerank_provider") != "local":
        pytest.skip("로컬 갈래가 아니면 장치를 안 쓴다")
    assert "rerank_device" in block, f"로컬 프로필 '{profile}' 에 rerank_device 가 없다"


# **모델을 가진 프로필은 provider 를 못 박아야 한다.** `rerank_provider` 기본값이 `openai` 라,
# 안 박으면 HuggingFace 리포 이름이 **유료** 리랭커로 간다 — 임베딩 쪽 같은 구멍
# (`test_every_profile_pins_its_provider`, 2026-08-25)보다 대가가 크다. 저쪽은 없는 모델
# 이름으로 죽지만 이쪽은 **에러 없이 과금된다.**
#
# `off` 를 skip 하는 것은 거기서 provider 가 뜻이 없어서다 — `rerank_model=None` 이면
# `get_reranker()` 가 None 을 돌려 구현체를 아예 안 고른다. 박아 두면 "합법값이 하나뿐인
# 죽은 값"이 되는데 이 저장소는 그것을 일관되게 기각해 왔다. 바로 위
# `test_local_rerank_profiles_pin_the_device` 가 쓰는 skip 패턴과 같은 모양이다
@pytest.mark.parametrize("profile", sorted(EVAL_RERANKERS))
def test_rerank_profiles_with_a_model_pin_their_provider(profile: str) -> None:
    block = EVAL_RERANKERS[profile]
    if block.get("rerank_model") is None:
        pytest.skip("모델이 없으면 구현체를 안 고른다 — provider 가 뜻이 없다")
    assert "rerank_provider" in block, (
        f"리랭커 프로필 '{profile}' 이 rerank_provider 를 안 박았습니다 — "
        "코드 기본값이 'openai' 라 HuggingFace 모델 이름이 유료 리랭커로 갑니다. "
        "임베딩 쪽 같은 구멍은 없는 모델로 죽었지만 이쪽은 에러 없이 과금됩니다"
    )


# 없는 이름이면 조용히 꺼짐으로 안 돌아간다. 돌아가면 리랭커를 켠 줄 알고 대조군을 두 번
# 재게 되고, 두 실행이 같은 수치를 내는 것이 "리랭커가 효과 없다"로 읽힌다
def test_unknown_rerank_profile_stops() -> None:
    with pytest.raises(SystemExit) as err:
        eval_config(rerank="없는리랭커")
    assert "없는리랭커" in str(err.value)
    assert DEFAULT_RERANK_PROFILE in str(err.value)


# 프로필을 골라도 `.env` 는 못 이긴다. 고정의 목적이 이 축으로 새면 안 된다
def test_env_cannot_move_a_named_rerank_profile(monkeypatch) -> None:
    monkeypatch.setenv("RERANK_MODEL", "gpt-4o")
    monkeypatch.setenv("RERANK_CANDIDATES", "3")

    config = eval_config(rerank="openai")

    assert config.rerank.rerank_model == "gpt-4.1-mini"
    assert config.rerank.rerank_candidates == 50


# **`.env` 가 꺼진 기본값을 켤 수도 없어야 한다.** 개발자가 `.env` 에 모델 이름을 넣어 두면
# `--rerank` 를 안 준 평가 실행이 조용히 과금되는데, 그것이 정확히 이 파일이 막는 것이다
def test_env_cannot_turn_the_default_off_profile_on(monkeypatch) -> None:
    monkeypatch.setenv("RERANK_MODEL", "gpt-4o")

    assert eval_config().rerank.rerank_model is None


# 질의 재작성 프로필 축 — 리랭커와 **같은 모양이라 같은 가드를 건다**
# ---------------------------------------------------------------------------


# 기본이 꺼짐이어야 한다. **`--rewrite` 를 안 준 실행은 지금까지와 글자 그대로 같아야 한다** —
# 켜지는 순간 질의당 LLM 호출이 하나 늘고, 리랭커와 함께 켜면 셋이 된다
def test_rewrite_default_profile_is_off() -> None:
    assert EVAL_SETTINGS["rewrite"] is EVAL_REWRITERS[DEFAULT_REWRITE_PROFILE]
    assert eval_config().rewrite.rewrite_model is None
    assert get_rewriter(eval_config().rewrite) is None


# 프로필도 `extra="ignore"` 를 통과하므로 오타가 조용히 무시된다. 전부 대조한다
@pytest.mark.parametrize("profile", sorted(EVAL_REWRITERS))
def test_every_rewrite_profile_key_is_a_real_field(profile: str) -> None:
    unknown = set(EVAL_REWRITERS[profile]) - set(RewriteConfig.model_fields)
    assert not unknown, f"재작성 프로필 '{profile}' 에 실재하지 않는 설정 이름: {sorted(unknown)}"


# 고른 프로필의 값이 실제로 객체에 실려야 한다. 이름만 맞고 안 실리면 뜻이 없다
@pytest.mark.parametrize("profile", sorted(EVAL_REWRITERS))
def test_named_rewrite_profile_actually_lands(profile: str) -> None:
    config = eval_config(rewrite=profile)
    for key, value in EVAL_REWRITERS[profile].items():
        assert getattr(config.rewrite, key) == value, f"{profile}.{key}"


# **모델을 가진 프로필은 합류 방식을 못 박아야 한다.** 이 축이 존재하는 이유 자체가 그것을
# 가르는 것이라, 안 박으면 코드 기본값(`fuse`)으로 조용히 떨어져 **`replace` 를 재려던 실행이
# `fuse` 를 두 번 재고 「합류 방식은 차이가 없다」로 읽힌다.** 리랭커 쪽 `rerank_provider`
# 가드와 같은 모양이고, `off` 를 skip 하는 이유도 같다 — 모델이 없으면 재작성기를 아예
# 안 만들어 mode 가 도달할 코드가 없다
@pytest.mark.parametrize("profile", sorted(EVAL_REWRITERS))
def test_rewrite_profiles_with_a_model_pin_their_mode(profile: str) -> None:
    block = EVAL_REWRITERS[profile]
    if block.get("rewrite_model") is None:
        pytest.skip("모델이 없으면 재작성기를 안 만든다 — mode 가 뜻이 없다")
    assert "rewrite_mode" in block, (
        f"재작성 프로필 '{profile}' 이 rewrite_mode 를 안 박았습니다 — "
        "코드 기본값이 'fuse' 라 replace 를 재려던 실행이 fuse 를 두 번 잽니다"
    )


# 두 모드가 **둘 다 실재해야** 이 축이 뜻을 갖는다. 하나만 남기면 *프로브 수치가 재현되나*
# 와 *로드맵이 옳았나* 중 하나를 영원히 못 묻는다 (.claude/wiki/plan_log/검색-생성.md 22회차)
def test_both_merge_modes_exist() -> None:
    modes = {
        block["rewrite_mode"]
        for block in EVAL_REWRITERS.values()
        if block.get("rewrite_model") is not None
    }
    assert modes == {"fuse", "replace"}


# 없는 이름이면 조용히 꺼짐으로 안 돌아간다. 돌아가면 재작성을 켠 줄 알고 대조군을 두 번
# 재게 되고, 두 실행이 같은 수치를 내는 것이 "재작성이 효과 없다"로 읽힌다
def test_unknown_rewrite_profile_stops() -> None:
    with pytest.raises(SystemExit) as err:
        eval_config(rewrite="없는재작성기")
    assert "없는재작성기" in str(err.value)
    assert DEFAULT_REWRITE_PROFILE in str(err.value)


# 프로필을 골라도 `.env` 는 못 이긴다. 고정의 목적이 이 축으로 새면 안 된다
def test_env_cannot_move_a_named_rewrite_profile(monkeypatch) -> None:
    monkeypatch.setenv("REWRITE_MODE", "replace")
    monkeypatch.setenv("REWRITE_CONTEXT_K", "3")

    config = eval_config(rewrite="fuse")

    assert config.rewrite.rewrite_mode == "fuse"
    assert config.rewrite.rewrite_context_k == 10


# **`.env` 가 꺼진 기본값을 켤 수도 없어야 한다.** 개발자가 `.env` 에 모델 이름을 넣어 두면
# `--rewrite` 를 안 준 평가 실행이 조용히 과금된다
def test_env_cannot_turn_the_default_off_rewrite_profile_on(monkeypatch) -> None:
    monkeypatch.setenv("REWRITE_MODEL", "gpt-4o")

    assert eval_config().rewrite.rewrite_model is None


# **판정 모델은 고정 블록이 이겨야 한다.** `.env` 의 EVALUATOR_MODEL 은 비어 있는 것이 기본
# (=꺼짐)이라, 안 박으면 채점 실행이 조용히 전건 `failed` 를 내고 그 표는 판정력이 아니라
# 설정 누락을 재게 된다. 리랭커가 기본 `off` 인 것과 축이 다른 자리다
def test_evaluator_model_is_pinned(monkeypatch) -> None:
    monkeypatch.setenv("EVALUATOR_MODEL", "")
    assert eval_config().evaluator.evaluator_model == EVAL_SETTINGS["evaluator"]["evaluator_model"]


# ---------------------------------------------------------------------------
# 판정 프로필 축 — 앞의 둘과 **같은 모양이라 같은 가드를 건다**
# ---------------------------------------------------------------------------


# **두 곳에 안 적는다.** 기본 프로필을 그대로 가리켜야 실행 JSON 의 `evaluator_profile` 과
# 실제로 실린 설정이 갈릴 수 없다. 그리고 기본이 **꺼짐이 아니다** — 채점은 판정이 반드시
# 돌아야 하는 자리라 리랭커(`off`)와 축이 다르다
def test_fixed_settings_point_at_the_default_evaluator_profile() -> None:
    assert EVAL_SETTINGS["evaluator"] is EVAL_EVALUATORS[DEFAULT_EVALUATOR_PROFILE]
    assert eval_config().evaluator.evaluator_model is not None


# 프로필도 `extra="ignore"` 를 통과하므로 오타가 조용히 무시된다. 전부 대조한다 —
# CLAUDE.md 가 *"프로필을 더할 때 기본 프로필만 대조하면 새 프로필의 오타가 그대로 산다"* 고
# 적어 둔 자리가 정확히 여기다
@pytest.mark.parametrize("profile", sorted(EVAL_EVALUATORS))
def test_every_evaluator_profile_key_is_a_real_field(profile: str) -> None:
    unknown = set(EVAL_EVALUATORS[profile]) - set(EvaluatorConfig.model_fields)
    assert not unknown, f"판정 프로필 '{profile}' 에 실재하지 않는 설정 이름: {sorted(unknown)}"


# 고른 프로필의 값이 실제로 객체에 실려야 한다. **`evaluator_temperature=None` 이 여기서
# 걸린다** — pydantic 이 `float | None` 을 안 받으면 luna 프로필이 통째로 죽는다
@pytest.mark.parametrize("profile", sorted(EVAL_EVALUATORS))
def test_named_evaluator_profile_actually_lands(profile: str) -> None:
    config = eval_config(evaluator=profile)
    for key, value in EVAL_EVALUATORS[profile].items():
        assert getattr(config.evaluator, key) == value, f"{profile}.{key}"


# **모든 프로필이 온도를 명시해야 한다.** 코드 기본값이 `0.0` 이라 안 적으면 gpt-5 계열
# 프로필이 조용히 0.0 을 보내고 **43건이 전부 400 으로 죽는다.** 리랭커 프로필이 provider 를
# 못 박게 한 것과 같은 함정이고, 저쪽처럼 에러 없이 과금되지는 않지만 실패 열이 가득 찬
# 표를 *"모델이 나쁘다"* 로 읽게 만든다
@pytest.mark.parametrize("profile", sorted(EVAL_EVALUATORS))
def test_every_evaluator_profile_pins_its_temperature(profile: str) -> None:
    assert "evaluator_temperature" in EVAL_EVALUATORS[profile], (
        f"판정 프로필 '{profile}' 이 evaluator_temperature 를 안 박았습니다 — "
        "코드 기본값이 0.0 이라 gpt-5 계열이면 전건 400 으로 죽습니다. "
        "안 보내려면 None 을 명시합니다 (evaluation/config.py 의 luna 주석)"
    )


# 없는 이름이면 조용히 기본값으로 안 돌아간다. 돌아가면 모델을 바꾼 줄 알고 대조군을 두 번
# 재게 되고, 두 실행이 같은 수치를 내는 것이 "모델이 안 움직였다"로 읽힌다 — 이 축이
# 답하려는 질문에 정확히 가짜 답이 나온다
def test_unknown_evaluator_profile_stops() -> None:
    with pytest.raises(SystemExit) as err:
        eval_config(evaluator="없는판정")
    assert "없는판정" in str(err.value)
    assert DEFAULT_EVALUATOR_PROFILE in str(err.value)


# 프로필을 골라도 `.env` 는 못 이긴다. 고정의 목적이 이 축으로 새면 안 된다
def test_env_cannot_move_a_named_evaluator_profile(monkeypatch) -> None:
    monkeypatch.setenv("EVALUATOR_MODEL", "gpt-4o")
    monkeypatch.setenv("EVALUATOR_TEMPERATURE", "0.7")

    config = eval_config(evaluator="luna")

    assert config.evaluator.evaluator_model == "gpt-5.6-luna"
    # **None 이 `.env` 값에 안 덮인다** — 덮이면 luna 가 400 으로 죽는다
    assert config.evaluator.evaluator_temperature is None


# 아무것도 안 주면 지금까지와 같은 실행이어야 한다. 축이 산출을 바꾸면 축이 아니라 변경이다
def test_evaluator_default_is_the_same_as_naming_it() -> None:
    assert eval_config().evaluator.model_dump() == (
        eval_config(evaluator=DEFAULT_EVALUATOR_PROFILE).evaluator.model_dump()
    )



# ---------------------------------------------------------------------------
# 생성 프로필 축 — **2026-08-28 에 생겼다.** 그전까지 평가가 생성을 한 번도 안 불렀다
# ---------------------------------------------------------------------------


# **두 곳에 안 적는다.** 기본 프로필을 그대로 가리켜야 리포트의 `generation_profile` 과
# 실제로 실린 설정이 갈릴 수 없다
def test_fixed_settings_point_at_the_default_generation_profile() -> None:
    assert EVAL_SETTINGS["generation"] is EVAL_GENERATORS[DEFAULT_GENERATOR_PROFILE]


# 프로필도 `extra="ignore"` 를 통과하므로 오타가 조용히 무시된다. 전부 대조한다
@pytest.mark.parametrize("profile", sorted(EVAL_GENERATORS))
def test_every_generation_profile_key_is_a_real_field(profile: str) -> None:
    unknown = set(EVAL_GENERATORS[profile]) - set(GenerationConfig.model_fields)
    assert not unknown, f"생성 프로필 '{profile}' 에 실재하지 않는 설정 이름: {sorted(unknown)}"


# 고른 프로필의 값이 실제로 객체에 실려야 한다. 이름만 맞고 안 실리면 뜻이 없다
@pytest.mark.parametrize("profile", sorted(EVAL_GENERATORS))
def test_named_generation_profile_actually_lands(profile: str) -> None:
    config = eval_config(generation=profile)
    for key, value in EVAL_GENERATORS[profile].items():
        assert getattr(config.generation, key) == value, f"{profile}.{key}"


# **이 축이 없으면 `answer_eval` 이 재는 답변만 `.env` 를 탄다.** 다른 여섯 축을 고정한
# 이유가 여기서만 새는 상태가 조용히 생기는데, 그러면 같은 명령이 머신마다 다른 답변을
# 채점한다 — 이 축을 만든 이유가 그것이다
def test_env_cannot_move_the_generation_settings(monkeypatch) -> None:
    monkeypatch.setenv("GENERATION_MODEL", "gpt-4o")
    monkeypatch.setenv("GENERATION_TEMPERATURE", "1.0")
    monkeypatch.setenv("GENERATION_TOP_K", "20")

    config = eval_config()

    assert config.generation.generation_model == "gpt-4.1-mini"
    assert config.generation.generation_temperature == 0.2
    assert config.generation.generation_top_k == 5


# **키는 여전히 `.env` 에서 온다** — 생성은 유료 갈래뿐이라 키가 없으면 못 재는 것이지
# 나쁘게 재는 것이 아니다. 이 파일이 일부러 고정하지 않는 둘 중 하나다
def test_generation_key_still_comes_from_env(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    assert eval_config().generation.openai_api_key == "sk-from-env"


# 없는 이름이면 조용히 기본값으로 안 돌아간다 — 다른 축과 같은 이유다
def test_unknown_generation_profile_stops() -> None:
    with pytest.raises(SystemExit) as err:
        eval_config(generation="없는생성")
    assert "없는생성" in str(err.value)
    assert DEFAULT_GENERATOR_PROFILE in str(err.value)


# 아무것도 안 주면 지금까지와 같은 실행이어야 한다
def test_generation_default_is_the_same_as_naming_it() -> None:
    assert eval_config().generation.model_dump() == (
        eval_config(generation=DEFAULT_GENERATOR_PROFILE).generation.model_dump()
    )


# ---------------------------------------------------------------------------
# 채점자 프로필 축 — **이것만 `eval_config()` 가 안 쓴다**
# ---------------------------------------------------------------------------


# 대응하는 제품 설정 클래스가 없다. 그래서 오타 방어가 `model_fields` 대조가 아니라
# **dataclass 가 모르는 키에 `TypeError` 를 내는 것**이고, 여기서 그것을 못 박는다.
#
# **모델 이름 검사는 `off` 를 갈라서 한다** — 2026-08-29 에 꺼짐 프로필이 생기며 «모든
# 프로필에 모델이 있다» 가 깨졌는데, 그 단언을 **그냥 빼면** 오타로 모델을 빠뜨린 새 프로필을
# 아무도 안 잡는다. 그래서 없애지 않고 **양쪽 방향으로** 못 박는다
@pytest.mark.parametrize("profile", sorted(EVAL_JUDGES))
def test_every_judge_profile_builds_a_judge_config(profile: str) -> None:
    from evaluation.answer_eval import JudgeConfig

    config = JudgeConfig(**EVAL_JUDGES[profile])
    if profile == "off":
        assert not config.judge_model, "꺼짐 프로필에 모델 이름이 있으면 채점자가 만들어진다"
    else:
        assert config.judge_model, f"채점자 프로필 '{profile}' 에 모델 이름이 없습니다"


# **모든 프로필이 온도를 명시해야 한다** — `EVAL_EVALUATORS` 와 같은 함정이다.
# dataclass 라 빠뜨리면 `TypeError` 로 죽지만, 그 사실을 여기서 이름으로도 못 박아
# 새 프로필을 더할 때 무엇을 적어야 하는지가 테스트에 남게 한다
@pytest.mark.parametrize("profile", sorted(EVAL_JUDGES))
def test_every_judge_profile_pins_its_temperature(profile: str) -> None:
    assert "judge_temperature" in EVAL_JUDGES[profile], (
        f"채점자 프로필 '{profile}' 이 judge_temperature 를 안 박았습니다 — "
        "gpt-5 계열이면 None 을 명시해야 하고, 아니면 재현을 위해 0.0 입니다"
    )


# 없는 이름이면 멈춘다
def test_unknown_judge_profile_stops() -> None:
    from evaluation.config import judge_profile

    with pytest.raises(SystemExit) as err:
        judge_profile("없는채점자")
    assert "없는채점자" in str(err.value)
    assert DEFAULT_JUDGE_PROFILE in str(err.value)
