"""답변 캐시 단계. 같은 뜻의 질문이 다시 오면 이전 답을 그대로 돌려준다.

## 어디에 붙나

`retrieve()` **앞**이다. 뒤에 두면 검색은 이미 돌아버려 아끼는 것이 LLM 호출 하나뿐인데,
앞에 두면 검색·생성이 통째로 빠진다.

`ingestion/` 아래가 아닌 이유는 `query.py` 와 같다 — 적재 단계가 아니다. `retrieval/` ·
`generation/` 아래도 아니다. 이 단계는 그 **둘을 건너뛰는** 자리라 어느 쪽에도 속하지 않는다.

이름이 `cache/` 가 아닌 이유: 임베딩·Vision 이 쓰는 `ResultCache`(내용 해시로 API 응답을
재사용한다)와 축이 다르다. 저쪽은 *같은 입력*을 아끼고 이쪽은 *비슷한 입력*을 아낀다.

## 무엇을 "같은 질문"으로 보나 — 축이 둘이다

`answer_cache_mode` 가 갈래를 고르고, 아래 임계값은 **시맨틱 갈래에만** 딸린 자[尺]다.

- `semantic`(기본) — 질문 벡터의 유사도. 임계값이 없으면 통째로 꺼진다(아래 절)
- `exact` — 정규화한 문자열이 같은가. **자[尺]가 없어 임계값도 필요 없다.**
  `cache_point_id_for()` 가 NFC + 공백 정규화 uuid5 라 같은 질문이 늘 같은 자리이고,
  조회가 벡터 검색이 아니라 id 조회라 **`lookup()` 에서 임베딩을 아예 안 부른다**

`exact` 가 차선으로 선 경위는 `plan_log/시맨틱-캐시.md` 4~6회차다 — 양성·음성 분포가
겹쳐 임계값을 *"못 정한다"* 로 닫았고, 1회차가 걸어둔 조건이 그때 발화했다.
**그래도 기본값은 `semantic` 이라 저장소 커밋본은 여전히 꺼진 채다** — 1회차의 기각 사유
절반(*비슷한 질문을 못 잡는다*)이 그대로 살아 있어, 켤지는 실사용 질의 로그가 답한다.

## 왜 임계값이 없으면 캐시가 통째로 꺼지나

**이 기능의 유일한 위험이 임계값이기 때문이다.** 헐거우면 다른 질문에 옛 답을 준다 —
`plan_log/embedding.md` 가 이 단계를 제일 뒤로 밀며 적어둔 그 이유다.

이 저장소의 임계값은 전부 **분포의 틈**에서 나왔다(표 되붙이기 12pt, 표시 면적비 0.08 vs
1.00). 이 축은 아직 안 쟀다. 그래서 기본값을 두지 않고, 없으면 `get_answer_cache()` 가
`None` 을 돌려준다 — **근거 없는 수가 코드에 존재할 수 없는 모양**이다. 켜는 것은 재고 나서
`.env` 에 숫자가 들어가는 순간이다.

덧붙여 임계값은 **임베딩 모델에 딸린 값**이다. 컬렉션 이름에 모델·차원이 박히는 것이 벡터
혼입은 막아주지만, `text-embedding-3-small` 에서 잰 0.9x 가 다른 모델에서 같은 뜻이라는
보장은 어디에도 없다. 모델을 바꾸면 다시 잰다.

## `Protocol` 을 안 만드는 이유

구현체가 하나다. `VectorStore` 가 두 번째 구현체를 기다리는 것과 같은 잣대이고,
`embedding.provider` 를 안 만든 것과도 같다 — 합법값이 하나뿐인 추상은 죽은 추상이다.
"""

from __future__ import annotations

from rag_pipeline.answer_cache.qdrant_cache import QdrantAnswerCache, cache_point_id_for
from rag_pipeline.config import IngestConfig
from rag_pipeline.ingestion.indexing.qdrant_store import QdrantStore

__all__ = ["QdrantAnswerCache", "cache_point_id_for", "get_answer_cache"]


# 설정에 맞는 답변 캐시를 만든다. **임계값이 없으면 None** — 그것이 곧 꺼진 상태다.
#
# `store` 를 받는 이유는 클라이언트를 빌리기 위해서다. 여기서 새로 열면 qdrant 로컬 파일
# 모드의 배타 잠금에 걸려 같은 프로세스 안에서 자기 자신과 충돌한다 (qdrant_cache.py).
# `QdrantStore` 를 구상 타입으로 받는 것은 `client` 가 Protocol 에 없어서이고, Protocol 을
# 넓히지 않는 판단은 `list_documents()` 와 같다.
def get_answer_cache(store: QdrantStore, config: IngestConfig) -> QdrantAnswerCache | None:
    cache_config = config.answer_cache
    mode = cache_config.answer_cache_mode
    threshold = cache_config.answer_cache_threshold

    # 꺼지는 조건은 **시맨틱 갈래에만** 있다. `exact` 는 정할 값이 없어 임계값을 안 본다
    if mode == "semantic" and threshold is None:
        return None

    return QdrantAnswerCache(
        store,
        indexing=config.indexing,
        embedding=config.embedding,
        config=cache_config,
        mode=mode,
        threshold=threshold,
    )
