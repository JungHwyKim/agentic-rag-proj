"""Jinja2 템플릿 로더. 두 라우터가 같은 인스턴스를 쓴다 —
따로 만들면 필터·전역을 한쪽에만 등록하고도 아무 에러가 안 난다."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# 지난 시각을 사람 말로 바꾼다. 캐시 배지가 "3일 전 비슷한 질문에…" 를 그리는 데 쓴다.
#
# **템플릿이 아니라 여기 있는 이유**: Jinja 안에서 시간 계산을 하면 테스트가 렌더된 HTML
# 문자열로만 검증할 수 있는데, 경계(59초 vs 1분)를 그 방식으로 재면 무엇이 틀렸는지 안 보인다.
#
# 시각이 순진한(naive) 값으로 들어올 수 있다 — payload 에서 되살린 값이라 저장 당시 형식에
# 달렸다. UTC 로 가정해 붙인다. 여기서 예외가 나면 배지 하나 때문에 답변 전체가 안 그려진다
def relative_time(when: datetime | None, *, now: datetime | None = None) -> str:
    if when is None:
        return ""
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)

    delta = (now or datetime.now(UTC)) - when
    seconds = int(delta.total_seconds())
    # 미래 시각(시계 어긋남 등)은 "방금" 으로 접는다. 음수를 그대로 쓰면 "-2일 전" 이 나온다
    if seconds < 60:
        return "방금"
    if seconds < 3600:
        return f"{seconds // 60}분 전"
    if seconds < 86400:
        return f"{seconds // 3600}시간 전"
    return f"{seconds // 86400}일 전"


templates.env.filters["relative_time"] = relative_time

__all__ = ["TEMPLATES_DIR", "relative_time", "templates"]
