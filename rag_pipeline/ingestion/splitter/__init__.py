"""적재 파이프라인 3단계 — 정리된 요소를 검색 단위(청크)로 만든다.

`ParsedDocument` 를 받아 `list[Chunk]` 를 돌려준다. **타입을 바꾸는 첫 단계다** — 앞 단계들은
`ParsedDocument -> ParsedDocument` 를 지켜 순차 합성되지만 여기서 사슬이 끝난다.

## 왜 자르지 않고 붙이는가

실측(`tests/fixtures/` 3종)에서 요소 자수 중앙값이 PDF 23 / HWPX 15 / 시행령 40 이었고
**1200자를 넘는 텍스트 요소가 한 개도 없었다.** PDF 는 PyMuPDF 블록 단위라 707개 중
349개(49%)가 20자 미만이다. 그래서 이 단계의 주 작업은 제목 경계까지 요소를 **병합**하는
것이고, 상한 분할은 표에서만 실제로 발화한다.

크기 하한은 두지 않는다. 섹션이 짧아도 다음 섹션과 합치지 않는다 — 서로 다른 절의 내용을
한 청크에 담으면 검색 결과가 어느 절 얘기인지 알 수 없게 된다.

병합 경계는 `ElementType.TITLE` 이다. **그 경계를 만드는 것은 이 단계가 아니라 로더**
(`loader/document/headings.py`)이고, splitter 는 포맷을 모른다 — HWPX 가 제목을 스타일이
아니라 글자 크기로 표시한다는 것 같은 사정은 전부 로더 쪽 문제다. 여기서 포맷을 보기
시작하면 로더의 2단계 라우팅이 세워둔 경계가 무너진다.

## ingest() 에 끼우지 않는 이유

`pipeline.py` 의 예약 주석은 `split → embed → index` 를 한 덩어리로 적어놨고, 청크를 소비할
곳(embedding)이 아직 없다. `ingest()` 의 반환 타입은 곧 `backend` 의 `response_model` 이라
청크를 내보내는 것은 splitter 결정이 아니라 API 결정이다. **빠뜨린 것이 아니라 결정이다** —
근거는 `plan_log/splitter.md` 의 "합성 지점" 절에 있다.

## chunk_id

`f"{document_key}:{chunk_index:04d}"` 다. `document_key` 를 안 주면 파싱 결과 전문의 해시를
쓴다. "재업로드 = 중복"인지는 indexing 결정이라 그 결정을 여기서 앞질러 내리지 않기
위해서다. **그 미결은 indexing 이 닫았다** — `records.py:document_id_for()` 가 원본 파일명에서
뽑는다. 상위(`pipeline.py:index_document()`)가 그 값을 `document_key` 로 넘기고
`Chunk` 스키마는 안 바뀌었다.

## 이 단계의 규약이 어디 있나

  sections.py    병합 경계(TITLE)까지 요소를 잇는다 · 제목 머리를 본문 앞에 붙이되
                 예산에서 미리 빼고 자른다(실측: 1200 상한에 최대 1258자)
  oversized.py   상한 초과 구간 분할 · 겹침을 여기서만 주는 이유
  tables.py      표는 텍스트 청커에 안 태운다 · 조각마다 헤더 행을 되풀이한다 ·
                 표에 겹침을 안 주는 이유(같은 행이 두 청크에 들어간다)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rag_pipeline.config import SplitterConfig

# content_hash 는 loader/content/base.py 에 있다. 새 단계가 같은 것을 복제하면 해시 규칙이
# 두 곳으로 갈라지므로 재사용한다 — rag-code-review references/contracts.md 3절
from rag_pipeline.ingestion.loader.content.base import content_hash
from rag_pipeline.ingestion.splitter.oversized import split_text
from rag_pipeline.ingestion.splitter.sections import Section, build_sections
from rag_pipeline.ingestion.splitter.tables import split_table
from rag_pipeline.schemas.chunk import Chunk, ChunkType
from rag_pipeline.schemas.document import Element, ElementType, ParsedDocument

__all__ = ["split"]

# document_key 로 쓸 해시 길이. 충돌 여지보다 로그 가독성을 택한 값이다
_KEY_LENGTH = 16

# 요소를 이어붙일 때 쓰는 구분자
_JOIN = "\n"


# 청크가 되기 직전의 조각. chunk_id/chunk_index 는 문서 전체를 훑어야 정해지므로 나중에 붙인다
@dataclass
class _Draft:
    type: ChunkType
    text: str
    orders: list[int]
    page: int | None
    page_end: int | None
    metadata: dict = field(default_factory=dict)


# 요소가 실제로 끝나는 쪽. HWPX 는 쪽을 넘긴 표에 page_end 를 실어 보낸다
def _element_page_end(element: Element) -> int | None:
    raw = element.metadata.get("page_end")
    if isinstance(raw, int) and raw > 0:
        return raw
    return element.page


# 요소 묶음이 걸쳐 있는 쪽 범위. 시작 쪽과, 그와 다를 때만 끝 쪽
def _page_span(elements: list[Element]) -> tuple[int | None, int | None]:
    starts = [e.page for e in elements if e.page is not None]
    ends = [p for p in (_element_page_end(e) for e in elements) if p is not None]
    if not starts:
        return None, None

    start = min(starts)
    end = max(ends) if ends else start
    return start, (end if end > start else None)


# 요소들을 상한 이하가 되도록 묶는다. **요소 경계에서만 끊는다** —
# 요소 하나가 통째로 상한을 넘는 경우만 나중에 글자 단위로 쪼갠다
def _group_by_size(elements: list[Element], max_chars: int) -> list[list[Element]]:
    groups: list[list[Element]] = []
    buffer: list[Element] = []
    size = 0

    for element in elements:
        length = len(element.text)
        if buffer and size + len(_JOIN) + length > max_chars:
            groups.append(buffer)
            buffer, size = [], 0
        buffer.append(element)
        size += length + (len(_JOIN) if size else 0)

    if buffer:
        groups.append(buffer)
    return groups


# 텍스트 요소 묶음 하나를 조각들로 만든다
def _text_drafts(group: list[Element], config: SplitterConfig, budget: int) -> list[_Draft]:
    text = _JOIN.join(element.text for element in group)
    orders = [element.order for element in group]
    page, page_end = _page_span(group)

    # 어느 요소가 이미지에서 왔는지 남긴다. 로더가 OCR/Vision 결과를 이미 텍스트로 만들어
    # 놨으므로 splitter 에 이미지 분기는 없지만, 인용할 때 출처를 되짚을 수 있어야 한다
    metadata: dict = {}
    image_orders = [e.order for e in group if e.type is ElementType.IMAGE_TEXT]
    if image_orders:
        metadata["image_orders"] = image_orders

    pieces = split_text(text, max_chars=budget, overlap=config.chunk_overlap_chars)
    if len(pieces) == 1:
        return [_Draft(ChunkType.TEXT, text, orders, page, page_end, metadata)]

    return [
        _Draft(
            ChunkType.TEXT,
            piece,
            orders,
            page,
            page_end,
            {**metadata, "part": index, "part_count": len(pieces)},
        )
        for index, piece in enumerate(pieces)
    ]


# 표 요소 하나를 조각들로 만든다
def _table_drafts(element: Element, config: SplitterConfig, budget: int) -> list[_Draft]:
    page, page_end = _page_span([element])
    base = {
        key: value
        for key, value in element.metadata.items()
        if key not in ("rows", "page_end")
    }

    pieces = split_table(element.text, element.metadata.get("rows"), max_chars=budget)

    # rows 를 못 얻었다. 마크다운을 글자 단위로 자르면 열 대응이 깨지지만, 통째로 두면
    # 상한을 크게 넘는 청크가 생긴다. 겹침은 주지 않는다 — 표에서는 행 중복이 된다
    if pieces is None:
        return [
            _Draft(ChunkType.TABLE, piece, [element.order], page, page_end, dict(base))
            for piece in split_text(element.text, max_chars=budget)
        ]

    if len(pieces) == 1:
        markdown, rows = pieces[0]
        metadata = dict(base)
        if rows:
            metadata["rows"] = rows
        return [_Draft(ChunkType.TABLE, markdown, [element.order], page, page_end, metadata)]

    return [
        _Draft(
            ChunkType.TABLE,
            markdown,
            [element.order],
            page,
            page_end,
            {**base, "rows": rows, "part": index, "part_count": len(pieces)},
        )
        for index, (markdown, rows) in enumerate(pieces)
    ]


# 섹션 하나를 문서 순서 그대로 조각들로 만든다.
# 표를 만나면 그 앞까지의 텍스트를 먼저 비운다 — 안 그러면 표 앞뒤 본문이 한 청크로 붙어
# 청크 순서가 문서 순서와 어긋난다
def _section_drafts(section: Section, config: SplitterConfig, budget: int) -> list[_Draft]:
    drafts: list[_Draft] = []
    pending: list[Element] = []

    def flush() -> None:
        for group in _group_by_size(pending, budget):
            drafts.extend(_text_drafts(group, config, budget))
        pending.clear()

    for element in section.body:
        if element.type is ElementType.TABLE:
            flush()
            drafts.extend(_table_drafts(element, config, budget))
            continue
        pending.append(element)

    flush()
    return drafts


# 청크 본문 맨 앞에 붙일 제목 경로. 표 청크와 분할 조각도 같은 머리를 받아야
# 그것만 검색됐을 때 어느 절 얘기인지 알 수 있다
def _heading_prefix(section: Section, config: SplitterConfig) -> str:
    if not config.include_heading_in_text or not section.heading_path:
        return ""
    return f"[{' > '.join(section.heading_path)}]\n"


# 본문에 쓸 수 있는 글자 수. 제목 머리를 붙인 **뒤에도** 상한을 지켜야 하므로 미리 뺀다.
# 안 빼면 상한을 선언해놓고 넘긴다(실측: 붙이고 나서 1200 상한에 3~9개 청크가 최대 1258자).
#
# 다만 절반 밑으로는 안 줄인다. 제목 경로가 상한의 절반을 먹는 문서라면 문제는 본문이 아니라
# 제목 쪽이고, 예산을 더 깎아봐야 청크 수만 폭발한다
def _body_budget(prefix: str, config: SplitterConfig) -> int:
    floor = max(config.max_chunk_chars // 2, 1)
    return max(config.max_chunk_chars - len(prefix), floor)


# 문서를 가리키는 기본 키. 같은 파싱 결과면 같은 값이 나오므로 chunk_id 가 안정된다
def _document_key(document: ParsedDocument) -> str:
    return content_hash(document.full_text.encode("utf-8"))[:_KEY_LENGTH]


# 정리된 문서를 청크 목록으로 만든다. 공개 진입점이고 HTTP 를 모른다
def split(
    document: ParsedDocument,
    config: SplitterConfig | None = None,
    *,
    document_key: str | None = None,
) -> list[Chunk]:
    config = config or SplitterConfig()
    key = document_key or _document_key(document)

    chunks: list[Chunk] = []
    for section in build_sections(document):
        prefix = _heading_prefix(section, config)
        for draft in _section_drafts(section, config, _body_budget(prefix, config)):
            index = len(chunks)
            chunks.append(
                Chunk(
                    chunk_id=f"{key}:{index:04d}",
                    document_key=key,
                    chunk_index=index,
                    type=draft.type,
                    text=prefix + draft.text,
                    source_name=document.metadata.source_name,
                    order=draft.orders[0] if draft.orders else 0,
                    element_orders=draft.orders,
                    page=draft.page,
                    page_end=draft.page_end,
                    heading=section.heading,
                    heading_path=list(section.heading_path),
                    heading_level=section.heading_level,
                    metadata=draft.metadata,
                )
            )
    return chunks
