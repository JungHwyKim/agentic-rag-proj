"""콘텐츠 핸들러들이 공유하는 자료구조와 헬퍼."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from rag_pipeline.config import ContentRoutingConfig
from rag_pipeline.schemas.document import Element


# 문서 안에서 발견되는 콘텐츠의 종류. 어떤 핸들러로 보낼지를 결정한다
class ContentType(StrEnum):
    TEXT = "text"
    TABLE = "table"
    IMAGE = "image"


# 문서 로더가 감지한 콘텐츠 한 조각. 핸들러가 이걸 받아 Element로 바꾼다
@dataclass
class ContentRegion:
    content_type: ContentType
    # 문서 내 원래 순서. 핸들러가 병렬 실행돼도 이 값으로 다시 정렬한다
    order: int
    source_format: str
    page: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    # TEXT: 이미 추출된 텍스트
    text: str | None = None
    # TABLE(네이티브): 2차원 셀 배열. docx/pptx/xlsx/hwpx 는 여기에 구조가 들어온다
    rows: list[list[str]] | None = None
    # TABLE/IMAGE(PDF·이미지 출처): 크롭된 이미지 또는 단일 페이지 PDF 바이트
    blob: bytes | None = None
    # blob의 종류를 알려준다 ("pdf" | "png")
    blob_kind: str | None = None
    # TABLE(PDF): 빈 칸을 **부분적으로** 덮는 드로잉이 있는 칸의 (행, 열) 자리.
    # 표를 Vision 으로 올릴지 판정하는 신호이자, 모델이 놓친 칸을 메우는 근거다.
    #
    # **개수가 아니라 자리인 이유**: 실측(2026-08-22, 유료 3회)으로 Vision 이 자리를
    # 실행마다 다르게 잡았다(65/68/70 of 73, 틀리는 쪽이 매번 옮겨 다녔다). 반면 기하는
    # 오검출 0 이다. 개수만 실어 나르면 "몇 개 부족한지"만 알고 "어디가 비었는지"를 모른다.
    #
    # 로더가 재고 라우터·핸들러가 읽는다 — `metadata` 사설 키를 못 쓰는 이유가 여기 있다.
    # 그 방식(`pdf.py:_BOUNDARY_SPANS_KEY`)은 `detect_regions()` 가 끝에서 지우는데,
    # 그건 판정이 로더 안에서 끝나기 때문이다. 이 값은 그 뒤에 읽힌다
    mark_cells: list[tuple[int, int]] = field(default_factory=list)
    # 요소에 함께 실어보낼 부가정보 (heading_level, sheet_name 등)
    metadata: dict[str, Any] = field(default_factory=dict)


# 한 문서를 파싱하는 동안 누적되는 상태. 비용 카운터와 경고를 모은다
@dataclass
class LoadContext:
    config: ContentRoutingConfig
    vision_calls: int = 0
    llamaparse_calls: int = 0
    ocr_applied: bool = False
    warnings: list[str] = field(default_factory=list)

    # 중복 경고가 쌓이지 않도록 같은 메시지는 한 번만 기록
    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)


# 콘텐츠 리전 하나를 Element 목록으로 변환하는 핸들러 인터페이스
class ContentHandler(Protocol):
    def handle(self, region: ContentRegion, ctx: LoadContext) -> list[Element]: ...


# 셀 안의 파이프·개행이 마크다운 표를 깨뜨리지 않도록 이스케이프
def _escape_cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\r\n", " ").replace("\n", " ").strip()


# 2차원 셀 배열을 마크다운 파이프 테이블 문자열로 직렬화
def rows_to_markdown(rows: list[list[str]]) -> str:
    cleaned = [[_escape_cell(c) for c in row] for row in rows if row is not None]
    # 전부 빈 문자열인 행은 버린다
    cleaned = [row for row in cleaned if any(c for c in row)]
    if not cleaned:
        return ""

    width = max(len(row) for row in cleaned)
    # 행마다 길이가 다르면 짧은 쪽을 빈 칸으로 채워 열 수를 맞춘다
    cleaned = [row + [""] * (width - len(row)) for row in cleaned]

    header, *body = cleaned
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in body)
    return "\n".join(lines)


# 바이트 내용의 해시. 같은 표·이미지를 두 번 외부 API에 보내지 않기 위한 캐시 키
def content_hash(data: bytes, *, salt: str = "") -> str:
    digest = hashlib.sha256()
    digest.update(salt.encode("utf-8"))
    digest.update(data)
    return digest.hexdigest()


# 외부 API 응답을 파일로 캐싱한다. 개발 중 같은 문서를 반복 업로드할 때 비용을 크게 아낀다
class ResultCache:
    def __init__(self, cache_dir: Path, *, enabled: bool = True) -> None:
        self.cache_dir = Path(cache_dir)
        self.enabled = enabled

    # 캐시 키에 해당하는 파일 경로
    def _path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    # 캐시된 결과를 읽는다. 없거나 깨졌으면 None
    def get(self, key: str) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        path = self._path(key)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    # 결과를 캐시에 기록. 실패해도 파이프라인을 막지 않는다
    def set(self, key: str, value: dict[str, Any]) -> None:
        if not self.enabled:
            return
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            self._path(key).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
