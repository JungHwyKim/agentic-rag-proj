"""BM25 갈래. 저장소에 든 청크를 훑어 낱말 기반 색인을 짓고 질의로 찾는다.

## 왜 저장소 밖 별도 인덱스인가

qdrant 의 sparse 벡터로 얹으면 `VectorStore` Protocol 과 컬렉션 스키마를 고쳐야 하고,
*"retriever 는 `VectorStore` Protocol 만 받는다"* 로 지킨 저장소 중립이 깨진다
(`plan_log/검색-생성.md` 1회차). 다른 저장소에는 sparse 가 없을 수도 있다.

## 왜 색인 시점이 아니라 질의 시점에 짓나

색인 시점에 파일로 떨궈 두면 **삭제·재색인을 두 곳에 해야 한다.** 한쪽만 갱신되면
`delete_by_document_id()` 가 지운 청크가 BM25 에는 남아 검색되는데, **아무 에러도 안 난다.**
청크의 진실을 저장소 한 벌로 두는 편이 낫고, 지금 코퍼스(문서 9개·청크 수백)에서는
전량 훑기가 싸다. 커지면 그때 다시 본다(2회차 "안 하기로 한 것").

## 왜 청크 객체를 통째로 들고 있나

**BM25 에만 걸린 후보는 dense 결과에 없다.** 텍스트만 들고 있으면 그런 후보의
`metadata`(= 출처 인용의 유일한 근거)를 채울 방법이 없어, 인용이 붙는 청크와 안 붙는
청크가 경로에 따라 갈린다.

## 실패는 예외가 아니라 빈 인덱스다

`build_bm25_index()` 가 `IndexingError` 를 삼켜 **빈 인덱스 + 경고**로 돌려준다. backend 가
첫 질의에 이걸 짓는데 여기서 던지면 **색인 전 서버가 질의마다 죽는다.** 부르는 쪽은
`warnings` 를 읽어 무슨 일이 있었는지 알 수 있다 — `Embedder.usage` 를 밖에서 읽는 것과
같은 모양이고, 그래서 `retrieve()` 의 반환 계약을 안 바꿔도 된다.

## 왜 `k1`·`b` 를 설정으로 안 여나

`rank_bm25` 기본값(1.5 / 0.75)을 쓴다. 이 코퍼스에서 무엇이 나은지 잰 적이 없어 지금
열면 근거 없는 값이 `.env` 로 굳는다. 잴 자[尺]가 생기면 그때 더한다.
"""

from __future__ import annotations

from rank_bm25 import BM25Okapi

from rag_pipeline.exceptions import IndexingError
from rag_pipeline.ingestion.indexing.base import VectorStore
from rag_pipeline.retrieval.tokenize import tokenize
from rag_pipeline.schemas.vector import StoredChunk

__all__ = ["Bm25Index", "build_bm25_index"]


# 낱말 기반 검색 인덱스 하나. 지어진 뒤에는 안 바뀐다 — 저장소가 갱신되면 다시 지어야 한다
class Bm25Index:
    # 토큰이 하나도 없는 청크는 애초에 걸릴 수 없어 뺀다. 안 빼면 문서 길이 평균이
    # 0 쪽으로 끌려가 남은 청크의 길이 보정이 함께 일그러진다
    def __init__(self, chunks: list[StoredChunk], warnings: list[str] | None = None) -> None:
        self.warnings: list[str] = list(warnings or [])
        self._chunks: list[StoredChunk] = []
        corpus: list[list[str]] = []
        for chunk in chunks:
            tokens = tokenize(chunk.text)
            if not tokens:
                continue
            self._chunks.append(chunk)
            corpus.append(tokens)
        # 빈 코퍼스로 BM25Okapi 를 만들면 평균 길이가 0 이라 점수 계산에서 죽는다
        self._bm25 = BM25Okapi(corpus) if corpus else None

    # 색인에 실제로 들어간 청크 수. 0 이면 이 갈래는 아무것도 못 한다
    def __len__(self) -> int:
        return len(self._chunks)

    # 질의와 맞는 청크를 점수 높은 순으로 top_k 개. **점수 0 은 후보가 아니다** —
    # 겹치는 낱말이 하나도 없다는 뜻이라, 넣으면 RRF 가 임의의 순서를 순위로 대접한다
    def search(self, query: str, top_k: int) -> list[tuple[StoredChunk, float]]:
        if self._bm25 is None or top_k <= 0:
            return []
        tokens = tokenize(query)
        if not tokens:
            return []

        scores = self._bm25.get_scores(tokens)
        ranked = sorted(
            ((chunk, float(score)) for chunk, score in zip(self._chunks, scores, strict=True)),
            key=lambda pair: pair[1],
            reverse=True,
        )
        return [pair for pair in ranked[:top_k] if pair[1] > 0.0]


# 저장소를 한 번 훑어 인덱스를 짓는다. **수명은 부르는 쪽이 정한다** —
# backend 는 첫 질의에 지어 app.state 에 두고, 스크립트는 실행마다 새로 짓는다
def build_bm25_index(store: VectorStore) -> Bm25Index:
    try:
        chunks = list(store.iter_chunks())
    except IndexingError as exc:
        # 여기서 올리면 색인 전 서버가 질의마다 죽는다 (모듈 docstring 참고)
        return Bm25Index([], [f"BM25 색인을 짓지 못해 dense 만 씁니다: {exc}"])

    index = Bm25Index(chunks)
    if not len(index):
        index.warnings.append("BM25 색인이 비어 있어 dense 만 씁니다 (색인된 청크가 없습니다)")
    return index
