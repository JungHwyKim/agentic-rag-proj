"""Jinja2 템플릿 로더. 두 라우터가 같은 인스턴스를 쓴다 —
따로 만들면 필터·전역을 한쪽에만 등록하고도 아무 에러가 안 난다."""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

__all__ = ["TEMPLATES_DIR", "templates"]
