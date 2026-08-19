"""표 청킹 — 헤더 행을 반복하며 행 그룹으로 나눈다.

표를 일반 텍스트 청커에 태우면 안 되는 이유는 검색 결과에서 드러난다. 행 하나만 걸렸을 때
`| A | 기업 | 1억 |` 만 남으면 '1억'이 무엇의 금액인지 복원할 수 없다. 그래서 쪼갠 **모든**
조각에 헤더 행을 다시 붙인다.

여기가 상한이 실제로 발화하는 유일한 자리다. 실측으로 텍스트 요소는 최대 554자인데
시행령 안내서의 표는 67행 3,243자였고 9개 중 3개가 1200자를 넘었다.

겹침은 주지 않는다. 표에서 겹침은 같은 행을 두 청크에 넣는다는 뜻이고, 그러면 같은 사실이
두 번 색인돼 검색 결과에 중복으로 올라온다.

`rows`(원본 2차원 배열)가 없으면 여기서 아무것도 못 한다 — 마크다운만 보고 행을 되살리려면
셀 안의 이스케이프된 파이프까지 되돌려야 해서 원본보다 나빠진다. 그때는 None 을 돌려주고
호출부가 일반 텍스트 분할로 내려간다.
"""

from __future__ import annotations

from typing import Any

from rag_pipeline.ingestion.loader.content.base import rows_to_markdown

# 헤더만 있고 데이터가 없는 표는 쪼갤 것이 없다
_MIN_ROWS = 2


# metadata["rows"] 가 2차원 문자열 배열인지 확인하고 정규화한다. 아니면 None
def _usable_rows(raw: Any) -> list[list[str]] | None:
    if not isinstance(raw, list) or len(raw) < _MIN_ROWS:
        return None

    rows: list[list[str]] = []
    for row in raw:
        if not isinstance(row, list):
            return None
        rows.append([("" if cell is None else str(cell)) for cell in row])
    return rows


# 헤더가 가로로 병합돼 있으면 하위 열이 **이름을 잃는다.** 로더는 "가로 연장은 빈 칸으로
# 둔다"는 규칙으로 그렇게 두는데, 그 규칙의 근거는 *splitter 가 표를 행 단위로만 쪼갠다*
# 는 것이라 **데이터 행에만 성립한다.** 헤더 행에서는 옆으로 이어진 이름이 곧 그 열의 이름이다.
#
# 실측: 시행령 24쪽 67행 표는 `항목` 이 1~3열에 걸쳐 병합돼 이름 있는 열이 0·1·4·5뿐이었고,
# 그 헤더가 5조각 전부에 되풀이됐다. 3열은 4/5 조각에서 통째로 비어 파이프만 실려 나갔다.
#
# **순서가 중요하다 — 버릴 열을 먼저 고른다.** 채우기를 먼저 하면 헤더가 안 비어 있게 되어
# 아무 열도 못 버린다.
#
# 판정은 **표 전체 기준**이다. 조각마다 따로 버리면 같은 표의 조각들이 서로 다른 열 구성을
# 갖게 되는데, `test_splitter_real.py:test_split_table_pieces_keep_header` 가 **모든 조각의
# 헤더가 같아야 한다**를 못 박고 있고 그 계약이 옳다 — 조각 둘이 나란히 검색됐을 때 열이
# 서로 다르면 같은 표로 안 읽힌다. 그래서 어느 조각에서는 비어 보이는 열이 남을 수 있다
def normalize_header(rows: list[list[str]]) -> list[list[str]]:
    if not rows:
        return rows

    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]
    header, *body = padded

    # 1) 원본 헤더가 비어 있고 이 조각의 모든 행에서도 빈 열만 버린다
    keep = [
        index
        for index in range(width)
        if header[index].strip() or any(row[index].strip() for row in body)
    ]
    if not keep:
        return rows

    trimmed = [[row[index] for index in keep] for row in padded]

    # 2) 남은 헤더의 빈 칸을 왼쪽 이름으로 채운다. 병합된 이름이 그 열의 이름이다
    filled = list(trimmed[0])
    last = ""
    for index, name in enumerate(filled):
        if name.strip():
            last = name
        elif last:
            filled[index] = last
    trimmed[0] = filled

    return trimmed


# 표를 상한 이하 조각들로 나눈다. 각 조각은 (마크다운, 그 조각의 행 배열)이다.
# 쪼갤 필요가 없으면 조각 하나, 원본 배열을 못 쓰면 None
def split_table(
    markdown: str, raw_rows: Any, *, max_chars: int
) -> list[tuple[str, list[list[str]]]] | None:
    raw = _usable_rows(raw_rows)
    # 쪼개기 **전에** 한 번 정규화한다. 그래야 모든 조각이 같은 헤더·같은 열 구성을 받는다
    rows = normalize_header(raw) if raw is not None else None

    if len(markdown) <= max_chars:
        if rows is None:
            return [(markdown, [])]
        # 안 바뀌었으면 원본 마크다운을 그대로 돌려준다. 무조건 다시 렌더하면 멀쩡한 표의
        # 텍스트가 공백까지 미묘하게 달라진다 — cleaner 가 쓰는 규약과 같다
        if rows == raw:
            return [(markdown, rows)]
        return [(rows_to_markdown(rows), rows)]

    if rows is None:
        return None

    header, *body = rows
    chunks: list[tuple[str, list[list[str]]]] = []
    group: list[list[str]] = []

    for row in body:
        candidate = [header, *group, row]
        rendered = rows_to_markdown(candidate)
        if group and len(rendered) > max_chars:
            chunks.append((rows_to_markdown([header, *group]), [header, *group]))
            group = [row]
            continue
        group.append(row)

    if group:
        # 헤더 + 행 하나가 이미 상한을 넘는 경우가 있다. 행을 쪼개면 열 대응이 깨져
        # 표의 의미가 사라지므로, 상한을 넘기더라도 행 단위는 지킨다
        chunks.append((rows_to_markdown([header, *group]), [header, *group]))

    return chunks
