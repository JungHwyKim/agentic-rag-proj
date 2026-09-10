"""채점자가 «미지지» 를 **어느 세기의 오류부터** 내는지 잰다. `answer_eval` 이 자기 자신을
검사하는 두 번째 자리다.

## 왜 따로 있나 — «재는 일» 과 «재는 도구를 검사하는 일» 은 다르다

`answer_eval` 은 파이프라인을 재고, 여기는 **그 도구를 잰다.** 같은 파일에 `--probe` 로
붙이면 프로브가 본 실행의 회계 게이트를 우회하거나 오염시키는 자리가 생긴다 —
`verdicts.py` · `rerank_diff.py` · `retry_diff.py` 와 같은 «이미 잰 것을 읽는 도구» 계보다.

## 표본이 0이었던 것이 아니라 **해상도**를 몰랐다

첫 실행(2026-08-28, 사람 43건)은 원래 답변 28건이 전부 `supported` 로 나와 *"미지지를 제대로 내는가의
표본이 0"* 으로 닫혔다. 그런데 **음성 대조군이 이미 `unsupported` 를 28건 냈다** — 즉
«채점자가 미지지를 낼 수 있나» 는 이미 28/28 로 답이 나와 있다. 못 답한 것은
**«어느 세기의 오류부터 잡나»** 이고, 「통째로 남의 근거」(대조군)와 「값 하나 바꿔치기」
사이 어디에 선이 있는지다. 그래서 이 도구의 이름은 변별력이 아니라 **민감도**다.

## 두 단, 그리고 **한쪽만 게이트다**

| 단 | 무엇 | 정답 | 어떻게 읽나 |
|---|---|---|---|
| **2단** 문장 덧붙이기 | 근거에 없는 문장을 통째로 하나 더한다 | `unsupported` | **전건 잡아야 통과** |
| **1단** 값 치환 | 숫자·조항·기관명 하나를 근거에 없는 값으로 바꾼다 | `unsupported` | **수만 남긴다** |

2단은 문장 하나가 통째로 근거 밖이라 문장 단위로 대조하면 반드시 걸린다 — 음성 대조군과
같은 «구성으로 만든 known-bad» 이고, 그래서 게이트가 될 수 있다. 1단은 **문장의 나머지가
근거와 같아** 놓칠 여지가 구조적으로 있다. 거기에 통과선을 두면 그 숫자의 출처가 없다 —
이 저장소가 `_MARK_AREA_RATIO` 와 되붙이기 12pt 를 **분포의 틈**에서만 고른 기준과 어긋난다.
그래서 1단은 관측값이고, 못 잡은 건은 `--wrong` 으로 사람이 읽는다.

## 변조는 **결정적**이어야 한다

LLM 으로 변조본을 만들면 자연스럽지만 (a) 다시 돌리면 다른 변조본이 되고 (b) 끼워 넣은 값이
우연히 근거에 있으면 *"정답은 unsupported"* 라는 **라벨 자체가 거짓**이 된다. 그래서 규칙으로
만들고, 만든 값이 근거 전체에 없는지 `squash()` 로 확인한다 — 공백을 지우고 보는 이유는
PDF 가 한국어 낱말 사이 공백을 잃기 때문이다(`evaluation/scoring.py`).

**못 만들면 `None` 이고 그 수를 회계에 찍는다.** 조용히 빠지면 «28건 중 20건 잡았다» 가
실은 22건 중 20건인 상태가 에러 없이 생긴다.

## 저장된 `abstained` 를 **안 믿는다**

디스크의 리포트는 `is_abstention()` 이 인용 번호를 걷어내기 **전** 버전일 수 있다(첫 실행이
그랬다 — 판정 30 · 대조군 `supported` 2 로 스스로 무효를 선언한 회차다). 기권은 판정 대상이
아니므로 여기서 `is_abstention()` 을 **다시 먹인다.**

## 근거는 되살리되 **순서가 계약이다**

리포트에는 `chunk_ids` 만 있고 본문이 없다. `point_id_for()` 가 `uuid5(chunk_id)` 결정값이라
`fetch_by_ids()` 로 되짚을 수 있는데, **qdrant 의 반환 순서는 보장이 없다.** 그 순서가
`build_evidence_block()` 의 근거 번호를 만들므로 반드시 `chunk_ids` 순서로 되돌린다.
`metadata` 도 통째로 실어야 한다 — `metadata["source_name"]` 이 근거 블록의 출처 이름이라,
빠지면 채점자가 본판과 **다른 글자**를 본다.

## 실패는 «못 잡았다» 가 아니다

키 없음·호출 실패·응답 손상은 `verdict="failed"` 로 나간다. 미지지로 접으면 **죽은 채점자가
민감도 만점**을 받고, «못 잡았다» 로 접으면 반대로 **멀쩡한 채점자가 0점**을 받는다.
둘 다 실력이 아니라 실패율을 재는 것이라 제3의 상태로 빼고 회계가 먼저 그것을 말한다.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from evaluation.answer_eval import JudgeConfig, JudgeUsage, SupportJudge, is_abstention
from evaluation.config import (
    DEFAULT_EMBEDDING_PROFILE,
    DEFAULT_PROBE_JUDGE_PROFILE,
    eval_config,
    judge_profile,
)
from evaluation.run_eval import RESULTS_DIR
from evaluation.scoring import squash
from rag_pipeline.generation.citations import CITATION_PATTERN
from rag_pipeline.schemas.retrieval import RetrievedChunk
from rag_pipeline.schemas.vector import StoredChunk, point_id_for

__all__ = [
    "EXTRA_SENTENCES",
    "LEVELS",
    "ProbeRow",
    "accounting_problems",
    "evidence_haystack",
    "mutate_sentence",
    "mutate_value",
    "probe",
    "reorder_chunks",
    "summarize",
]

# 두 단의 이름과 사람이 읽는 이름. **순서가 뜻을 갖는다** — 게이트인 2단을 먼저 찍는다
LEVELS: tuple[tuple[str, str], ...] = (
    ("sentence", "2단 · 문장 덧붙이기 (게이트)"),
    ("value", "1단 · 값 치환 (관측)"),
)

# 값 치환 후보를 찾는 패턴들. **순서가 곧 우선순위다** — 앞엣것일수록 «근거에 없는 값» 인지를
# 확인할 수 있는 단위가 커서, 우연히 근거에 있어 라벨이 거짓이 될 위험이 작다.
# 맨 숫자가 마지막인 것은 그 확인이 제일 헐거워서다(`7` 은 어느 문서에나 있다)
_BARE_NUMBER = r"\d[\d,]*"
_VALUE_PATTERNS: tuple[str, ...] = (
    r"제\s?\d+조(?:의\s?\d+)?",
    r"\d{4}\s?\.\s?\d{1,2}\s?\.\s?\d{1,2}",
    r"\d[\d,]*\s?(?:년|개월|일간|일|시간|분|주|회|차|건|명|개|쪽|페이지|퍼센트|%|원|만원|억원)",
    _BARE_NUMBER,
)

# 치환에 쓸 증분. **고정 순서라 같은 입력이 늘 같은 변조를 낸다** — 무작위로 두면 다시 돌린
# 회차와 나란히 못 놓는다
_DELTAS: tuple[int, ...] = (1, 2, 3, 7, 11, 13, 17, 23)

# 기관·문서명 후보를 찾는 패턴. 숫자가 없는 답변의 폴백 축이다.
#
# **뒤에 조사나 경계가 와야 한다.** 안 걸면 `기록관리` 안의 `기록관` 을 잡아
# **`한국기록평가원리`** 라는 없는 낱말을 만든다 — 그럴듯한 오답이 아니라 잡음이고,
# 채점자가 그것을 무시하는 것은 민감도 실패가 아니다(2026-08-28 실측: 놓친 8건 중 2건).
# 한국어는 명사에 조사가 붙으므로 `(?![가-힣])` 로는 못 막는다(`기록관이` 까지 막힌다)
_NAME_PATTERN = (
    r"[가-힣]{0,8}(?:기록관|위원회|교육청|연구원|진흥원|평가원|행정안전부|교육부|중앙부처|기관)"
    r"(?=[은는이가을를에의로와과도만및으,.·\s\[\]()]|$)"
)

# 갈아 끼울 가짜 기관명. **근거에 없는 것을 고른다** — 있으면 라벨이 거짓이 된다
_FAKE_NAMES: tuple[str, ...] = ("한국기록평가원", "중앙문서관리청", "국가정보표준원")

# 법령·문서 이름을 감싸는 낫표. **기관명 축만으로는 이 자리가 안 잡힌다** — 실측(2026-08-28,
# 사람 43건)으로 1단을 못 만든 9건 중 `「사립학교법」` 처럼 낫표 안에만 고유명사가 있는
# 답변이 있었다. 축을 「기관·문서명」으로 정해 놓고 기관만 구현하면 분모가 이유 없이 작아진다
_DOCUMENT_PATTERN = r"[「『][^」』\n]{2,40}[」』]"

# 갈아 끼울 가짜 법령·문서명. 낫표는 그대로 두고 안쪽만 바꾼다
_FAKE_DOCUMENTS: tuple[str, ...] = ("전자기록물표준화법", "기록물보존특례법", "공공데이터관리법")

# 2단에서 덧붙일 문장들. 날짜·기관·금액·기한을 한 문장에 담아 **근거로는 확인할 길이 없게**
# 만든다. 인용 번호는 일부러 안 붙인다 — 붙이면 인용 축을 건드리는데 이 실행은 지지만 잰다
EXTRA_SENTENCES: tuple[str, ...] = (
    "이 절차는 2029년 3월 1일부터 한국기록평가원의 사전 승인을 받아 시행됩니다.",
    "위반 시 과태료 750만원이 부과되며 처리 기한은 영업일 기준 17일입니다.",
    "관련 문의는 중앙문서관리청 제2심의관실이 전담하여 처리합니다.",
)


# 변조 하나의 채점 결과. **변조된 답변 원문을 함께 든다** — 채점 규칙이 바뀌어도 이 행만으로
# 유료 재실행 없이 다시 먹일 수 있어야 한다 (`answer_eval` 의 «원자료를 싣는다» 와 같은 규약)
@dataclass
class ProbeRow:
    query_id: str
    query: str
    source_doc: str
    answerable: bool
    # 두 단 중 어느 쪽인가. `LEVELS` 의 키다
    level: str
    # 무엇을 무엇으로 바꿨나. 사람이 «못 잡았다» 를 읽을 때 제일 먼저 보는 칸이다
    mutation: str
    # 본판 답변 원문
    answer: str
    # 변조된 답변 원문
    mutated: str
    chunk_ids: list[str] = field(default_factory=list)
    # supported | unsupported | failed. **`unsupported` 가 정답이다**
    verdict: str = "failed"
    reasoning: str = ""
    unsupported_sentences: list[str] = field(default_factory=list)


# 근거 전체를 공백 지운 한 덩어리로 만든다. 변조값이 여기 있으면 라벨이 거짓이 된다
def evidence_haystack(chunks: list[RetrievedChunk]) -> str:
    return squash("\n".join(chunk.text for chunk in chunks))


# 저장소가 준 청크를 **리포트에 실린 순서로** 되돌린다. qdrant 의 반환 순서는 보장이 없는데
# 그 순서가 근거 번호를 만든다 (모듈 docstring 참고). 못 찾은 id 는 그냥 빠지므로 부르는 쪽이
# 길이를 대조해야 한다 — 조용히 짧아지면 채점자가 본판보다 적은 근거를 본다
def reorder_chunks(stored: list[StoredChunk], chunk_ids: list[str]) -> list[RetrievedChunk]:
    by_id = {chunk.chunk_id: chunk for chunk in stored}
    restored: list[RetrievedChunk] = []
    for chunk_id in chunk_ids:
        found = by_id.get(chunk_id)
        if found is None:
            continue
        restored.append(
            RetrievedChunk(
                chunk_id=found.chunk_id,
                document_id=found.document_id,
                text=found.text,
                metadata=dict(found.metadata),
            )
        )
    return restored


# 인용 번호가 놓인 자리들. **여기 든 숫자는 사실이 아니라 근거 번호라 변조 대상이 아니다** —
# 바꾸면 재는 것이 지지가 아니라 인용 정합성이 된다
def _citation_spans(text: str) -> list[tuple[int, int]]:
    return [match.span() for match in CITATION_PATTERN.finditer(text)]


# 그 자리가 인용 번호와 겹치나
def _overlaps(span: tuple[int, int], spans: list[tuple[int, int]]) -> bool:
    return any(span[0] < end and start < span[1] for start, end in spans)


# 목록 머리 번호인가. **거기를 바꾸면 아무 사실도 안 바뀐다** — `1. 기관 내 …` 를
# `18. 기관 내 …` 로 만들어도 답변이 주장하는 내용은 그대로라, 채점자가 «뒷받침된다» 고
# 말하는 것이 맞다. 그런 변조는 known-bad 가 아니라 잡음이고 민감도를 실력보다 낮게 만든다
# (2026-08-28 실측: 놓친 8건 중 3건이 이것이었다).
#
# **맨 숫자 패턴에만 먹인다** — 날짜(`2026. 3. 1.`)도 «숫자 + 마침표 + 공백» 으로 끝나서,
# 전체에 걸면 멀쩡한 날짜 변조가 통째로 막힌다
def _is_list_marker(answer: str, span: tuple[int, int]) -> bool:
    if not re.match(r"[.)]\s", answer[span[1] : span[1] + 2]):
        return False
    return span[0] == 0 or answer[span[0] - 1] in " \t\n\r"


# 숫자 하나를 다른 숫자로 바꾼 문자열. 자릿수 구분 쉼표는 버린다(다시 넣으면 근거 대조가
# 표기법을 타는데, 재려는 것은 표기법이 아니다)
def _shift_digits(phrase: str, delta: int) -> str | None:
    match = re.search(r"\d[\d,]*", phrase)
    if match is None:
        return None
    raw = match.group(0).replace(",", "")
    if not raw.isdigit():
        return None
    shifted = str(int(raw) + delta)
    return phrase[: match.start()] + shifted + phrase[match.end() :]


# 1단 — 값 하나를 근거에 없는 값으로 바꾼다. 못 만들면 `None`.
#
# **«근거에 없는 값인가» 를 반드시 확인한다** — 우연히 근거에 있으면 *"정답은 unsupported"*
# 라는 라벨 자체가 거짓이 되고, 그러면 채점자가 맞혔는데 틀린 것으로 세어진다.
# 숫자 축이 다 막히면 기관·문서명 축으로 물러선다
def mutate_value(answer: str, chunks: list[RetrievedChunk]) -> tuple[str, str] | None:
    haystack = evidence_haystack(chunks)
    citations = _citation_spans(answer)

    for pattern in _VALUE_PATTERNS:
        for match in re.finditer(pattern, answer):
            if _overlaps(match.span(), citations):
                continue
            if pattern == _BARE_NUMBER and _is_list_marker(answer, match.span()):
                continue
            original = match.group(0)
            for delta in _DELTAS:
                replacement = _shift_digits(original, delta)
                if replacement is None or replacement == original:
                    continue
                if squash(replacement) in haystack:
                    continue
                mutated = answer[: match.start()] + replacement + answer[match.end() :]
                return mutated, f"{original} -> {replacement}"

    # **낫표 축이 기관명보다 먼저다** — 확인 단위가 이름 통째라, 우연히 근거에 있어 라벨이
    # 거짓이 될 위험이 맨 이름보다 작다(숫자 축을 그 순서로 둔 것과 같은 기준이다)
    for match in re.finditer(_DOCUMENT_PATTERN, answer):
        original = match.group(0)
        opener, closer = original[0], original[-1]
        for fake in _FAKE_DOCUMENTS:
            replacement = f"{opener}{fake}{closer}"
            if replacement == original or squash(replacement) in haystack:
                continue
            mutated = answer[: match.start()] + replacement + answer[match.end() :]
            return mutated, f"{original} -> {replacement}"

    for match in re.finditer(_NAME_PATTERN, answer):
        original = match.group(0)
        for fake in _FAKE_NAMES:
            if fake == original or squash(fake) in haystack:
                continue
            mutated = answer[: match.start()] + fake + answer[match.end() :]
            return mutated, f"{original} -> {fake}"

    return None


# 어느 템플릿부터 시도할지. **`hash()` 를 쓰면 안 된다** — 파이썬이 문자열 해시를 프로세스마다
# 무작위로 섞어(PYTHONHASHSEED) 같은 리포트가 실행마다 다른 변조를 내는데 에러가 안 난다
def _template_offset(seed: str) -> int:
    return sum(ord(ch) for ch in seed) % len(EXTRA_SENTENCES)


# 2단 — 근거에 없는 문장을 한 줄 덧붙인다. 템플릿이라 거의 늘 만들어지지만, 셋 다 근거에
# 있으면 `None` 이다(그 경우도 회계에 찍혀야 «분모가 28이 아니다» 를 사람이 안다).
#
# **`seed` 로 시작 템플릿을 돌린다.** 전건에 같은 문장 하나를 쓰면 28개 판정이 통째로 상관돼
# 게이트가 사실상 **한 모양만** 재는데, 이 게이트가 세우려는 것은 *"이 채점자가 명백한 조작을
# 잡는다"* 라 모양이 하나면 표본이 1이다. 돌리면 어느 모양을 놓쳤는지도 함께 읽힌다
def mutate_sentence(
    answer: str, chunks: list[RetrievedChunk], *, seed: str = ""
) -> tuple[str, str] | None:
    haystack = evidence_haystack(chunks)
    offset = _template_offset(seed)
    for step in range(len(EXTRA_SENTENCES)):
        sentence = EXTRA_SENTENCES[(offset + step) % len(EXTRA_SENTENCES)]
        if squash(sentence) in haystack:
            continue
        return f"{answer.rstrip()} {sentence}", f"+ {sentence}"
    return None


# 분모가 얼마나 작아졌나. **회차를 무효로 만들지 않는다** — 이것을 무효화와 한 목록에 섞으면
# 게이트가 늑대를 부른다. 실제로 첫 유료 실행(2026-08-28)이 2단 28/28 · 실패 0 인 멀쩡한
# 회차인데 *"실력으로 읽지 않는다"* 를 띄웠다. 헛경보를 내는 게이트는 `SKIP_*=1` 과 같은
# 무시하는 습관을 만든다 — 이 저장소가 차단 게이트를 셋으로 안 늘린 것과 같은 이유다
def scope_notes(
    rows: list[ProbeRow],
    *,
    unmutated: dict[str, list[str]],
    missing_evidence: list[str],
) -> list[str]:
    notes: list[str] = []

    for level, korean in (("sentence", "2단"), ("value", "1단")):
        skipped = unmutated.get(level) or []
        if skipped:
            probed = sum(1 for row in rows if row.level == level)
            notes.append(
                f"{korean} 은 {probed}건에서만 성립합니다 — 바꿀 값이 없어 못 만든 질의가 "
                f"{len(skipped)}건입니다 {skipped[:5]}"
            )

    if missing_evidence:
        notes.append(
            f"근거를 못 되살린 질의 {len(missing_evidence)}건 {missing_evidence[:5]} — "
            "컬렉션이 그 실행의 것이 맞는지 봅니다(청크가 사라졌으면 그 질의는 아예 안 재집니다)"
        )
    return notes


# 회계가 맞나. **지표보다 먼저 읽는다** — 빈 리스트일 때만 아래 수치를 실력으로 읽는다.
# 여기 드는 것은 *수치를 못 읽게 만드는 것* 뿐이고, 분모가 작아지는 것은 `scope_notes()` 다
def accounting_problems(
    rows: list[ProbeRow],
    usage: JudgeUsage,
    *,
    missing_evidence: list[str],
) -> list[str]:
    problems: list[str] = []

    got = usage.api_calls + usage.cache_hits
    if got != len(rows):
        problems.append(
            f"판정 회계가 안 맞습니다: 호출 {usage.api_calls} + 캐시 {usage.cache_hits} "
            f"= {got} 인데 만든 변조본은 {len(rows)}개입니다"
        )

    # **변조본이 하나도 없으면 그것은 분모가 작은 것이 아니라 잰 것이 없는 것이다.**
    # 임계값 없이 «컬렉션이 통째로 다르다» 를 잡는 자리이기도 하다 — 그때 전건이 여기로 온다
    if not rows and missing_evidence:
        problems.append(
            f"변조본이 하나도 없습니다 — 근거를 못 되살린 질의가 {len(missing_evidence)}건이라 "
            "리포트와 컬렉션이 같은 실행의 것인지부터 봅니다"
        )

    failed = [row.query_id for row in rows if row.verdict == "failed"]
    if failed:
        problems.append(
            f"판정 실패 {len(failed)}건 {failed[:5]} — «못 잡았다» 가 아니라 «못 쟀다» 입니다"
        )

    # **2단이 게이트다** (모듈 docstring 참고)
    missed = [row.query_id for row in rows if row.level == "sentence" and row.verdict == "supported"]
    if missed:
        problems.append(
            f"2단(근거에 없는 문장 덧붙이기)을 {len(missed)}건에서 놓쳤습니다 {missed[:5]} — "
            "이 채점자로 잰 지지 수치는 못 씁니다"
        )
    return problems


# 채점표. **단마다 따로 낸다** — 합치면 «쉬운 것을 잡았나» 와 «어려운 것을 잡았나» 가
# 한 수에 섞여 무엇이 그 수를 만들었는지 못 읽는다
def summarize(rows: list[ProbeRow]) -> dict[str, Any]:
    def bucket(level: str) -> dict[str, Any]:
        subset = [row for row in rows if row.level == level]
        caught = sum(1 for row in subset if row.verdict == "unsupported")
        judged = sum(1 for row in subset if row.verdict != "failed")
        return {
            "probed": len(subset),
            "judged": judged,
            "caught": caught,
            "missed": sum(1 for row in subset if row.verdict == "supported"),
            "failed": sum(1 for row in subset if row.verdict == "failed"),
            # 분모는 **판정이 난 것만**이다 — 실패를 섞으면 실패율이 민감도로 읽힌다
            "sensitivity": (caught / judged) if judged else None,
        }

    return {level: bucket(level) for level, _ in LEVELS}


# 리포트 하나를 읽어 두 단을 먹인다. 생성도 검색도 안 부른다 — 재료는 전부 리포트에 있다
def probe(
    *,
    report_path: Path,
    judge_name: str = DEFAULT_PROBE_JUDGE_PROFILE,
    cache_enabled: bool = True,
) -> tuple[list[ProbeRow], dict[str, Any]]:
    from rag_pipeline.ingestion.indexing import get_store

    # **«채점자 없음» 은 여기서 합법값이 아니다.** 이 도구가 재는 것이 채점자 자신이라,
    # 꺼진 프로필로 들어오면 민감도 표가 **전건 `failed`** 로 나온다 — 그것은
    # *«죽은 채점자가 만점»* 의 거울상이라 소리 내어 막는다(`DEFAULT_JUDGE_PROFILE` 이
    # 2026-08-29 에 `off` 로 내려가면서 실재하게 된 자리다)
    if not judge_profile(judge_name).get("judge_model"):
        raise SystemExit(
            f"채점자 프로필 '{judge_name}' 에는 모델이 없습니다 — 이 도구는 채점자를 재는 "
            f"자리라 꺼진 프로필로는 돌 수 없습니다 (예: --judge {DEFAULT_PROBE_JUDGE_PROFILE})"
        )

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    source_meta = payload.get("meta") or {}
    label = source_meta.get("label")
    fmt = source_meta.get("format")
    if not label or not fmt:
        raise SystemExit(f"리포트에 label·format 이 없습니다: {report_path}")

    embedding = source_meta.get("embedding_profile") or DEFAULT_EMBEDDING_PROFILE
    config = eval_config(qdrant_path=RESULTS_DIR / f"qdrant-{label}-{fmt}", embedding=embedding)
    judge = SupportJudge(
        JudgeConfig(**judge_profile(judge_name)),
        api_key=config.generation.openai_api_key,
        cache_enabled=cache_enabled,
    )

    rows: list[ProbeRow] = []
    unmutated: dict[str, list[str]] = {level: [] for level, _ in LEVELS}
    missing_evidence: list[str] = []
    abstained: list[str] = []

    store = get_store(config.indexing, config.embedding)
    try:
        print(
            f"리포트 {report_path.name} · 행 {len(payload.get('rows') or [])}건 · "
            f"컬렉션 {store.count()}건 · 채점자 {judge_name}({judge.model})"
        )
        for raw in payload.get("rows") or []:
            query_id = raw.get("query_id", "?")
            chunk_ids = list(raw.get("chunk_ids") or [])
            answer = raw.get("answer") or ""
            if not chunk_ids or not answer.strip():
                abstained.append(query_id)
                continue

            stored = store.fetch_by_ids([point_id_for(chunk_id) for chunk_id in chunk_ids])
            chunks = reorder_chunks(stored, chunk_ids)
            if len(chunks) != len(chunk_ids):
                missing_evidence.append(query_id)
                continue

            # **저장된 `abstained` 를 안 믿는다** (모듈 docstring 참고)
            if is_abstention(answer, chunks):
                abstained.append(query_id)
                continue

            for level, _ in LEVELS:
                if level == "sentence":
                    made = mutate_sentence(answer, chunks, seed=query_id)
                else:
                    made = mutate_value(answer, chunks)
                if made is None:
                    unmutated[level].append(query_id)
                    continue
                mutated, mutation = made
                verdict = judge.judge(raw.get("query", ""), mutated, chunks)
                rows.append(
                    ProbeRow(
                        query_id=query_id,
                        query=raw.get("query", ""),
                        source_doc=raw.get("source_doc", ""),
                        answerable=bool(raw.get("answerable", True)),
                        level=level,
                        mutation=mutation,
                        answer=answer,
                        mutated=mutated,
                        chunk_ids=chunk_ids,
                        verdict=verdict.verdict,
                        reasoning=verdict.reasoning,
                        unsupported_sentences=list(verdict.unsupported_sentences),
                    )
                )
    finally:
        # 안 닫으면 다음 실행이 배타 잠금으로 통째로 죽는다
        store.close()

    meta = {
        "source_report": report_path.name,
        "label": label,
        "format": fmt,
        "embedding_profile": embedding,
        "judge_profile": judge_name,
        "support_prompt_version": source_meta.get("support_prompt_version"),
        # 본판이 어느 조건이었나. 프로브는 그것을 안 바꾸지만 표에 함께 실려야 앞뒤가 맞는다
        "source_rerank_profile": source_meta.get("rerank_profile"),
        "source_rewrite_profile": source_meta.get("rewrite_profile"),
        "source_generation_profile": source_meta.get("generation_profile"),
        # **프로필 이름만으로는 생성 조건이 안 잡힌다** — `SYSTEM_PROMPT` 를 고쳐도 이름은
        # `prod` 그대로다. 옛 리포트에는 이 칸이 없어 `None` 이고 그것이 곧 «그전 버전» 이다
        "source_generation_prompt_version": source_meta.get("generation_prompt_version"),
        "probed_queries": len({row.query_id for row in rows}),
        "abstained": abstained,
        "unmutated": unmutated,
        "missing_evidence": missing_evidence,
        "judge_usage": asdict(judge.usage),
        "judge_warnings": list(judge.warnings),
    }
    return rows, meta


# 회계를 먼저, 게이트를 다음, 관측을 마지막에 찍는다
def _print(rows: list[ProbeRow], meta: dict[str, Any], *, show_wrong: bool) -> None:
    usage = JudgeUsage(**meta["judge_usage"])
    problems = accounting_problems(rows, usage, missing_evidence=meta["missing_evidence"])
    notes = scope_notes(
        rows, unmutated=meta["unmutated"], missing_evidence=meta["missing_evidence"]
    )

    print("\n[회계 게이트]")
    print(
        f"  판정 호출 {usage.api_calls} · 캐시 {usage.cache_hits} · 토큰 {usage.total_tokens} · "
        f"변조본 {len(rows)} · 기권 제외 {len(meta['abstained'])}"
    )
    if problems:
        for line in problems:
            print(f"  x {line}")
        print("  -> 아래 수치를 실력으로 읽지 않는다")
    else:
        print("  o 통과")

    # **분모 주석은 게이트와 따로 찍는다** — 섞으면 멀쩡한 회차가 무효로 보인다
    if notes:
        print("\n[분모]")
        for line in notes:
            print(f"  · {line}")

    summary = summarize(rows)
    for level, korean in LEVELS:
        bucket = summary[level]
        if not bucket["probed"]:
            continue
        rate = bucket["sensitivity"]
        shown = "-" if rate is None else f"{rate:.3f}"
        print(f"\n[{korean}] 변조 {bucket['probed']}건")
        print(
            f"  잡음 {bucket['caught']} · 놓침 {bucket['missed']} · 실패 {bucket['failed']} · "
            f"민감도 {shown}"
        )

    if show_wrong:
        print("\n[놓친 변조]")
        for row in rows:
            if row.verdict != "supported":
                continue
            print(f"  [{row.level}] {row.query_id} ({row.source_doc}) — {row.mutation}")
            print(f"      {row.reasoning}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="답변을 일부러 틀리게 고쳐 채점자가 그것을 미지지로 잡는지 잰다"
    )
    # **경로로 직접 받는다** — 이름 규칙으로 유추하면 리포트 이름이 바뀔 때 옛 실행을 못 연다
    parser.add_argument("--report", required=True, help="answer_eval 이 남긴 리포트 JSON 경로")
    # **기본이 `answer_eval` 과 다르다** — 저쪽은 `off` 가 기본이고 여기는 채점자가 있어야 한다
    parser.add_argument("--judge", default=DEFAULT_PROBE_JUDGE_PROFILE)
    parser.add_argument("--wrong", action="store_true", help="놓친 변조를 하나씩 보여준다")
    parser.add_argument("--json", action="store_true", help="결과를 JSON 으로도 남긴다")
    args = parser.parse_args()

    report_path = Path(args.report)
    if not report_path.exists():
        raise SystemExit(f"리포트가 없습니다: {report_path}")

    rows, meta = probe(report_path=report_path, judge_name=args.judge)
    _print(rows, meta, show_wrong=args.wrong)

    if args.json:
        # **이름에 채점자 프로필이 들어간다** — 안 넣으면 라벨이 같은 두 채점자의 표가 같은
        # 자리를 다퉈 에러 없이 덮인다 (`verdicts.py` 가 13회차에 실제로 잃은 사고다)
        out = RESULTS_DIR / f"{meta['label']}-judgeprobe-{args.judge}-{meta['format']}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {"meta": meta, "summary": summarize(rows), "rows": [asdict(r) for r in rows]}
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n남김: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
