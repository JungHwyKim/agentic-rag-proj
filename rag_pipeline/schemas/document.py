"""로더가 만들어내는 통일 출력 스키마. 파일 포맷과 무관하게 항상 이 형태로 수렴한다."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


# 지원·감지 가능한 파일 포맷. 확장자가 아니라 매직바이트 판별 결과를 담는다
class FileFormat(StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    PPTX = "pptx"
    XLSX = "xlsx"
    XLS = "xls"
    HWPX = "hwpx"
    IMAGE = "image"
    TEXT = "text"
    CSV = "csv"
    MARKDOWN = "markdown"
    # 감지는 되지만 파싱은 지원하지 않는 구형 포맷 (안내 메시지를 주기 위해 구분)
    HWP = "hwp"
    DOC = "doc"
    PPT = "ppt"
    UNKNOWN = "unknown"


# 파싱 결과 요소의 의미적 종류. 청킹 단계에서 요소별로 다르게 다루기 위한 구분
class ElementType(StrEnum):
    TITLE = "title"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    LIST = "list"
    IMAGE_TEXT = "image_text"
    SHEET = "sheet"
    SLIDE_NOTE = "slide_note"


# 파싱된 문서를 이루는 최소 단위 요소
class Element(BaseModel):
    type: ElementType
    text: str
    # PDF 페이지 / PPT 슬라이드 / Excel 시트 인덱스 (1부터). 해당 없으면 None
    page: int | None = None
    # 문서 내 원래 순서. 핸들러가 병렬 실행돼도 이 값으로 다시 정렬한다
    order: int = 0
    # 포맷별 부가정보: bbox, sheet_name, heading_level, rows(표 원본 배열) 등
    metadata: dict[str, Any] = Field(default_factory=dict)


# 원본 파일과 파싱 과정에 대한 메타데이터
class DocumentMetadata(BaseModel):
    source_name: str
    file_format: FileFormat
    size_bytes: int
    page_count: int | None = None
    title: str | None = None
    author: str | None = None
    created_at: datetime | None = None
    # OCR을 한 번이라도 돌렸는지
    ocr_applied: bool = False
    # Vision 모델 호출 횟수 (비용 추적용)
    vision_calls: int = 0
    # LlamaParse 호출 횟수 (비용 추적용)
    llamaparse_calls: int = 0
    parse_duration_ms: int = 0
    # cleaner 가 실제로 손댄 요소 수. 검색 품질이 이상할 때 정리 단계를 의심할 근거가 된다
    cleaned_elements: int = 0
    # 파싱 중 발생했으나 치명적이지 않았던 문제 (폴백 발생 등)
    warnings: list[str] = Field(default_factory=list)


# 로더의 최종 산출물. 다음 단계(cleaner -> splitter)의 입력이 된다
class ParsedDocument(BaseModel):
    metadata: DocumentMetadata
    elements: list[Element] = Field(default_factory=list)

    # 모든 요소의 텍스트를 순서대로 이어붙인 전문
    @property
    def full_text(self) -> str:
        return "\n\n".join(e.text for e in self.elements if e.text.strip())
