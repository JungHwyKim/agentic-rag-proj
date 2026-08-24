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

## 왜 `cached_from` 이 필드 하나인가

캐시가 답을 되돌려줄 때 화면은 셋을 함께 쓴다 — **원 질문**(사용자가 뜻이 같은지 직접
판단한다) · **시각**("3일 전") · **점수**(임계값이 어디쯤에서 걸렸나). 이것을
`cached: bool` + `cached_query` + `cached_at` 처럼 나란한 필드로 쪼개면 *`cached=True` 인데
`cached_at` 이 없는* 못 쓰는 조합이 생기고, 그 조합을 템플릿이 매번 방어해야 한다.

한 덩어리를 `None` 로 두면 **"캐시가 아니다"가 한 곳에서만 판정된다.** 이 저장소가 두 번
같은 선택을 했다 — `RetrievedChunk` 의 경로별 점수를 `float | None` 로 둔 것,
`StoredChunk` 와 `SearchResult` 를 가른 것. 둘 다 *"0.0 과 없음은 다르다"* 였다.

## 왜 `sources` 가 비어 있을 수 있나

**근거를 못 찾은 것은 실패가 아니다.** 관련 청크가 없으면 생성 쪽은 "문서에서 찾지
못했습니다"를 답하고 `sources` 는 빈 리스트로 나간다. 실패(키 없음·API 오류)는 그것과 달리
`GenerationError` 로 올라간다 — 사용자가 할 일이 서로 다르기 때문이다.
**템플릿은 이 분기를 반드시 처리한다.**
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from rag_pipeline.schemas.retrieval import RetrievedChunk

__all__ = ["Answer", "CacheOrigin"]


# 이 답변이 캐시에서 왔을 때 그 출처. **셋이 늘 함께 있거나 통째로 없다** (모듈 docstring 참고)
class CacheOrigin(BaseModel):
    # 캐시에 저장될 때의 원래 질문. 지금 질문과 다르다 — 그 다름을 사람이 보고 판단한다
    query: str
    created_at: datetime
    # 지금 질문과 원 질문의 유사도. 임계값 판정에 쓴 그 값이다
    score: float


# 질문 하나에 대한 답변과 그 근거
class Answer(BaseModel):
    query: str
    text: str
    # 답변이 근거로 삼은 청크들. **비어 있을 수 있다** (모듈 docstring 참고)
    sources: list[RetrievedChunk] = Field(default_factory=list)
    # 캐시에서 왔으면 그 출처, 새로 만들었으면 None. **이것이 캐시 여부의 유일한 판정**이다
    cached_from: CacheOrigin | None = None
