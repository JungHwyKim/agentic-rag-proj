"""사람이 여는 화면. API 가 아니라서 api_prefix 를 안 붙인다 (`main.py` 참고)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from backend.app.core.templates import templates

router = APIRouter(tags=["pages"])


# 채팅 화면 전체. 문서 목록은 여기서 안 싣고 HTMX 가 실린 뒤 따로 불러온다 —
# 저장소를 못 읽어도 화면 자체는 떠야 하기 때문이다
@router.get("/", response_class=HTMLResponse)
async def chat(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "chat.html", {})
