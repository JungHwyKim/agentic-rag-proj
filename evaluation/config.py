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

## 축이 셋 있다 — 이름 붙인 프로필

고정 중에 **세 블록만** 이름으로 갈아끼울 수 있다 — `EVAL_EMBEDDINGS` · `EVAL_RERANKERS` ·
`EVAL_EVALUATORS`. 셋 다 같은 이유로 열렸다: 비교하려는 대상이 **둘 이상 실재**하는데
`.env` 를 타게 하면 위 "왜 `.env` 를 안 타나"가 통째로 무너지기 때문이다.

**셋의 기본값 규칙이 서로 다르고, 그 차이가 뜻을 갖는다.**

| 축 | 기본 | 왜 |
|---|---|---|
| `EVAL_EMBEDDINGS` | `baseline` | 지금까지의 모든 수치가 그것으로 나왔다 |
| `EVAL_RERANKERS` | `off` | 켜지는 순간 질의당 LLM 호출이 2회가 된다 — 기본값이 곧 과금 여부다 |
| `EVAL_EVALUATORS` | `mini` | **꺼짐이 기본이 아니다.** 채점 도구는 판정이 반드시 돌아야 하는 자리라, 안 돌면 전건 실패인데 표는 멀쩡해 보인다 |

**프로필은 갈아끼지 않고 더한다.** 이름이 곧 실험의 정체라, 갈아끼면 이미 나온 수치가 어느
모델·장치로 재졌는지를 파일이 못 말한다. `local` 옆의 `local-gpu`, `kure-v1` 옆의
`kure-v1-gpu`, `mini` 옆의 `luna` 가 전부 같은 판단이다.

**`--config-env` 처럼 설정을 통째로 푸는 것과는 다르다**(`plan_log/평가-세트.md` 가 기각한
그 안이다). 여기서 고를 수 있는 값은 **저장소에 커밋된 닫힌 집합**이라, `baseline.json` 만
보고도 어느 설정으로 잰 값인지 안다는 성질이 그대로 산다. 기각 사유였던 *"합법값이 하나뿐인
죽은 설정"* 에도 안 걸린다 — 값이 둘 이상 실재할 때만 이 축이 뜻을 갖는다.

**기본값은 늘 `baseline` 이다.** 아무것도 안 주면 지금까지와 같은 실행이 된다.

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
    EvaluatorConfig,
    IndexingConfig,
    IngestConfig,
    RerankConfig,
    RetrievalConfig,
    SplitterConfig,
)

__all__ = [
    "DEFAULT_EMBEDDING_PROFILE",
    "DEFAULT_EVALUATOR_PROFILE",
    "DEFAULT_RERANK_PROFILE",
    "EVAL_EMBEDDINGS",
    "EVAL_EVALUATORS",
    "EVAL_RERANKERS",
    "EVAL_SETTINGS",
    "embedding_profile",
    "eval_config",
    "evaluator_profile",
    "rerank_profile",
]


# 갈아끼울 수 있는 임베딩 설정들. **이름이 곧 실험의 정체**라 결과 JSON 과 기준선에 함께
# 실린다 — 어느 모델로 잰 수치인지 파일만 보고 알아야 한다.
#
# 새 프로필을 더할 때 **`embedding_model` 만 바꾸면 안 되는 경우가 있다** — 차원이 다르면
# `embedding_dimensions` 도 함께 적는다. 캐시 salt 와 컬렉션 이름이 둘 다 (모델, 차원) 에서
# 파생하므로, 차원을 안 적으면 다른 모델인데 같은 자리를 볼 수 있다
EVAL_EMBEDDINGS: dict[str, dict[str, Any]] = {
    # 지금까지의 모든 수치가 이 프로필로 나왔다. **바꾸면 기준선이 무효가 된다**
    "baseline": {
        # **provider 를 못 박는다.** 안 박으면 코드 기본값을 타는데, 그 기본값이 2026-08-25 에
        # `openai` -> `local` 로 뒤집히면서(`cba8e8d`) 이 프로필이 조용히 *로컬
        # text-embedding-3-small* 을 뜻하게 됐다 — 그런 HF 리포는 없다. **프로필은 자기
        # 자신만으로 실행을 결정해야 한다**: 그래야 `--embedding baseline` 이 언제 돌려도
        # 같은 실행이고, `baseline.json` 이 재현될 때만 기준선이라는 말이 성립한다.
        # `offline_config` 가 provider 를 못 박는 것과 같은 이유다 (CLAUDE.md)
        "embedding_provider": "openai",
        "embedding_model": "text-embedding-3-small",
        "embedding_dimensions": None,
        "embedding_batch_size": 100,
        "max_embedding_tokens_per_doc": 200_000,
        "embedding_cache_enabled": True,
    },
    # 로컬 한국어 모델. **모델 비교의 실험군이고 운영 기본값이 아니다** — 갈아탈지는
    # 60건·20건을 **겹침 구간별로** 읽은 뒤 사람이 정한다(plan_log/embedding.md 3회차).
    #
    # 고른 근거는 순위표가 아니라 모델 카드의 사실이다 — 1024차원 · 8192컨텍스트 · 2.27GB ·
    # MIT · 접두사 없음(`prompts: {}`) · bge-m3 를 한국어 검색으로 파인튜닝.
    # `multilingual-e5-large` 는 컨텍스트 상한 512 인데 실측 최대 청크가 1,201토큰이라 뺐다.
    #
    # **`embedding_dimensions` 를 안 적는 것이 맞다** — bge-m3 계열은 MRL 미학습이라 못 줄이고,
    # LocalEmbedder 가 값이 오면 예외를 던진다. 캐시 salt 와 컬렉션 이름은 **모델 이름이
    # 이미 갈라** baseline 과 섞이지 않는다
    "kure-v1": {
        "embedding_provider": "local",
        "embedding_model": "nlpai-lab/KURE-v1",
        "embedding_dimensions": None,
        # **`cpu` 를 여기에 못 박는 것이 이 프로필의 재현성이다.** `EMBEDDING_DEVICE` 가
        # 설정이 되었으므로(2026-08-25), 안 박으면 GPU 를 켠 머신에서 같은 명령이 다른
        # 벡터를 내고 *"baseline 은 재현될 때만 기준선"* 이 무너진다 — 이 파일이 `.env` 를
        # 통째로 막은 것과 같은 잣대다. **리랭커 축도 같은 함정이라** `EVAL_SETTINGS` 쪽에
        # 같은 이름 규칙으로 `rerank_device` 가 붙을 예정이다
        "embedding_device": "cpu",
        "embedding_batch_size": 100,
        "max_embedding_tokens_per_doc": 200_000,
        "embedding_cache_enabled": True,
    },
    # 같은 모델을 GPU 에서. **`kure-v1` 을 갈아끼지 않고 더하는 것이 결정이다** —
    # `EVAL_RERANKERS` 가 `local` 옆에 `local-gpu` 를 더한 선례와 같고, 이유도 같다:
    # 이름이 곧 실험의 정체라, 갈아끼면 이미 나온 수치가 **어느 장치로 재졌는지를 파일이
    # 못 말한다.**
    #
    # **컬렉션은 갈리지 않는다.** `collection_name_for()` 가 모델·차원에서 파생하므로
    # 프로필 이름이 달라도 같은 `nlpai-lab/KURE-v1` 컬렉션을 연다 — 재색인이 필요 없다.
    # 캐시 salt 도 같다(`cache_salt_for()` 에 device 가 없다).
    #
    # **그래도 되는 근거가 실측이다** (2026-08-26, 실제 청크 414개):
    # `1-cos` 최대 **1.354e-09** · 성분 최대 절대차 5.728e-06 로 float32 오차 수준이라
    # 두 장치의 벡터를 한 컬렉션에 섞어도 검색이 안 흔들린다. 눈에 띄게 달랐다면
    # 컬렉션을 갈라야 했고, 그때는 이 프로필이 재색인을 뜻했을 것이다.
    #
    # **여는 이유는 발열이다** — 같은 414청크가 cpu 236.4초/CPU-초 1856.7 vs
    # cuda 15.4초/CPU-초 15.8 로 **CPU-초 117배**다. `.env` 의 `EMBEDDING_DEVICE` 로는
    # 이걸 못 바꾼다: 이 파일의 프로필이 `.env` 를 이기는 것이 재현성 장치라서,
    # **프로필이 없으면 GPU 를 켜 놓고도 평가가 CPU 8코어를 포화시킨다**
    "kure-v1-gpu": {
        "embedding_provider": "local",
        "embedding_model": "nlpai-lab/KURE-v1",
        "embedding_dimensions": None,
        "embedding_device": "cuda",
        "embedding_batch_size": 100,
        "max_embedding_tokens_per_doc": 200_000,
        "embedding_cache_enabled": True,
    },
}

# 아무것도 안 주면 이것. 지금까지와 같은 실행이 된다
DEFAULT_EMBEDDING_PROFILE = "baseline"


# 갈아끼울 수 있는 리랭커 설정들. **`EVAL_EMBEDDINGS` 와 같은 모양인 것이 의도다** —
# 이름이 곧 실험의 정체라 결과 JSON 에 함께 실리고, 없는 이름을 주면 조용히 기본값으로
# 안 돌아가고 멈춘다(그러면 대조군을 두 번 재고 *"리랭커가 효과 없다"* 로 읽힌다).
#
# **기본이 `off` 인 것이 이 축의 요점이다.** 리랭커를 켜면 질의당 LLM 호출이 1회에서 2회가
# 되므로, `--rerank` 를 명시하지 않은 실행은 지금까지와 글자 그대로 같아야 한다.
#
# 새 프로필을 더할 때 **`rerank_device` 를 빠뜨리지 않는다** — `kure-v1` 이 `embedding_device`
# 를 못 박은 것과 같은 함정이다(GPU 를 켠 머신이 다른 점수를 내면 비교표가 조용히 흔들린다).
#
# **`rerank_provider` 도 마찬가지이고 이쪽은 대가가 돈이다** — 기본값이 `openai` 라 안 박으면
# HuggingFace 리포 이름이 유료 리랭커로 간다. `EVAL_EMBEDDINGS["baseline"]` 이 provider 를
# 안 박아 *로컬 text-embedding-3-small* 을 뜻하게 됐던 것과 같은 구멍인데(2026-08-25),
# 저쪽은 없는 모델 이름으로 죽고 이쪽은 **에러 없이 과금된다.**
# `tests/evaluation/test_eval_config.py` 의 전수 가드 둘이 이 문단을 강제한다 —
# 모델이 있는 프로필은 provider 를, 로컬 프로필은 device 를 박아야 통과한다.
EVAL_RERANKERS: dict[str, dict[str, Any]] = {
    # 리랭커 없음. **`rerank_model` 이 비면 `get_reranker()` 가 None 을 돌려준다**
    "off": {
        "rerank_model": None,
    },
    # 유료 갈래. `gpt-4.1-mini` 는 Vision·생성과 같은 모델인데, 우연이 아니라 싼 쪽에서
    # 시작한다는 같은 판단이다
    "openai": {
        "rerank_provider": "openai",
        "rerank_model": "gpt-4.1-mini",
        "rerank_candidates": 50,
        "rerank_temperature": 0.0,
        "rerank_max_tokens": 4096,
        "rerank_cache_enabled": True,
    },
    # 무료 갈래. **모델 이름은 탐침값이다** — 한국어 cross-encoder 를 견줘본 적이 없다.
    # 대안은 `Dongjin-kr/ko-reranker` 이고, 로컬이라 견주는 데 과금이 없다
    # (`plan_log/검색-생성.md` 5회차 미결)
    "local": {
        "rerank_provider": "local",
        "rerank_model": "BAAI/bge-reranker-v2-m3",
        "rerank_device": "cpu",
        "rerank_candidates": 50,
        "rerank_cache_enabled": True,
    },
    # 같은 모델을 GPU 에서. **`local` 을 갈아끼지 않고 더하는 것이 요점이다** — 이름이 곧
    # 실험의 정체라, 갈아끼면 이미 나온 E2·E4 수치가 어느 장치로 재졌는지를 파일이 더 이상
    # 말하지 못한다(`kure-v1` 이 `embedding_device` 를 못 박은 것과 같은 잣대다).
    #
    # **재려는 것은 품질이 아니라 지연이다** — 로컬 CE 는 품질로는 무료 칸 넷 중 최고인데
    # CPU 에서 질의당 51초라 화면에 못 쓴다(verify_log/2026-08-25-리랭커-2x2-무료칸.md 3-8).
    # 그 탈락 사유가 장치 때문인지 모델 때문인지가 안 갈려 있었다.
    #
    # **점수도 함께 읽어야 한다** — `rerank_device` 가 존재하는 이유가 *"CPU 와 GPU 가 미세하게
    # 다른 점수를 낸다"* 라서다. `cpu` 실행과 지표가 다르면 그것이 그 성질의 실측이고,
    # 캐시가 섞이지 않는 것은 `rerank_cache_salt_for()` 가 device 를 싣기 때문이다
    "local-gpu": {
        "rerank_provider": "local",
        "rerank_model": "BAAI/bge-reranker-v2-m3",
        "rerank_device": "cuda",
        "rerank_candidates": 50,
        "rerank_cache_enabled": True,
    },
}

# 아무것도 안 주면 이것. 리랭커 없이 도는 지금까지의 실행이다
DEFAULT_RERANK_PROFILE = "off"


# 갈아끼울 수 있는 판정 설정들. **앞의 둘과 같은 모양인 것이 의도다** — 이름이 곧 실험의
# 정체라 결과 JSON 에 함께 실린다.
#
# **기본이 꺼짐이 아닌 것이 이 축의 다른 점이다.** 리랭커는 `off` 가 기본인데 여기는 아니다 —
# 채점 도구(`evaluation/verdicts.py`)는 판정이 **반드시 돌아야** 하는 자리라, 안 돌면 전건
# 실패인데 표는 멀쩡해 보인다. `.env` 의 빈 `EVALUATOR_MODEL`(=꺼짐)을 이기는 것도 그래서다.
#
# **`temperature` 가 이 dict 를 만든 이유다.** 2026-08-27 실측으로 `gpt-5.6-luna` 는
# `temperature=0.0` 을 400 으로 거부하고 기본값(1)만 받는다. 지금까지의 판정 수치(v1·v2·v3)는
# 전부 `gpt-4.1-mini` · temperature 0 이라, luna 수치와 **나란히 놓으면 차이에 모델과
# 샘플링이 함께** 들어간다 — 그래서 이 축으로 재는 것은 *모델 간 절대 비교* 가 아니라
# **한 모델 안에서의 프롬프트 비교**다(`plan_log/검색-생성.md` 13회차).
#
# 새 프로필을 더할 때 **`evaluator_model` 만 바꾸면 안 되는 경우가 있다** — gpt-5 계열이면
# `evaluator_temperature: None` 을 함께 적는다. 빠뜨리면 43건이 전부 400 으로 죽는다.
EVAL_EVALUATORS: dict[str, dict[str, Any]] = {
    # 지금까지의 모든 판정 수치(v1 28/31 · v2 28/32 · v3 30/32)가 이 프로필로 나왔다.
    # **바꾸면 그 표들이 무효가 된다**
    "mini": {
        "evaluator_model": "gpt-4.1-mini",
        "evaluator_temperature": 0.0,
        "evaluator_max_tokens": 512,
    },
    # 모델 축의 실험군. **`mini` 를 갈아끼지 않고 더하는 것이 규칙이다** — `local` 옆에
    # `local-gpu` 를, `kure-v1` 옆에 `kure-v1-gpu` 를 더한 선례와 같고 이유도 같다:
    # 갈아끼면 이미 나온 수치가 어느 모델로 재졌는지를 파일이 못 말한다.
    #
    # **`evaluator_temperature: None` 이 이 프로필의 핵심이다** — *보내지 않는다* 는 뜻이고,
    # 0.0 도 1.0 도 아니다. 그 대가로 **이 프로필은 재현이 안 된다**: 캐시를 지우고 다시
    # 돌리면 다른 표가 나올 수 있어, 한 번 돌린 수치로 우열을 단정하면 안 된다
    "luna": {
        "evaluator_model": "gpt-5.6-luna",
        "evaluator_temperature": None,
        "evaluator_max_tokens": 512,
    },
}

# 아무것도 안 주면 이것. 지금까지의 모든 혼동행렬이 이 프로필로 나왔다
DEFAULT_EVALUATOR_PROFILE = "mini"

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
    # 검색 후보 폭과 RRF 상수. **과금 항목이 아닌데도 고정한다** — 이 둘이 바뀌면
    # 같은 질의가 다른 청크를 물어 hit@k 가 통째로 달라진다. 지금은 dense 만 쓰면
    # 아무 영향이 없지만(--hybrid 없이는 인덱스를 안 넘긴다) hybrid 실행부터 결과를 가른다
    "retrieval": {
        "candidate_k": 50,
        "rrf_k": 60,
    },
    # **임베딩과 같은 이유로 두 곳에 안 적는다** — 기본 프로필(=꺼짐)을 그대로 가리켜,
    # `--rerank` 를 안 준 실행이 자기가 안 쓴 모델을 기록하는 일이 없게 한다
    "rerank": EVAL_RERANKERS[DEFAULT_RERANK_PROFILE],
    # **두 곳에 적지 않는다.** 기본 프로필을 그대로 가리켜, `baseline.json` 의 `settings`
    # 와 실제로 실린 설정이 갈릴 수 없게 한다
    "embedding": EVAL_EMBEDDINGS[DEFAULT_EMBEDDING_PROFILE],
    # 판정(Evaluator) 설정. **재는 대상 자체라 더더욱 고정한다** — 모델이나 온도가 머신마다
    # 다르면 같은 명령이 다른 혼동행렬을 내고, 그러면 이 모듈이 존재하는 이유가 무너진다.
    # `evaluator_model` 을 여기 박는 것이 `.env` 의 빈 값(=꺼짐)을 이긴다 — 채점 도구는
    # 판정이 **반드시 돌아야** 하는 자리라 리랭커(`off` 가 기본)와 축이 다르다.
    # **앞의 둘과 같은 이유로 두 곳에 안 적는다** — 기본 프로필을 그대로 가리킨다
    "evaluator": EVAL_EVALUATORS[DEFAULT_EVALUATOR_PROFILE],
}


# 이름으로 임베딩 프로필을 찾는다.
#
# **없는 이름이면 조용히 기본값으로 안 돌아간다.** 돌아가면 비교하려던 모델 대신 대조군을
# 두 번 재게 되는데, 그때 두 실행이 같은 수치를 내는 것은 *"모델이 안 움직였다"* 로 읽힌다 —
# 정확히 이 회차가 답하려는 질문에 가짜 답이 나온다. `--format` 에 없는 판을 줬을 때
# 멈추는 것과 같은 판단이다(그쪽은 그룹이 문서 없이 채점되는 것을 막는다)
def embedding_profile(name: str) -> dict[str, Any]:
    if name not in EVAL_EMBEDDINGS:
        raise SystemExit(
            f"'{name}' 은 없는 임베딩 프로필입니다. "
            f"있는 것: {sorted(EVAL_EMBEDDINGS)} (evaluation/config.py 의 EVAL_EMBEDDINGS)"
        )
    return EVAL_EMBEDDINGS[name]


# 이름으로 리랭커 프로필을 찾는다. **`embedding_profile()` 과 같은 이유로 같은 모양이다** —
# 없는 이름에 조용히 기본값(=꺼짐)으로 떨어지면 리랭커를 켠 줄 알고 대조군을 두 번 재게 되고,
# 두 실행이 같은 수치를 내는 것이 *"리랭커가 효과 없다"* 로 읽힌다
def rerank_profile(name: str) -> dict[str, Any]:
    if name not in EVAL_RERANKERS:
        raise SystemExit(
            f"'{name}' 은 없는 리랭커 프로필입니다. "
            f"있는 것: {sorted(EVAL_RERANKERS)} (evaluation/config.py 의 EVAL_RERANKERS)"
        )
    return EVAL_RERANKERS[name]


# 이름으로 판정 프로필을 찾는다. **앞의 둘과 같은 이유로 같은 모양이다** — 없는 이름에
# 조용히 기본값으로 떨어지면 모델을 바꾼 줄 알고 대조군을 두 번 재게 되고, 두 실행이 같은
# 수치를 내는 것이 *"모델이 안 움직였다"* 로 읽힌다. 정확히 이 축이 답하려는 질문이라 제일
# 나쁜 가짜 답이 나오는 자리다
def evaluator_profile(name: str) -> dict[str, Any]:
    if name not in EVAL_EVALUATORS:
        raise SystemExit(
            f"'{name}' 은 없는 판정 프로필입니다. "
            f"있는 것: {sorted(EVAL_EVALUATORS)} (evaluation/config.py 의 EVAL_EVALUATORS)"
        )
    return EVAL_EVALUATORS[name]


# 평가용 IngestConfig. lazy 프로퍼티를 명시 값으로 덮어 `.env` 가 결과를 못 흔들게 한다.
# `qdrant_path` 는 색인하는 쪽만 넘긴다 — 로컬 파일 모드가 배타 잠금을 걸어 실행마다 갈라야 한다.
# `embedding`·`rerank`·`evaluator` 가 갈아끼울 수 있는 세 축이고 나머지는 고정이다
# (모듈 docstring 참고)
def eval_config(
    *,
    qdrant_path: Path | None = None,
    embedding: str = DEFAULT_EMBEDDING_PROFILE,
    rerank: str = DEFAULT_RERANK_PROFILE,
    evaluator: str = DEFAULT_EVALUATOR_PROFILE,
) -> IngestConfig:
    config = IngestConfig(**EVAL_SETTINGS["ingest"])

    # `object.__setattr__` 로 덮는 것은 이 저장소 관례다 — lazy 프로퍼티가 `_content` 등을
    # 캐시하므로, 먼저 채워 두면 `.env` 를 읽는 기본 생성이 아예 안 일어난다
    object.__setattr__(config, "_content", ContentRoutingConfig(**EVAL_SETTINGS["content"]))
    object.__setattr__(config, "_cleaner", CleanerConfig(**EVAL_SETTINGS["cleaner"]))
    object.__setattr__(config, "_splitter", SplitterConfig(**EVAL_SETTINGS["splitter"]))
    object.__setattr__(config, "_embedding", EmbeddingConfig(**embedding_profile(embedding)))
    object.__setattr__(config, "_retrieval", RetrievalConfig(**EVAL_SETTINGS["retrieval"]))
    # **키는 안 지운다** — 유료 갈래는 실제로 키가 필요하고, 이 파일이 일부러 고정하지 않는
    # 둘(API 키·tesseract 경로) 중 하나다. 꺼진 상태는 키가 아니라 `rerank_model=None` 이 만든다
    object.__setattr__(config, "_rerank", RerankConfig(**rerank_profile(rerank)))
    # 판정도 같은 이유로 고정한다. **키는 여기서도 안 지운다** — 판정은 유료 갈래뿐이라
    # 키가 없으면 `verdict="failed"` 가 나오고, 그것은 채점 도구의 실패 열에 그대로 보인다
    object.__setattr__(config, "_evaluator", EvaluatorConfig(**evaluator_profile(evaluator)))

    if qdrant_path is not None:
        object.__setattr__(config, "_indexing", IndexingConfig(qdrant_path=qdrant_path))

    return config
