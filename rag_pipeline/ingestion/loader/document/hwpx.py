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
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

from lxml import etree

from rag_pipeline.ingestion.loader.content.base import ContentRegion, ContentType, LoadContext
from rag_pipeline.ingestion.loader.document.base import BaseDocumentLoader
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


# 표 요소를 2차원 셀 배열로 복원한다
def _table_rows(table: etree._Element) -> list[list[str]]:
    rows: list[list[str]] = []
    for tr in table.iter("{*}tr"):
        cells = [_text_of(tc) for tc in tr.iter("{*}tc")]
        if cells:
            rows.append(cells)
    return rows


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

    # 새 섹션은 새 쪽에서 시작한다
    def start_section(self) -> None:
        self.page += 1
        self._prev_vertpos = -1

    # 최상위 문단 하나를 보고 쪽이 넘어갔는지 판단한다
    def advance(self, paragraph: etree._Element) -> None:
        vertpos = _first_vertpos(paragraph)
        if vertpos is None:
            return
        self._has_layout = True
        if vertpos < self._prev_vertpos:
            self.page += 1
        self._prev_vertpos = vertpos

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
                tracker = _PageTracker()
                for name in sections:
                    root = etree.fromstring(zf.read(name))
                    tracker.start_section()
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
            if any(any(cell for cell in row) for row in rows):
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
                        metadata={"element_type": ElementType.PARAGRAPH},
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
