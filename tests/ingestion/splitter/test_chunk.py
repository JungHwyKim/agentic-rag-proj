"""청크 자체의 계약 — id·상한·겹침·메타데이터."""

from __future__ import annotations

from rag_pipeline.config import SplitterConfig
from rag_pipeline.ingestion.splitter import split
from rag_pipeline.ingestion.splitter.oversized import split_text
from rag_pipeline.schemas.document import Element, ElementType
from tests.ingestion.splitter.conftest import para, title

PLAIN = SplitterConfig(include_heading_in_text=False)


# 같은 문서를 두 번 쪼개면 같은 id 가 나와야 한다. 재적재마다 id 가 바뀌면 색인이 중복된다
def test_chunk_id_is_deterministic(make_document) -> None:
    document = make_document([title("제목", level=1), para("본문")])

    first = split(document, PLAIN)
    second = split(document, PLAIN)

    assert [c.chunk_id for c in first] == [c.chunk_id for c in second]
    assert first[0].chunk_id.endswith(":0000")


# 내용이 다르면 키가 갈린다. 안 그러면 서로 다른 문서의 청크가 같은 id 를 갖는다
def test_document_key_differs_by_content(make_document) -> None:
    one = split(make_document([para("가")]), PLAIN)
    other = split(make_document([para("나")]), PLAIN)

    assert one[0].document_key != other[0].document_key


# indexing 이 진짜 document_id 를 정하면 그걸 넘긴다. 그때 Chunk 스키마는 안 바뀌어야 한다
def test_document_key_can_be_supplied(make_document) -> None:
    document = make_document([para("본문")])

    chunks = split(document, PLAIN, document_key="doc-42")

    assert chunks[0].document_key == "doc-42"
    assert chunks[0].chunk_id == "doc-42:0000"


# chunk_index 는 0부터 빈틈 없이 이어진다. order 와는 다른 수다
def test_chunk_index_is_contiguous(make_document) -> None:
    document = make_document(
        [title("가", level=1, order=5), para("A", order=7), title("나", level=1, order=9), para("B", order=30)]
    )

    chunks = split(document, PLAIN)

    assert [c.chunk_index for c in chunks] == [0, 1]
    assert [c.order for c in chunks] == [7, 30]


# 요소 하나가 통째로 상한을 넘으면 글자 단위로 쪼갠다
def test_oversized_element_is_split(make_document) -> None:
    document = make_document([para("문장이다. " * 200)])

    chunks = split(document, SplitterConfig(max_chunk_chars=300, include_heading_in_text=False))

    assert len(chunks) > 1
    assert all(len(c.text) <= 300 for c in chunks)
    assert [c.metadata["part"] for c in chunks] == list(range(len(chunks)))


# 요소 경계에서 먼저 끊는다. 요소가 상한보다 작으면 요소 중간을 자를 일이 없다
def test_groups_break_at_element_boundaries(make_document) -> None:
    document = make_document([para("가" * 80) for _ in range(10)])

    chunks = split(document, SplitterConfig(max_chunk_chars=200, include_heading_in_text=False))

    # 조각 번호가 안 붙는다 = 요소 중간을 자르지 않았다
    assert all("part" not in c.metadata for c in chunks)
    assert all(len(c.text) <= 200 for c in chunks)


# 겹침은 상한 초과로 쪼갠 구간에만 준다
def test_overlap_only_applies_to_oversized_split() -> None:
    text = "가나다라마바사아자차" * 50

    pieces = split_text(text, max_chars=100, overlap=20)

    assert len(pieces) > 1
    assert all(len(piece) <= 100 for piece in pieces)
    # 두 번째 조각은 앞 조각의 꼬리로 시작한다
    assert pieces[1].startswith(pieces[0][-20:])


# 겹침이 상한만큼 크면 조각이 앞으로 못 나아간다. 그때는 겹침을 포기한다
def test_overlap_larger_than_cap_is_ignored() -> None:
    pieces = split_text("가" * 500, max_chars=100, overlap=100)

    assert all(len(piece) <= 100 for piece in pieces)


# 섹션 경계로 나뉜 청크에는 겹침을 주지 않는다
def test_no_overlap_between_sections(make_document) -> None:
    document = make_document([title("가", level=1), para("첫째"), title("나", level=1), para("둘째")])

    chunks = split(document, SplitterConfig(chunk_overlap_chars=50, include_heading_in_text=False))

    assert [c.text for c in chunks] == ["첫째", "둘째"]


# 로더가 OCR/Vision 결과를 이미 텍스트로 만들어 놨으므로 splitter 에 이미지 분기는 없다.
# 다만 어느 요소가 이미지에서 왔는지는 인용을 위해 남는다
def test_image_text_is_merged_and_traceable(make_document) -> None:
    document = make_document(
        [
            title("그림 설명", level=1),
            para("아래 그림을 보라."),
            Element(
                type=ElementType.IMAGE_TEXT,
                text="그림 안의 글자",
                order=99,
                metadata={"image_source": "ocr"},
            ),
        ]
    )

    chunks = split(document, PLAIN)

    assert len(chunks) == 1
    assert "그림 안의 글자" in chunks[0].text
    assert chunks[0].metadata["image_orders"] == [99]


# 쪽 범위는 묶인 요소 전체를 덮어야 한다
def test_page_span_covers_group(make_document) -> None:
    document = make_document(
        [title("절", level=1), para("A", page=3), para("B", page=4), para("C", page=5)]
    )

    chunks = split(document, PLAIN)

    assert chunks[0].page == 3
    assert chunks[0].page_end == 5


# 한 쪽에 들어간 청크는 끝 쪽을 주장하지 않는다
def test_single_page_chunk_has_no_page_end(make_document) -> None:
    document = make_document([para("A", page=7), para("B", page=7)])

    chunks = split(document, PLAIN)

    assert chunks[0].page == 7
    assert chunks[0].page_end is None


# 제목 머리는 본문 맨 앞에 붙고 metadata 에도 남는다
def test_heading_prefix_in_text(make_document) -> None:
    document = make_document(
        [title("2. 지원 대상", level=1), title("2.1 제외 대상", level=2), para("본문이다.")]
    )

    chunks = split(document, SplitterConfig())

    assert chunks[0].text == "[2. 지원 대상 > 2.1 제외 대상]\n본문이다."
    assert chunks[0].heading_path == ["2. 지원 대상", "2.1 제외 대상"]


# 머리를 붙인 **뒤에도** 상한을 지켜야 한다. 안 지키면 상한을 선언해놓고 넘긴다
def test_cap_holds_after_heading_prefix(make_document) -> None:
    document = make_document([title("긴 제목 " * 10, level=1), para("본문 " * 400)])

    chunks = split(document, SplitterConfig(max_chunk_chars=400))

    assert len(chunks) > 1
    assert all(len(c.text) <= 400 for c in chunks)


# 끄면 본문만 남는다. 제목은 metadata 로만 따라간다
def test_heading_prefix_can_be_disabled(make_document) -> None:
    document = make_document([title("제목", level=1), para("본문")])

    chunks = split(document, PLAIN)

    assert chunks[0].text == "본문"
    assert chunks[0].heading == "제목"


# 요소가 하나도 없는 문서는 청크도 없다
def test_empty_document(make_document) -> None:
    assert split(make_document([]), PLAIN) == []


# 원본 파일명은 청크마다 따라간다. 인용할 때 출처를 대야 한다
def test_source_name_travels(make_document) -> None:
    document = make_document([para("본문")], source_name="지침.hwpx")

    assert split(document, PLAIN)[0].source_name == "지침.hwpx"
