"""평가 실행이 쓰는 **고정** 적재 설정.

## 왜 `.env` 를 안 타나

`run_eval` 의 dense 실행은 *"이후 모든 비교의 기준선"* 이다(`plan_log/평가-세트.md` 2회차).
그런데 `IngestConfig()` 를 그냥 만들면 개발자 `.env` 를 그대로 타므로 **같은 명령이 머신마다
다른 수치를 낸다.** 그러면 나중에 수치가 달라졌을 때 *검색이 변한 것인지 설정이 변한 것인지*
가릴 수 없고, 기준선이라는 목적 자체가 성립하지 않는다.

`tests/ingestion/indexing/test_indexing_real.py` 가 이미 같은 판단을 했다 — *"설정은 전부
명시해 넘긴다. 개발자의 `.env` 가 상한이나 제목 승격을 덮으면 결과가 흔들린다."*

## 왜 러너가 아니라 별도 모듈인가

**소비자가 둘이다** — `corpus.py`(`--check` 의 파싱)와 `run_eval.py`(`--dense` 의 색인).
러너 안에만 두면 두 경로가 다른 설정으로 돌아, *"정답 span 이 청크 하나 안에 있다"* 고
검증한 것과 실제로 색인된 청크가 갈린다. 그 검증이 세트의 성립 근거라 갈리면 안 된다.

## 무엇을 고정하고 무엇을 안 하나

고정하는 것은 **산출을 바꾸는 값**이다. 고정하지 않는 것이 둘 있고 둘 다 이유가 같다 —
*머신마다 달라야 하는 것*이다.

- **API 키** — `.env` 에서 온다. 없으면 폴백이 돌고, 그것도 관측 대상이다
- **`tesseract_cmd`** — 설치 경로가 머신마다 다르다

## 캐시를 켠 채로 두는 것은 의도다

`image_handler="hybrid"` 는 Vision 을 부르고 **Vision 응답은 실행마다 다르다**(실측:
`plan_log/표-경로.md` 12회차 — 같은 쪽을 1회차 호출은 10/10, 2회차는 6/10 으로 잡았다).
`ResultCache` 가 첫 응답을 얼려 주는 것이 여기서는 **재현성의 일부**다.

**그래서 `.cache/content` 를 지우면 baseline 이 조용히 흔들린다.** 막을 방법이 없어서
대신 보이게 했다 — 실행 리포트가 `vision_calls`·`ocr_applied`·`warnings` 를 함께 남긴다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rag_pipeline.config import (
    CleanerConfig,
    ContentRoutingConfig,
    EmbeddingConfig,
    IndexingConfig,
    IngestConfig,
    SplitterConfig,
)

__all__ = ["EVAL_SETTINGS", "eval_config"]

# 평가가 고정하는 값 전부. **`baseline.json` 의 `_meta` 가 이것을 그대로 싣는다** —
# 기준선 파일만 보고도 어떤 설정으로 잰 값인지 알 수 있어야 하고, 두 곳에 손으로 적으면
# 한쪽이 조용히 낡는다. 여기 없는 것(API 키·tesseract 경로)은 일부러 뺀 것이다
EVAL_SETTINGS: dict[str, dict[str, Any]] = {
    "ingest": {
        "detect_headings": True,
        "max_excel_rows": 5000,
    },
    # 현재 `.env` 운영값 그대로다. 평가가 운영 산출을 재야 하기 때문이지 기본값이어서가 아니다
    "content": {
        "table_pdf_handler": "pymupdf",
        "image_handler": "hybrid",
        "vision_model": "gpt-4.1-mini",
        "max_vision_calls_per_doc": 30,
        "min_image_width": 100,
        "min_image_height": 100,
        "min_image_display_pt": 20.0,
        "max_image_aspect_ratio": 8.0,
        "ocr_lang": "kor+eng",
        "ocr_psm": 6,
        "ocr_preserve_spaces": True,
        "ocr_min_chars": 10,
        "ocr_min_confidence": 60.0,
        "pdf_scan_text_threshold": 10,
        "pdf_render_dpi": 300,
        "cache_enabled": True,
    },
    "cleaner": {
        "remove_control_chars": True,
        "normalize_whitespace": True,
        "remove_repeated_headers": True,
        "header_min_pages": 3,
        "header_min_span_ratio": 0.60,
    },
    "splitter": {
        "max_chunk_chars": 1200,
        "chunk_overlap_chars": 150,
        "include_heading_in_text": True,
    },
    "embedding": {
        "embedding_model": "text-embedding-3-small",
        "embedding_dimensions": None,
        "embedding_batch_size": 100,
        "max_embedding_tokens_per_doc": 200_000,
        "embedding_cache_enabled": True,
    },
}


# 평가용 IngestConfig. lazy 프로퍼티를 명시 값으로 덮어 `.env` 가 결과를 못 흔들게 한다.
# `qdrant_path` 는 색인하는 쪽만 넘긴다 — 로컬 파일 모드가 배타 잠금을 걸어 실행마다 갈라야 한다
def eval_config(*, qdrant_path: Path | None = None) -> IngestConfig:
    config = IngestConfig(**EVAL_SETTINGS["ingest"])

    # `object.__setattr__` 로 덮는 것은 이 저장소 관례다 — lazy 프로퍼티가 `_content` 등을
    # 캐시하므로, 먼저 채워 두면 `.env` 를 읽는 기본 생성이 아예 안 일어난다
    object.__setattr__(config, "_content", ContentRoutingConfig(**EVAL_SETTINGS["content"]))
    object.__setattr__(config, "_cleaner", CleanerConfig(**EVAL_SETTINGS["cleaner"]))
    object.__setattr__(config, "_splitter", SplitterConfig(**EVAL_SETTINGS["splitter"]))
    object.__setattr__(config, "_embedding", EmbeddingConfig(**EVAL_SETTINGS["embedding"]))

    if qdrant_path is not None:
        object.__setattr__(config, "_indexing", IndexingConfig(qdrant_path=qdrant_path))

    return config
