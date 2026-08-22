"""`gold_page` 를 **PDF 원본**과 대조한다.

## 왜 따로 검증하나

`gold_page` 는 채점에 안 쓴다. 그래서 틀려도 지표가 안 움직이고 **아무도 모른다.** 실제로
2026-08-22 사람 검수에서 세 건(`sih-11`·`sch-13`·`sch-14`)이 한 쪽씩 어긋난 것이 발견됐다 —
원인은 청크의 `page` 가 **시작 쪽**이라 쪽을 넘긴 청크의 뒷부분에 있는 span 이 한 쪽 앞으로
보고된 것이었다. 사람이 그 쪽을 열었을 때 정답이 없으면 세트 전체를 못 믿게 된다.

검수자가 손으로 잡은 것을 다시 손에 맡기지 않으려고 여기서 자동으로 본다.

## 왜 PDF 만 보나

**사람이 여는 것이 PDF 다.** HWPX 는 쪽 번호가 문서에 따라 크게 부풀어 있어(CLAUDE.md —
같은 문서를 PDF 28쪽 / HWPX 97쪽으로 센다) 대조할 진실이 없다. PDF 는 `page_count` 와
쪽별 텍스트를 파일에서 직접 읽을 수 있다.

## 표는 셀로 본다

표 span 은 로더가 조립한 마크다운 행(`| 준비 | 지침확인 | … |`)이라 **PDF 원문에 그대로는
없다.** 파이프를 걷어내고 셀 내용이 그 쪽에 있는지로 본다.
"""

from __future__ import annotations

import re
import unicodedata

from evaluation.documents import DOCUMENT_GROUPS, FIXTURES_DIR
from evaluation.qa_set import QAItem

__all__ = ["validate_gold_pages_against_pdf"]

_WS = re.compile(r"\s+")


# PDF 에서 뽑은 날것의 텍스트를 대조용으로 맞춘다. **제어문자를 여기서 지운다** —
# 파이프라인에서는 cleaner 가 하지만 이 함수는 원본을 직접 읽으므로 스스로 해야 한다
# (CLAUDE.md "PDF 제어문자" — 글머리 기호가 U+0001 등으로 떨어진다)
def _flatten(text: str) -> str:
    kept = "".join(ch for ch in text if unicodedata.category(ch) not in ("Cc", "Cf"))
    return _WS.sub("", unicodedata.normalize("NFC", kept)).lower()


# 표 마크다운 행에서 셀 내용만 뽑는다. 구분선(`---`)과 빈 칸은 버린다
def _cells(span: str) -> list[str]:
    return [
        cell.strip()
        for cell in span.strip().strip("|").split("|")
        if cell.strip() and set(cell.strip()) != {"-"}
    ]


# 기록된 gold_page["pdf"] 가 실제 그 쪽에 있는지 본다. 문제 목록을 돌려준다
def validate_gold_pages_against_pdf(items: list[QAItem]) -> list[str]:
    import pymupdf

    problems: list[str] = []
    cache: dict[str, list[str]] = {}

    for item in items:
        name = DOCUMENT_GROUPS.get(item.source_doc, {}).get("pdf")
        path = FIXTURES_DIR / name if name else None
        if not path or not path.exists():
            continue

        if name not in cache:
            with pymupdf.open(path) as doc:
                cache[name] = [_flatten(page.get_text()) for page in doc]
        pages = cache[name]

        recorded = item.gold_page.get("pdf")
        if recorded is None:
            problems.append(f"{item.query_id}: gold_page 에 pdf 가 없다")
            continue
        if not 1 <= recorded <= len(pages):
            problems.append(f"{item.query_id}: pdf 쪽 {recorded} 이 범위 밖이다(1~{len(pages)})")
            continue

        haystack = pages[recorded - 1]
        span = item.gold_spans[0]
        # 표는 조립된 마크다운이라 원문에 그대로 없다. 셀로 본다
        needles = _cells(span) if item.answer_type == "table" else [span]
        missing = [n for n in needles if _flatten(n) not in haystack]
        if missing:
            elsewhere = [
                i + 1
                for i, text in enumerate(pages)
                if all(_flatten(n) in text for n in needles)
            ]
            problems.append(
                f"{item.query_id}: pdf {recorded}쪽에 정답이 없다"
                + (f" (실제 {elsewhere}쪽)" if elsewhere else " (어느 쪽에서도 못 찾음)")
            )

    return problems
