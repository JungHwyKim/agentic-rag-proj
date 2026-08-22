"""재색인 — 문서를 고쳤을 때 사라진 청크가 실제로 없어지는가.

설계서가 든 예시 그대로다. 문서가 `A B C D E` 에서 `A B D` 로 바뀌면 C·E 는 저장소에서
없어져야 한다. `upsert` 만으로는 안 된다 — 새 청크가 앞의 세 자리를 덮어쓰고 **뒤의 두
자리는 옛 내용 그대로 남는다.** 남은 둘은 계속 검색되지만 원문에는 없는 문장이라, 인용을
따라가면 문서에 없는 말을 문서가 했다고 하게 된다.

`document_id` 를 **파일명**에서 뽑는 이유가 여기서 드러난다. 파싱 내용 해시로 잡으면 고친
문서가 새 키를 얻어 아래 `delete_by_document_id` 가 옛 청크를 아예 못 찾는다.
"""

from __future__ import annotations

from rag_pipeline.ingestion.indexing import to_records

# 문서를 고치기 전후의 본문. 가운데와 끝이 사라진다
BEFORE = ["가 항목", "나 항목", "다 항목", "라 항목", "마 항목"]
AFTER = ["가 항목", "나 항목", "라 항목"]


# 본문 목록을 그 문서의 청크 목록으로. splitter 처럼 index 를 0부터 다시 매긴다
def _records(make_chunk, unit_vector, texts, *, document_id="안내서.pdf"):
    chunks = [
        make_chunk(index, text, document_key=document_id) for index, text in enumerate(texts)
    ]
    vectors = [unit_vector(index % 4) for index in range(len(texts))]
    return to_records(chunks, vectors, document_id=document_id)


# 저장소에 남아 있는 본문 전부. 순위와 무관하게 내용만 본다
def _stored_texts(store, unit_vector):
    return {result.text for result in store.search(unit_vector(0), top_k=50)}


def test_upsert_alone_leaves_removed_chunks_behind(store, make_chunk, unit_vector):
    """왜 삭제가 필요한지를 보이는 대조군. 이게 통과하는 한 delete 를 지우면 안 된다."""
    store.ensure_collection(4)
    store.upsert(_records(make_chunk, unit_vector, BEFORE))

    store.upsert(_records(make_chunk, unit_vector, AFTER))

    assert store.count() == 5
    assert "마 항목" in _stored_texts(store, unit_vector)


def test_delete_then_upsert_removes_them(store, make_chunk, unit_vector):
    """설계서 9절이 원한 동작."""
    store.ensure_collection(4)
    store.upsert(_records(make_chunk, unit_vector, BEFORE))

    store.delete_by_document_id("안내서.pdf")
    store.upsert(_records(make_chunk, unit_vector, AFTER))

    assert store.count() == 3
    assert _stored_texts(store, unit_vector) == set(AFTER)


def test_reindexing_does_not_touch_other_documents(store, make_chunk, unit_vector):
    """문서 단위 삭제가 옆 문서까지 쓸어가면 안 된다."""
    store.ensure_collection(4)
    store.upsert(_records(make_chunk, unit_vector, BEFORE, document_id="안내서.pdf"))
    store.upsert(_records(make_chunk, unit_vector, ["다른 문서"], document_id="지침.pdf"))

    store.delete_by_document_id("안내서.pdf")

    assert store.count() == 1
    assert _stored_texts(store, unit_vector) == {"다른 문서"}


def test_reindexing_identical_content_is_stable(store, make_chunk, unit_vector):
    """같은 문서를 두 번 올려도 개수가 안 는다 — chunk_id 가 결정론적이라야 성립한다."""
    store.ensure_collection(4)
    records = _records(make_chunk, unit_vector, BEFORE)

    store.upsert(records)
    store.delete_by_document_id("안내서.pdf")
    store.upsert(_records(make_chunk, unit_vector, BEFORE))

    assert store.count() == len(BEFORE)


def test_document_emptied_leaves_nothing(store, make_chunk, unit_vector):
    """문서가 빈 문서로 바뀌었을 때 옛 청크가 통째로 남으면 안 된다."""
    store.ensure_collection(4)
    store.upsert(_records(make_chunk, unit_vector, BEFORE))

    store.delete_by_document_id("안내서.pdf")
    store.upsert([])

    assert store.count() == 0
