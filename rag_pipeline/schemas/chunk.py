"""splitter 의 산출물 스키마.

`ParsedDocument` 와 나란히 두되 섞지 않는다. 앞 단계들은 전부
`ParsedDocument -> ParsedDocument` 를 지켜 떼었다 붙였다 할 수 있지만, splitter 는 타입을
바꾸는 첫 단계다. 그래서 청크를 `Element` 에 욱여넣지 않고 따로 만든다 — 그렇게 하면
`chunk_id` 나 '헤더를 반복해 넣은 조각인가' 같은 청크 전용 사실이 `metadata` 로 숨어버린다.

`page_end` 를 따로 두는 이유: HWPX 로더가 쪽을 넘긴 표에 `metadata["page_end"]` 를 싣는다.
`page` 만 옮기면 여러 쪽에 걸친 표를 인용할 때 시작 쪽만 남아 뒷부분이 엉뚱한 쪽을 가리킨다.

`element_orders` 를 남기는 이유: 청크에서 원본 요소로 되짚어야 인용과 디버깅이 된다.
`order` 는 원본 요소의 값이고 `chunk_index` 는 청크의 순번이라 **서로 다른 수**다 —
cleaner 가 요소를 지운 자리와 핸들러가 요소를 0개 만든 자리 때문에 `order` 에는 빈틈이 있다.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


# 청크의 종류. 표는 텍스트와 분할 방식이 달라 구분해야 한다
class ChunkType(StrEnum):
    TEXT = "text"
    TABLE = "table"


# 검색 단위 하나. embedding/indexing 이 이 목록을 받는다
class Chunk(BaseModel):
    # f"{document_key}:{chunk_index:04d}". 같은 파싱 결과면 항상 같은 값이 나온다
    chunk_id: str
    # 문서를 가리키는 키. indexing 이 진짜 document_id 를 정하면 그걸 넘겨받는다
    document_key: str
    # 문서 안에서의 청크 순번. 0부터 빈틈 없이 이어진다
    chunk_index: int
    type: ChunkType
    text: str

    source_name: str
    # 이 청크를 이루는 첫 요소의 order. 문서 순서 정렬에 쓴다
    order: int = 0
    # 이 청크가 삼킨 원본 요소들의 order 목록
    element_orders: list[int] = Field(default_factory=list)

    page: int | None = None
    # 여러 쪽에 걸친 경우의 끝 쪽. 한 쪽짜리면 None
    page_end: int | None = None

    # 이 청크가 속한 제목. 조상까지 포함한 경로와 마지막 제목을 함께 둔다
    heading: str | None = None
    heading_path: list[str] = Field(default_factory=list)
    heading_level: int | None = None

    # 표 원본 배열, 이미지에서 온 요소 목록, 분할 조각 번호 등
    metadata: dict[str, Any] = Field(default_factory=dict)
