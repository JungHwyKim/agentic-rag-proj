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

from evaluation.config import EVAL_SETTINGS, eval_config
from rag_pipeline.config import (
    CleanerConfig,
    ContentRoutingConfig,
    EmbeddingConfig,
    IngestConfig,
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
