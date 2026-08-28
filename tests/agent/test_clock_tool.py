"""날짜 도구 — 꽂은 시계로 재고, 타임존이 KST 인지 본다.

시계를 주입 못 하면 잠글 수 있는 것이 「예외가 안 난다」뿐이다. 그래서 이 파일은 값을
직접 단언하고, 그럴 수 있는 근거가 `CurrentDateTool(now=...)` 하나다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from rag_pipeline.agent.tools.clock import KST, CurrentDateTool
from rag_pipeline.schemas.agent import ToolInput


def _at(moment: datetime) -> dict:
    return CurrentDateTool(now=lambda: moment).run(ToolInput(query="")).data


# 꽂은 시계가 그대로 나온다. 날마다 다른 값을 보지 않는다
def test_injected_clock_decides_the_answer():
    data = _at(datetime(2026, 8, 29, 14, 3, 0, tzinfo=KST))

    assert data["date"] == "2026-08-29"
    assert data["year"] == 2026
    assert data["month"] == 8
    assert data["day"] == 29
    assert data["weekday"] == "토"
    assert data["datetime"].startswith("2026-08-29T14:03:00")


# 오프셋이 +09:00 이다. **`zoneinfo` 를 안 쓰는 것이 이 값을 지키는 방법이다** —
# Windows 에는 tz 데이터베이스가 없어 ZoneInfo("Asia/Seoul") 이 죽는다
def test_offset_is_kst():
    data = _at(datetime(2026, 1, 1, 0, 0, tzinfo=KST))

    assert data["datetime"].endswith("+09:00")
    assert "UTC+09:00" in data["timezone"]


# 다른 타임존을 꽂아도 KST 로 옮겨 답한다 — 「사용자가 사는 달력」이 이 도구의 축이다.
# UTC 8월 28일 22시는 한국에서 이미 29일 아침이다
def test_other_timezone_is_converted_not_relabelled():
    data = _at(datetime(2026, 8, 28, 22, 0, tzinfo=UTC))

    assert data["date"] == "2026-08-29"
    assert data["day"] == 29


# **naive 시각은 로컬 시간으로 읽지 않는다.** `astimezone()` 에 그대로 넘기면 테스트가 도는
# 머신의 타임존에 따라 날짜가 갈리는데 에러가 안 난다
def test_naive_clock_is_declared_kst_not_localtime():
    data = _at(datetime(2026, 8, 29, 0, 30))

    assert data["date"] == "2026-08-29"
    assert data["datetime"].endswith("+09:00")


# 질의를 안 읽는다 — 입력이 없는 도구다
def test_query_is_ignored():
    tool = CurrentDateTool(now=lambda: datetime(2026, 8, 29, tzinfo=KST))

    assert tool.run(ToolInput(query="")).data == tool.run(ToolInput(query="아무 말")).data


# 주입이 없으면 진짜 시계다. 값은 못 박고 **모양만** 잰다
def test_default_clock_is_now_in_kst():
    data = CurrentDateTool().run(ToolInput(query="")).data
    now = datetime.now(KST)

    assert data["date"] == now.date().isoformat()
    assert abs(datetime.fromisoformat(data["datetime"]) - now) < timedelta(seconds=5)
