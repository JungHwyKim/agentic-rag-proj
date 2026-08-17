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


# 표를 상한 이하 조각들로 나눈다. 각 조각은 (마크다운, 그 조각의 행 배열)이다.
# 쪼갤 필요가 없으면 조각 하나, 원본 배열을 못 쓰면 None
def split_table(
    markdown: str, raw_rows: Any, *, max_chars: int
) -> list[tuple[str, list[list[str]]]] | None:
    if len(markdown) <= max_chars:
        rows = _usable_rows(raw_rows)
        return [(markdown, rows if rows is not None else [])]

    rows = _usable_rows(raw_rows)
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
