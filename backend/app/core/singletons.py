"""앱 수명 동안 하나뿐이어야 하는 것들을 만드는 자리.

## 왜 라우트 밖으로 나왔나

`_embedder()` 는 원래 `routes/query.py` 에 있었다. 그런데 **적재 쪽도 같은 임베더를 써야
한다**는 것이 2026-08-26 실측으로 드러났다 — `index_document()` 가 `embedder` 를 안 받아
매번 `get_embedder()` 로 새 `LocalEmbedder` 를 만들었고, `LocalEmbedder` 는 **임베딩 캐시가
빗나갈 때만** 모델을 올리므로 *새 문서* 하나를 올릴 때마다 KURE-v1 2.27GB 가 처음부터
적재됐다. 실측: 25청크 첫 임베딩 **23.629초** vs 적재 후 같은 크기 **0.164초**.

그런데 `routes/query.py` 는 이미 `routes/documents.py` 의 `original_exists()` 를 import 한다.
거기서 `documents.py` 가 거꾸로 `query._embedder` 를 부르면 **순환 import** 다. 그래서
공유하는 것만 여기로 내렸다 — 라우트끼리 부르지 않게 하는 것이 요점이고, 함수 안 지역
import 로 순환을 덮는 안은 냄새만 가린다.

## 락이 여기 있는 이유

`BUILD_LOCK` 은 **여기와 `routes/query.py` 의 getter 들이 함께 쓴다.** 세 자원(embedder ·
BM25 · reranker)을 만드는 구간을 한 번에 하나만 지나가게 하려는 것이고, 나눠서 락을 둘로
만들면 그 성질이 모듈 경계에서 조용히 사라진다.

**워밍업이 이 락을 필요하게 만들었다.** 전에는 만드는 쪽이 요청 하나뿐이라 경쟁이 없었는데,
이제 배경 스레드가 같은 getter 를 부른다. 락이 없으면 워밍업이 `state.embedder` 를 세팅하기
**전에** 들어온 질의가 자기 것을 또 만들어 **KURE 2.27GB 가 두 벌** 뜬다 — 에러 없이
메모리만 두 배가 되므로 눈에 안 띈다. 적재 경로가 여기 합류하며 그 경쟁자가 셋이 됐다.
"""

from __future__ import annotations

import threading

from fastapi import FastAPI

from rag_pipeline.ingestion.embedding import get_embedder

# 아래 getter 들과 `routes/query.py` 의 `_bm25()`·`_reranker()` 가 함께 잡는 락
# (모듈 docstring 참고). 자원마다 나누지 않는 것이 계약이다
BUILD_LOCK = threading.Lock()


# 앱 수명 동안 하나뿐인 embedder 를 얻는다. 없으면 이때 만든다.
#
# **`Request` 가 아니라 `FastAPI` 를 받는 것은 워밍업 때문이다** — lifespan 의 배경 스레드에는
# 요청이 없다. 요청 밖에서도 부를 수 있어야 질의·적재·워밍업 **셋이 같은 인스턴스**를 쓴다.
#
# **여기서 만든 것을 닫지 않는다.** 임베더에는 `close()` 가 없고 수명은 앱과 같다 —
# `store` 의 "연 쪽이 닫는다" 와 갈리는 지점이라 헷갈리지 말 것
def embedder_for(app: FastAPI):  # noqa: ANN202 - Embedder Protocol 은 런타임 타입이 아니다
    with BUILD_LOCK:
        embedder = getattr(app.state, "embedder", None)
        if embedder is None:
            embedder = get_embedder(app.state.ingest_config.embedding)
            app.state.embedder = embedder
        return embedder
