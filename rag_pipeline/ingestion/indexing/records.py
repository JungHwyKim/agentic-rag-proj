"""청크와 벡터를 짝지어 저장 레코드로 만든다. **`zip` 이 일어나는 유일한 자리다.**

## 왜 여기인가

embedding 은 `list[str] -> list[list[float]]` 라 `Chunk` 를 모르는 것이 계약이고, splitter 는
벡터를 모른다. 그래서 "몇 번째 청크의 벡터가 몇 번째인가"를 실제로 맞추는 곳은 여기뿐이다.
embedding 이 `len(out) == len(in)` 을 계약으로 세웠어도 **그 계약을 소비하는 쪽에서 확인하지
않으면 계약이 아니다** — 한 칸만 밀려도 아무 에러 없이 검색이 엉뚱한 문서를 가리킨다.

## document_id 를 파일명에서 뽑는 이유

`Chunk.document_key` 의 기본값은 **파싱 결과 해시**라 문서를 고치면 키가 바뀐다. 그러면
재색인이 옛 청크를 지울 열쇠를 잃어 문서가 통째로 두 벌 남는다. 그래서 내용이 아니라
이름에서 뽑는다 — 같은 이름이면 같은 문서로 보고 갱신한다.

**NFC 정규화가 핵심이다.** 한글 파일명을 macOS 는 NFD("ㄱㅏ"처럼 자모 분리), Windows 는
NFC 로 저장한다. 정규화하지 않으면 눈에 똑같은 파일명이 서로 다른 키가 되어, 막으려던 중복이
그대로 일어난다. 이 코퍼스가 한국어라 이론적인 위험이 아니다.

감수 사항: **내용이 다른 동명 파일은 서로를 덮어쓴다.** backend 에 문서 레지스트리가 생기면
명시적 id 로 옮긴다 — 근거는 plan_log/indexing.md 의 "미결".
"""

from __future__ import annotations

import unicodedata
from pathlib import PurePath

from rag_pipeline.exceptions import IndexingError
from rag_pipeline.schemas.chunk import Chunk
from rag_pipeline.schemas.vector import VectorRecord, point_id_for

__all__ = ["document_id_for", "to_records"]


# 원본 파일명에서 문서 키를 만든다. 경로를 떼고 NFC 로 맞춘다 (모듈 docstring 참고)
def document_id_for(source_name: str) -> str:
    # PurePath 를 쓰는 이유: 업로드된 이름에 "C:\a\b.pdf" 나 "a/b.pdf" 가 섞여 들어올 수 있고,
    # 그대로 두면 같은 문서가 경로 표기만 달라도 다른 키가 된다
    name = PurePath(str(source_name).replace("\\", "/")).name.strip()
    document_id = unicodedata.normalize("NFC", name)

    if not document_id:
        raise IndexingError(
            f"원본 파일명에서 문서 키를 만들 수 없습니다: {source_name!r}",
            user_message="파일 이름을 확인할 수 없어 색인하지 못했습니다.",
        )
    return document_id


# 청크와 벡터를 자리대로 묶어 저장 레코드로 만든다. 길이가 다르면 여기서 멈춘다
def to_records(
    chunks: list[Chunk],
    vectors: list[list[float]],
    *,
    document_id: str,
) -> list[VectorRecord]:
    if len(chunks) != len(vectors):
        raise IndexingError(
            f"청크와 벡터 개수가 다릅니다: 청크 {len(chunks)} / 벡터 {len(vectors)}",
            user_message="임베딩 결과가 청크와 맞지 않아 색인을 중단했습니다.",
        )

    # 차원이 섞이면 qdrant 가 알아보기 힘든 메시지로 실패한다. 보내기 전에 여기서 가른다
    sizes = {len(vector) for vector in vectors}
    if len(sizes) > 1:
        raise IndexingError(
            f"벡터 차원이 섞여 있습니다: {sorted(sizes)}",
            user_message="임베딩 결과가 일관되지 않아 색인을 중단했습니다.",
        )
    if sizes == {0}:
        raise IndexingError(
            "벡터가 비어 있습니다",
            user_message="임베딩 결과가 비어 있어 색인을 중단했습니다.",
        )

    return [
        VectorRecord(
            id=point_id_for(chunk.chunk_id),
            vector=vector,
            text=chunk.text,
            document_id=document_id,
            chunk_id=chunk.chunk_id,
            metadata=_payload_metadata(chunk),
        )
        for chunk, vector in zip(chunks, vectors, strict=True)
    ]


# 검색 결과에서 원본 청크를 되짚는 데 필요한 것을 모은다.
# 구조 필드를 **나중에** 넣는 이유: chunk.metadata 에 같은 이름의 키가 들어 있어도
# 구조 필드가 이겨야 한다. 반대로 두면 표의 metadata 하나가 page 를 조용히 덮어쓴다
def _payload_metadata(chunk: Chunk) -> dict:
    return {
        **chunk.metadata,
        "type": str(chunk.type),
        "chunk_index": chunk.chunk_index,
        "source_name": chunk.source_name,
        "order": chunk.order,
        "element_orders": list(chunk.element_orders),
        "page": chunk.page,
        "page_end": chunk.page_end,
        "heading": chunk.heading,
        "heading_path": list(chunk.heading_path),
        "heading_level": chunk.heading_level,
    }
