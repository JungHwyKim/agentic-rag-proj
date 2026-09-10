"""오늘이 며칠인지 알려주는 도구.

## 왜 이것이 필요한가

모델은 **오늘을 모른다.** 지식 컷오프가 코퍼스 연도와 어긋나 있어서, 「올해 지침」·「최근
개정」·「이번 달」 같은 상대 표현을 절대 날짜로 풀 방법이 없다. 이 코퍼스가 연도를 달고 있는
공문서(`2026 중등 기초학력…`)라 그 풀이가 실제로 검색을 가른다.

무료·결정적이고 키가 없어 **기본 pytest 를 한 줄도 안 건드린다** — 도구 넷 중 유일하다.

## 시계를 주입받는다

안 꽂으면 테스트가 날마다 다른 값을 보고, 그러면 잠글 수 있는 것이 「예외가 안 난다」뿐이다.
`RetrieverTool` 이 `store`·`embedder` 를 생성자로 받는 것과 같은 기준이다 —
**수명(여기서는 시각)을 정하는 것은 도구가 아니라 도구를 만드는 쪽이다.**

## `zoneinfo` 를 안 쓴다 — Windows 에서 죽는다

`zoneinfo.ZoneInfo("Asia/Seoul")` 은 **시스템 tz 데이터베이스**를 찾는데 Windows 에는 그것이
없어 `ZoneInfoNotFoundError` 가 난다(`tzdata` 를 따로 깔아야 한다). 이 저장소의 개발·운영
머신이 Windows 라 그 의존성을 들이는 대신 고정 오프셋을 쓴다 — **한국은 서머타임이 없어
UTC+9 가 영구히 참이고**, 그래서 이 축에서는 고정 오프셋이 근사가 아니라 정확한 값이다.

## KST 인 것과 `ToolCallLog.timestamp` 가 UTC 인 것은 안 어긋난다

축이 다르다. 저쪽이 답하는 것은 *언제 기록됐나*(기계의 시각)이고 이쪽은 *사용자가 사는
달력*이다. 「이번 달」의 뜻을 UTC 로 답하면 한국 시간 오전 9시 이전에 날짜가 하루 어긋난다.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from rag_pipeline.agent.tools.base import BaseTool
from rag_pipeline.schemas.agent import ToolInput, ToolOutput

__all__ = ["KST", "CurrentDateTool"]

# 한국 표준시. **고정 오프셋인 이유는 모듈 docstring 참고** (서머타임이 없다)
KST = timezone(timedelta(hours=9), "KST")

# 월요일이 0 인 `datetime.weekday()` 순서다
_WEEKDAYS = ("월", "화", "수", "목", "금", "토", "일")


# 기본 시계. 주입이 없을 때만 쓴다
def _now_kst() -> datetime:
    return datetime.now(KST)


# 오늘 날짜·시각을 돌려준다. **질의를 안 읽는다** — 입력이 없는 도구다
class CurrentDateTool(BaseTool):
    name = "current_date"
    description = "오늘 날짜와 시각을 알려준다 (한국 표준시)"

    # 시계는 부르는 쪽이 정한다 (모듈 docstring 참고)
    def __init__(self, *, now: Callable[[], datetime] | None = None) -> None:
        self._now = now or _now_kst

    def _run(self, tool_input: ToolInput) -> ToolOutput:
        moment = _as_kst(self._now())
        return ToolOutput(
            success=True,
            data={
                "date": moment.date().isoformat(),
                "datetime": moment.isoformat(),
                "year": moment.year,
                "month": moment.month,
                "day": moment.day,
                "weekday": _WEEKDAYS[moment.weekday()],
                "timezone": "Asia/Seoul (UTC+09:00)",
            },
        )


# 꽂힌 시계가 무엇을 주든 KST 로 맞춘다. **naive 는 `astimezone()` 에 안 넘긴다** —
# 그 메서드는 tzinfo 가 없는 값을 **머신의 로컬 시간**으로 읽어, 테스트가 도는 머신에 따라
# 결과가 갈리는데 에러가 안 난다. 여기서는 「시각을 정하는 것은 부르는 쪽」이라
# naive 를 KST 로 **선언**하는 것이 맞다
def _as_kst(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=KST)
    return moment.astimezone(KST)
