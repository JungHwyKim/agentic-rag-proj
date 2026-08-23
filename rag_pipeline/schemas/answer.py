"""질의 경로의 최종 출력 스키마.

## 왜 `sources` 가 `RetrievedChunk` 인가

**출처 인용에 필요한 것이 이미 거기 다 있다.** `text` · `metadata`(`page`·`page_end`·
`heading_path`·`source_name`) · `dense_score` 까지. 여기에 `{page, heading_path, chunk_text}`
같은 축소 dict 를 새로 정의하면 `SearchResult`·`RetrievedChunk` 위에 **세 번째 진실**이
생기고, 단계를 넘을 때마다 이름을 바꿔 붙이게 된다(`retrieval.py` 가 `content` 를 안 쓰고
`text` 를 쓴 것과 같은 이야기다).

`retrieval.py` docstring 은 *"dense 만 쓸 때는 `SearchResult` 로 충분하다"* 고 말한다. 그것은
**검색 내부의 이야기**이고 여기는 축이 다르다 — `Answer` 는 라우트와 템플릿이 렌더하는
**공개 계약**이라, BM25 가 붙는 날 원소 타입이 바뀌면 그 둘을 함께 고쳐야 한다.

## 왜 `sources` 가 비어 있을 수 있나

**근거를 못 찾은 것은 실패가 아니다.** 관련 청크가 없으면 생성 쪽은 "문서에서 찾지
못했습니다"를 답하고 `sources` 는 빈 리스트로 나간다. 실패(키 없음·API 오류)는 그것과 달리
`GenerationError` 로 올라간다 — 사용자가 할 일이 서로 다르기 때문이다.
**템플릿은 이 분기를 반드시 처리한다.**
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from rag_pipeline.schemas.retrieval import RetrievedChunk

__all__ = ["Answer"]


# 질문 하나에 대한 답변과 그 근거
class Answer(BaseModel):
    query: str
    text: str
    # 답변이 근거로 삼은 청크들. **비어 있을 수 있다** (모듈 docstring 참고)
    sources: list[RetrievedChunk] = Field(default_factory=list)
