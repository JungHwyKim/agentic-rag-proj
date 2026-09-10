"""선택된 청크를 `final_context` 문자열로 조립한다.

## 근거 블록 모양을 여기서 만들지 않는다

`generation/prompts.py:build_evidence_block()` 을 부른다. 그 모양의 출처가 하나여야 하는
이유는 그 파일이 이미 적어 둔 것과 같다 — **판정이 본 것과 생성이 보는 것이 글자까지 같아야**
*"판정은 맞았는데 답이 틀렸다"* 가 성립한다. 여기서 제 손으로 만들면 그 문장에 **세 번째
모양**이 끼어들고, 세 곳 중 한 곳만 고쳐져도 아무 에러가 안 난다.

## 다시 정렬하지 않는다

`retrieve()` 가 이미 정렬해 돌려준다 — 리랭커를 켰으면 그 순서이고, 아니면 RRF 순서다
(`retrieval/hybrid.py:_finish()`). 여기서 `ranking_score` 로 또 정렬하면 **리랭커가 만든
순서를 덮어쓸 위험만 있고 얻는 것이 없다.** 순서를 바꿀 자리는 리랭커이지 조립기가 아니다.

## 예산은 토큰이 아니라 문자다

한국어 청크는 **문자 ≈ 토큰**이다(cl100k_base 로 잰 문자/토큰 중앙값 1.03~1.09 · 최소 0.90,
CLAUDE.md). splitter 의 `max_chunk_chars` 가 문자인 것과 같은 근거다. 토크나이저를 부르면
이 단계가 임베딩 모델 선택에 묶이는데, 얻는 정확도는 그 오차 범위 안이다.

## 자를 때는 청크 단위로 자른다

**중간에서 안 자른다.** 청크 절반은 (a) 출처 번호가 가리키는 내용과 실제로 실린 내용이
어긋나고 (b) 마크다운 표를 행 한가운데서 끊는다. 리랭커의 *"전부 아니면 전무"* 와
재작성의 *"반쯤 쓰지 않는다"* 가 같은 기준이다.

## 기본값이 「안 자른다」인 이유 — 쟀고, 그대로 두기로 했다

**자를 값을 안 정한 것이 아니라 정할 필요가 없다는 것을 쟀다**(2026-08-29, 외부 호출 0).
저장된 평가 실행에서 **생성 386회 · 문맥 초과로 실패한 것 0건**이고, 실사용 로그의 생성
57건에도 그 사례가 없다. 한때 재판정 조건이던 *«실제로 문맥 상한에 걸린 사례»* 가 **한 번도
안 일어났다.**

`openai_llm.py` 가 *청크를 안 자른다* 를 고른 이유는 그대로 문다 — **정답 span 이 표 행에
흩어져 있어** 뒤를 자르면 답이 통째로 빠질 수 있다. 그리고 **자르는 것이 답을 낫게 하는지는
못 잰다**: 골든 답변이 없고, 조건 비교에서 지금 유일하게 변별하는 인용 정합성 축은
*인용한 청크가 `gold_span` 을 담나* 라 근거를 줄이면 **정의상 나빠지기만 한다.** 즉 이 측정은
*«값을 골라야 하나»* 에만 답하고 *«얼마가 좋은 값인가»* 에는 원리적으로 못 답한다.

**근거 블록 크기는 `top_k` 에 선형이다** — 독립 근거 구성 6개에서 `top_k=5` 가 중앙값
2,986자·최대 5,908자, `top_k=10` 이 중앙값 5,920자·최대 11,029자다(청크 최대가 1,201토큰이라는
실측이 그 기울기이고, 문자 ≈ 토큰을 최소비 0.90 으로 보수적으로 읽으면 최대 약 12,254토큰).
**상한까지의 거리를 숫자로 못 적는 것은 생성 모델의 문맥 상한을 저장소 안에서 확인할 근거가
없어서다** — 없는 분모를 지어내는 대신 선형이라는 것만 남긴다.

그래서 **재판정 조건이 둘이고 둘 다 관측 가능한 사건이다**: 생성이 문맥 초과로 한 번이라도
실패하거나, `generation_top_k` 를 지금 관측된 범위(5~10) 밖으로 크게 키울 때.
수치는 `.claude/wiki/verify_log/2026-08-29-근거예산-값.md`.
"""

from __future__ import annotations

from rag_pipeline.generation.prompts import build_evidence_block
from rag_pipeline.schemas.retrieval import RetrievedChunk

__all__ = ["build_context", "select_within_budget"]


# 예산 안에 들어가는 만큼만 앞에서부터 고른다. **순서는 안 바꾼다** (모듈 docstring 참고)
def select_within_budget(
    chunks: list[RetrievedChunk], budget_chars: int | None = None
) -> list[RetrievedChunk]:
    if budget_chars is None:
        return list(chunks)

    kept: list[RetrievedChunk] = []
    used = 0
    for chunk in chunks:
        size = len(chunk.text)
        # 한 청크도 못 담는 예산이면 빈 근거가 되는데, 그것이 사실이다 —
        # 반쪽 청크를 넣어 답이 있는 것처럼 보이게 하지 않는다
        if used + size > budget_chars:
            break
        kept.append(chunk)
        used += size
    return kept


# 청크를 근거 문자열로 조립한다. 잘린 목록이 따로 필요하면 select_within_budget() 을 먼저 부른다
def build_context(
    chunks: list[RetrievedChunk], *, budget_chars: int | None = None
) -> str:
    return build_evidence_block(select_within_budget(chunks, budget_chars))
