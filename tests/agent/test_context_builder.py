"""근거 조립. **모양을 여기서 만들지 않는다**가 제일 중요한 검사다.

`generation/prompts.py` 의 블록 모양과 글자까지 같아야 한다 — 갈리면 판정이 본 것과 생성이
보는 것이 달라져 *"판정은 맞았는데 답이 틀렸다"* 가 성립하지 않는다.

나머지 둘은 예산 규칙이다: **청크 단위로 자른다**(반쪽 청크 금지)와 **순서를 안 바꾼다**.
"""

from __future__ import annotations

from rag_pipeline.agent.context import build_context, select_within_budget
from rag_pipeline.generation.prompts import build_evidence_block, build_user_prompt
from rag_pipeline.schemas.retrieval import RetrievedChunk


def _chunk(chunk_id: str, text: str, source: str = "안내서.pdf") -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        document_id=source,
        text=text,
        metadata={"source_name": source},
    )


# 조립 결과가 생성 프롬프트의 근거 블록과 **글자까지 같다**
def test_shape_matches_generation_prompt():
    chunks = [_chunk("c1", "첫 근거"), _chunk("c2", "둘째 근거")]

    context = build_context(chunks)

    assert context == build_evidence_block(chunks)
    assert context in build_user_prompt("질문", chunks)


# 출처와 번호가 붙는다
def test_numbers_and_source_are_present():
    context = build_context([_chunk("c1", "본문", source="시행령.pdf")])

    assert "[1] (시행령.pdf)" in context
    assert "본문" in context


# 예산이 없으면 전부 싣는다 — 오늘 경로와 글자 그대로 같다
def test_no_budget_keeps_everything():
    chunks = [_chunk(f"c{i}", "가" * 100) for i in range(5)]

    assert select_within_budget(chunks) == chunks


# 예산을 넘기면 청크 단위로 끊는다. **반쪽 청크가 안 생긴다**
def test_budget_cuts_on_chunk_boundary():
    chunks = [_chunk("c1", "가" * 100), _chunk("c2", "나" * 100), _chunk("c3", "다" * 100)]

    kept = select_within_budget(chunks, 250)

    assert [c.chunk_id for c in kept] == ["c1", "c2"]
    assert all(len(c.text) == 100 for c in kept)


# 첫 청크도 못 담는 예산이면 빈 근거다 — 반쪽을 넣어 있는 것처럼 보이게 하지 않는다
def test_budget_smaller_than_first_chunk_yields_empty():
    assert select_within_budget([_chunk("c1", "가" * 100)], 10) == []
    assert build_context([_chunk("c1", "가" * 100)], budget_chars=10) == ""


# 순서를 안 바꾼다 — 정렬은 리랭커의 일이고 조립기의 일이 아니다
def test_order_is_preserved():
    chunks = [_chunk("c3", "셋"), _chunk("c1", "하나"), _chunk("c2", "둘")]

    kept = select_within_budget(chunks, 100)

    assert [c.chunk_id for c in kept] == ["c3", "c1", "c2"]


# 번호는 실제로 실린 청크 기준이다 — 잘린 뒤 [3] 이 남으면 없는 근거를 가리킨다
def test_numbering_follows_kept_chunks():
    chunks = [_chunk("c1", "가" * 50), _chunk("c2", "나" * 50), _chunk("c3", "다" * 50)]

    context = build_context(chunks, budget_chars=120)

    assert "[2]" in context
    assert "[3]" not in context
