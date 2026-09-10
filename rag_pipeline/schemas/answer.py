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

## 왜 `verdict` 가 `bool` 이 아니라 세 상태인가

**실패가 판정이 아니기 때문이다.** Evaluator 가 죽었을 때(키 없음·응답 손상·호출 실패) 그것을
*불충분* 으로 떨어뜨리면, 답이 코퍼스에 없는 부정 예시 질의에서 **죽은 Evaluator 가 만점을
받는다** — 점수가 실력이 아니라 실패율을 재게 된다(`.claude/wiki/plan_log/검색-생성.md` 10회차).

그래서 `bool | None` 도 아니다. `None` 은 falsy 라 호출부의 `if not verdict.is_sufficient:`
**한 줄**이 실패를 다시 *불충분* 쪽으로 삼키는데, 그 한 줄은 아무 에러도 안 낸다. 계약을
지키는 것이 문장이 아니라 **타입**이어야 하는 자리다. 리랭커의 *"전부 아니면 전무"* 폴백과
같은 모양이고(`retrieval/reranker/base.py`), 거기서 경고가 별도 채널인 것도 같다.

`Answer.verdict` 자체가 `None` 인 것은 **판정을 안 돌렸다**는 뜻이다 — 꺼져 있거나(모델 이름이
없다) 답이 캐시에서 왔거나. `cached_from` 이 캐시 여부를 혼자 판정하는 것과 같은 모양이다.

## 왜 `rewrite` 가 문자열이 아니라 덩어리인가

**화면 문구가 `mode` 에서 갈리기 때문이다.** `fuse` 는 원 질의 갈래를 남긴 채 재작성 갈래를
더한 것이라 *"…로 바꿔 검색했습니다"* 가 **거짓**이고, `replace` 는 갈아끼운 것이라
*"…도 함께"* 가 거짓이다. 둘을 나란한 필드로 쪼개면 *질의는 있는데 mode 가 없는* 조합이
생기고, 그 조합에서 템플릿이 어느 쪽 문구를 고를지 정할 수 없다 — `cached_from` 이 셋을
한 덩어리로 묶은 것과 **글자 그대로 같은 기준**다.

`None` 은 **재작성으로 검색하지 않았다**는 뜻 하나다: 꺼져 있었거나(모델 이름이 없다),
폴백으로 내려갔거나(호출 실패·응답 손상), 답이 캐시에서 왔거나. 셋을 여기서 안 가르는 이유는
화면이 셋에 대해 **같은 것**을 해야 하기 때문이다 — 줄을 안 그린다. 사유를 갈라야 하는 날은
`rewriter.warnings` 가 이미 그 자리에 있다.

## 왜 `citation` 이 문제 있을 때만 채워지나

`SYSTEM_PROMPT` 규칙 3이 시킨 `[1]`·`[2]` 를 모델이 지켰는지의 관측이다. **맞게 인용했을
때는 `None`** 이라 화면이 줄을 안 그린다 — `cached_from`·`verdict`·`rewrite` 셋이 이미
쓰는 규칙이고 이유도 같다. 늘 채우면 모든 답변에 줄이 하나 늘어 정상이 소음이 된다.

덩어리인 이유도 저 셋과 같다. `ok: bool` + 번호 목록으로 쪼개면 *`ok=True` 인데 목록이
찬* 못 쓰는 조합이 생기고, 화면이 그 조합을 매번 방어해야 한다.

**`verdict` 와 캐시 대우가 일부러 다르다.** `verdict` 는 캐시 히트면 `None` 인데 이쪽은
값이 실린다 — 저쪽이 답하는 것은 *이번 실행에서 판정을 돌렸나* 라 **실행의 속성**이고,
이쪽은 *이 답변의 인용이 맞나* 라 **답변의 속성**이다. 답변 본문과 `sources` 는 캐시에
그대로 실려 있어(`answer_cache/qdrant_cache.py:_to_payload`) 조회 때 **공짜로 다시
계산된다**(`generation/citations.py`). 그래서 캐시 payload 에도 안 싣는다 — 실으면 칸이
늘고 낡을 수 있는데 얻는 것이 없다.

## 왜 `sources` 가 비어 있을 수 있나

**근거를 못 찾은 것은 실패가 아니다.** 관련 청크가 없으면 생성 쪽은 "문서에서 찾지
못했습니다"를 답하고 `sources` 는 빈 리스트로 나간다. 실패(키 없음·API 오류)는 그것과 달리
`GenerationError` 로 올라간다 — 사용자가 할 일이 서로 다르기 때문이다.
**템플릿은 이 분기를 반드시 처리한다.**
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from rag_pipeline.schemas.retrieval import RetrievedChunk

__all__ = [
    "Answer",
    "CacheOrigin",
    "CitationTrace",
    "EvaluatorVerdict",
    "RetryTrace",
    "RewriteTrace",
    "StopReason",
]


# 이 답변이 캐시에서 왔을 때 그 출처. **셋이 늘 함께 있거나 통째로 없다** (모듈 docstring 참고)
class CacheOrigin(BaseModel):
    # 캐시에 저장될 때의 원래 질문. 지금 질문과 다르다 — 그 다름을 사람이 보고 판단한다
    query: str
    created_at: datetime
    # 지금 질문과 원 질문의 유사도. 임계값 판정에 쓴 그 값이다
    score: float


# 검색된 근거가 이 질문에 답하기에 충분한지에 대한 판정. **세 상태다** (모듈 docstring 참고)
class EvaluatorVerdict(BaseModel):
    # "failed" 는 **모델이 말할 수 없는 값**이다 — 판정이 아니라 판정을 못 했다는 뜻이라
    # 파서가 붙인다. 모델이 자기 실패를 선언하게 두면 실패 버킷이 모델의 말버릇을 재게 된다
    verdict: Literal["sufficient", "insufficient", "failed"]
    # 왜 그렇게 봤나. 채점표의 틀린 칸을 사람이 읽을 때 쓴다. 실패면 그 사유가 들어간다
    reasoning: str = ""


# 이 질의를 다시 써서 검색했을 때 그 흔적. **둘이 늘 함께 있거나 통째로 없다**
# (모듈 docstring 참고)
class RewriteTrace(BaseModel):
    # 재작성기가 낸 질의. **원 질의는 `Answer.query` 에 그대로 있다** — 여기가 답하는 것은
    # "그럼 무엇으로 검색했나" 하나다
    query: str
    # 원 질의 갈래를 남겼나(`fuse`) 갈아끼웠나(`replace`). 화면 문구가 여기서 갈린다
    mode: Literal["fuse", "replace"]


# 재시도 루프가 멈춘 이유. **`answered` 에 *충분* 과 *실패* 와 *판정 꺼짐* 이 함께 들어온다** —
# 셋을 가르는 것은 `evaluator_verdict` 이지 이 칸이 아니다. 여기가 답하는 것은 하나다:
# 근거를 더 찾으려는 시도가 **왜 끝났나**
#
# **루프의 개념인데 여기 사는 것은 import 방향 때문이다** — `schemas/agent.py` 가
# `EvaluatorVerdict` 를 여기서 가져가므로 반대로는 순환이다. `RetryTrace` 가 `Answer` 에
# 붙으면서 이 낱말이 답변 쪽 어휘도 되었고, 두 벌로 적으면 한쪽만 고쳐져도 에러가 안 난다.
# `schemas/agent.py` 가 `StopReason` 이라는 이름으로 그대로 재수출하므로 부르는 쪽은 안 바뀐다
StopReason = Literal["answered", "retries_exhausted", "rewrite_unavailable"]


# 재시도 루프로 답했을 때 그 흔적. **루프를 안 돌았으면 통째로 None 이다**
# (모듈 docstring 참고) — 꺼짐·캐시 히트·단발 경로 셋을 한꺼번에 뜻한다
class RetryTrace(BaseModel):
    # 몇 번 다시 찾아갔나. **0 이 정상값이다** — 첫 검색으로 «충분» 이 나오면 0 이고,
    # 그것은 루프를 돌았다는 사실과 다르지 않다(화면이 0 을 안 그리는 것은 화면의 규칙이다)
    count: int
    # 왜 멈췄나. `Answer.verdict` 와 함께 읽어야 «충분해서» 와 «실패해서» 가 갈린다
    stop_reason: StopReason


# 답변이 쓴 출처 번호가 근거와 어긋났을 때 그 흔적. **맞으면 통째로 None 이다**
# (모듈 docstring 참고)
class CitationTrace(BaseModel):
    # 무엇이 어긋났나. **둘은 동시에 못 난다** — 번호가 하나도 없어야 "missing" 이고
    # 그러면 범위 밖일 번호 자체가 없다 (`generation/citations.py`)
    problem: Literal["out_of_range", "missing"]
    # 근거 범위 밖을 가리킨 번호들. "missing" 이면 빈 목록이다
    numbers: list[int] = Field(default_factory=list)
    # 실제 근거 개수. 화면이 «근거는 2개인데» 를 말하려면 이 값이 있어야 한다 —
    # `sources` 를 세도 같지만, 그러면 흔적 하나를 읽는 데 두 필드를 맞춰 봐야 한다
    source_count: int


# 질문 하나에 대한 답변과 그 근거
class Answer(BaseModel):
    query: str
    text: str
    # 답변이 근거로 삼은 청크들. **비어 있을 수 있다** (모듈 docstring 참고)
    sources: list[RetrievedChunk] = Field(default_factory=list)
    # 캐시에서 왔으면 그 출처, 새로 만들었으면 None. **이것이 캐시 여부의 유일한 판정**이다
    cached_from: CacheOrigin | None = None
    # Evaluator 를 돌렸으면 그 판정, 안 돌렸으면 None (꺼짐 또는 캐시 히트).
    # **판정이 답변을 바꾸지 않는다** — 지금은 재시도할 곳이 없어 싣기만 한다
    verdict: EvaluatorVerdict | None = None
    # 재작성으로 검색했으면 그 흔적, 아니면 None (꺼짐·폴백·캐시 히트).
    # **재작성이 답변을 바꾸는 것은 맞지만 그 사실이 여기 실리는 것은 관측이다** —
    # `verdict` 와 같은 자리이고, 화면과 `query_log` 가 둘 다 이 하나를 읽는다
    rewrite: RewriteTrace | None = None
    # 인용이 근거와 어긋났으면 그 흔적, 맞으면 None. **검사가 답변을 바꾸지 않는다** —
    # `verdict` 와 같은 계약이고, 틀린 번호를 지우지 않는 이유는
    # `generation/citations.py` docstring 에 있다. 캐시 히트에도 값이 실린다(위 참고)
    citation: CitationTrace | None = None
    # 재시도 루프로 답했으면 그 흔적, 아니면 None (루프 꺼짐 · 캐시 히트 · 단발 경로).
    # **`verdict` 와 같은 축이라 캐시 payload 에는 안 실린다** — *이번 실행에서 몇 번
    # 돌았나* 는 실행의 속성이지 답변의 속성이 아니다. 인용 검사가 반대쪽인 이유
    # (*이 답변의 인용이 맞나* 는 답변의 속성이라 캐시 히트에도 다시 계산된다)와 짝이다
    retry: RetryTrace | None = None
