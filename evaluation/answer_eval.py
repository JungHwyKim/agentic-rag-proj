"""답변 품질을 잰다. **`run_eval` 도 `verdicts` 도 못 재는 자[尺]다.**

## 왜 세 번째 도구인가

`run_eval` 은 **순위**를 재고(`hit@k`·`MRR`·`nDCG`), `verdicts` 는 **판정**을 잰다. 둘 다
답변 본문을 한 글자도 안 본다 — 저장소가 *"생성 품질은 못 잰다"* 를 `plan_log/검색-생성.md`
24·26·27·29회차에서 네 번 미결로 넘긴 것이 이 빈자리다.

여기가 재는 것은 셋이고 **셋이 서로를 검사한다.**

| 무엇 | 유료 | 무엇이 없어도 되나 |
|---|---|---|
| **인용 정합성** — 답변이 인용한 `[n]` 청크가 `gold_span` 을 담나 | 0 | 골든 답변 |
| **근거 지지** — 답변 문장이 근거로 뒷받침되나 (LLM) | 판정당 1 | 골든 답변 |
| **음성 대조군** — 같은 답변에 **남의 근거**를 물려 같은 판정 | 판정당 1 | — |

## 이름을 `faithfulness` 로 안 짓는다

29회차가 *"Faithfulness 라 부르지 않고 잰 것의 이름으로 부른다"* 로 닫았다. 리포트 칸도
`citation_groundedness` · `support` · `abstention` · `shuffled_control` 이다. 셋을 한 낱말로
접으면 **무엇이 그 수를 만들었는지**가 사라지고, 그러면 수치가 나빴을 때 자[尺]를 의심할지
파이프라인을 의심할지 못 가른다.

## 음성 대조군이 없으면 이 도구는 자기 자신을 못 믿는다

LLM 판정자는 **죽어도 표가 멀쩡해 보인다.** 저장소가 이미 그 자리를 밟았다 — `verdicts.py`
가 *죽은 판정자 기준선* 을 세워야 했고, 실측으로 K=5 에서 네 판 중 셋이 그보다 낮았다.
여기서는 기준선을 **구성으로** 만든다: 같은 답변에 **다른 문서의 근거**를 물려 물으면 옳은
답이 `unsupported` 하나뿐이다. 그때도 `supported` 가 나오면 **그 회차의 지지 수치는 못 쓴다.**

**이것이 「전부 기권」에서도 사는 유일한 검사다.** 부정 예시 25건이 전부 옳게 기권하면
*지어낸 답을 잡았나* 의 표본이 0이 되는데, 대조군은 기권과 무관하게 판정된 행마다 선다.

## 건너뛰는 자리가 둘이고 **둘 다 정상 경로다**

`generation/citations.py` 와 **같은 규칙을 같은 이유로** 쓴다.

- **근거가 0건일 때** — `generate_answer()` 가 모델을 안 부르고 `NO_EVIDENCE_ANSWER` 를 그대로
  돌려주는 자리다
- **답변이 `NO_EVIDENCE_ANSWER` 일 때** — 근거가 **있어도** 모델이 규칙 2를 따라 그 문장을
  고를 수 있다

**이 둘을 판정에 넣으면 «근거를 못 찾았다»가 전부 미지지로 세어진다** — 그리고 그것은 이
저장소가 정상 경로로 못 박은 답변이다(`schemas/answer.py`). 그래서 판정 대상에서 빼고
**기권율로 따로 센다.** 부정 예시에서는 기권이 곧 정답이라, 그 수가 이 도구의 첫 지표다.

## 실패는 판정이 아니라 **제3의 상태**다

키 없음·호출 실패·응답 손상은 전부 `verdict="failed"` 로 나가고 사유는 `warnings` 에 쌓인다.
*미지지* 로 떨어뜨리면 **죽은 채점자가 부정 예시에서 만점**을 받아 점수가 실력이 아니라
실패율을 잰다(`generation/evaluate.py` · 10회차). 여기는 그 함정이 한 겹 더 나쁘다 —
대조군의 기대값도 `unsupported` 라, 실패를 미지지로 접으면 **대조군까지 통과한 것처럼 보인다.**

## 회계 게이트를 지표보다 **먼저** 읽는다

Cohere 한도 초과가 품질 저하로 읽힌 선례(2026-08-27) 그대로다 — 지표 여덟 개가 전부 그럴듯
하게 나빴고 가른 것은 `rerank_calls` 가 60이 아니라 30이었다는 것 하나였다. 그래서
`_print()` 가 `judge_calls + judge_cache_hits == 판정 대상` 을 먼저 찍고, 어긋나면 지표
위에 경고를 놓는다.

## 원자료를 리포트에 싣는다 — **생성에는 캐시가 없다**

`generate_answer()` 는 `ResultCache` 를 안 거친다. 그래서 다시 돌리면 **돈이 또 들고 답변도
달라진다**(`generation_temperature=0.2`). 재현은 온도를 0으로 내려서가 아니라 **답변 원문·
근거 `chunk_id`·판정 원문을 JSON 에 남겨서** 얻는다 — 30회차가 `retry_diff` 에서 원자료 덕에
유료 재실행 없이 새 규칙을 먹인 그 길이다. 온도를 내리면 재현은 얻지만 **재는 것이 운영이
내는 답변이 아니게 된다.**

## 판정 프롬프트가 여기 사는 이유

제품이 이 프롬프트를 안 부른다. `rag_pipeline/generation/prompts.py` 에 두면
`EVALUATOR_PROMPT_VERSION` 옆에 **죽은 상수**가 하나 생기고, 제품 프롬프트를 고칠 때 이것도
함께 고쳐야 하는 것처럼 보인다. 대신 **근거 블록은 반드시 `build_evidence_block()` 로 만든다**
— *"이 모양의 유일한 출처"* 라, 제 손으로 만들면 *채점자가 본 것과 생성이 본 것이 글자까지
같다* 가 조용히 깨진다.

## 기본 세트가 `verdicts.py` 와 다르다

저쪽은 `qa_set.jsonl`(60건) 이고 여기는 **`qa_set_human.jsonl`(43건)** 이다. 이 도구의 첫
지표가 기권율인데, **기권이 정답인 질의(부정 예시 25건)가 사람 세트에만 있다.** LLM 60건은
전부 답이 실재해 기권율이 정의상 0에 가깝고, 그러면 *채점자가 지어낸 답을 잡나* 를 물을
표본이 아예 안 생긴다.

## 임베딩 프로필은 인자로 안 받는다

`verdicts.py` 와 같다 — 컬렉션 이름에 임베딩 모델이 들어가서, 손으로 넘기면 같은 디렉터리의
**다른 컬렉션**을 열어 0건이 나온다. `require_fresh_store()` 가 소리 내어 막는 것까지 같다.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from evaluation.config import (
    DEFAULT_EMBEDDING_PROFILE,
    DEFAULT_GENERATOR_PROFILE,
    DEFAULT_JUDGE_PROFILE,
    DEFAULT_RERANK_PROFILE,
    DEFAULT_REWRITE_PROFILE,
    eval_config,
    judge_profile,
)
from evaluation.inspect import load_report, require_fresh_store
from evaluation.qa_set import QAItem, load_qa_set
from evaluation.run_eval import RESULTS_DIR
from evaluation.scoring import squash
from rag_pipeline.generation.citations import CITATION_PATTERN, check_citations, cited_numbers
from rag_pipeline.generation.prompts import NO_EVIDENCE_ANSWER, build_evidence_block
from rag_pipeline.ingestion.loader.content.base import ResultCache, content_hash
from rag_pipeline.schemas.retrieval import RetrievedChunk

__all__ = [
    "JUDGE_CACHE_DIR",
    "QA_SET_HUMAN_PATH",
    "SUPPORT_PROMPT_VERSION",
    "AnswerRow",
    "JudgeConfig",
    "JudgeUsage",
    "SupportJudge",
    "SupportVerdict",
    "accounting_problems",
    "build_support_prompt",
    "control_partner",
    "is_abstention",
    "report_name",
    "score_citations",
    "summarize",
]

# 기본 세트. **`verdicts.py` 와 다른 것이 의도다** (모듈 docstring 참고)
QA_SET_HUMAN_PATH = Path(__file__).resolve().parent / "qa_set_human.jsonl"

# 판정 캐시. 제품 설정에 이 축이 없어(제품이 안 부른다) 경로를 여기서 정한다
JUDGE_CACHE_DIR = Path(__file__).resolve().parents[1] / ".cache" / "answer_judge"

# **고치면 반드시 올린다.** 캐시 salt 에 들어가 있어, 안 올리면 옛 응답이 새 프롬프트의
# 답인 척 돌아온다 — 에러가 아니라 수치로만 드러난다 (`표-경로.md` 14회차)
SUPPORT_PROMPT_VERSION = "v1"

SUPPORT_SYSTEM_PROMPT = """당신은 한국어 공문서 답변을 검사하는 채점자입니다.
답을 새로 쓰지 말고, **주어진 답변의 각 문장이 아래 근거로 뒷받침되는지**만 판정하세요.

규칙:
1. 답변의 모든 문장이 근거에 적힌 내용으로 확인되면 "supported" 입니다.
2. 근거에 없는 사실·수치·절차·기관명이 한 문장이라도 있으면 "unsupported" 입니다.
3. 표현이 달라도 뜻이 근거와 같으면 뒷받침된 것으로 봅니다. 반대로 근거에 없는 값을
   덧붙였으면 그럴듯해 보여도 "unsupported" 입니다.
4. 바깥 지식으로 채워서 판정하지 않습니다. 근거에 있는 것만 봅니다.
5. 출력은 설명 없이 JSON 객체 하나입니다:
   {"verdict": "supported" 또는 "unsupported",
    "unsupported_sentences": ["근거에 없는 문장", ...],
    "reasoning": "한국어 한 문장"}"""


# 채점자 설정. **dataclass 인 것이 오타 방어다** — 다른 프로필 축은 pydantic 이
# `extra="ignore"` 라 이름 대조 테스트가 따로 필요한데, 여기는 모르는 키가 오면
# `TypeError` 로 그 자리에서 죽는다. 조용히 안 먹는 실패 모양이 원리적으로 없다
@dataclass(frozen=True)
class JudgeConfig:
    judge_model: str
    # **`None` 은 «보내지 않는다» 는 뜻이다** — gpt-5 계열이 0.0 을 400 으로 거부한다.
    # 1.0 을 대신 보내지 않는 이유는 `EvaluatorConfig` 주석에 있다
    judge_temperature: float | None
    judge_max_tokens: int


# 채점하는 동안 쌓이는 비용. **`EvaluatorUsage` 와 같은 모양인 것이 의도다** —
# 리포트 모양이 갈리면 비용표를 손으로 맞추게 된다
@dataclass
class JudgeUsage:
    api_calls: int = 0
    total_tokens: int = 0
    cache_hits: int = 0
    # 실제로 판정을 얻은 수. 실패는 여기 안 센다 — 그것이 실패 버킷의 분자다
    judged: int = 0


# 지지 판정 하나. **`failed` 가 제3의 상태다** (모듈 docstring 참고)
@dataclass(frozen=True)
class SupportVerdict:
    verdict: str
    unsupported_sentences: list[str] = field(default_factory=list)
    reasoning: str = ""


# 질의 하나의 채점 결과. **원자료를 함께 든다** — 생성에 캐시가 없어 다시 돌리면 돈이 또
# 들고 답변도 달라진다. 채점 규칙이 바뀌어도 이 행만으로 다시 먹일 수 있어야 한다
@dataclass
class AnswerRow:
    query_id: str
    query: str
    source_doc: str
    answerable: bool
    # 생성된 답변 **원문**
    answer: str
    # 그 답변이 본 근거의 신원과 순서. 순서까지 담는 이유는 프롬프트의 번호가 거기서 나오기 때문이다
    chunk_ids: list[str] = field(default_factory=list)
    # 재작성이 걸렸으면 그 질의. 안 걸렸으면 None
    rewritten: str | None = None

    # --- 기권 (판정 대상이 아니다) ---
    abstained: bool = False

    # --- 인용 정합성 (무료·결정적) ---
    # `check_citations()` 가 낸 문제. 없으면 None
    citation_problem: str | None = None
    cited: list[int] = field(default_factory=list)
    # 인용한 청크 중 `gold_span` 을 담은 수. 부정 예시는 정답이 없어 None
    cited_gold_hits: int | None = None
    cited_count: int = 0

    # --- 근거 지지 (유료·LLM) ---
    support: str | None = None
    support_reasoning: str = ""
    unsupported_sentences: list[str] = field(default_factory=list)

    # --- 음성 대조군 (유료·LLM) ---
    # 근거를 빌려온 질의. 대조군을 못 만들었으면 None
    control_from: str | None = None
    control: str | None = None

    warnings: list[str] = field(default_factory=list)


# 판정 대상이 아닌가.
#
# **인용 번호를 걷어내고 비교한다 — 완전 일치로 재면 실측에서 새어 나갔다.** 첫 판(2026-08-28,
# 사람 43건)에서 모델이 `문서에서 찾지 못했습니다. [1][2][3][4][5]` 를 **2건** 냈다.
# `SYSTEM_PROMPT` 규칙 2(*정확히 이렇게만 답합니다*)와 규칙 3(*[1],[2] 형식으로 표시합니다*)이
# 서로 싸우고 모델이 **둘 다** 따른 결과다.
#
# 완전 일치로 두면 그 답변이 판정 대상이 되는데, *"못 찾았습니다"* 는 **어떤 근거로도
# 뒷받침되므로** 음성 대조군이 `supported` 를 낸다 — 즉 **채점자가 멀쩡한데 회차가 통째로
# 무효로 찍힌다.** 실제로 첫 판이 그렇게 찍혔고, 그 둘을 빼니 대조군이 **0/28** 이었다.
#
# **`startswith` 가 아니라 인용 제거 후 완전 일치인 것이 요점이다** —
# `문서에서 찾지 못했습니다. 다만 …` 처럼 말을 잇는 답변은 기권이 아니라 판정 대상이다.
#
# `check_citations()` 는 여전히 완전 일치를 쓰고 **거기서는 그것이 맞다** — 저쪽이 재는 것은
# *번호가 범위 안인가* 라, 인용이 붙은 기권 답변은 번호가 멀쩡하면 경고할 것이 없다
def is_abstention(text: str, chunks: list[RetrievedChunk]) -> bool:
    if not chunks:
        return True
    return CITATION_PATTERN.sub("", text).strip() == NO_EVIDENCE_ANSWER


# 대조군으로 쓸 짝을 고른다. **다른 문서를 먼저 찾는다** — 같은 문서의 근거는 우연히 답변을
# 뒷받침할 수 있어 «구성으로 만든 known-bad» 가 아니게 된다. 없으면 다른 질의로 물러서고,
# 그것도 없으면 None 이다.
#
# **거르는 축은 질의가 아니라 근거다.** 서로 다른 두 질의가 **같은 청크를 물어오는 일이
# 흔한데**(같은 절을 묻는 질문 둘), 그때 «남의 근거» 는 실은 제 근거라 판정이 본판과 같아진다.
# 그러면 대조군이 `supported` 로 답하는 것이 정상인데 게이트는 그것을 **채점자 누출로 읽어**
# 멀쩡한 회차를 못 쓰게 만든다. 질의 id 만 보고 걸렀다가 이 자리를 지나칠 뻔했다 —
# **`step` 이 1부터라 자기 자신은 구조적으로 못 골라, id 검사는 사실상 아무것도 안 막았다**
def control_partner(index: int, rows: list[AnswerRow]) -> AnswerRow | None:
    total = len(rows)
    if total < 2:
        return None
    me = rows[index]
    mine = set(me.chunk_ids)
    fallback: AnswerRow | None = None
    for step in range(1, total):
        other = rows[(index + step) % total]
        # **완전 일치가 아니라 겹침 하나로 막는다.** 41회차가 실측으로 밟았다 — `ai-01` 의
        # 대조군이 `sch-01` 의 근거를 빌렸는데 그 다섯 중 하나가 **답변의 이메일 주소를 담은
        # 바로 그 청크**였다. 채점자는 옳게 «뒷받침된다» 고 했고 게이트만 무효를 외쳤다.
        # 검색은 문서를 안 가려 오므로 «질의의 `source_doc` 이 다르다» 가 근거의 출처를 전혀
        # 보장하지 않는다 — 60건 중 3건이 이 상태였다
        if not other.chunk_ids or mine & set(other.chunk_ids):
            continue
        if other.source_doc != me.source_doc:
            return other
        if fallback is None:
            fallback = other
    return fallback


# 인용 정합성을 잰다. **유료가 0이고 결정적이다** — 같은 (답변, 근거, 라벨)이면 늘 같은 수다.
#
# `check_citations()` 가 답하는 것은 *번호가 범위 안인가* 뿐이라, 여기서 한 겹을 더 본다:
# **그 번호가 가리키는 청크가 정답 문장을 담나.** 대조는 `squash()` 로 한다 — PDF 가 한국어
# 낱말 사이 공백을 잃어서, 공백을 살려 비교하면 내용이 같은데도 0점이 된다(`scoring.py`)
def score_citations(row: AnswerRow, item: QAItem, chunks: list[RetrievedChunk]) -> None:
    trace = check_citations(row.answer, chunks)
    row.citation_problem = trace.problem if trace is not None else None
    row.cited = cited_numbers(row.answer)

    # 범위 안인 번호만 청크를 가리킨다. 밖은 가리킬 것이 없어 분모에서 뺀다
    inside = [n for n in row.cited if 1 <= n <= len(chunks)]
    row.cited_count = len(inside)

    # **부정 예시는 분모가 없다.** 정답이 없어 «인용한 청크가 정답을 담나» 가 성립하지 않는다 —
    # 0으로 적으면 «인용을 다 틀렸다» 로 읽히는데 그것은 이 질의에 대해 물을 수 없는 것이다
    if not item.answerable or not item.gold_spans:
        row.cited_gold_hits = None
        return

    needles = [squash(span) for span in item.gold_spans]
    hits = 0
    for number in inside:
        hay = squash(chunks[number - 1].text)
        if any(needle in hay for needle in needles):
            hits += 1
    row.cited_gold_hits = hits


# 캐시 키. 질의·답변 본문·근거 신원(순서까지)·모델·프롬프트 버전이 전부 들어간다.
# **근거가 순서까지 들어가야 대조군이 자기 칸을 갖는다** — 같은 답변에 다른 근거를 물린 것이
# 대조군이므로, 근거가 키에 없으면 본판과 대조군이 같은 칸을 다퉈 대조가 사라진다
def _cache_key(query: str, answer: str, chunks: list[RetrievedChunk], *, salt: str) -> bytes:
    ids = "\n".join(chunk.chunk_id for chunk in chunks)
    return f"{salt}\n{query}\n{answer}\n{ids}".encode()


# OpenAI 클라이언트를 만든다. 키가 없으면 None.
# **모듈 레벨이어야 한다** — 테스트의 모킹 이음새다 (`generation/evaluate.py` 와 같은 규약)
def _make_client(api_key: str | None) -> Any | None:
    if not api_key:
        return None
    try:
        from openai import OpenAI
    except ImportError:
        return None
    return OpenAI(api_key=api_key)


# 판정용 프롬프트. **근거 블록은 `build_evidence_block()` 이 만든다** (모듈 docstring 참고)
def build_support_prompt(query: str, answer: str, chunks: list[RetrievedChunk]) -> str:
    return (
        f"근거:\n\n{build_evidence_block(chunks)}\n\n---\n\n"
        f"질문: {query}\n\n답변:\n{answer}\n\n"
        "이 답변의 각 문장이 위 근거로 뒷받침됩니까?"
    )


def _parse_json(content: str | None) -> Any:
    if not content:
        return None
    try:
        return json.loads(content)
    except (TypeError, ValueError):
        return None


# 응답이든 캐시 항목이든 **같은 검사를 탄다.** 캐시 쪽만 느슨하면 옛 스키마 항목이 검사 없이
# 되살아나는데, 그건 응답 손상과 구분이 안 되는 자리다 (`generation/evaluate.py` 와 같은 규약)
def _verdict_from_payload(payload: Any) -> SupportVerdict | None:
    if not isinstance(payload, dict):
        return None
    verdict = payload.get("verdict")
    # **모델은 "failed" 를 말할 수 없다** — 그것은 판정이 아니라 판정을 못 했다는 뜻이라
    # 부르는 쪽이 붙인다. 모델이 자기 실패를 선언하게 두면 실패 버킷이 말버릇을 잰다
    if verdict not in ("supported", "unsupported"):
        return None
    raw = payload.get("unsupported_sentences")
    sentences = [s for s in raw if isinstance(s, str)] if isinstance(raw, list) else []
    reasoning = payload.get("reasoning")
    return SupportVerdict(
        verdict=verdict,
        unsupported_sentences=sentences,
        reasoning=reasoning.strip() if isinstance(reasoning, str) else "",
    )


# 답변이 근거로 뒷받침되는지 LLM 한 번으로 판정한다. **예외를 안 던진다**
class SupportJudge:
    # 클라이언트를 여기서 만들지 않는다. `_make_client()` 를 호출 시점에 부르는 것이
    # 테스트의 모킹 이음새다
    def __init__(
        self,
        config: JudgeConfig,
        *,
        api_key: str | None = None,
        cache_dir: Path | None = None,
        cache_enabled: bool = True,
    ) -> None:
        self._config = config
        self._api_key = api_key
        self._usage = JudgeUsage()
        self._warnings: list[str] = []
        self._cache = ResultCache(cache_dir or JUDGE_CACHE_DIR, enabled=cache_enabled)

    # 실패했을 때 그 사유가 쌓인다. 경고가 없으면 "판정이 나빴다"와 "판정을 못 했다"가
    # 구분되지 않는다
    @property
    def warnings(self) -> list[str]:
        return self._warnings

    @property
    def usage(self) -> JudgeUsage:
        return self._usage

    # 리포트와 진행 줄이 읽는다. 사적 필드를 밖에서 들여다보지 않게 열어 둔다
    @property
    def model(self) -> str:
        return self._config.judge_model

    @property
    def _salt(self) -> str:
        return f"support:{self._config.judge_model}:{SUPPORT_PROMPT_VERSION}"

    # 근거가 답변을 뒷받침하는지 판정한다. **실패는 제3의 상태로 나간다**
    def judge(self, query: str, answer: str, chunks: list[RetrievedChunk]) -> SupportVerdict:
        if not chunks or not answer.strip():
            # 판정할 것이 없다. **미지지가 아니라 판정을 못 한 것이다** — 부르는 쪽이
            # 기권을 먼저 걸러야 하고, 여기까지 왔다면 그 계약이 깨진 것이다
            self._warnings.append("근거나 답변이 비어 있어 지지를 판정하지 않았습니다")
            return SupportVerdict(verdict="failed", reasoning="근거나 답변이 비어 있습니다")

        key = content_hash(_cache_key(query, answer, chunks, salt=self._salt))
        restored = _verdict_from_payload(self._cache.get(key))
        if restored is not None:
            self._usage.cache_hits += 1
            return restored

        verdict = self._call_api(query, answer, chunks)
        if verdict is None:
            # 경고는 이미 `_call_api` 가 남겼다. 판정을 지어내지 않는다
            return SupportVerdict(verdict="failed", reasoning="판정을 얻지 못했습니다")

        # **못 쓸 응답은 캐시에 안 넣는다**
        self._cache.set(key, asdict(verdict))
        self._usage.judged += 1
        return verdict

    # 실제로 보낸다. 실패·손상은 전부 None 으로 바꿔 제3의 상태에 맡긴다
    def _call_api(
        self, query: str, answer: str, chunks: list[RetrievedChunk]
    ) -> SupportVerdict | None:
        config = self._config
        client = _make_client(self._api_key)
        if client is None:
            self._warnings.append("OPENAI_API_KEY 가 없어 근거 지지를 판정하지 못했습니다")
            return None

        # **`max_completion_tokens` 는 분기가 없다** — gpt-5 계열이 `max_tokens` 를 400 으로
        # 거부하는데 gpt-4.1-mini 는 새 이름도 받는다(2026-08-27 실측). 갈리는 것은
        # temperature 하나라, **None 이면 인자 자체를 안 싣는다**
        kwargs: dict[str, Any] = {
            "model": config.judge_model,
            "messages": [
                {"role": "system", "content": SUPPORT_SYSTEM_PROMPT},
                {"role": "user", "content": build_support_prompt(query, answer, chunks)},
            ],
            "max_completion_tokens": config.judge_max_tokens,
            "response_format": {"type": "json_object"},
        }
        if config.judge_temperature is not None:
            kwargs["temperature"] = config.judge_temperature

        try:
            response = client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - 실패가 예외가 아니라 제3의 상태다 (docstring)
            self._warnings.append(f"지지 판정 호출에 실패했습니다: {type(exc).__name__}: {exc}")
            return None

        verdict = _verdict_from_payload(_parse_json(self._content_of(response)))
        if verdict is None:
            self._warnings.append("지지 판정 응답의 모양이 달라 판정을 못 얻었습니다")
            return None

        # 카운터는 성공한 뒤에만 올린다 (embedding·리랭커와 같은 규약)
        self._usage.api_calls += 1
        usage = getattr(response, "usage", None)
        tokens = getattr(usage, "total_tokens", None)
        if isinstance(tokens, int):
            self._usage.total_tokens += tokens
        return verdict

    # 응답에서 본문을 꺼낸다. 모양이 틀려도 예외를 안 내고 None 으로 떨어뜨린다
    @staticmethod
    def _content_of(response: Any) -> str | None:
        choices = getattr(response, "choices", None) or []
        if not choices:
            return None
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None)
        return content if isinstance(content, str) else None


# 회계가 맞나. **지표보다 먼저 읽는 것이 이 함수의 존재 이유다** (모듈 docstring 참고).
# 빈 리스트면 통과이고, 그때만 아래 지표를 실력으로 읽는다
def accounting_problems(
    rows: list[AnswerRow], usage: JudgeUsage, *, expected_generations: int
) -> list[str]:
    problems: list[str] = []

    judged = [row for row in rows if not row.abstained]
    controls = [row for row in judged if row.control is not None]
    want = len(judged) + len(controls)
    got = usage.api_calls + usage.cache_hits
    if got != want:
        problems.append(
            f"판정 회계가 안 맞습니다: 호출 {usage.api_calls} + 캐시 {usage.cache_hits} "
            f"= {got} 인데 판정 대상은 {want}(본판 {len(judged)} + 대조군 {len(controls)})입니다"
        )

    generated = sum(1 for row in rows if row.chunk_ids)
    if generated != expected_generations:
        problems.append(
            f"생성 회계가 안 맞습니다: 근거를 받은 질의 {generated} vs 생성 {expected_generations}"
        )

    failed = sum(1 for row in judged if row.support == "failed")
    if failed:
        problems.append(f"지지 판정 실패 {failed}건 — 지표의 분모가 그만큼 줄었습니다")

    # **대조군이 통과하면 그 회차의 지지 수치는 못 쓴다** (모듈 docstring 참고)
    leaked = [row.query_id for row in controls if row.control == "supported"]
    if leaked:
        problems.append(
            f"음성 대조군이 {len(leaked)}건에서 supported 로 답했습니다 {leaked[:5]} — "
            "채점자가 남의 근거로도 «뒷받침된다» 고 말하므로 이 회차의 지지 수치는 못 씁니다"
        )
    return problems


# 채점표. **갈래별로 따로 낸다** — 부정 예시는 기권이 정답이고 긍정은 아니라, 합치면
# 두 정답표가 한 수에 섞여 무엇이 그 수를 만들었는지 못 읽는다
def summarize(rows: list[AnswerRow]) -> dict[str, Any]:
    def bucket(subset: list[AnswerRow]) -> dict[str, Any]:
        judged = [row for row in subset if not row.abstained]
        supported = sum(1 for row in judged if row.support == "supported")
        unsupported = sum(1 for row in judged if row.support == "unsupported")
        failed = sum(1 for row in judged if row.support == "failed")
        cited_rows = [row for row in judged if row.cited_gold_hits is not None]
        cited_total = sum(row.cited_count for row in cited_rows)
        cited_hits = sum(row.cited_gold_hits or 0 for row in cited_rows)
        return {
            "queries": len(subset),
            "abstained": sum(1 for row in subset if row.abstained),
            "judged": len(judged),
            "supported": supported,
            "unsupported": unsupported,
            "failed": failed,
            # 인용 정합성 — 인용한 청크 중 정답을 담은 비율. 분모가 0이면 None
            "cited_chunks": cited_total,
            "cited_gold_hits": cited_hits,
            "citation_groundedness": (cited_hits / cited_total) if cited_total else None,
            # 인용을 아예 안 한 답변. `check_citations()` 의 `missing` 이다
            "citation_missing": sum(1 for row in judged if row.citation_problem == "missing"),
            "citation_out_of_range": sum(
                1 for row in judged if row.citation_problem == "out_of_range"
            ),
        }

    controls = [row for row in rows if row.control is not None]
    return {
        # **부정이 먼저다** — 이 도구의 첫 지표가 «기권이 정답인 질의에서 기권했나» 이고,
        # 그것이 자[尺] 자신을 검사하는 판이다
        "negative": bucket([row for row in rows if not row.answerable]),
        "positive": bucket([row for row in rows if row.answerable]),
        "shuffled_control": {
            "judged": len(controls),
            # **짝을 못 찾아 대조군이 아예 없는 행. 제3의 상태다** — 41회차가 짝 거르기를
            # «완전 일치» 에서 «하나라도 겹치면» 으로 넓히면서 이 자리가 실재하게 됐다.
            # 안 세면 대조군이 줄어든 것이 «supported 0» 으로 보여 **표본 부족이 실력으로
            # 읽힌다** — Cohere 한도 초과가 품질로 읽힌 그 사고와 같은 모양이다.
            # **무효화가 아니라 분모 주석이다**(40회차) — 게이트에 섞으면 헛경보가 된다
            "unpaired": sum(1 for row in rows if not row.abstained and row.control_from is None),
            # **여기가 0이어야 지지 수치를 읽는다**
            "supported": sum(1 for row in controls if row.control == "supported"),
            "unsupported": sum(1 for row in controls if row.control == "unsupported"),
            "failed": sum(1 for row in controls if row.control == "failed"),
        },
    }


# 세트 하나를 한 `top_k` 로 채점한다. 생성은 제품 함수가 하고 이 함수는 재기만 한다
def score_answers(
    *,
    label: str,
    fmt: str,
    top_k: int,
    qa_path: Path | None = None,
    rerank: str = DEFAULT_RERANK_PROFILE,
    rewrite: str = DEFAULT_REWRITE_PROFILE,
    generation: str = DEFAULT_GENERATOR_PROFILE,
    judge_name: str = DEFAULT_JUDGE_PROFILE,
) -> tuple[list[AnswerRow], dict[str, Any]]:
    from rag_pipeline.exceptions import GenerationError
    from rag_pipeline.generation import generate_answer
    from rag_pipeline.ingestion.embedding import get_embedder
    from rag_pipeline.ingestion.indexing import get_store
    from rag_pipeline.retrieval import build_bm25_index, get_reranker, get_rewriter, retrieve

    qa = load_qa_set(qa_path or QA_SET_HUMAN_PATH)
    if not qa.items:
        raise SystemExit(f"세트가 비어 있습니다: {qa.path}")

    report = load_report(label, fmt)
    embedding = report.get("embedding_profile") or DEFAULT_EMBEDDING_PROFILE
    config = eval_config(
        qdrant_path=RESULTS_DIR / f"qdrant-{report['label']}-{fmt}",
        embedding=embedding,
        rerank=rerank,
        rewrite=rewrite,
        generation=generation,
    )
    judge = SupportJudge(
        JudgeConfig(**judge_profile(judge_name)),
        api_key=config.generation.openai_api_key,
    )

    embedder = get_embedder(config.embedding)
    store = get_store(config.indexing, config.embedding)
    reranker = get_reranker(config.rerank)
    rewriter = get_rewriter(config.rewrite)

    rows: list[AnswerRow] = []
    chunks_by_id: dict[str, list[RetrievedChunk]] = {}
    generations = 0
    try:
        total = store.count()
        require_fresh_store(
            total, sum(x["chunks"] for x in report["indexed"]), label=report["label"], fmt=fmt
        )
        bm25 = build_bm25_index(store)
        print(
            f"컬렉션 {total}건 · 세트 {len(qa.items)}건 · top_k {top_k} · "
            f"리랭커 {rerank} · 재작성 {rewrite} · 생성 {generation} · "
            f"채점자 {judge_name}({judge.model})"
        )

        for item in qa.items:
            chunks = retrieve(
                item.query,
                store=store,
                embedder=embedder,
                top_k=top_k,
                bm25=bm25,
                reranker=reranker,
                rewriter=rewriter,
                config=config.retrieval,
            )
            # **`retrieve()` 뒤에 읽어야 값이 있다** — 재작성은 그 안에서 돈다 (`query.py`)
            rewritten = getattr(rewriter, "last_rewritten", None) if rewriter else None

            seen = len(judge.warnings)
            try:
                text = generate_answer(item.query, chunks, config.generation)
                if chunks:
                    generations += 1
            except GenerationError as exc:
                # **생성 실패는 기권이 아니다.** 빈 답변으로 적으면 «못 찾았다» 와 섞인다
                text = ""
                judge.warnings.append(f"{item.query_id}: 생성 실패 — {exc}")

            row = AnswerRow(
                query_id=item.query_id,
                query=item.query,
                source_doc=item.source_doc,
                answerable=item.answerable,
                answer=text,
                chunk_ids=[c.chunk_id for c in chunks],
                rewritten=rewritten,
                abstained=is_abstention(text, chunks) if text else True,
            )
            chunks_by_id[item.query_id] = chunks
            if not row.abstained:
                score_citations(row, item, chunks)
                verdict = judge.judge(item.query, text, chunks)
                row.support = verdict.verdict
                row.support_reasoning = verdict.reasoning
                row.unsupported_sentences = list(verdict.unsupported_sentences)
            row.warnings = list(judge.warnings[seen:])
            rows.append(row)

        # **대조군은 본판이 다 끝난 뒤에 돈다** — 짝을 고르려면 모든 행의 근거가 있어야 한다
        for index, row in enumerate(rows):
            if row.abstained:
                continue
            partner = control_partner(index, rows)
            if partner is None:
                continue
            row.control_from = partner.query_id
            control = judge.judge(row.query, row.answer, chunks_by_id[partner.query_id])
            row.control = control.verdict
    finally:
        # 안 닫으면 다음 실행이 배타 잠금으로 통째로 죽는다
        store.close()

    meta = {
        "label": report["label"],
        "format": fmt,
        "top_k": top_k,
        "qa_set": qa.path.name,
        "embedding_profile": embedding,
        "rerank_profile": rerank,
        "rewrite_profile": rewrite,
        "generation_profile": generation,
        "judge_profile": judge_name,
        # **프로필과 함께 프롬프트 판도 싣는다** — 캐시 salt 가 이미 이 값으로 갈리므로
        # 표도 같이 갈려야 앞뒤가 맞는다 (`verdicts.py` 와 같은 규칙)
        "support_prompt_version": SUPPORT_PROMPT_VERSION,
        "queries": len(rows),
        "generations": generations,
        "judge_usage": asdict(judge.usage),
        "judge_warnings": list(judge.warnings),
        "bm25_warnings": list(bm25.warnings),
        "rerank_warnings": list(getattr(reranker, "warnings", []) or []),
        "rewrite_warnings": list(getattr(rewriter, "warnings", []) or []),
    }
    return rows, meta


# 리포트가 저장될 이름. **실행을 가르는 축이 전부 들어가야 한다** — 채점자만 넣고 재작성을
# 빼 두었더니, 같은 라벨에 `--rewrite fuse` 로 한 번 더 돌리면 본판을 **에러 없이 덮었다**.
# `verdicts.py` 가 13회차에 실제로 잃은 사고(luna v1 이 v3 표를 덮었다)와 같은 모양이다.
#
# **`rerank` 와 `top_k` 는 41회차에 같은 이유로 들어왔다.** 그 판이 같은 라벨·같은 채점자로
# `--top-k` 만 5→10 으로 바꿔 두 짝을 돌리는데, 안 담으면 **뒤 짝이 앞 짝을 에러 없이 덮는다**.
# `--tag k5` 로 손으로 막을 수도 있었지만, 그것을 기각하는 문장이 바로 아래에 이미 있었다.
#
# **`tag` 는 프로필로도 안 갈리는 회차를 위한 것이다** — 같은 조건을 두 번 돌려 온도 잡음을
# 재는 회차가 그렇다(생성에 캐시가 없어 두 실행의 답변이 다르다). 손으로 지키는 규약은
# 잊는 순간 근거가 사라지므로 이름으로 옮긴다
def report_name(
    *, label: str, judge: str, rerank: str, rewrite: str, top_k: int, fmt: str, tag: str = ""
) -> str:
    suffix = f"-{tag}" if tag else ""
    return f"{label}-answers-{judge}-{rerank}-{rewrite}-k{top_k}{suffix}-{fmt}.json"


# 회계를 먼저, 지표를 나중에 찍는다 (모듈 docstring 참고)
def _print(rows: list[AnswerRow], meta: dict[str, Any], *, show_wrong: bool) -> None:
    usage = JudgeUsage(**meta["judge_usage"])
    problems = accounting_problems(rows, usage, expected_generations=meta["generations"])

    print("\n[회계 게이트]")
    print(
        f"  판정 호출 {usage.api_calls} · 캐시 {usage.cache_hits} · 토큰 {usage.total_tokens} · "
        f"생성 {meta['generations']}"
    )
    if problems:
        for line in problems:
            print(f"  ✗ {line}")
        print("  → 아래 지표를 실력으로 읽지 않는다")
    else:
        print("  ✓ 통과")

    summary = summarize(rows)
    for name, korean in (("negative", "부정 예시"), ("positive", "정답 있음")):
        b = summary[name]
        if not b["queries"]:
            continue
        print(f"\n[{korean}] {b['queries']}건")
        print(f"  기권 {b['abstained']} · 판정 {b['judged']}")
        if b["judged"]:
            print(
                f"  지지 {b['supported']} · 미지지 {b['unsupported']} · 실패 {b['failed']}"
            )
            ground = b["citation_groundedness"]
            shown = "—" if ground is None else f"{ground:.3f}"
            print(
                f"  인용 정합성 {shown} ({b['cited_gold_hits']}/{b['cited_chunks']}) · "
                f"인용 없음 {b['citation_missing']} · 범위 밖 {b['citation_out_of_range']}"
            )

    control = summary["shuffled_control"]
    print(f"\n[음성 대조군] 판정 {control['judged']}건 · 짝 없음 {control['unpaired']}건")
    print(
        f"  supported {control['supported']} (0이어야 한다) · "
        f"unsupported {control['unsupported']} · 실패 {control['failed']}"
    )

    if show_wrong:
        print("\n[미지지로 판정된 답변]")
        for row in rows:
            if row.support == "unsupported":
                print(f"  {row.query_id} ({row.source_doc}) — {row.support_reasoning}")
                for sentence in row.unsupported_sentences[:3]:
                    print(f"      · {sentence}")


def main() -> int:
    parser = argparse.ArgumentParser(description="생성된 답변의 인용 정합성과 근거 지지를 잰다")
    parser.add_argument("--label", required=True, help="읽을 실행 라벨 (그 컬렉션을 연다)")
    parser.add_argument("--format", dest="fmt", default="pdf")
    parser.add_argument(
        "--qa-set",
        dest="qa_set",
        default=str(QA_SET_HUMAN_PATH),
        help="기본이 사람 세트다. 기권이 정답인 부정 예시가 거기 있다",
    )
    parser.add_argument("--top-k", dest="top_k", type=int, default=5)
    parser.add_argument("--rerank", default=DEFAULT_RERANK_PROFILE)
    parser.add_argument("--rewrite", default=DEFAULT_REWRITE_PROFILE)
    parser.add_argument("--generation", default=DEFAULT_GENERATOR_PROFILE)
    parser.add_argument("--judge", default=DEFAULT_JUDGE_PROFILE)
    parser.add_argument("--wrong", action="store_true", help="미지지 판정을 하나씩 보여준다")
    parser.add_argument("--json", action="store_true", help="결과를 JSON 으로도 남긴다")
    # 프로필로도 안 갈리는 회차를 가른다 — 같은 조건을 두 번 돌려 온도 잡음을 재는 자리다
    parser.add_argument("--tag", default="", help="리포트 이름에 붙일 꼬리표 (같은 조건 재실행)")
    args = parser.parse_args()

    rows, meta = score_answers(
        label=args.label,
        fmt=args.fmt,
        top_k=args.top_k,
        qa_path=Path(args.qa_set),
        rerank=args.rerank,
        rewrite=args.rewrite,
        generation=args.generation,
        judge_name=args.judge,
    )
    _print(rows, meta, show_wrong=args.wrong)

    if args.json:
        # 이름을 만드는 자리는 `report_name()` 하나다 (거기 주석에 이유가 있다)
        out = RESULTS_DIR / report_name(
            label=meta["label"],
            judge=args.judge,
            rerank=args.rerank,
            rewrite=args.rewrite,
            top_k=args.top_k,
            fmt=args.fmt,
            tag=args.tag,
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {"meta": meta, "summary": summarize(rows), "rows": [asdict(r) for r in rows]}
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n남김: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
