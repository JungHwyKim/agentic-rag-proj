"""Word 로더 (.docx).

python-docx의 document.paragraphs 와 document.tables 는 각각 따로 나오기 때문에
그대로 쓰면 본문과 표의 원본 순서가 섞인다. 여기서는 body의 자식 XML을 직접
순회해서 문단·표·이미지가 문서에 등장한 순서를 그대로 보존한다.
"""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from rag_pipeline.ingestion.loader.content.base import ContentRegion, ContentType, LoadContext
from rag_pipeline.ingestion.loader.document.base import BaseDocumentLoader
from rag_pipeline.schemas.document import ElementType, FileFormat


# 표를 2차원 셀 배열로 변환. 병합된 셀은 같은 텍스트가 반복되므로 그대로 둔다
def _table_to_rows(table: Table) -> list[list[str]]:
    return [[cell.text.strip() for cell in row.cells] for row in table.rows]


# 문단 스타일 이름으로 제목/목록/본문을 구분한다
def _element_type_of(paragraph: Paragraph) -> ElementType:
    style = (paragraph.style.name or "").lower() if paragraph.style is not None else ""
    if style.startswith("heading") or style in ("title", "subtitle"):
        return ElementType.TITLE
    if "list" in style:
        return ElementType.LIST
    return ElementType.PARAGRAPH


# 제목 스타일에서 단계(Heading 1 -> 1)를 뽑는다. 없으면 None
def _heading_level(paragraph: Paragraph) -> int | None:
    style = (paragraph.style.name or "") if paragraph.style is not None else ""
    parts = style.split()
    if len(parts) == 2 and parts[0].lower() == "heading" and parts[1].isdigit():
        return int(parts[1])
    return None


# .docx 를 읽는 로더
class DocxLoader(BaseDocumentLoader):
    file_format = FileFormat.DOCX

    # body 자식 요소를 순서대로 훑어 문단·표·이미지 리전을 만든다
    def detect_regions(self, path: Path, ctx: LoadContext) -> list[ContentRegion]:
        document = Document(str(path))
        regions: list[ContentRegion] = []
        order = 0

        for child in document.element.body.iterchildren():
            if child.tag == qn("w:p"):
                paragraph = Paragraph(child, document)

                # 문단 안에 박힌 그림을 먼저 꺼낸다 (본문보다 앞에 그려지는 경우가 많다)
                for blob in self._images_in(paragraph, document):
                    regions.append(
                        ContentRegion(
                            content_type=ContentType.IMAGE,
                            order=order,
                            source_format=self.file_format.value,
                            blob=blob,
                            blob_kind="png",
                        )
                    )
                    order += 1

                text = paragraph.text.strip()
                if not text:
                    continue

                metadata: dict[str, object] = {"element_type": _element_type_of(paragraph)}
                level = _heading_level(paragraph)
                if level is not None:
                    metadata["heading_level"] = level

                regions.append(
                    ContentRegion(
                        content_type=ContentType.TEXT,
                        order=order,
                        source_format=self.file_format.value,
                        text=text,
                        metadata=metadata,
                    )
                )
                order += 1

            elif child.tag == qn("w:tbl"):
                rows = _table_to_rows(Table(child, document))
                if any(any(cell for cell in row) for row in rows):
                    regions.append(
                        ContentRegion(
                            content_type=ContentType.TABLE,
                            order=order,
                            source_format=self.file_format.value,
                            rows=rows,
                        )
                    )
                    order += 1

        return regions

    # 문단 XML에서 참조된 이미지 바이트를 꺼낸다
    def _images_in(self, paragraph: Paragraph, document) -> list[bytes]:
        blobs: list[bytes] = []
        for blip in paragraph._p.findall(f".//{qn('a:blip')}"):
            rel_id = blip.get(qn("r:embed"))
            if not rel_id:
                continue
            try:
                part = document.part.related_parts[rel_id]
            except KeyError:
                continue
            blobs.append(part.blob)
        return blobs

    # 문서 속성에서 제목·작성자를 읽는다
    def extract_metadata(self, path: Path) -> dict[str, object]:
        document = Document(str(path))
        props = document.core_properties
        return {
            "title": props.title or None,
            "author": props.author or None,
        }
