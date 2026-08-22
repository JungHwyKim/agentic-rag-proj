"""검색 단계의 출력 스키마.

`SearchResult`(`vector.py`)와 **나란히 두되 대체하지 않는다.** 저쪽은 저장소 하나가 돌려주는
결과 한 건이고, 이쪽은 **여러 검색 경로의 결과를 합친 뒤**의 한 건이다. 경로가 하나뿐이면
둘은 같은 것이고 이 타입은 필요 없다 — 실제로 dense 만 쓸 때는 `SearchResult` 로 충분하다.
BM25 가 붙는 순간 "이 청크가 어느 경로에서 몇 등이었나"를 실어 나를 곳이 필요해진다.

## 왜 `content` 가 아니라 `text` 인가

`Element.text` · `Chunk.text` · `SearchResult.text` 가 전부 `text` 다. 여기만 `content` 로
두면 단계를 넘을 때마다 이름을 바꿔 붙이게 되고, 그 지점이 자리 밀림을 숨긴다.

## 왜 `metadata` 를 반드시 실어 나르나

**출처 인용의 유일한 근거다.** `page` · `page_end` · `heading_path` · `source_name` 이 전부
여기 들어 있고(`vector.py` 참고), 이걸 버리면 답변에 "무슨 문서 몇 쪽"을 붙일 수 없다.
검색 결과를 사람이 되짚는 것이 `metadata` 를 저장하기로 한 본래 이유이기도 하다
(`plan_log/indexing.md` 1회차 — 필터는 안 만들되 metadata 는 싣는다).

## 왜 경로별 점수가 `float | None` 인가

**한쪽 경로에만 걸린 청크가 실재한다.** dense 상위 50개와 BM25 상위 50개는 대부분 안 겹친다.
필수 `float` 로 두고 빠진 자리에 `0.0` 을 채우면 **"점수가 0"과 "후보가 아니었음"이 안
갈린다** — 나중에 "BM25 가 이 청크를 못 찾았다"를 세려 할 때 0.0 을 센 것인지 알 수 없다.
PyMuPDF 의 `extract()` 만 보면 병합 셀과 빈 칸이 안 갈리던 것과 같은 종류의 손실이다
(CLAUDE.md "PDF 병합 셀").

## 왜 `fused_score` 를 따로 두나

**dense 점수와 BM25 점수는 단위가 다르다.** dense 는 단위 벡터의 내적이라 대략 [-1, 1] 이고
BM25 는 상한이 없다. 그대로 더하면 BM25 가 항상 이긴다. RRF(Reciprocal Rank Fusion)는
점수가 아니라 **순위**만 써서 정규화 자체가 필요 없고, 그 결과를 여기 담는다.
`sort` 의 기준은 `rerank_score` 가 있으면 그것, 없으면 이 값이다.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from rag_pipeline.schemas.vector import SearchResult

__all__ = ["RetrievedChunk"]


# 검색으로 돌아온 청크 하나. SearchResult 에 "어느 경로에서 왔나"를 얹은 것이다
class RetrievedChunk(BaseModel):
    # 사람이 읽는 청크 id. f"{document_id}:{chunk_index:04d}"
    chunk_id: str
    # 문서 단위 키(원본 파일명 NFC). 같은 문서의 청크를 묶을 때 쓴다
    document_id: str
    text: str
    # page·page_end·heading_path·source_name. **출처 인용의 유일한 근거다**
    metadata: dict[str, Any] = Field(default_factory=dict)

    # dense 후보가 아니었으면 None. 0.0 과 구분된다 (모듈 docstring 참고)
    dense_score: float | None = None
    # BM25 후보가 아니었으면 None
    bm25_score: float | None = None
    # RRF 병합 결과. 경로가 하나뿐이어도 채운다 — 정렬 기준이 늘 같은 자리에 있어야 한다
    fused_score: float = 0.0
    # 리랭커를 안 돌렸으면 None. 있으면 이 값이 정렬 기준이 된다
    rerank_score: float | None = None

    # 저장소가 돌려준 결과 한 건을 검색 결과로 승격한다. 경로별 점수는 부르는 쪽이 채운다
    @classmethod
    def from_search_result(
        cls,
        result: SearchResult,
        *,
        dense_score: float | None = None,
        bm25_score: float | None = None,
    ) -> RetrievedChunk:
        return cls(
            chunk_id=result.chunk_id,
            document_id=result.document_id,
            text=result.text,
            metadata=result.metadata,
            dense_score=dense_score,
            bm25_score=bm25_score,
        )

    # 정렬에 쓸 점수. 리랭커를 돌렸으면 그 값이 이긴다 —
    # 이 우선순위를 부르는 쪽마다 다시 쓰면 한 곳만 빠뜨렸을 때 조용히 순서가 달라진다
    @property
    def ranking_score(self) -> float:
        return self.rerank_score if self.rerank_score is not None else self.fused_score
