"""PDF 로더 (.pdf).

PDF에는 '표'라는 구조가 없다 — 선과 글자의 좌표만 있다. 그래서 여기서만 2차 라우팅이
진짜 의미를 갖는다:
  - 텍스트 블록 -> 기본 추출
  - 표 영역     -> 잘라서 LlamaParse (실패하면 PyMuPDF가 뽑은 행으로 폴백)
  - 그림 영역   -> classify 후 OCR 또는 Vision
  - 글자가 거의 없는 페이지 -> 스캔본으로 보고 페이지 전체를 이미지로 처리
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pymupdf

from rag_pipeline.ingestion.loader.content.base import ContentRegion, ContentType, LoadContext
from rag_pipeline.ingestion.loader.document.base import BaseDocumentLoader
from rag_pipeline.ingestion.loader.document.headings import FONT_SIZES_KEY
from rag_pipeline.schemas.document import FileFormat

# 표 영역을 잘라낼 때 테두리가 잘리지 않도록 주는 여백(pt)
_CROP_PADDING = 4.0

# 조각난 표를 되붙일 때 쓰는 두 값. 근거는 PDF 5개 · 표 리전 103개 · 같은 쪽 인접쌍 36개
# 실측이다(2026-08-19, plan_log/표-경로.md 2회차).
#
# 세로 간격: 실제로 조각난 표는 6.8 이었고, 별개 표·별개 상자는 전부 18.8 이상이었다.
# 2.8배 틈이 있어 이 임계값은 표본 하나에 기대지 않는다. 12 는 그 사이다
_MERGE_MAX_GAP = 12.0
# 좌우 끝: 같은 표에서 잘린 조각은 x 가 소수점까지 같다. 반올림 오차만 흡수한다
_MERGE_X_TOLERANCE = 2.0

# 격자 칸의 중심점이 어느 칸에 덮였는지 볼 때 쓰는 여유. 테두리에 정확히 걸치는 경우만
# 흡수하면 되므로 작다 — 크게 잡으면 이웃 칸까지 덮은 것으로 오인한다
_GRID_EPS = 0.5

# 빈 칸에 있는 벡터 드로잉이 **정보인가 배경 음영인가**를 가르는 자[尺].
#
# 이 신호가 필요한 이유는 체크 표시가 이미지도 텍스트도 아니라는 것이다 — AI가이드 PDF
# 12~17쪽 표는 `page.get_images()` 가 0개를 주고 `get_drawings()` 에서 채움 경로로만
# 나와서, `extract()` 로는 **원리적으로** 못 얻는다. 그 표의 정보 내용이 사실상 체크 표시
# 전부라 지금 색인되는 것은 빈 격자다.
#
# **"빈 칸에 드로잉이 있는가" 로는 못 가른다** — 그 규칙(plan_log/표-경로.md 9회차 원안)은
# 실측으로 2026중등 22쪽 한 표에서만 233건을 오검출한다. 그 표는 엑셀 서식이고 드로잉이
# 전부 열 띠 배경 음영이다(글자 있는 칸에도 똑같이 깔리고 행마다 26칸씩 균일하다).
#
# 갈라주는 것은 **셀 대비 면적비**다. 실측(2026-08-22, PDF 4개·히트 306건):
#
#     2026중등 22쪽   면적비 1.00  (233건, 편차 0)   ← 셀을 통째로 덮는다 = 배경
#     AI가이드 12~17쪽 면적비 0.08~0.10 (73건)        ← 셀 일부를 차지한다 = 그려진 것
#
# **0.10 과 1.00 사이에 값이 하나도 없다.** 그리고 이것은 관측값에서 고른 임계값이 아니라
# 의미의 경계다 — 칸을 통째로 덮으면 정의상 배경이고, 일부만 차지하면 그 자리에 그려진
# 무엇이다. 8회차의 "한글 유무는 임계값이 아니라 이분법"과 같은 종류라 분포의 틈을 고르는
# 순서를 밟을 필요가 없다
_MARK_AREA_RATIO = 0.5

# 드로잉이 그 칸의 것인지 볼 때 쓰는 비율. 칸 밖으로 걸친 선·테두리를 걸러낸다
_MARK_INSIDE_RATIO = 0.5

# 선·테두리를 도형에서 가르는 최소 변 길이(pt). 이보다 얇으면 획이다
_MARK_MIN_SIDE = 2.0

# 대각선으로 갈린 모서리 셀을 찾을 때 모서리와의 거리 여유(pt).
#
# **텍스트 위치로 판정하면 안 된다.** *오른쪽 위 조각 + 왼쪽 아래 조각* 규칙은 실측
# (2026-08-22, PDF 4개·표 셀 2,606개)으로 47건을 잡는데 **그중 41건이 오검출**이다 —
# 줄바꿈된 본문에서 둘째 줄이 짧으면 전부 걸린다. 같은 표본에서 **셀의 좌상↔우하 모서리를
# 잇는 stroke 선분**은 6건이고 전부 참이다(AI가이드 12~17쪽, 오검출 0·미검출 0).
# 8회차가 쪽 넘김에서 헤더 반복안을 오검출 4로 버린 것과 같은 형태다.
#
# 2.0 은 `_MERGE_X_TOLERANCE` 와 같은 값이지만 **같은 질문이 아니라서 따로 둔다** —
# 저쪽은 "두 조각의 좌우 끝이 같은가"이고 이쪽은 "선분이 이 칸의 모서리에 닿는가"다
_DIAGONAL_EPS = 2.0

# 쪽을 가로지르는 표를 이을 때 "사이에 본문이 끼었는가"를 가르는 자[尺].
#
# 실측(PDF 5개 · 쪽을 가로지르는 인접 표 쌍 11개, 2026-08-20, plan_log/표-경로.md 8회차)으로
# **참 연속 4건의 두 리전 사이에 있는 것은 쪽 번호 하나뿐이었고**(`- 19 -`·`- 26 -`·`- 13 -`·
# `- 24 -`, 전부 6자에 한글 0자), 별개 표 7건은 전부 한글 본문이 낀다(제일 아슬아슬한 것도
# 한글 12자·20자 블록 둘). 오검출 0 · 미검출 0.
#
# **한글 유무는 임계값이 아니라 이분법**이라 이 상수는 "분포의 틈에서 고른 값"이 아니다.
# 기하 띠(쪽 높이의 상·하위 몇 %)로 머리말·꼬리말을 거르는 안은 실측이 기각했다 — 머리말
# 최하단 5.7% vs 본문 최상단 6.7% 로 틈이 1%p 뿐이라 관측값을 그대로 임계값으로 쓰는 꼴이다.
#
# 감수 사항: 한글 머리말이 매 쪽 반복되는 문서는 이 규칙으로 영영 못 잇는다(실측 표본에서는
# AI가이드가 그렇고, 그 문서에는 참 연속이 없어 손해가 관측되지 않았다)
_HANGUL = re.compile(r"[가-힣]")

# (bbox, rows) — 표 하나. 되붙이기 전후로 같은 모양을 쓴다
_Table = tuple[tuple[float, ...], list[list[str]]]


# 블록 안 span 들의 글자 크기. 제목 승격 판정에만 쓰이고 headings.py 가 걷어낸다.
# 공백뿐인 span 은 뺀다 — 줄바꿈용 빈 span 이 엉뚱한 크기를 갖고 있어 최빈값을 흔든다.
# 소수점 첫째 자리로 반올림하는 이유는 같은 본문이 9.5 와 9.499996 으로 갈려 들어오기 때문
def _span_sizes(spans: list[dict]) -> list[float]:
    return [
        round(float(span["size"]), 1)
        for span in spans
        if span.get("text", "").strip() and span.get("size")
    ]


# 두 사각형이 의미 있게 겹치는지 판단. 표 영역 안의 텍스트 블록을 걸러내는 데 쓴다
def _overlaps(inner: tuple[float, ...], outer: tuple[float, ...], threshold: float = 0.5) -> bool:
    ix0, iy0, ix1, iy1 = inner
    ox0, oy0, ox1, oy1 = outer

    overlap_w = max(0.0, min(ix1, ox1) - max(ix0, ox0))
    overlap_h = max(0.0, min(iy1, oy1) - max(iy0, oy0))
    overlap_area = overlap_w * overlap_h

    inner_area = max((ix1 - ix0) * (iy1 - iy0), 1e-6)
    return overlap_area / inner_area >= threshold


# 병합에 덮인 칸을 값으로 채운다.
#
# PyMuPDF 는 병합에 덮인 자리를 `row.cells[j] is None` 으로 내놓고 `extract()` 는 같은 자리에
# 빈 문자열을 준다. 그래서 **빈 칸이 "병합에 덮인 자리"인지 "그냥 빈 칸"인지 가를 수 있다** —
# 이 구분이 없으면 되풀이 자체가 불가능하다.
#
# 규칙은 HWPX 와 같다(`hwpx.py:_table_rows`). **세로로 이어진 칸에는 값을 되풀이하고 가로로
# 이어진 칸은 비운다** — splitter 가 표를 행 단위로만 쪼개므로 아래로 이어진 행은 다른 청크로
# 갈 수 있지만, 옆으로 이어진 칸은 언제나 같은 행에 남는다.
#
# **둘을 가르는 것이 이 함수의 어려운 부분이다.** 실측(PDF 5개·칸 3,503개, 2026-08-19)으로
# None 칸이 897개(25.6%)이고 그중 위에 앵커가 있는 것이 661개인데, **그 661개 중 226개(34.2%)는
# 왼쪽 칸이 이 열까지 뻗은 가로 병합이기도 하다.** 위만 보고 되풀이하면 그 226개에 엉뚱한 값이
# 들어간다. 그래서 가로를 먼저 보고, 가로가 아닐 때만 세로로 채운다.
def _fill_merged_cells(table: Any) -> list[list[str]]:
    values = [[("" if cell is None else str(cell)) for cell in row] for row in table.extract()]
    rows = getattr(table, "rows", None)
    if not rows or len(rows) != len(values):
        return values

    anchors = [
        (r, c, cell)
        for r, row in enumerate(rows)
        for c, cell in enumerate(row.cells)
        if cell is not None
    ]
    if not anchors:
        return values

    # 격자 경계는 **모든 칸 모서리의 합집합**에서 얻는다. 그 행에 있는 칸들만 보면, 여러 격자
    # 행에 걸친 칸 때문에 행의 세로 범위가 부풀어 중심점이 엉뚱한 행으로 간다(실측으로 그렇게
    # 재다가 멀쩡한 채움 80칸을 오류로 오판했다)
    xs = sorted({round(float(v), 1) for _, _, cell in anchors for v in (cell[0], cell[2])})
    ys = sorted({round(float(v), 1) for _, _, cell in anchors for v in (cell[1], cell[3])})
    if len(rows) != len(ys) - 1:
        return values

    for r, row in enumerate(rows):
        if len(row.cells) != len(xs) - 1:
            continue
        cy = (ys[r] + ys[r + 1]) / 2
        for c, cell in enumerate(row.cells):
            if cell is not None or c >= len(values[r]) or values[r][c]:
                continue

            cx = (xs[c] + xs[c + 1]) / 2
            for anchor_row, anchor_col, rect in anchors:
                if not (rect[0] - _GRID_EPS <= cx <= rect[2] + _GRID_EPS):
                    continue
                if not (rect[1] - _GRID_EPS <= cy <= rect[3] + _GRID_EPS):
                    continue
                # 덮은 칸의 **왼쪽 열**이고 그 칸이 위에 있을 때만 세로 연장이다.
                # 열이 다르면 가로 연장이므로 비워 둔다
                if anchor_col == c and anchor_row < r and anchor_col < len(values[anchor_row]):
                    values[r][c] = values[anchor_row][anchor_col]
                break

    return values


# 좌상↔우하 모서리를 잇는 선분들. 대각선으로 갈린 셀을 찾는 데 쓴다
def _diagonal_lines(page: pymupdf.Page) -> list[tuple[Any, Any]]:
    try:
        drawings = page.get_drawings()
    except Exception:  # noqa: BLE001 - 신호 하나 때문에 페이지를 막지 않는다
        return []

    lines: list[tuple[Any, Any]] = []
    for item in drawings:
        if item.get("type") != "s":
            continue
        for shape in item.get("items", ()):
            if shape[0] != "l":
                continue
            lines.append((shape[1], shape[2]))
    return lines


# 이 칸이 대각선으로 갈려 있나. 갈렸으면 (왼쪽아래 글, 오른쪽위 글), 아니면 None.
#
# 한글 표에서 이 모양은 **두 축의 이름을 한 칸에 적는 관용**이다 — 왼쪽 아래가 행 축,
# 오른쪽 위가 열 축이다. PyMuPDF 는 둘을 한 문자열로 주므로 그대로 두면 0열의 이름이
# 열 이름 자리에 두 축 이름이 함께 들어가고, splitter 의 헤더 채우기가 그것을 옆 칸에도 복제한다
def _split_diagonal(page: pymupdf.Page, cell: Any, lines: list[tuple[Any, Any]]) -> tuple[str, str] | None:
    x0, y0, x1, y1 = (float(v) for v in cell)
    for start, end in lines:
        left, right = sorted([(start.x, start.y), (end.x, end.y)])
        if abs(left[0] - x0) > _DIAGONAL_EPS or abs(left[1] - y0) > _DIAGONAL_EPS:
            continue
        if abs(right[0] - x1) > _DIAGONAL_EPS or abs(right[1] - y1) > _DIAGONAL_EPS:
            continue
        break
    else:
        return None

    rect = pymupdf.Rect(x0, y0, x1, y1)
    try:
        blocks = page.get_text("dict", clip=rect)["blocks"]
    except Exception:  # noqa: BLE001
        return None

    spans = [
        (span["text"].strip(), span["bbox"])
        for block in blocks
        for line in block.get("lines", ())
        for span in line["spans"]
        if span["text"].strip()
    ]
    if len(spans) != 2:
        # 조각이 둘이 아니면 어느 쪽이 어느 축인지 못 가른다. 건드리지 않는다
        return None

    # 오른쪽 위가 열 축, 왼쪽 아래가 행 축이다
    spans.sort(key=lambda item: item[1][0])
    (row_axis, row_box), (col_axis, col_box) = spans
    if not (row_box[1] > col_box[1]):
        return None
    return row_axis, col_axis


# 빈 칸을 **부분적으로** 덮는 드로잉이 있는 칸 수. 표를 Vision 으로 올릴지 판정하는 신호다.
#
# 세는 것은 `row.cells` 의 실제 셀 사각형이다. `_fill_merged_cells` 의 격자가 아니라 —
# 저쪽은 병합에 덮인 자리를 채우려고 격자를 펴지만, 여기서는 "이 칸에 무엇이 그려졌나"를
# 묻는 것이라 실제 칸 하나가 판정 단위다.
#
# 판정이 두 겹인 이유: 면적비만 보면 표 테두리 선이 걸린다(칸보다 훨씬 작다). 그래서
# 드로잉이 그 칸 **안에** 있는지를 먼저 보고(`_MARK_INSIDE_RATIO`), 얇은 획은 아예 뺀다
def _mark_cells(table: Any, drawings: list[Any]) -> list[tuple[int, int]]:
    if not drawings:
        return []

    rows = getattr(table, "rows", None)
    if not rows:
        return []

    try:
        values = table.extract()
    except Exception:  # noqa: BLE001 - 신호 하나 때문에 페이지를 막지 않는다
        return []

    marked: list[tuple[int, int]] = []
    for r, row in enumerate(rows):
        if r >= len(values):
            break
        for c, cell in enumerate(row.cells):
            if cell is None or c >= len(values[r]):
                continue
            if (values[r][c] or "").strip():
                continue

            rect = pymupdf.Rect(cell)
            area = rect.get_area()
            if area <= 0:
                continue

            for shape in drawings:
                overlap = shape & rect
                if overlap.is_empty:
                    continue
                shape_area = shape.get_area()
                if shape_area <= 0:
                    continue
                if overlap.get_area() / shape_area < _MARK_INSIDE_RATIO:
                    continue
                if shape_area / area >= _MARK_AREA_RATIO:
                    # 칸을 통째로 덮는다 = 배경 음영. 정보가 아니다
                    continue
                marked.append((r, c))
                break

    return marked


# 그 쪽에서 표 안의 표시로 볼 만한 채움 도형들. 얇은 획(테두리·구분선)은 뺀다
def _mark_shapes(page: pymupdf.Page) -> list[Any]:
    try:
        drawings = page.get_drawings()
    except Exception:  # noqa: BLE001 - 신호 하나 때문에 페이지를 막지 않는다
        return []

    shapes: list[Any] = []
    for item in drawings:
        if item.get("type") != "f":
            continue
        rect = item.get("rect")
        if rect is None or rect.width < _MARK_MIN_SIDE or rect.height < _MARK_MIN_SIDE:
            continue
        shapes.append(rect)
    return shapes


# 되붙인 표에서의 표시 자리. `_spans_within` 과 같은 이유로 필요하다 — 되붙이기가
# `(bbox, rows)` 만 다뤄 표 객체를 잃는다.
#
# **행 번호를 조각마다 밀어 준다.** `_merge_split_tables()` 가 조각을 위에서 아래로
# `prev_rows + rows` 로 잇기 때문에, 둘째 조각의 0행은 되붙인 표에서 첫 조각의 행 수만큼
# 아래다. 안 밀면 자리가 통째로 어긋난 채 채워진다
def _marks_within(
    bbox: tuple[float, ...], raw: list[tuple[tuple[float, ...], int, list[tuple[int, int]]]]
) -> list[tuple[int, int]]:
    inside = sorted(
        (item for item in raw if bbox[1] - _GRID_EPS <= item[0][1] and item[0][3] <= bbox[3] + _GRID_EPS),
        key=lambda item: item[0][1],
    )
    marks: list[tuple[int, int]] = []
    offset = 0
    for _rect, row_count, positions in inside:
        marks.extend((r + offset, c) for r, c in positions)
        offset += row_count
    return marks


# 표 리전이 잠깐 지고 다니는 사설 키. 쪽 경계에서 갈린 셀을 판정할 때만 쓰고
# `detect_regions()` 가 병합을 마친 뒤 지운다 — 공개 스키마로 새면 안 된다
_BOUNDARY_SPANS_KEY = "_boundary_spans"


# 표의 첫 격자 행과 마지막 격자 행에 놓인 각 셀이 **몇 개 격자 행을 덮는가**.
#
# 쪽 경계에서 갈린 셀을 판정하려면 이 수가 필요한데, 되붙이기가 도는 시점에는 PyMuPDF 표
# 객체가 이미 없다. 그래서 리전을 만들 때 재서 실어 보낸다.
#
# **격자 가로선은 모든 칸 모서리의 합집합에서 얻는다.** 그 행의 칸들만 보면 여러 행에 걸친
# 칸 때문에 범위가 부풀어 수가 틀린다(`_fill_merged_cells` 가 같은 이유로 같은 방식을 쓴다).
def _boundary_spans(table: Any) -> tuple[list[list[int]], list[int]] | None:
    rows = getattr(table, "rows", None)
    if not rows:
        return None

    ys = sorted(
        {round(float(v), 1) for row in rows for c in row.cells if c is not None for v in (c[1], c[3])}
    )
    if len(ys) < 2:
        return None
    bands = list(zip(ys, ys[1:], strict=False))

    def counts(row: Any) -> list[int]:
        out = []
        for cell in row.cells:
            if cell is None:
                out.append(0)
                continue
            out.append(
                sum(1 for a, b in bands if cell[1] - _GRID_EPS <= a and b <= cell[3] + _GRID_EPS)
            )
        return out

    # 첫 행을 **둘** 담는다. 반복 헤더를 버리면 두 번째 행이 첫 데이터 행이 되기 때문이다 —
    # 하나만 담았다가 정작 헤더가 반복되는 경우(시행령 24~25쪽)에서 판정이 죽었다
    return [counts(row) for row in rows[:2]], counts(rows[-1])


# 되붙인 표의 경계 span 을 원본 조각들에서 되찾는다.
#
# 같은 쪽 되붙이기(`_merge_split_tables`)는 `(bbox, rows)` 만 다뤄 표 객체를 잃는다. 그래서
# 되붙인 사각형 안에 들어가는 원본 조각을 찾아, **맨 위 조각의 첫 행**과 **맨 아래 조각의
# 마지막 행** span 을 쓴다 — 되붙인 표의 첫·마지막 격자 행이 실제로 그 둘이다
def _spans_within(
    bbox: tuple[float, ...],
    raw: list[tuple[tuple[float, ...], tuple[list[list[int]], list[int]]]],
) -> tuple[list[list[int]], list[int]] | None:
    inside = [
        (rect, spans)
        for rect, spans in raw
        if bbox[1] - _GRID_EPS <= rect[1] and rect[3] <= bbox[3] + _GRID_EPS
    ]
    if not inside:
        return None
    inside.sort(key=lambda item: item[0][1])
    return inside[0][1][0], inside[-1][1][1]


# 두 조각이 원래 한 표였나. 셋을 **모두** 만족해야 한다
def _is_same_table(a: _Table, b: _Table) -> bool:
    (ax0, _, ax1, ay1), a_rows = a
    (bx0, by0, bx1, _), b_rows = b

    if not 0 <= by0 - ay1 < _MERGE_MAX_GAP:
        return False
    if abs(ax0 - bx0) > _MERGE_X_TOLERANCE or abs(ax1 - bx1) > _MERGE_X_TOLERANCE:
        return False
    return bool(a_rows) and bool(b_rows) and len(a_rows[0]) == len(b_rows[0])


# 같은 표가 여러 조각으로 잡힌 것을 되붙인다.
#
# PyMuPDF 의 `find_tables()` 가 한 표를 여러 개로 나눠 내놓는 경우가 있다. 그러면 splitter 가
# **각 조각의 첫 행을 헤더로 되풀이**하므로 실제 열 이름은 첫 조각에만 남고 나머지 조각은
# 데이터 행이 헤더 자리에 온다 — 시행령 안내서 4쪽의 표 하나가 5조각으로 잡혀 열 이름
# `단계|실행항목|세부내용 및 방법|담당` 이 첫 조각에만 있었다(실측 2026-08-18).
#
# **여기서 붙여야 한다.** LlamaParse 로 올려도 안 고쳐진다 — 핸들러는 리전 하나의 blob 만
# 보내므로 외부 API 는 애초에 조각 하나밖에 못 본다(실측: 유료 경로에서 같은 표가 10조각).
def _merge_split_tables(tables: list[_Table]) -> list[_Table]:
    merged: list[_Table] = []

    for bbox, rows in sorted(tables, key=lambda item: item[0][1]):
        if merged and _is_same_table(merged[-1], (bbox, rows)):
            (px0, py0, px1, py1), prev_rows = merged[-1]
            merged[-1] = (
                (min(px0, bbox[0]), py0, max(px1, bbox[2]), max(py1, bbox[3])),
                prev_rows + rows,
            )
            continue
        merged.append((bbox, rows))

    return merged


# 쪽 안에서 그 표가 마지막(또는 처음)인가. 두 리전 사이에 무엇이 있었는지를 보려면
# 표의 사각형이 필요해서 리전과 함께 돌려준다
def _edge_table(regions: list[ContentRegion], last: bool) -> ContentRegion | None:
    tables = [r for r in regions if r.content_type is ContentType.TABLE and r.bbox and r.rows]
    if not tables:
        return None
    return tables[-1] if last else tables[0]


# 두 리전 사이에 본문이 끼어 있나. 끼어 있으면 별개 표다.
#
# 세는 것은 **한글을 가진 텍스트 리전**과 **이미지 리전**이다. 쪽 번호(`- 19 -`)는 한글이
# 없어 안 세이고, 그것이 참 연속 4건에서 사이에 있던 유일한 것이었다. 이미지 조건은 실측
# 표본 11쌍에서 **한 번도 발화하지 않았다** — 안전 방향이라 넣지만 근거가 있는 척하지 않는다
def _has_body_between(
    regions: list[ContentRegion], *, below: float | None = None, above: float | None = None
) -> bool:
    for region in regions:
        if region.bbox is None:
            continue
        if below is not None and region.bbox[1] <= below:
            continue
        if above is not None and region.bbox[3] >= above:
            continue
        if region.content_type is ContentType.IMAGE:
            return True
        if region.content_type is ContentType.TEXT and _HANGUL.search(region.text or ""):
            return True
    return False


# 앞 쪽 바닥의 표와 다음 쪽 머리의 표가 원래 한 표였나. 셋을 **모두** 만족해야 한다.
#
# `_is_same_table()` 과 나란히 서지만 축이 다르다 — 같은 쪽에서는 *세로 간격 12pt 미만* 이
# 기준이었는데 쪽이 바뀌면 그 축 자체가 사라진다. 대신 **사이에 본문이 끼었는가**를 본다.
def _is_page_continuation(
    tail: ContentRegion,
    tail_bbox: tuple[float, ...],
    tail_page_regions: list[ContentRegion],
    head: ContentRegion,
    head_page_regions: list[ContentRegion],
) -> bool:
    if not tail.rows or not head.rows or head.bbox is None:
        return False
    if len(tail.rows[0]) != len(head.rows[0]):
        return False

    # 여유값은 같은 쪽 되붙이기의 것을 그대로 쓴다. 같은 "같은 좌표인가" 질문이라
    # 상수를 늘리면 근거 없는 값이 하나 더 생긴다
    if abs(tail_bbox[0] - head.bbox[0]) > _MERGE_X_TOLERANCE:
        return False
    if abs(tail_bbox[2] - head.bbox[2]) > _MERGE_X_TOLERANCE:
        return False

    if _has_body_between(tail_page_regions, below=tail_bbox[3]):
        return False
    return not _has_body_between(head_page_regions, above=head.bbox[1])


# 쪽 경계가 셀 하나를 가로질러 글자가 갈린 것을 다시 잇는다.
#
# 실측(시행령 PDF 24~25쪽): 원래 `업무정보(4)` 한 셀(4행 세로 병합)인데 쪽 경계가 그 셀을
# 지나 **24쪽 1행 `업무정보` + 25쪽 3행 `(4)`** 로 갈렸다. 이으면 같은 표의 안 갈린 형제
# 셀(`업무정보\n(3)`)과 모양이 같아진다 — 그래서 `\n` 으로 잇는다.
#
# **판정은 기하다.** 앞 조각이 1행이고 뒤 조각이 다행 앵커일 때만 잇는다. 쪽 경계의 열 쌍
# 10개(참 2·거짓 8)로 재니 이 규칙이 **오검출 0** 이고, 값 반복만 보는 규칙은 `구분` 열의
# `선택`/`선택`·`필수`/`필수` 를 병합으로 오인해 **2건을 오검출**했다. 값이 같은 것과 병합된
# 것은 다르다 — `_fill_merged_cells` 가 같은 이유로 `row.cells[j] is None` 을 겹쳐 본다.
#
# 못 잡는 것: 쪽 경계가 **행 자체**를 가르는 경우(학교기록물 23~24쪽). 표본 1건이고 거짓
# 8건과 안 갈려 미결로 뒀다 — 근거는 `plan_log/표-경로.md`
def _join_split_cells(
    tail_rows: list[list[str]],
    head_rows: list[list[str]],
    tail_spans: list[int] | None,
    head_spans: list[int] | None,
) -> None:
    if not tail_rows or not head_rows or not tail_spans or not head_spans:
        return

    last = tail_rows[-1]
    first = head_rows[0]
    for col in range(min(len(last), len(first), len(tail_spans), len(head_spans))):
        head_value = first[col].strip()
        if not last[col].strip() or not head_value:
            continue
        # 앞 조각이 1행이 아니거나 뒤 조각이 다행 앵커가 아니면 서로 다른 행의 값이다
        if tail_spans[col] != 1 or head_spans[col] < 2:
            continue

        joined = f"{last[col]}\n{first[col]}"
        last[col] = joined
        # 뒤 조각이 덮던 행 전부. `_fill_merged_cells` 가 이미 같은 값으로 채워 뒀다
        for row in head_rows:
            if col >= len(row) or row[col].strip() != head_value:
                break
            row[col] = joined


# 쪽 경계에서 끊긴 세로 병합을 이어받는다.
#
# 채우는 범위를 **첫 행부터 빈 칸이 이어지는 구간까지**로 좁히는 것이 이 함수의 핵심이다.
# `_fill_merged_cells()` 를 다시 돌릴 수는 없다 — 그것은 PyMuPDF 의 **쪽별 셀 기하**에
# 기대는데 이어 붙인 표에는 그 기하가 없다. 그렇다고 *"같은 열 위에 값이 있으면 채운다"* 로
# 돌아가면 위 칸이 실제로 여기까지 내려오지 않는 자리까지 채운다(실측으로 897칸 중 13칸).
# 값이 나오는 순간 멈추면 그 위험이 쪽 경계 한 곳으로 갇힌다
def _carry_merge_anchor(last_row: list[str], rows: list[list[str]]) -> None:
    for col, anchor in enumerate(last_row):
        if not anchor.strip():
            continue
        for row in rows:
            if col >= len(row) or row[col].strip():
                break
            row[col] = anchor


# 실어 온 경계 span 에서 n 번째 격자 행의 것을 꺼낸다. 없으면 None
def _nth_head_span(boundary: tuple[Any, Any], index: int) -> list[int] | None:
    heads = boundary[0]
    if not isinstance(heads, list) or index >= len(heads):
        return None
    return heads[index]


# 다음 쪽 표를 앞 쪽 표에 흡수시킨다
def _absorb(tail: ContentRegion, head: ContentRegion, tail_spans: list[int] | None) -> None:
    tail_rows = tail.rows or []
    head_rows = list(head.rows or [])
    head_boundary = head.metadata.get(_BOUNDARY_SPANS_KEY) or (None, None)

    # 문서가 쪽마다 헤더를 되풀이하면 그 행은 데이터가 아니다. 시행령 24~25쪽이
    # 33+35-1=67 로 HWPX 판과 정확히 맞는 것이 근거다(2026-08-20 실측)
    if (
        tail_rows
        and head_rows
        and [cell.strip() for cell in head_rows[0]] == [cell.strip() for cell in tail_rows[0]]
    ):
        head_rows = head_rows[1:]
        # 헤더를 버렸으니 첫 데이터 행은 **두 번째** 격자 행이다
        head_spans = _nth_head_span(head_boundary, 1)
    else:
        head_spans = _nth_head_span(head_boundary, 0)

    # 갈린 셀 잇기가 앵커 이어받기보다 **먼저**다. 순서가 바뀌면 이어받기가 갈린 조각을
    # 아래로 퍼뜨려 되돌릴 수 없다
    _join_split_cells(tail_rows, head_rows, tail_spans, head_spans)

    if tail_rows and head_rows:
        _carry_merge_anchor(tail_rows[-1], head_rows)

    # 표시 자리도 함께 잇는다. 뒤 조각의 행 번호는 앞 조각의 행 수만큼 밀리고,
    # 반복 헤더를 버렸으면 한 행 더 당겨진다 — 안 맞추면 메우기가 엉뚱한 칸을 채운다
    dropped = len(head.rows) - len(head_rows) if head.rows else 0
    tail.mark_cells = list(tail.mark_cells) + [
        (r - dropped + len(tail_rows), c)
        for r, c in head.mark_cells
        if r - dropped >= 0
    ]
    tail.rows = tail_rows + head_rows
    # 쪽을 가로지르는 표는 단일 쪽 크롭으로 표현할 수 없다. 남겨 두면 유료 경로가
    # **조각 하나만 보낸 결과를 표 전체인 줄 안다** — 없으면 라우터가 table_native 로 간다
    tail.blob = None
    tail.blob_kind = None
    # 쪽을 넘긴 표에 끝 쪽을 남기는 것은 HWPX 가 이미 쓰는 규약이다(`hwpx.py`) —
    # splitter 가 `_element_page_end()` 로 읽어 `Chunk.page_end` 로 나르고 indexing 이
    # payload 에 싣는다. 여기서 새 키를 만들면 그 흐름을 통째로 놓친다
    if head.page:
        tail.metadata["page_end"] = head.page


# 쪽을 가로질러 이어지는 표를 되붙인다. 쪽별 리전 목록을 제자리에서 고친다.
#
# 흡수한 표를 `tail` 로 **물고 가는** 것이 요점이다 — 세 쪽 이상 걸친 표에서 두 번째 쪽의
# 표는 이미 지워졌으므로, 그 쪽의 바닥 표를 다시 찾으면 엉뚱한 표를 잡는다. 판정에 쓰는
# 사각형은 그때그때 **그 쪽에서 차지한 사각형**이어야 해서 따로 들고 다닌다
def _merge_across_pages(per_page: list[list[ContentRegion]]) -> None:
    tail: ContentRegion | None = None
    tail_bbox: tuple[float, ...] | None = None
    # tail 의 **마지막 격자 행** span. 흡수할 때마다 head 의 것으로 갈린다 — 이어 붙인 표의
    # 마지막 행은 방금 흡수한 조각의 마지막 행이기 때문이다
    tail_spans: list[int] | None = None

    for index in range(len(per_page) - 1):
        if tail is None:
            tail = _edge_table(per_page[index], last=True)
            tail_bbox = tuple(tail.bbox) if tail and tail.bbox else None
            boundary = (tail.metadata.get(_BOUNDARY_SPANS_KEY) if tail else None) or (None, None)
            tail_spans = boundary[1]

        head = _edge_table(per_page[index + 1], last=False)
        if (
            tail is None
            or tail_bbox is None
            or head is None
            or not _is_page_continuation(tail, tail_bbox, per_page[index], head, per_page[index + 1])
        ):
            tail, tail_bbox, tail_spans = None, None, None
            continue

        _absorb(tail, head, tail_spans)
        tail_bbox = tuple(head.bbox) if head.bbox else None
        head_boundary = head.metadata.get(_BOUNDARY_SPANS_KEY) or (None, None)
        tail_spans = head_boundary[1]
        per_page[index + 1].remove(head)


# .pdf 를 읽는 로더
class PdfLoader(BaseDocumentLoader):
    file_format = FileFormat.PDF

    # 페이지를 훑어 텍스트·표·이미지 리전을 위치 순서대로 만든다.
    #
    # **모으고 → 잇고 → 번호를 매기는** 세 단계다. 쪽을 가로지르는 표를 되붙이려면 쪽 루프가
    # 끝난 뒤에 볼 수 있어야 해서, 쪽 안에서 곧바로 `order` 를 매기던 것을 미뤘다
    def detect_regions(self, path: Path, ctx: LoadContext) -> list[ContentRegion]:
        per_page: list[list[ContentRegion]] = []

        with pymupdf.open(str(path)) as document:
            for page_no in range(len(document)):
                page = document[page_no]

                # 글자가 거의 없으면 스캔본 -> 페이지 전체를 이미지로 넘긴다.
                # 스캔본 쪽도 같은 목록에 담아야 쪽 순서가 어긋나지 않는다
                if len(page.get_text().strip()) < self.config.content.pdf_scan_text_threshold:
                    region = self._page_as_image(page, page_no)
                    per_page.append([region] if region is not None else [])
                    if region is not None:
                        ctx.warn(f"{page_no + 1}쪽은 텍스트가 없어 이미지로 처리했습니다.")
                    continue

                items = self._page_items(document, page, page_no, ctx)
                # 세로 위치 -> 가로 위치 순으로 정렬해야 사람이 읽는 순서가 된다
                per_page.append(
                    [region for _, _, region in sorted(items, key=lambda it: (round(it[0], 1), it[1]))]
                )

            _merge_across_pages(per_page)

        regions = [region for page_regions in per_page for region in page_regions]
        # 사설 키는 여기서 끝난다. 남기면 공개 스키마(ParsedDocument)로 새어 나간다
        for region in regions:
            region.metadata.pop(_BOUNDARY_SPANS_KEY, None)
        for order, region in enumerate(regions):
            region.order = order
        return regions

    # 페이지 하나에서 표·텍스트·이미지 리전을 좌표와 함께 수집
    def _page_items(
        self, document: pymupdf.Document, page: pymupdf.Page, page_no: int, ctx: LoadContext
    ) -> list[tuple[float, float, ContentRegion]]:
        items: list[tuple[float, float, ContentRegion]] = []
        table_bboxes: list[tuple[float, ...]] = []

        # --- 표 ---
        try:
            found = page.find_tables()
            tables = list(found.tables)
        except Exception as exc:  # noqa: BLE001 - 표 탐지 실패가 페이지 전체를 막으면 안 된다
            ctx.warn(f"{page_no + 1}쪽 표 탐지에 실패했습니다: {type(exc).__name__}")
            tables = []

        extracted: list[_Table] = []
        # 같은 쪽 되붙이기가 표 객체를 잃으므로, 경계 span 을 되찾을 수 있게 원본을 남겨 둔다
        raw_spans: list[tuple[tuple[float, ...], tuple[list[list[int]], list[int]]]] = []
        # 승급 신호도 같은 이유로 조각 단위로 재어 둔다
        raw_marks: list[tuple[tuple[float, ...], int, list[tuple[int, int]]]] = []
        shapes = _mark_shapes(page) if tables else []
        diagonals = _diagonal_lines(page) if tables else []
        column_axis = ""
        for table in tables:
            bbox = tuple(float(v) for v in table.bbox)
            try:
                rows = _fill_merged_cells(table)
            except Exception:  # noqa: BLE001
                rows = []
            if not any(any(cell.strip() for cell in row) for row in rows):
                continue
            # 대각선으로 갈린 모서리 셀은 두 축의 이름을 함께 담고 있다. 행 축 이름만
            # 그 칸에 남기고 열 축 이름은 격자 밖(metadata)으로 뺀다 — 평평한 격자에
            # '열 전체를 가리키는 이름' 이 앉을 자리가 없다
            head = table.rows[0].cells[0] if table.rows and table.rows[0].cells else None
            if head is not None and rows and rows[0]:
                split = _split_diagonal(page, head, diagonals)
                if split is not None:
                    rows[0][0], column_axis = split
            extracted.append((bbox, rows))
            spans = _boundary_spans(table)
            if spans is not None:
                raw_spans.append((bbox, spans))
            raw_marks.append((bbox, len(rows), _mark_cells(table, shapes)))

        for bbox, rows in _merge_split_tables(extracted):
            table_bboxes.append(bbox)
            metadata: dict[str, Any] = {}
            if column_axis:
                metadata["column_axis"] = column_axis
            merged_spans = _spans_within(bbox, raw_spans)
            if merged_spans is not None:
                metadata[_BOUNDARY_SPANS_KEY] = merged_spans
            items.append(
                (
                    bbox[1],
                    bbox[0],
                    ContentRegion(
                        content_type=ContentType.TABLE,
                        order=0,
                        source_format=self.file_format.value,
                        page=page_no + 1,
                        bbox=bbox,  # type: ignore[arg-type]
                        rows=rows,
                        # 외부 표 핸들러에 보낼 표 영역만 잘라낸 단일 페이지 PDF
                        blob=self._crop_pdf(document, page_no, bbox),
                        blob_kind="pdf",
                        mark_cells=_marks_within(bbox, raw_marks),
                        metadata=metadata,
                    ),
                )
            )

        # --- 텍스트 (표 영역과 겹치는 블록은 중복이므로 제외) ---
        for block in page.get_text("dict").get("blocks", []):
            if block.get("type") != 0:
                continue
            bbox = tuple(float(v) for v in block["bbox"])
            if any(_overlaps(bbox, tb) for tb in table_bboxes):
                continue

            spans = [
                span for line in block.get("lines", []) for span in line.get("spans", [])
            ]
            text = "".join(span.get("text", "") for span in spans).strip()
            if not text:
                continue

            items.append(
                (
                    bbox[1],
                    bbox[0],
                    ContentRegion(
                        content_type=ContentType.TEXT,
                        order=0,
                        source_format=self.file_format.value,
                        page=page_no + 1,
                        bbox=bbox,  # type: ignore[arg-type]
                        text=text,
                        metadata={FONT_SIZES_KEY: _span_sizes(spans)},
                    ),
                )
            )

        # --- 이미지 ---
        items.extend(self._image_items(document, page, page_no))
        return items

    # 페이지에 삽입된 그림들을 위치와 함께 수집
    def _image_items(
        self, document: pymupdf.Document, page: pymupdf.Page, page_no: int
    ) -> list[tuple[float, float, ContentRegion]]:
        items: list[tuple[float, float, ContentRegion]] = []

        for image_info in page.get_images(full=True):
            xref = image_info[0]
            try:
                rects = page.get_image_rects(xref)
                extracted = document.extract_image(xref)
            except Exception:  # noqa: BLE001 - 깨진 이미지 하나가 문서 전체를 막으면 안 된다
                continue

            blob = extracted.get("image")
            if not blob:
                continue

            rect = rects[0] if rects else page.rect
            bbox = (float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1))

            items.append(
                (
                    bbox[1],
                    bbox[0],
                    ContentRegion(
                        content_type=ContentType.IMAGE,
                        order=0,
                        source_format=self.file_format.value,
                        page=page_no + 1,
                        bbox=bbox,
                        blob=blob,
                        # PyMuPDF 가 실제 포맷을 준다(jpeg·png·jpx…). "png" 로 굳히면
                        # 뒤에서 이 값을 믿는 쪽이 거짓을 읽는다
                        blob_kind=extracted.get("ext") or "png",
                        metadata={"xref": xref},
                    ),
                )
            )
        return items

    # 텍스트가 없는 페이지를 통째로 렌더링해 이미지 리전으로 만든다.
    # `order` 는 detect_regions() 의 마지막 단계가 매기므로 여기서는 자리만 잡는다
    def _page_as_image(self, page: pymupdf.Page, page_no: int) -> ContentRegion | None:
        try:
            pixmap = page.get_pixmap(dpi=self.config.content.pdf_render_dpi)
            blob = pixmap.tobytes("png")
        except Exception:  # noqa: BLE001
            return None

        return ContentRegion(
            content_type=ContentType.IMAGE,
            order=0,
            source_format=self.file_format.value,
            page=page_no + 1,
            blob=blob,
            blob_kind="png",
            metadata={"scanned_page": True},
        )

    # 표 영역만 남긴 단일 페이지 PDF를 만들어 바이트로 돌려준다
    def _crop_pdf(
        self, document: pymupdf.Document, page_no: int, bbox: tuple[float, ...]
    ) -> bytes | None:
        try:
            cropped = pymupdf.open()
            cropped.insert_pdf(document, from_page=page_no, to_page=page_no)
            page = cropped[0]

            rect = (
                pymupdf.Rect(
                    bbox[0] - _CROP_PADDING,
                    bbox[1] - _CROP_PADDING,
                    bbox[2] + _CROP_PADDING,
                    bbox[3] + _CROP_PADDING,
                )
                & page.mediabox
            )

            if rect.is_empty or rect.width < 1 or rect.height < 1:
                cropped.close()
                return None

            page.set_cropbox(rect)
            data = cropped.tobytes()
            cropped.close()
            return data
        except Exception:  # noqa: BLE001 - 크롭 실패 시 LlamaParse를 건너뛰고 로컬 추출만 쓴다
            return None

    # 페이지 수와 문서 속성을 읽는다
    def extract_metadata(self, path: Path) -> dict[str, object]:
        with pymupdf.open(str(path)) as document:
            info = document.metadata or {}
            return {
                "page_count": len(document),
                "title": info.get("title") or None,
                "author": info.get("author") or None,
            }
