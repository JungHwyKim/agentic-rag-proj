"""캐시 배지의 상대 시각 필터. **경계만 본다.**

`_answer.html` 안에서 계산하지 않고 필터로 뺀 이유가 이 파일이다 — 렌더된 HTML 문자열로
59초와 60초를 가르려 하면 무엇이 틀렸는지 안 보인다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.app.core.templates import relative_time

_NOW = datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("delta", "expected"),
    [
        (timedelta(seconds=0), "방금"),
        (timedelta(seconds=59), "방금"),
        (timedelta(seconds=60), "1분 전"),
        (timedelta(minutes=59), "59분 전"),
        (timedelta(minutes=60), "1시간 전"),
        (timedelta(hours=23, minutes=59), "23시간 전"),
        (timedelta(hours=24), "1일 전"),
        (timedelta(days=3), "3일 전"),
    ],
)
def test_boundaries(delta: timedelta, expected: str):
    assert relative_time(_NOW - delta, now=_NOW) == expected


# **미래 시각은 "방금" 으로 접는다.** 시계가 어긋나면 음수 초가 나오는데, 그대로 나누면
# "-1일 전" 이 화면에 뜬다
def test_future_time_folds_to_just_now():
    assert relative_time(_NOW + timedelta(hours=5), now=_NOW) == "방금"


# 시각대가 없는 값이 들어올 수 있다 — payload 에서 되살린 값이라 저장 당시 형식에 달렸다.
# 여기서 예외가 나면 배지 하나 때문에 답변 전체가 안 그려진다
def test_naive_datetime_is_assumed_utc():
    naive = (_NOW - timedelta(days=2)).replace(tzinfo=None)

    assert relative_time(naive, now=_NOW) == "2일 전"


def test_none_renders_nothing():
    assert relative_time(None) == ""
