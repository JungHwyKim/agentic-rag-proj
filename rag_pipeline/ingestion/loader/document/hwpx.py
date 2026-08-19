"""한글 로더 (.hwpx).

HWPX는 OWPML(KS X 6101) 규격의 ZIP+XML 문서다. 네임스페이스 URI가 한컴 버전에 따라
달라질 수 있어 하드코딩하지 않고, lxml의 '{*}' 와일드카드로 로컬 이름만 보고 찾는다.

표 셀 안에도 문단(hp:p)이 들어있기 때문에, 문서를 재귀적으로 훑되 표를 만나면
그 아래로는 내려가지 않는다. 안 그러면 표 내용이 본문에도 중복으로 들어간다.

그림은 본문 XML에 바이트가 들어있지 않다. hp:pic 안의 hc:img 가 binaryItemIDRef 로
ZIP 안의 BinData 엔트리를 가리키는 구조라, ID -> 엔트리 경로를 먼저 만들어두고 참조한다.
도형(rect/polygon/curve) 안의 글자는 hp:drawText > hp:subList > hp:p 에 들어있어
일반 문단과 같은 경로로 잡힌다.

페이지 번호는 파일에 저장되어 있지 않다. HWPX는 흐름 문서라 쪽 나눔이 렌더링 시점에
정해지기 때문이다. 다만 한컴이 마지막 편집 시점의 배치 정보를 hp:linesegarray >
hp:lineseg 의 vertpos(쪽 안에서의 세로 위치)로 남겨두는데, 이 값이 다음 쪽으로 넘어갈 때
작아진다. 그래서 최상위 문단의 vertpos가 줄어드는 지점을 쪽 경계로 보고 번호를 매긴다.
배치 정보가 없는 문서(직접 만든 XML 등)에서는 페이지를 None으로 둔다.

vertpos만으로는 부족한 경우가 하나 있다. 표가 쪽을 넘기면 vertpos가 작아지는 사건은
표 다음 문단에서 딱 한 번 일어나는데, 표가 두 쪽을 먹었어도 한 쪽으로만 세게 된다.
그래서 이후 모든 쪽 번호가 밀린다(실측: 시행령 개정안 안내서 26쪽 문서를 24쪽으로
셌고, 쪽을 넘기는 표 2개 뒤로 표·그림 위치가 전부 어긋났다). 표 높이를 직접 재서
쪽 수에 반영하는 이유다 — _table_height 와 _PageTracker.consume_table 참고.

한글은 제목·안내문을 그릴 때 표 모양을 빌린 상자를 쓴다 — 1칸짜리 제목 상자("Ⅰ. 개 요"),
붙임 머리표("붙임1 | 제목"), 테두리를 그리려고 가운데 칸만 채운 3x3 인용 상자다.
이걸 표로 두면 제목이 마크다운 표 문법 안에 갇혀 검색·임베딩이 나빠지므로 본문
텍스트로 강등한다(_is_layout_box). 실측: 위 문서의 표 27개 중 17개가 이런 껍데기였고,
남은 10개(실데이터 표 9개 + 그림 상자 1개)만 표로 남는다.
"""

from __future__ import annotations

import math
import re
import zipfile
from pathlib import Path

from lxml import etree

from rag_pipeline.ingestion.loader.content.base import ContentRegion, ContentType, LoadContext
from rag_pipeline.ingestion.loader.document.base import BaseDocumentLoader
from rag_pipeline.ingestion.loader.document.headings import FONT_SIZES_KEY
from rag_pipeline.schemas.document import ElementType, FileFormat

# Contents/section0.xml, section1.xml ... 순서대로 읽어야 문서 순서가 맞는다
_SECTION_PATTERN = re.compile(r"contents/section(\d+)\.xml$", re.IGNORECASE)


# 태그에서 네임스페이스를 떼고 로컬 이름만 반환
def _local_name(element: etree._Element) -> str:
    tag = element.tag
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


# 요소 아래 모든 텍스트 노드(hp:t)를 이어붙인다
def _text_of(element: etree._Element) -> str:
    parts = [t.text for t in element.iter("{*}t") if t.text]
    return "".join(parts).strip()


# Contents/header.xml 의 hh:charPr 에서 {id: 글자높이} 표를 만든다.
# HWPX 는 글자 크기를 문단이 아니라 run 마다 charPrIDRef 로 가리키므로 이 표가 있어야
# 문단의 글자 크기를 알 수 있다. header.xml 이 없거나 깨지면 빈 표를 돌려주고,
# 그러면 제목 승격이 조용히 꺼진다(승격은 크기를 아는 문서에서만 하는 것이 맞다)
def _charpr_heights(zf: zipfile.ZipFile) -> dict[str, int]:
    try:
        root = etree.fromstring(zf.read("Contents/header.xml"))
    except (KeyError, etree.XMLSyntaxError):
        return {}

    heights: dict[str, int] = {}
    for char_pr in root.iter("{*}charPr"):
        identifier = char_pr.get("id")
        height = _int_attr(char_pr, "height", 0)
        if identifier is not None and height > 0:
            heights[identifier] = height
    return heights


# 요소 아래 run 들의 글자 높이 목록. 글자가 없는 run 은 뺀다 —
# 빈 run 의 크기가 섞이면 '문단 전체가 본문보다 큰가' 판정이 흔들린다
def _run_sizes(element: etree._Element, heights: dict[str, int]) -> list[int]:
    if not heights:
        return []

    sizes: list[int] = []
    for run in element.iter("{*}run"):
        text = "".join(t.text or "" for t in run.iter("{*}t"))
        if not text.strip():
            continue
        height = heights.get(run.get("charPrIDRef", ""))
        if height:
            sizes.append(height)
    return sizes


# 표의 셀 텍스트를 문서 순서 그대로. 격자로 펴기 전의 **실제 tc 개수**가 필요한 곳에 쓴다
def _table_anchor_cells(table: etree._Element) -> list[str]:
    return [_text_of(tc) for tr in table.findall("{*}tr") for tc in tr.findall("{*}tc")]


# 표 요소를 2차원 셀 배열로 복원한다.
#
# **병합된 칸은 격자에서 자리를 차지하지만 tc 로는 한 번만 나온다.** hp:cellAddr 를 안 보고
# tc 를 나온 순서대로 담으면 가려진 칸만큼 값이 왼쪽으로 밀린다 — 실측으로 시행령 안내서
# HWPX 표 173행 중 127행(73%)이 그 상태였고, `지침확인`(실행항목)이 `단계` 열에 들어가 있었다.
# 열 이름과 값이 틀리게 짝지어진 채 색인되므로 검색이 틀린 근거를 내놓는다.
#
# **세로로 이어진 칸에는 값을 되풀이하고 가로로 이어진 칸은 비운다.** splitter 가 표를 **행
# 단위로만** 쪼개기 때문이다 — 아래로 이어진 행은 다른 청크로 갈 수 있어 제 값을 들고 있어야
# 하지만, 옆으로 이어진 칸은 언제나 같은 행에 남으므로 되풀이해봐야 한 줄 안에서 같은 말이
# 두 번 나올 뿐이다.
#
# **직계 tr/tc 만 본다.** 중첩 표의 텍스트는 그것을 담은 칸의 `_text_of()` 에 이미 들어 있고
# (`iter` 로 훑는다), 예전처럼 `iter` 로 셀까지 훑으면 같은 내용이 두 번 들어간다.
def _table_rows(table: etree._Element) -> list[list[str]]:
    grid: dict[tuple[int, int], str] = {}
    max_row = -1
    max_col = -1

    for row_index, tr in enumerate(table.findall("{*}tr")):
        for tc in tr.findall("{*}tc"):
            addr = tc.find("{*}cellAddr")
            if addr is None:
                continue
            row = _int_attr(addr, "rowAddr", row_index)
            col = _int_attr(addr, "colAddr", -1)
            if row < 0 or col < 0:
                continue

            span = tc.find("{*}cellSpan")
            row_span = max(_int_attr(span, "rowSpan", 1), 1)
            col_span = max(_int_attr(span, "colSpan", 1), 1)
            text = _text_of(tc)

            for r in range(row, row + row_span):
                for c in range(col, col + col_span):
                    grid[(r, c)] = text if c == col else ""
                    max_row = max(max_row, r)
                    max_col = max(max_col, c)

    if not grid:
        # cellAddr 가 없는 문서. 예전처럼 tc 순서를 그대로 쓴다
        rows: list[list[str]] = []
        for tr in table.findall("{*}tr"):
            cells = [_text_of(tc) for tc in tr.findall("{*}tc")]
            if cells:
                rows.append(cells)
        return rows

    return [[grid.get((r, c), "") for c in range(max_col + 1)] for r in range(max_row + 1)]


# 내용이 든 칸이 이 개수 이하면 표 모양을 빌린 제목·안내 상자로 본다
_LAYOUT_BOX_MAX_FILLED = 2
# 다만 3x3(테두리를 그리려고 가운데 칸만 쓰는 인용 상자)보다 큰 표는 제외한다.
# 칸이 대부분 비었을 뿐인 서식·양식 표까지 텍스트로 뭉개면 구조가 사라지기 때문
_LAYOUT_BOX_MAX_CELLS = 9


# 속성을 정수로 읽는다. 값이 없거나 숫자가 아니면 기본값
def _int_attr(element: etree._Element | None, name: str, default: int = 0) -> int:
    if element is None:
        return default
    try:
        return int(element.get(name, default))
    except (TypeError, ValueError):
        return default


# hp:pagePr 의 landscape 가 이 값이면 가로 쪽이다. 이름과 달리 NARROWLY 가 가로다
# — 근거는 _usable_body_height 주석에 있다
_LANDSCAPE = "NARROWLY"


# 쪽 안에서 본문이 쓸 수 있는 세로 높이. 용지 치수에서 여백을 뺀다.
# 머리말·꼬리말 영역도 본문이 못 쓰므로 함께 뺀다. 기하 정보가 없으면 None
def _usable_body_height(section_root: etree._Element) -> int | None:
    page = section_root.find(".//{*}pagePr")
    if page is None:
        return None
    margin = page.find("{*}margin")
    if margin is None:
        return None

    # 가로 쪽에서도 width/height 에는 물리 용지(세로) 치수가 그대로 들어 있고 방향은
    # landscape 속성으로만 표시된다. 그래서 가로 섹션의 세로 여유는 width 에서 얻어야 한다.
    # (실측: 시행령 개정안 안내서 가로쪽 판 — 가로 섹션과 세로 섹션 모두 width=59528
    #  height=84188 으로 같고 landscape 만 NARROWLY/WIDELY 로 갈렸다. WIDELY 섹션의
    #  최상위 문단 vertpos 가 69898 까지 올라가 71435(=height-여백)에 육박하는 것이
    #  WIDELY=세로임을 확정한다 — 가로였다면 46775 를 넘을 수 없다)
    # 이걸 안 가르면 가로 섹션의 본문 높이를 53% 과대평가해서, 쪽을 넘긴 표를 한 쪽에
    # 들어간다고 판단하고 그 뒤 쪽 번호가 전부 밀린다
    landscape = page.get("landscape") == _LANDSCAPE
    extent = _int_attr(page, "width" if landscape else "height")
    used = sum(_int_attr(margin, key) for key in ("top", "bottom", "header", "footer"))
    usable = extent - used
    return usable if usable > 0 else None


# 표가 실제로 차지하는 세로 높이.
# hp:tbl 의 sz height 를 그냥 믿으면 안 된다 — 쪽을 넘기는 표는 이 값이 '시작 쪽에
# 남은 공간'으로 잘려서 저장돼 있다(실측: 43행 표가 선언 34206 / 실제 98326).
# 그래서 셀 높이로 실제 높이를 따로 복원해 둘 중 큰 값을 쓴다.
# 세로 병합된 셀은 cellSpan rowSpan 만큼의 행을 덮으므로 나눠서 한 행 몫만 센다
def _table_height(table: etree._Element) -> int:
    declared = _int_attr(table.find("{*}sz"), "height")

    restored = 0
    for tr in table.findall("{*}tr"):
        per_row = []
        for tc in tr.findall("{*}tc"):
            cell_size = tc.find("{*}cellSz")
            if cell_size is None:
                continue
            span = max(_int_attr(tc.find("{*}cellSpan"), "rowSpan", 1), 1)
            per_row.append(_int_attr(cell_size, "height") // span)
        if per_row:
            # 같은 행의 셀 높이가 어긋나면 병합 때문이므로 가장 작은 값이 실제 행 높이다
            restored += min(per_row)

    return max(declared, restored)


# 작고 내용 든 칸이 1~2개뿐이며 그림도 없는 표는 데이터가 아니라 제목·인용 상자다.
# (실측: 시행령 개정안 안내서의 표 27개 중 실데이터 표는 내용 칸이 최소 9개였고
#  상자는 최대 2개였다. 그 사이가 비어 있어 이 기준으로 깨끗하게 갈린다)
# 그림이 든 상자는 캡션-그림 배치라서 표로 남겨야 둘의 관계가 유지된다
# 세는 것은 `_table_rows()` 의 격자가 아니라 **실제 tc 목록**이다. 격자는 병합된 칸을 여러
# 칸으로 펴므로, 그걸 세면 9칸·2칸이라는 임계값의 뜻이 조용히 달라진다 — 실제로 격자를
# 세도록 뒀더니 시행령 안내서의 제목 상자 4개가 강등되지 않아 표가 9개에서 13개로 늘었다
def _is_layout_box(table: etree._Element, cells: list[str]) -> bool:
    if table.find(".//{*}pic") is not None:
        return False

    # 표를 담은 상자도 남긴다. 강등하면 안쪽 표가 문단 텍스트로 뭉개져 구조가 통째로 사라진다
    # — 그림이 든 상자를 남기는 것과 같은 이유다. 예전에는 `iter` 로 중첩 칸까지 세는 바람에
    # 칸 수 상한(9)에 걸려 우연히 남아 있었고, 직계 칸만 세도록 바꾸자 학교기록물 지침의
    # 표 3개가 문단으로 내려갔다
    if table.find(".//{*}tbl") is not None:
        return False

    total = len(cells)
    if not 0 < total <= _LAYOUT_BOX_MAX_CELLS:
        return False

    filled = sum(1 for cell in cells if cell.strip())
    return 0 < filled <= _LAYOUT_BOX_MAX_FILLED


# 문단의 첫 줄이 쪽 안에서 어느 높이에 놓였는지 읽는다. 배치 정보가 없으면 None.
# 표 셀 안의 linesegarray 를 잡지 않도록 자식 요소만 본다
def _first_vertpos(paragraph: etree._Element) -> int | None:
    array = paragraph.find("{*}linesegarray")
    if array is None:
        return None
    seg = array.find("{*}lineseg")
    if seg is None:
        return None
    try:
        return int(seg.get("vertpos", 0))
    except (TypeError, ValueError):
        return None


# 최상위 문단들의 vertpos 흐름을 보고 쪽 번호를 매긴다.
# vertpos 가 이전 문단보다 작아지면 다음 쪽으로 넘어간 것으로 본다
class _PageTracker:
    def __init__(self) -> None:
        self.page = 0
        self._prev_vertpos = -1
        self._has_layout = False
        # 본문에 쓸 수 있는 쪽 높이. 섹션마다 용지 설정이 다를 수 있어 섹션 시작에 갱신한다
        self._usable_height: int | None = None

    # 새 섹션은 새 쪽에서 시작한다. 쪽 기하도 이때 함께 받는다
    def start_section(self, usable_height: int | None = None) -> None:
        self.page += 1
        self._prev_vertpos = -1
        self._usable_height = usable_height

    # 최상위 문단 하나를 보고 쪽이 넘어갔는지 판단한다
    def advance(self, paragraph: etree._Element) -> None:
        vertpos = _first_vertpos(paragraph)
        if vertpos is None:
            return
        self._has_layout = True
        if vertpos < self._prev_vertpos:
            self.page += 1
        self._prev_vertpos = vertpos

    # 표가 쪽을 넘겨 먹은 쪽 수를 반영하고, 넘긴 쪽 수를 돌려준다.
    # vertpos 는 '쪽이 넘어갔다'는 사건만 알려줄 뿐 몇 쪽을 넘겼는지는 못 알려주기
    # 때문에, 표 높이를 직접 재서 더해주지 않으면 이후 쪽 번호가 전부 밀린다.
    # 표 안의 리전들이 시작 쪽 번호를 받은 뒤에 호출해야 한다
    def consume_table(self, height: int) -> int:
        if not self._has_layout or self._usable_height is None or height <= 0:
            return 0

        start = max(self._prev_vertpos, 0)
        room = self._usable_height - start
        if height <= room:
            # 한 쪽에 들어가는 표는 vertpos 를 건드리지 않는다.
            # 떠 있는 표(pos treatAsChar=0)는 뒤따르는 문단의 vertpos 에 자기 높이가
            # 반영되지 않는 경우가 있어서, 끝 위치로 앞당기면 같은 쪽을 새 쪽으로
            # 오인한다(실측: 학교기록물 지침의 22행 표)
            return 0

        overflow = height - room
        extra = math.ceil(overflow / self._usable_height)
        self.page += extra
        # 쪽을 넘긴 표는 마지막 쪽에서 끝난 높이를 남긴다. 이걸 안 남기면 뒤따르는
        # 문단의 작은 vertpos 를 또 하나의 쪽 경계로 세어 쪽이 두 번 올라간다
        self._prev_vertpos = overflow - (extra - 1) * self._usable_height
        return extra

    # 배치 정보가 전혀 없는 문서면 페이지를 주장하지 않는다
    def current(self) -> int | None:
        return self.page if self._has_layout else None


# binaryItemIDRef 값("image1")을 ZIP 엔트리 경로("BinData/image1.jpg")로 잇는 표를 만든다.
# 확장자가 jpg/png/bmp/gif 로 제각각이라 파일명 어간으로 맞춘다
def _binary_map(zf: zipfile.ZipFile) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for name in zf.namelist():
        if not name.lower().startswith("bindata/"):
            continue
        stem = Path(name).stem
        mapping[stem] = name
        mapping[stem.lower()] = name
    return mapping


# .hwpx 를 읽는 로더
class HwpxLoader(BaseDocumentLoader):
    file_format = FileFormat.HWPX

    def __init__(self, config=None) -> None:
        super().__init__(config)
        # detect_regions 가 계산한 쪽 수. extract_metadata 가 재파싱 없이 가져다 쓴다
        self._page_count: int | None = None
        # {charPr id: 글자높이}. _walk 의 인자를 8개로 늘리는 대신 파싱 동안만 여기 둔다
        self._charpr: dict[str, int] = {}

    # 섹션 XML을 순서대로 파싱해 문단·표·그림 리전을 만든다
    def detect_regions(self, path: Path, ctx: LoadContext) -> list[ContentRegion]:
        regions: list[ContentRegion] = []
        counter = [0]

        try:
            with zipfile.ZipFile(path) as zf:
                sections = self._section_names(zf)
                if not sections:
                    ctx.warn("HWPX 안에서 본문 섹션(Contents/sectionN.xml)을 찾지 못했습니다.")
                    return []

                binaries = _binary_map(zf)
                self._charpr = _charpr_heights(zf)
                tracker = _PageTracker()
                for name in sections:
                    root = etree.fromstring(zf.read(name))
                    tracker.start_section(_usable_body_height(root))
                    # 최상위 문단에서만 쪽을 갱신한다. 표 셀·도형 안의 문단은
                    # 자체 좌표계를 쓰기 때문에 쪽 판단에 쓰면 안 된다
                    for child in root.iterchildren():
                        if _local_name(child) == "p":
                            tracker.advance(child)
                        self._walk(child, regions, counter, zf, binaries, ctx, tracker)

                self._page_count = tracker.current()
        except (zipfile.BadZipFile, etree.XMLSyntaxError, KeyError) as exc:
            ctx.warn(f"HWPX 구조를 읽지 못했습니다: {type(exc).__name__}")
            return []

        return regions

    # 쪽 수를 메타데이터로 넘긴다. 배치 정보가 없는 문서면 None
    def extract_metadata(self, path: Path) -> dict[str, object]:
        return {"page_count": self._page_count}

    # 섹션 파일 이름을 번호 순으로 정렬해 반환
    def _section_names(self, zf: zipfile.ZipFile) -> list[str]:
        matched: list[tuple[int, str]] = []
        for name in zf.namelist():
            match = _SECTION_PATTERN.search(name)
            if match:
                matched.append((int(match.group(1)), name))
        return [name for _, name in sorted(matched)]

    # 문서 트리를 순서대로 훑는다. 표를 만나면 리전으로 만들고 그 아래로는 내려가지 않는다
    def _walk(
        self,
        element: etree._Element,
        regions: list[ContentRegion],
        counter: list[int],
        zf: zipfile.ZipFile,
        binaries: dict[str, str],
        ctx: LoadContext,
        tracker: _PageTracker,
    ) -> None:
        name = _local_name(element)

        if name == "tbl":
            rows = _table_rows(element)
            # 강등 판정과 문단 텍스트는 격자가 아니라 실제 tc 목록에서 얻는다.
            # 격자는 병합된 칸을 되풀이하므로 여기 쓰면 같은 말이 여러 번 들어간다
            cells = _table_anchor_cells(element)
            text = " ".join(cell for cell in cells if cell).strip()
            # 이 표가 만들어낼 리전들의 시작 위치. 쪽 소비를 계산한 뒤 page_end 를 채운다
            region_start = len(regions)

            if _is_layout_box(element, cells):
                # 표가 아니라 제목 상자다. 본문 문단으로 내려보내 마크다운 표에 갇히지 않게 한다
                if text:
                    regions.append(
                        ContentRegion(
                            content_type=ContentType.TEXT,
                            order=counter[0],
                            source_format=self.file_format.value,
                            page=tracker.current(),
                            text=text,
                            metadata={
                                "element_type": ElementType.PARAGRAPH,
                                # 원래 표였다는 사실. 한글이 표로 그린 제목 상자라
                                # promote_headings 가 글자 크기를 보고 제목으로 올릴 수 있다
                                "layout_table": True,
                                FONT_SIZES_KEY: _run_sizes(element, self._charpr),
                            },
                        )
                    )
                    counter[0] += 1
            elif any(any(cell for cell in row) for row in rows):
                regions.append(
                    ContentRegion(
                        content_type=ContentType.TABLE,
                        order=counter[0],
                        source_format=self.file_format.value,
                        page=tracker.current(),
                        rows=rows,
                    )
                )
                counter[0] += 1

            # 셀 안에 든 그림은 셀 텍스트로 표현되지 않아 그냥 두면 통째로 사라진다.
            # (실측: 학교기록물 지침 문서의 그림 20개 중 18개가 표 안에 있었다)
            # 중첩 표 안의 그림까지 훑도록 iter 로 전부 찾는다
            for picture in element.iter("{*}pic"):
                self._append_picture(picture, regions, counter, zf, binaries, ctx, tracker)

            # 표 안의 리전들이 시작 쪽 번호를 받은 뒤에 쪽 소비를 반영한다
            spanned = tracker.consume_table(_table_height(element))
            if spanned:
                # 여러 쪽에 걸친 표는 시작 쪽만으로 출처를 달면 뒷부분이 엉뚱한 쪽을 가리킨다.
                # 표 안 그림도 마찬가지로 시작 쪽 번호를 받는다 — 그림이 몇 번째 행에 있는지
                # 역산하지 않기 때문이다. 끝 쪽을 함께 남겨, 틀린 쪽을 단언하는 대신
                # 인용할 때 범위를 쓸 수 있게 한다
                for region in regions[region_start:]:
                    if region.page is not None:
                        region.metadata["page_end"] = region.page + spanned
            return

        if name == "pic":
            self._append_picture(element, regions, counter, zf, binaries, ctx, tracker)
            # 캡션이 subList 로 딸려올 수 있으므로 아래로는 계속 내려간다

        if name == "p":
            # 문단 안에 표나 그림이 있으면 그것부터 처리해야 순서가 유지된다
            has_nested = (
                element.find(".//{*}tbl") is not None or element.find(".//{*}pic") is not None
            )
            if has_nested:
                for child in element.iterchildren():
                    self._walk(child, regions, counter, zf, binaries, ctx, tracker)
                return

            text = _text_of(element)
            if text:
                regions.append(
                    ContentRegion(
                        content_type=ContentType.TEXT,
                        order=counter[0],
                        source_format=self.file_format.value,
                        page=tracker.current(),
                        text=text,
                        metadata={
                            "element_type": ElementType.PARAGRAPH,
                            FONT_SIZES_KEY: _run_sizes(element, self._charpr),
                        },
                    )
                )
                counter[0] += 1
            return

        for child in element.iterchildren():
            self._walk(child, regions, counter, zf, binaries, ctx, tracker)

    # hp:pic 이 가리키는 BinData 이미지를 읽어 이미지 리전으로 만든다
    def _append_picture(
        self,
        element: etree._Element,
        regions: list[ContentRegion],
        counter: list[int],
        zf: zipfile.ZipFile,
        binaries: dict[str, str],
        ctx: LoadContext,
        tracker: _PageTracker,
    ) -> None:
        img = element.find(".//{*}img")
        if img is None:
            return

        ref = img.get("binaryItemIDRef")
        if not ref:
            return

        entry = binaries.get(ref) or binaries.get(ref.lower())
        if entry is None:
            ctx.warn(f"HWPX 그림 '{ref}'에 해당하는 BinData 항목을 찾지 못했습니다.")
            return

        try:
            blob = zf.read(entry)
        except KeyError:
            return

        regions.append(
            ContentRegion(
                content_type=ContentType.IMAGE,
                order=counter[0],
                source_format=self.file_format.value,
                page=tracker.current(),
                blob=blob,
                blob_kind="png",
                metadata={"binary_item": ref, "zip_entry": entry},
            )
        )
        counter[0] += 1
