"""청크↔벡터 대응과 문서 키 파생의 계약.

이 파일이 지키는 것 둘이다.

1. **`len(chunks) == len(vectors)` 이거나 예외다.** embedding 이 세운 계약을 소비하는 쪽에서
   확인하지 않으면 계약이 아니다 — 한 칸만 밀려도 아무 에러 없이 검색이 엉뚱한 문서를 준다
2. **같은 문서는 같은 키를 얻는다.** 파일명 표기가 달라도(경로·NFD/NFC) 같아야 재색인이
   옛 청크를 찾는다
"""

from __future__ import annotations

import unicodedata

import pytest

from rag_pipeline.exceptions import IndexingError
from rag_pipeline.ingestion.indexing import document_id_for, to_records
from rag_pipeline.schemas.vector import point_id_for

# --- document_id_for ---


def test_path_prefix_is_stripped():
    """업로드 이름에 경로가 섞여 와도 같은 문서여야 한다."""
    assert document_id_for("C:\\upload\\안내서.pdf") == "안내서.pdf"
    assert document_id_for("tmp/2024/안내서.pdf") == "안내서.pdf"


def test_nfd_and_nfc_collapse_to_one_key():
    """macOS(NFD)와 Windows(NFC)가 같은 파일명을 다르게 저장한다. 안 맞추면 문서가 두 벌 된다."""
    nfc = unicodedata.normalize("NFC", "안내서.pdf")
    nfd = unicodedata.normalize("NFD", "안내서.pdf")

    # 전제 확인 — 두 표기가 실제로 다른 문자열이다
    assert nfc != nfd
    assert document_id_for(nfd) == document_id_for(nfc)


def test_surrounding_space_is_trimmed():
    assert document_id_for("  안내서.pdf  ") == "안내서.pdf"


@pytest.mark.parametrize("name", ["", "   ", "/", "C:\\"])
def test_unusable_name_raises(name):
    """빈 키는 delete_by_document_id 를 무의미하게 만든다. 조용히 넘기지 않는다."""
    with pytest.raises(IndexingError):
        document_id_for(name)


# --- point id ---


def test_point_id_is_deterministic():
    """같은 chunk_id 는 항상 같은 point 로 떨어져야 upsert 가 갱신이 된다."""
    assert point_id_for("안내서.pdf:0007") == point_id_for("안내서.pdf:0007")
    assert point_id_for("안내서.pdf:0007") != point_id_for("안내서.pdf:0008")


def test_point_id_is_uuid_shaped():
    """qdrant 는 point ID 로 uint 또는 UUID 만 받는다 — 임의 문자열은 ValueError 다."""
    import uuid

    assert uuid.UUID(point_id_for("안내서.pdf:0007"))


# --- to_records ---


def test_records_pair_each_chunk_with_its_own_vector(make_chunk, unit_vector):
    chunks = [make_chunk(index, f"본문 {index}") for index in range(3)]
    vectors = [unit_vector(index) for index in range(3)]

    records = to_records(chunks, vectors, document_id="안내서.pdf")

    assert [record.chunk_id for record in records] == [chunk.chunk_id for chunk in chunks]
    assert [record.vector for record in records] == vectors
    assert [record.text for record in records] == [chunk.text for chunk in chunks]


def test_length_mismatch_raises(make_chunk, unit_vector):
    """embedding 이 짧은 목록을 돌려주는 일이 생기면 여기서 멈춰야 한다."""
    chunks = [make_chunk(index) for index in range(3)]
    vectors = [unit_vector(0), unit_vector(1)]

    with pytest.raises(IndexingError, match="개수가 다릅니다"):
        to_records(chunks, vectors, document_id="안내서.pdf")


def test_ragged_dimensions_raise(make_chunk, unit_vector):
    """차원이 섞이면 qdrant 가 알아보기 힘든 메시지로 죽는다. 먼저 가른다."""
    chunks = [make_chunk(index) for index in range(2)]
    vectors = [unit_vector(0), unit_vector(0, dim=8)]

    with pytest.raises(IndexingError, match="차원이 섞여"):
        to_records(chunks, vectors, document_id="안내서.pdf")


def test_empty_vectors_raise(make_chunk):
    chunks = [make_chunk(0)]

    with pytest.raises(IndexingError, match="비어 있습니다"):
        to_records(chunks, [[]], document_id="안내서.pdf")


def test_no_chunks_gives_no_records():
    assert to_records([], [], document_id="안내서.pdf") == []


def test_structural_fields_reach_metadata(make_chunk, unit_vector):
    """검색 결과에서 원본 청크를 되짚으려면 이것들이 payload 에 있어야 한다."""
    chunk = make_chunk(
        3,
        "본문",
        page=12,
        page_end=13,
        heading="제2장 적용범위",
        heading_path=["제2장 적용범위"],
        heading_level=1,
        element_orders=[40, 41],
    )

    metadata = to_records([chunk], [unit_vector(0)], document_id="안내서.pdf")[0].metadata

    assert metadata["chunk_index"] == 3
    assert metadata["page"] == 12
    assert metadata["page_end"] == 13
    assert metadata["heading_path"] == ["제2장 적용범위"]
    assert metadata["element_orders"] == [40, 41]
    assert metadata["source_name"] == "안내서.pdf"


def test_chunk_metadata_is_carried_through(make_chunk, unit_vector):
    chunk = make_chunk(0, "| 구분 | 값 |", metadata={"rows": [["구분", "값"]], "part": 1})

    metadata = to_records([chunk], [unit_vector(0)], document_id="안내서.pdf")[0].metadata

    assert metadata["rows"] == [["구분", "값"]]
    assert metadata["part"] == 1


def test_structural_fields_win_over_chunk_metadata(make_chunk, unit_vector):
    """표의 metadata 하나가 page 를 조용히 덮어쓰면 인용이 엉뚱한 쪽을 가리킨다."""
    chunk = make_chunk(0, "본문", page=12, metadata={"page": 999})

    metadata = to_records([chunk], [unit_vector(0)], document_id="안내서.pdf")[0].metadata

    assert metadata["page"] == 12


def test_document_id_is_stamped_not_taken_from_chunk(make_chunk, unit_vector):
    """document_key 는 splitter 것이고 document_id 는 indexing 이 정한다."""
    chunk = make_chunk(0, document_key="해시값123")

    record = to_records([chunk], [unit_vector(0)], document_id="안내서.pdf")[0]

    assert record.document_id == "안내서.pdf"
