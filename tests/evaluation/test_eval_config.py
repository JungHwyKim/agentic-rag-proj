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
    EVAL_EMBEDDINGS,
    EVAL_SETTINGS,
    embedding_profile,
    eval_config,
)
from rag_pipeline.config import (
    CleanerConfig,
    ContentRoutingConfig,
    EmbeddingConfig,
    IngestConfig,
    RetrievalConfig,
    SplitterConfig,
)

EVALUATION_DIR = Path(__file__).resolve().parents[2] / "evaluation"

# EVAL_SETTINGS 의 블록 이름 -> 그 값을 받는 설정 클래스
_MODELS = {
    "ingest": IngestConfig,
    "content": ContentRoutingConfig,
    "cleaner": CleanerConfig,
    "splitter": SplitterConfig,
    "embedding": EmbeddingConfig,
    "retrieval": RetrievalConfig,
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
