"""요소를 제목 경계로 묶어 섹션을 만든다.

splitter 의 주 작업이 **자르기가 아니라 병합**이라는 것이 이 모듈의 전제다. 실측으로 요소
자수 중앙값이 PDF 23 / HWPX 15 / 시행령 40 이고, PDF 텍스트 블록은 707개 중 349개(49%)가
20자 미만이었다. 이걸 그대로 청크로 만들면 20자짜리 청크가 절반이 된다. 그래서 제목이
나올 때까지 이어 붙인다.

제목 계층은 스택으로 만든다. 새 제목의 레벨이 스택 꼭대기보다 크지 않으면(= 같거나 더 상위면)
꼭대기를 걷어내고 밀어 넣는다. 그러면 `3. 용어정의` 아래 `3.1` `3.2` 가 자식으로 붙고,
`4.` 가 오면 `3.x` 가 걷힌다.

**본문 없는 제목은 청크를 만들지 않는다.** 제목만 든 청크는 검색되어도 알맹이가 없다.
제목이 연달아 나오면 앞의 것은 조상으로 남거나(하위 제목이 이어질 때) 걷힌다(같은 층이
이어질 때). 표지의 장식 문구들이 서로를 걷어내는 것이 이 규칙의 부수 효과다.

`order` 의 연속성을 가정하지 않는다. cleaner 가 반복 머리말을 지운 자리와 핸들러가 요소를
0개 만든 자리 때문에 빈틈이 있고, 계약상 `order` 의 용도는 정렬 하나뿐이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rag_pipeline.schemas.document import Element, ElementType, ParsedDocument

# heading_level 이 없는 제목의 기본 레벨. markdown/pptx 처럼 단계를 안 주는 로더가 있다
_DEFAULT_LEVEL = 1


# 제목 하나와 그 아래 본문 요소들
@dataclass
class Section:
    # 이 섹션을 여는 제목. 문서 첫머리처럼 제목이 없는 구간이면 None
    heading: str | None = None
    heading_level: int | None = None
    # 조상 제목까지 포함한 경로. 마지막 항목이 heading 과 같다
    heading_path: list[str] = field(default_factory=list)
    body: list[Element] = field(default_factory=list)


# 요소 목록을 제목 경계로 끊어 섹션 목록으로 만든다
def build_sections(document: ParsedDocument) -> list[Section]:
    elements = sorted(document.elements, key=lambda e: e.order)

    sections: list[Section] = []
    stack: list[tuple[int, str]] = []
    current = Section()

    for element in elements:
        if element.type is not ElementType.TITLE:
            if element.text.strip():
                current.body.append(element)
            continue

        text = element.text.strip()
        if not text:
            continue

        # 앞 섹션에 본문이 있을 때만 남긴다. 없으면 제목만 든 청크가 되므로 버린다
        if current.body:
            sections.append(current)

        level = element.metadata.get("heading_level")
        level = level if isinstance(level, int) and level > 0 else _DEFAULT_LEVEL

        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, text))

        current = Section(
            heading=text,
            heading_level=level,
            heading_path=[title for _, title in stack],
        )

    if current.body:
        sections.append(current)

    return sections
