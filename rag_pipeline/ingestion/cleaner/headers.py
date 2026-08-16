"""반복 머리말 제거 — 쪽마다 되풀이되는 문서 제목·기관명을 걷어낸다.

실측: 신뢰가능 AI 가이드에서 `'공공기관 신뢰가능 AI 구현 실용가이드 DNA플러스 2019'` 가
25개 쪽의 **첫 요소**로 되풀이된다. 그대로 두면 그 25개 쪽에서 만들어지는 모든 청크에 문서
제목이 딸려 들어가고, 검색할 때 이 문구가 제목 질의와 매칭돼 엉뚱한 쪽이 상위로 올라온다.

## 왜 '등장 비율'이 아니라 '걸친 폭'인가

처음에는 "전체 쪽수의 20% 이상에 등장하면 머리말"로 잡았다가 폐기했다. 같은 문서를 로더가
HWPX 는 97쪽, PDF 는 28쪽으로 세기 때문이다(PDF 파일 자체는 28쪽이므로 HWPX 쪽 계산이 과다
분할하고 있다 — 별건). 이 분모 차이 때문에 등장 비율이 25.8% ↔ 89.3% 로 요동쳤고, 쪽 계산을
고치면 `'AI 활동'`·`'기획'` 같은 표 머리행 문구가 6.2% → 21.4% 로 뛰어 임계값을 넘었다.
즉 그 방식의 '여유'는 버그가 만든 착시였다.

걸친 폭 `(마지막 쪽 - 첫 쪽 + 1) / 전체 쪽수` 은 분자와 분모가 함께 부풀어 분모 오류에 강하다.
실측으로 진짜 머리말은 HWPX 93.8% / PDF 89.3% 로 거의 같았고, 오탐 후보들은 29.9~45.4% 였다.
임계값 60% 는 그 사이에서 양쪽으로 여유가 있는 지점이다(50/60/70% 모두 오탐 0).

## 안전장치

- **문단만 본다.** 표를 대상에 넣으면 양식·체크리스트처럼 같은 표가 여러 쪽 반복되는 문서에서
  진짜 데이터 표가 통째로 지워진다. 실측으로 문단만 봐도 판정 결과가 같아 잃는 것이 없다.
  한글이 표로 그린 제목 상자는 로더의 `_is_layout_box` 가 이미 문단으로 강등해 둔다
- **쪽의 첫 요소인 것만 지운다.** 텍스트만 보고 지우면 본문 한가운데 나온 같은 문구까지 사라진다
- **문서가 비어버리면 아무것도 안 지운다.** 쪽마다 요소가 하나뿐인 짧은 문서가 전부 머리말로
  판정될 수 있다. 그때는 판정을 통째로 무른다
- `order` 는 재부여하지 않는다. 계약상 용도는 정렬 하나뿐이고 `elements` 에는 이미 빈틈이 있다
"""

from __future__ import annotations

from rag_pipeline.config import CleanerConfig
from rag_pipeline.schemas.document import Element, ElementType, ParsedDocument

# 경고에 넣을 때 문구가 길면 잘라 준다
_WARN_TEXT_LIMIT = 60


# 각 쪽에서 제일 앞선 문단을 골라 {쪽: 요소} 로 돌려준다.
# 표·이미지를 빼는 것이 표 삭제 사고를 막는 핵심이다
def _page_first_paragraphs(document: ParsedDocument) -> dict[int, Element]:
    first: dict[int, Element] = {}
    for element in sorted(document.elements, key=lambda e: e.order):
        if element.page is None or element.type is not ElementType.PARAGRAPH:
            continue
        if not element.text.strip():
            continue
        first.setdefault(element.page, element)
    return first


# 문서가 아는 서로 다른 쪽 번호의 개수. 쪽 정보가 없으면 0
def _page_count(document: ParsedDocument) -> int:
    pages = {e.page for e in document.elements if e.page is not None}
    return len(pages)


# 쪽 첫 문단 중 '문서 전체에 걸쳐 되풀이되는' 문구를 찾는다
def _detect(document: ParsedDocument, config: CleanerConfig) -> dict[str, list[int]]:
    total = _page_count(document)
    if total <= 0:
        return {}

    pages_by_text: dict[str, list[int]] = {}
    for page, element in _page_first_paragraphs(document).items():
        pages_by_text.setdefault(element.text.strip(), []).append(page)

    detected: dict[str, list[int]] = {}
    for text, pages in pages_by_text.items():
        if len(pages) < config.header_min_pages:
            continue
        span = max(pages) - min(pages) + 1
        if span / total >= config.header_min_span_ratio:
            detected[text] = sorted(pages)
    return detected


# 반복 머리말을 지운 새 문서를 돌려준다. 지운 게 없으면 입력을 그대로 돌려준다
def remove_repeated_headers(
    document: ParsedDocument, config: CleanerConfig
) -> tuple[ParsedDocument, int]:
    detected = _detect(document, config)
    if not detected:
        return document, 0

    first = _page_first_paragraphs(document)
    # 지울 대상은 '쪽 첫 문단이면서 판정된 문구와 같은' 요소뿐이다.
    # id() 로 잡아야 같은 문구가 본문 한가운데 있을 때 그건 남는다
    doomed = {id(e) for e in first.values() if e.text.strip() in detected}
    kept = [e for e in document.elements if id(e) not in doomed]

    if not kept:
        # 전부 머리말로 판정된 문서. 본문을 다 날리느니 아무것도 안 한다.
        # 이건 정상 동작이 아니라 이상 징후이므로 warnings 쪽에 남긴다
        warnings = [
            *document.metadata.warnings,
            "모든 요소가 반복 머리말로 판정돼 제거를 취소했습니다. "
            "쪽마다 요소가 하나뿐인 문서일 수 있습니다.",
        ]
        metadata = document.metadata.model_copy(update={"warnings": warnings})
        return document.model_copy(update={"metadata": metadata}), 0

    # 지운 내역은 warnings 가 아니라 removed_texts 에 남긴다. 정상적으로 제거한 것은
    # '문제'가 아니므로, 파싱 경고를 보는 쪽과 섞이면 둘 다 못 읽게 된다
    removed_texts = list(document.metadata.removed_texts)
    for text, pages in detected.items():
        shown = text if len(text) <= _WARN_TEXT_LIMIT else text[:_WARN_TEXT_LIMIT] + "..."
        removed_texts.append(f"반복 머리말 '{shown}' ({len(pages)}개 쪽)")

    removed = len(document.elements) - len(kept)
    metadata = document.metadata.model_copy(
        update={
            "removed_texts": removed_texts,
            "removed_elements": document.metadata.removed_elements + removed,
        }
    )
    return document.model_copy(update={"elements": kept, "metadata": metadata}), removed
