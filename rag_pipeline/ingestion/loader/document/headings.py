"""제목 승격 — 로더가 관측한 글자 크기를 보고 문단을 TITLE 로 올린다.

## 왜 로더에서 하는가

뒤에 붙는 splitter 는 요소를 heading 경계까지 **병합**하는 것이 주 작업이다(실측: 요소 자수
중앙값이 PDF 23 / HWPX 15 / 시행령 40 이고 1200자를 넘는 텍스트 요소가 세 문서 전부 0개다).
그런데 그 경계를 내놓는 로더가 둘뿐이었다 — docx 는 스타일 이름으로, markdown 은 `#` 로
`TITLE` 을 만들지만 **PDF 와 HWPX 는 전부 PARAGRAPH 고정**이었고, 실제 코퍼스는 그 둘이다.
"어디에 무엇이 있는가"는 로더 몫이므로 splitter 가 텍스트를 보고 추론하지 않는다.

## 왜 글자 크기인가

스타일을 먼저 봤으나 못 쓴다. 실측으로 AI 가이드 HWPX 의 `Contents/header.xml` 에는 스타일이
`Normal` **하나뿐**이었다. 반면 글자 크기(`charPr height`)는 240~2617 로 살아 있다.

## 판정 규칙 셋 — 전부 실측으로 정했다

**(1) 본문 크기는 문서별 최빈값으로 구한다.** 절대 임계값을 쓰지 않는다. 실측으로 본문이
HWPX 949 / 1500 / 1200, PDF 9.5 라 문서마다 다르다.

**(2) 배율이 아니라 부등호 하나로 판정한다.** 시행령 안내서는 제목 1600 / 본문 1500 으로 차이가
6.7% 뿐이다. 1.2배 같은 임계값을 두면 그 문서의 제목 19개가 통째로 사라진다.

**(3) `min(sizes) > body` 다. `max` 가 아니다.** 문단 안에 본문 크기 이하 글자가 하나라도
섞이면 제목이 아니라 강조로 본다 — 큰 글씨가 사람 이름이나 인용 강조인 경우가 여기서 걸린다
(실측으로 '혼재' 문단의 실제 내용은 ※인용, 표지 장식, 목차 줄이었다: PDF 4개 / HWPX 24·3·1개).

## 기각한 신호

**번호·기호 머리(`□` · `1.` · `제N조`)로 승격하지 않는다.** 직관과 반대로, 큰 글씨와 마커가
겹치는 문단이 AI 가이드에서 PDF 0/74 · HWPX 0/54 였다. 마커만 보면 PDF 75개 · HWPX 80개 ·
시행령 158개를 제목으로 오인한다. 볼드도 쓰지 않는다 — 재본 적 없는 신호다.

## 호출 위치

`BaseDocumentLoader.load()` 의 `detect_regions()` 와 `route_all()` **사이**다. `detect_regions()`
안이 아닌 이유는 `regression/baseline.json` 이 그 함수의 출력(리전 수·타입별 수·쪽 집합)을
기준선으로 재기 때문이다. 여기 두면 같은 결과를 얻으면서 기준선이 흔들리지 않는다.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from rag_pipeline.ingestion.loader.content.base import ContentRegion, ContentType
from rag_pipeline.schemas.document import ElementType

# 로더가 관측한 글자 크기를 실어 보내는 칸. 판정이 끝나면 걷어낸다
FONT_SIZES_KEY = "font_sizes"


# 리전에 실린 글자 크기 목록을 꺼낸다. 없거나 형태가 다르면 빈 목록
def _sizes_of(region: ContentRegion) -> list[float]:
    raw = region.metadata.get(FONT_SIZES_KEY)
    if not isinstance(raw, list):
        return []
    return [float(v) for v in raw if isinstance(v, int | float) and not isinstance(v, bool) and v > 0]


# 문서의 본문 글자 크기 = 관측된 크기의 최빈값. 관측이 없으면 None.
#
# 동률이면 **큰 쪽**을 본문으로 잡는다. 작은 쪽을 고르면 같은 빈도의 큰 본문이 통째로 제목으로
# 올라가 문서가 수백 개 섹션으로 쪼개진다. 반대로 큰 쪽을 고르면 그 크기의 제목만 놓치므로
# 손해가 훨씬 작다
def _body_size(size_lists: Iterable[list[float]]) -> float | None:
    counter = Counter(size for sizes in size_lists for size in sizes)
    if not counter:
        return None
    most = max(counter.values())
    return max(size for size, count in counter.items() if count == most)


# 승격 대상인가. 이미 로더가 종류를 정해 보낸 요소(docx 의 제목·목록 등)는 건드리지 않는다
def _is_promotable(region: ContentRegion) -> bool:
    if region.content_type is not ContentType.TEXT:
        return False
    raw = region.metadata.get("element_type", ElementType.PARAGRAPH)
    try:
        return ElementType(raw) is ElementType.PARAGRAPH
    except ValueError:
        return False


# 리전 목록을 제자리에서 고쳐 제목을 승격한다. 관측값은 승격 여부와 무관하게 항상 걷어낸다
def promote_headings(regions: list[ContentRegion], *, enabled: bool = True) -> None:
    observed = [(region, _sizes_of(region)) for region in regions]

    if enabled:
        body = _body_size(sizes for _, sizes in observed)
        if body is not None:
            # 대표 크기는 max 로 잡는다. 승격된 리전은 이미 모든 글자가 본문보다 크므로
            # 어느 쪽을 써도 되지만, 등수는 '제일 큰 글자'로 매기는 편이 계층과 맞는다
            promoted = [
                (region, max(sizes))
                for region, sizes in observed
                if sizes and min(sizes) > body and _is_promotable(region)
            ]
            ranks = {
                size: rank
                for rank, size in enumerate(sorted({s for _, s in promoted}, reverse=True), start=1)
            }
            for region, size in promoted:
                region.metadata["element_type"] = ElementType.TITLE
                region.metadata["heading_level"] = ranks[size]

    # 요소마다 숫자 리스트가 딸려 나가면 Element.metadata 가 부푼다. 판정에만 쓰고 버린다
    for region, _ in observed:
        region.metadata.pop(FONT_SIZES_KEY, None)
