"""답변 생성 프롬프트.

## 왜 프롬프트로만 근거를 묶나

**임계값을 지어내지 않기 위해서다.** dense 점수가 얼마 아래면 "관련 없음"인지는 아직 못
잰다 — Dot 점수 분포를 재본 적이 없고, 이 저장소의 임계값은 관측값이 아니라 **분포의 틈**
에서 나와야 한다(표 되붙이기 12pt, 표시 면적비 0.08~0.10 대 1.00). 틈이 어디인지 모르는 채
숫자를 고르면 이미지 표시 크기 50pt 를 탐침값으로 박았다가 되돌린 일을 되풀이한다.

대신 `RetrievedChunk.dense_score` 가 답변에 실려 나가므로 **분포는 저절로 모인다.**
그것이 쌓이면 임계값 논의를 실측으로 할 수 있다(`plan_log/backend.md` 미결).

## 왜 청크에 번호를 다나

번호가 있어야 모델이 "몇 번 근거"라고 가리킬 수 있고, 사람이 답변과 소스 태그를 눈으로
맞춰볼 수 있다. 화면의 소스 태그가 그 번호 순서 그대로 그려진다.

## 쪽 번호를 왜 프롬프트에 넣지 않나

**HWPX 쪽 수가 문서에 따라 부풀거나 붕괴한다**(CLAUDE.md — AI가이드가 PDF 28쪽 대 HWPX
97쪽, 변환본은 1쪽). 모델에게 "N쪽을 보라"고 시키면 그 값이 틀린 문서에서 답변이 틀린 쪽을
인용한다. 쪽은 **화면의 소스 태그가 metadata 에서 직접** 그린다 — 모델을 거치지 않는다.
"""

from __future__ import annotations

from rag_pipeline.schemas.retrieval import RetrievedChunk

__all__ = ["NO_EVIDENCE_ANSWER", "SYSTEM_PROMPT", "build_user_prompt"]

# 근거를 못 찾았을 때 쓸 문장. 모델이 이 문장을 그대로 쓰도록 프롬프트가 지시하고,
# 검색이 0건이면 모델을 부르지 않고 코드가 이 값을 그대로 돌려준다
NO_EVIDENCE_ANSWER = "문서에서 찾지 못했습니다."

SYSTEM_PROMPT = f"""당신은 한국어 공문서를 읽고 질문에 답하는 도우미입니다.

규칙:
1. 아래 제공된 근거에 있는 내용만으로 답합니다. 근거에 없는 사실을 덧붙이지 않습니다.
2. 근거로 답할 수 없으면 정확히 이렇게만 답합니다: "{NO_EVIDENCE_ANSWER}"
   추측하거나 일반 상식으로 채우지 않습니다.
3. 답변에 쓴 내용마다 어느 근거에서 왔는지 [1], [2] 형식으로 표시합니다.
4. 근거의 표현을 그대로 쓰되, 질문에 맞게 필요한 부분만 추립니다.
5. 간결하게 답합니다. 근거를 통째로 옮겨 적지 않습니다."""


# 검색된 청크를 번호 달린 근거 블록으로 만들어 질문과 함께 싣는다
def build_user_prompt(query: str, chunks: list[RetrievedChunk]) -> str:
    blocks = []
    for index, chunk in enumerate(chunks, start=1):
        source = chunk.metadata.get("source_name") or chunk.document_id
        blocks.append(f"[{index}] ({source})\n{chunk.text}")
    evidence = "\n\n".join(blocks)
    return f"근거:\n\n{evidence}\n\n---\n\n질문: {query}"
