"""답변에 쓰인 출처 번호가 실제 근거를 가리키는지 검사한다.

`prompts.py` 의 `SYSTEM_PROMPT` 규칙 3이 *"[1], [2] 형식으로 표시합니다"* 를 시키는데,
그 지시를 모델이 지켰는지는 지금까지 아무도 안 봤다. 근거가 2개인데 답변이 `[3]` 을
가리켜도 화면까지 그대로 나간다.

번호의 뜻을 정하는 것은 `prompts.py:build_evidence_block()` 이다 — 그것이 청크를 1부터
세어 `[n]` 을 붙인다. **여기는 그 거울상이라 규칙이 하나여야 한다**: 1..len(chunks) 밖은
가리킬 것이 없는 번호다.

## 검사가 답변을 안 바꾼다

`evaluate.py` 의 *"판정이 답변을 안 바꾼다"* 와 **같은 계약이다.** 틀린 번호를 지우거나
다시 매기기 시작하면 *검사가 맞았나* 와 *답이 좋아졌나* 가 한 실행에 섞이는데, 답변 품질을
재는 자[尺]가 아직 없어(`plan_log/backend.md` 미결) 그 섞임을 풀 방법이 없다. 그래서
`Answer.citation` 은 관측이고, 화면도 답변 옆에 한 줄을 놓을 뿐이다.

## 문제가 없으면 `None` 이다

`ok: bool` + 번호 목록으로 쪼개면 *`ok=True` 인데 목록이 찬* 못 쓰는 조합이 생기고 그것을
템플릿이 매번 방어해야 한다. `cached_from`(셋을 한 덩어리로) · `rewrite`(질의와 mode 를 한
덩어리로)가 같은 이유로 두 번 고른 모양이다 — 판정이 한 곳에서만 난다.

## 검사를 건너뛰는 자리가 둘이고 **둘 다 정상 경로다**

- **근거가 0개일 때.** `answer.py` 가 모델을 아예 안 부르고 `NO_EVIDENCE_ANSWER` 를 그대로
  돌려주는 자리다. 인용할 것이 없으니 인용 0개가 옳다
- **답변이 `NO_EVIDENCE_ANSWER` 일 때.** 근거가 **있어도** 모델이 규칙 2를 따라 그 문장을
  고를 수 있다. 그때도 인용 0개가 옳다

**이 둘을 안 가르면 "근거를 못 찾았다"가 전부 인용 오류로 뜬다** — 그리고 그것은 이 저장소가
정상 경로로 못 박아 둔 답변이다(`schemas/answer.py` — *"근거를 못 찾은 것은 실패가 아니다"*).

## 대괄호 안이 순수 숫자일 때만 인용으로 본다

한국 공문서는 `[별표 1]`·`[붙임 2]` 를 쓰는데 `\[(\d+)\]` 는 그것을 안 문다. 감수 사항이
하나 남는다 — 근거 문장을 그대로 옮긴 답변이 **원문의 `[3]`** 을 품으면 범위 밖으로 셀 수
있다. 그때 나가는 것은 **거짓 경고이지 거짓 답변이 아니다**(위 계약이 답변을 지킨다).
반대로 이 함정을 피하려고 "근거에 없는 `[n]` 만 센다" 로 가면, 모델이 근거를 베껴 쓴
정상 인용까지 통과시켜 검사가 아무것도 안 잡는다.
"""

from __future__ import annotations

import re

from rag_pipeline.generation.prompts import NO_EVIDENCE_ANSWER
from rag_pipeline.schemas.answer import CitationTrace
from rag_pipeline.schemas.retrieval import RetrievedChunk

__all__ = ["CITATION_PATTERN", "check_citations", "cited_numbers"]

# 대괄호 안이 **순수 숫자일 때만** 인용이다 (모듈 docstring 참고)
CITATION_PATTERN = re.compile(r"\[(\d+)\]")


# 답변에 쓰인 출처 번호를 **나온 순서대로 중복 없이** 모은다.
# 순서를 지키는 이유는 경고 문구가 "[3] 을 인용했습니다" 로 사람이 읽을 것이기 때문이다
def cited_numbers(text: str) -> list[int]:
    found: dict[int, None] = {}
    for raw in CITATION_PATTERN.findall(text):
        found.setdefault(int(raw), None)
    return list(found)


# 인용이 근거와 맞나. **문제가 없으면 None** 이다 (모듈 docstring 참고).
# 순수 함수라 (text, sources) 만 있으면 언제든 다시 계산된다 — 답변 캐시가 이 값을
# 안 싣고 조회 때 다시 부르는 것이 그래서 성립한다(`query.py`)
def check_citations(text: str, chunks: list[RetrievedChunk]) -> CitationTrace | None:
    # 건너뛰는 자리 둘. 둘 다 정상 경로다 (모듈 docstring 참고)
    if not chunks or text.strip() == NO_EVIDENCE_ANSWER:
        return None

    numbers = cited_numbers(text)
    # **둘은 동시에 못 난다** — 번호가 하나도 없어야 `missing` 이고 그러면 범위 밖일
    # 번호 자체가 없다. 그래서 순서를 다투지 않는다
    out_of_range = [number for number in numbers if number < 1 or number > len(chunks)]
    if out_of_range:
        return CitationTrace(
            problem="out_of_range", numbers=out_of_range, source_count=len(chunks)
        )
    if not numbers:
        return CitationTrace(problem="missing", numbers=[], source_count=len(chunks))
    return None
