"""제목 경계로 요소를 묶는 규칙."""

from __future__ import annotations

from rag_pipeline.config import SplitterConfig
from rag_pipeline.ingestion.splitter import split
from rag_pipeline.ingestion.splitter.sections import build_sections
from tests.ingestion.splitter.conftest import para, table, title

# 제목 머리를 빼고 본문만 확인하기 위한 설정
PLAIN = SplitterConfig(include_heading_in_text=False)


# 제목이 나오기 전까지 요소를 이어 붙인다. 이게 이 단계의 주 작업이다
def test_merges_elements_until_next_heading(make_document) -> None:
    document = make_document(
        [
            title("1. 지원 대상", level=1),
            para("중소기업"),
            para("스타트업"),
            para("연구기관"),
            title("2. 신청 방법", level=1),
            para("온라인으로 접수한다."),
        ]
    )

    chunks = split(document, PLAIN)

    assert len(chunks) == 2
    assert chunks[0].text == "중소기업\n스타트업\n연구기관"
    assert chunks[0].heading == "1. 지원 대상"
    assert chunks[1].text == "온라인으로 접수한다."


# 서로 다른 절의 내용이 한 청크에 섞이면 검색 결과가 어느 절 얘기인지 알 수 없게 된다
def test_sections_never_merge_even_when_small(make_document) -> None:
    document = make_document(
        [
            title("가", level=1),
            para("짧다"),
            title("나", level=1),
            para("이것도 짧다"),
        ]
    )

    chunks = split(document, PLAIN)

    assert [c.text for c in chunks] == ["짧다", "이것도 짧다"]


# 하위 제목은 상위 제목의 자식으로 쌓이고, 같은 층이 오면 걷힌다
def test_heading_path_follows_level_stack(make_document) -> None:
    document = make_document(
        [
            title("3. 용어정의", level=1),
            para("아래와 같다."),
            title("3.1 기록관", level=2),
            para("기록물을 관리하는 곳."),
            title("3.2 이관", level=2),
            para("옮기는 일."),
            title("4. 절차", level=1),
            para("순서대로 한다."),
        ]
    )

    chunks = split(document, PLAIN)

    assert [c.heading_path for c in chunks] == [
        ["3. 용어정의"],
        ["3. 용어정의", "3.1 기록관"],
        ["3. 용어정의", "3.2 이관"],
        ["4. 절차"],
    ]


# 제목만 있고 본문이 없으면 청크를 만들지 않는다. 알맹이 없는 검색 결과가 되기 때문
def test_heading_without_body_makes_no_chunk(make_document) -> None:
    document = make_document(
        [
            title("표지 장식", level=3),
            title("문서 제목", level=1),
            para("첫 본문."),
        ]
    )

    chunks = split(document, PLAIN)

    assert len(chunks) == 1
    assert chunks[0].heading == "문서 제목"
    # 같은 층 이하의 앞 제목은 스택에서 걷힌다
    assert chunks[0].heading_path == ["문서 제목"]


# 제목이 하나도 없는 문서도 청크가 나와야 한다. PDF·HWPX 는 승격이 꺼지면 이 상태가 된다
def test_document_without_headings(make_document) -> None:
    document = make_document([para("첫째 줄"), para("둘째 줄"), para("셋째 줄")])

    chunks = split(document, PLAIN)

    assert len(chunks) == 1
    assert chunks[0].heading is None
    assert chunks[0].heading_path == []
    assert chunks[0].text == "첫째 줄\n둘째 줄\n셋째 줄"


# 제목 앞에 놓인 본문(머리글·발행정보)도 버리지 않는다
def test_body_before_first_heading_is_kept(make_document) -> None:
    document = make_document(
        [para("발행: 국가기록원"), title("Ⅰ. 개요", level=1), para("본문이다.")]
    )

    chunks = split(document, PLAIN)

    assert [c.text for c in chunks] == ["발행: 국가기록원", "본문이다."]
    assert chunks[0].heading is None


# cleaner 가 반복 머리말을 지운 자리와 핸들러가 요소를 0개 만든 자리 때문에 order 에는
# 빈틈이 있다. 연속성을 가정하면 여기서 깨진다
def test_tolerates_gaps_in_order(make_document) -> None:
    document = make_document(
        [
            title("제목", level=1, order=3),
            para("먼저 온 문단", order=9),
            para("나중 문단", order=40),
        ]
    )

    chunks = split(document, PLAIN)

    assert chunks[0].text == "먼저 온 문단\n나중 문단"
    assert chunks[0].element_orders == [9, 40]
    assert chunks[0].order == 9


# 입력 순서가 뒤섞여 들어와도 order 로 다시 세운다
def test_sorts_by_order(make_document) -> None:
    document = make_document(
        [para("나중", order=20), title("제목", level=1, order=5), para("먼저", order=10)]
    )

    sections = build_sections(document)

    assert len(sections) == 1
    assert [e.text for e in sections[0].body] == ["먼저", "나중"]


# 빈 텍스트 요소는 청크에 섞이지 않는다
def test_blank_elements_are_ignored(make_document) -> None:
    document = make_document([title("제목", level=1), para("   "), para("실제 내용")])

    chunks = split(document, PLAIN)

    assert [c.text for c in chunks] == ["실제 내용"]


# 표를 만나면 그 앞까지의 본문을 먼저 끊는다. 안 그러면 표 앞뒤 본문이 한 청크로 붙어
# 청크 순서가 문서 순서와 어긋난다
def test_table_splits_surrounding_text(make_document) -> None:
    document = make_document(
        [
            title("절", level=1),
            para("표 앞 문단"),
            table("| 이름 |\n| --- |\n| 홍길동 |", [["이름"], ["홍길동"]]),
            para("표 뒤 문단"),
        ]
    )

    chunks = split(document, PLAIN)

    assert [c.type.value for c in chunks] == ["text", "table", "text"]
    assert [c.text for c in chunks][0] == "표 앞 문단"
    assert [c.text for c in chunks][2] == "표 뒤 문단"
