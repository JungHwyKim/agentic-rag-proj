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
    DEFAULT_RERANK_PROFILE,
    EVAL_EMBEDDINGS,
    EVAL_RERANKERS,
    EVAL_SETTINGS,
    embedding_profile,
    eval_config,
)
from rag_pipeline.config import (
    CleanerConfig,
    ContentRoutingConfig,
    EmbeddingConfig,
    IngestConfig,
    RerankConfig,
    RetrievalConfig,
    SplitterConfig,
)
from rag_pipeline.retrieval import get_reranker

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
