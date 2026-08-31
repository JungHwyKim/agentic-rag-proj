"""답변 품질을 잰다. **`run_eval` 도 `verdicts` 도 못 재는 자[尺]다.**

## 왜 세 번째 도구인가

`run_eval` 은 **순위**를 재고(`hit@k`·`MRR`·`nDCG`), `verdicts` 는 **판정**을 잰다. 둘 다
답변 본문을 한 글자도 안 본다 — 저장소가 *"생성 품질은 못 잰다"* 를 `.claude/wiki/plan_log/검색-생성.md`
24·26·27·29회차에서 네 번 미결로 넘긴 것이 이 빈자리다.

여기가 재는 것은 **다섯**이고, 그중 **아래 셋이 서로를 검사한다**(인용 정합성 · 근거 지지 ·
음성 대조군). 다섯 다 **골든 답변 없이** 선다.

| 무엇 | 유료 | 무엇을 내나 |
|---|---|---|
| **인용 정합성** — 답변이 인용한 `[n]` 청크가 `gold_span` 을 담나 | 0 | 비[比] |
| **근거 지지** — 답변 문장이 근거로 뒷받침되나 (LLM) | 판정당 1 | 판정 |
| **음성 대조군** — 같은 답변에 **남의 근거**를 물려 같은 판정 | 판정당 1 | 판정 |
| **필수 사실** — 담아야 할 값을 담았나 (값=결정적 · 서술=어휘 탐침) | 0 | 비[比] + 탐침 |
| **단정 금지** — 미리 적어 둔 오류를 냈나 | 0 | **후보 목록** |

**마지막 줄만 수치를 안 낸다 — 그것이 그 칸의 계약이다.** 앞 넷은 *답변이 근거에 충실한가*
쪽이고 단정 금지는 ***근거에 없는 것을 덧붙였나*** 라 축이 반대인데, 그것을 어휘로 재려
했더니 실측에서 **문 넷 중 둘이 부정문**이었다 — 전제가 틀린 질문을 바로잡은 답변이 금지어를
쓰기 때문이다(구조적인 이유는 `score_must_not_claim` 참고). 그래서 어휘 대조는 **후보를
모으는 데까지만** 쓰고 단정이냐 부정이냐는 사람이 읽는다. **비율을 내면 그 순간
«오류율» 로 읽히고, 제일 좋은 답변이 분자에 앉는다.**

## 언제 켜나 — **기본은 꺼짐이고, 그것이 2026-08-29 의 결정이다**

`--judge` 기본값이 `off` 다. 지지 판정(②)이 **다섯 판 연속 포화**라 조건을 견주는 판에서
아무것도 안 가르는데 호출은 **판정 대상의 2배**(본판 + 대조군)가 나가기 때문이다 —
사람 43건 **28/28** · LLM 60건 네 판 **240/240** · 생성모델 짝 **16/16** · **58/59** 이고,
그 58/59 의 한 건마저 **채점자의 거짓 음성**이었다(근거 전수 확인). 실제로 갈린 것은 **0건**.

**켜는 자리는 셋이고, 전부 «답변 본문이 실제로 달라지는» 축이다.**

- 생성 모델을 바꿨을 때 (`--generation`)
- **생성 프롬프트를 고쳤을 때** — `GENERATION_PROMPT_VERSION` 이 그 판이고 meta 에 실린다.
  `generation_profile` 은 프롬프트를 고쳐도 `prod` 그대로라 이름만으로는 안 잡힌다
- `generation_top_k` 를 바꿨을 때

**읽는 법은 통과선이 아니라 관측값이다.** «전건 `supported`» 를 통과선으로 박고 싶어지는데,
박았으면 위 거짓 음성이 **실패로 기록됐을 것**이다. 그래서 `_print()` 는 미지지 수를 회계
게이트와 **분리된 줄**로 찍고 *«근거를 열어 확인한 뒤에 기록한다»* 를 덧붙인다 —
40회차가 못 박은 «무효화 vs 분모 주석» 구분의 이쪽 편이다.

**그 방향은 `judge_probe` 가 원리적으로 못 잰다.** 그쪽은 답변을 **변조해** «틀린 것을
잡나» 를 재지 «멀쩡한 것을 틀렸다 하나» 를 안 잰다. 재려면 정상 답변을 사람이 읽어야 한다.

**끈 회차가 «멀쩡해 보이면» 안 된다.** `EVAL_JUDGES` 주석이 꺼짐 프로필을 오래 막아 둔
이유가 그것이고(*"안 돌면 전건 실패인데 표는 멀쩡해 보인다"*), 여기서 둘로 갚는다 —
`summarize(judge_on=False)` 가 지지 칸을 `0` 이 아니라 **`None`** 으로 내고,
`accounting_problems(judge_on=False)` 가 **게이트를 뒤집어** *정말 호출이 0이었나* 를 묻는다.

**되돌리는 것은 `DEFAULT_JUDGE_PROFILE` 한 줄이다.** 다시 볼 조건: 이 축이 실제로 무언가를
가르기 시작할 때.

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
import os
import re
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
from rag_pipeline.cost_log import CostOutcome, append_cost_log, split_tokens
from rag_pipeline.generation.citations import CITATION_PATTERN, check_citations, cited_numbers
from rag_pipeline.generation.prompts import (
    GENERATION_PROMPT_VERSION,
    NO_EVIDENCE_ANSWER,
    build_evidence_block,
)
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
    "backfill_baseline",
    "build_support_prompt",
    "control_partner",
    "get_judge",
    "is_abstention",
    "report_name",
    "score_citations",
    "score_must",
    "score_must_not_claim",
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
    # **`None` 이 «채점자 없음» 이다** — `EVAL_JUDGES["off"]` 가 그 값을 담고
    # `get_judge()` 가 그것을 읽어 `None` 을 돌려준다. 리랭커·재작성기·Evaluator 가
    # *모델 이름이 비면 꺼진다* 를 쓰는 것과 같은 규약이다
    judge_model: str | None
    # **`None` 은 «보내지 않는다» 는 뜻이다** — gpt-5 계열이 0.0 을 400 으로 거부한다.
    # 1.0 을 대신 보내지 않는 이유는 `EvaluatorConfig` 주석에 있다
    judge_temperature: float | None
    judge_max_tokens: int


# 채점하는 동안 쌓이는 비용. **`EvaluatorUsage` 와 같은 모양인 것이 의도다** —
# 리포트 모양이 갈리면 비용표를 손으로 맞추게 된다
@dataclass
class JudgeUsage:
    api_calls: int = 0
    # **입력과 출력을 가른다** — 단가가 서로 달라 합계 하나로는 금액을 원리적으로 못 낸다.
    # `EvaluatorUsage`·`RerankUsage`·`RewriteUsage`·`EmbeddingUsage` 넷이 같은 날 같은
    # 모양으로 바뀌었다(위 주석의 "같은 모양" 약속이 여기까지다)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_hits: int = 0
    # 실제로 판정을 얻은 수. 실패는 여기 안 센다 — 그것이 실패 버킷의 분자다
    judged: int = 0

    # 합계는 **파생이지 필드가 아니다.** 나란한 필드로 두면 두 번째 진실이 되어 조용히
    # 어긋난다. 프로퍼티라 `_print()` 의 회계 줄이 한 글자도 안 바뀐다
    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


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
    # 근거 k 개 중 `gold_span` 을 담은 청크 수 — **인용 정합성의 대조군 분자다.**
    # 무작위로 `cited_count` 개를 인용했다면 기대 적중이 `cited_count * G / k` 다.
    # **`k` 를 따로 안 싣는다** — `len(chunk_ids)` 가 이미 그 수라 실으면 두 번째 진실이 생긴다.
    # 부정 예시는 `cited_gold_hits` 와 **같은 규칙으로** None (아래 `score_citations` 주석)
    evidence_gold_chunks: int | None = None

    # --- 필수 사실 (무료·결정적 + 어휘 탐침) ---
    # **라벨이 없으면 전부 None 이다** — 0 으로 적으면 «다 틀렸다» 로 읽히는데
    # 그것은 «아직 라벨을 안 달았다» 와 전혀 다른 뜻이다(`cited_gold_hits` 와 같은 규약).
    # `must_values` 는 값 문자열이 답변에 있나로 **결정적**이고,
    # `must_point_tokens_*` 는 어휘 겹침이라 **점수가 아니라 탐침이다**(`score_must` 참고)
    must_values_total: int | None = None
    must_values_hit: int | None = None
    must_point_tokens_total: int | None = None
    must_point_tokens_hit: int | None = None

    # --- 단정하면 안 되는 것 (무료 · **후보만 뽑는다**) ---
    # **비율을 안 낸다.** 이 칸이 내놓는 것은 «사람이 읽을 문장 목록» 이고 수치가 아니다 —
    # 어휘 대조가 부정문과 단정을 못 가르기 때문이다(`score_must_not_claim` 참고).
    # 라벨이 없으면 둘 다 `None`(위 필수 사실과 같은 규약)
    must_not_claim_total: int | None = None
    must_not_claim_candidates: list[dict[str, str]] | None = None

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
        # **완전 일치가 아니라 겹침 하나로 막는다.** 43회차가 실측으로 밟았다 — `ai-01` 의
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
    # 0으로 적으면 «인용을 다 틀렸다» 로 읽히는데 그것은 이 질의에 대해 물을 수 없는 것이다.
    # **대조군 칸도 같은 규칙으로 `None` 이다** — 근거에 정답이 몇 개인가 역시 못 묻는 것이고,
    # 0으로 적으면 «근거에 정답이 하나도 없었다» 라는 **다른 뜻**이 된다
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

    # **대조군 분자. 인용과 같은 `needles`·같은 `squash()` 로 센다** — 다른 경로로 세면
    # 관측과 기대값이 서로 다른 자[尺]를 써 비[比]가 성립하지 않는다. 이것이 답하는 것은
    # «근거에 애초에 정답이 몇 개 들어 있었나» 이고, 그래서 **인용 안 한 청크도 세어진다**
    row.evidence_gold_chunks = sum(
        1 for chunk in chunks if any(needle in squash(chunk.text) for needle in needles)
    )


# 어절 끝에 붙는 조사·어미. **긴 것부터 벗긴다** — `에서` 를 `에` 로 먼저 자르면 `서` 가 남는다.
# 열거하는 방식은 `judge_probe` 가 낱말 경계를 한국어에서 다룬 것과 같다 — `(?![가-힣])` 로는
# 못 막는다(명사에 조사가 붙어 `기록관이` 까지 막힌다)
_PARTICLES = (
    "으로서", "으로써", "에서는", "에게서", "이라고", "으로는", "에서도", "라고는",
    "에서", "에게", "으로", "라고", "부터", "까지", "처럼", "보다", "한테", "이나",
    "와의", "과의", "의", "은", "는", "이", "가", "을", "를", "에", "로", "와", "과",
    "도", "만", "나", "며", "고", "요",
)


# 어절에서 조사를 벗겨 2글자 이상 토막만 남긴다. **어미 활용은 안 건드린다** —
# `지정해야` 와 `지정한다` 를 잇는 규칙은 이 저장소에 근거가 없다
def _content_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for word in text.split():
        stem = "".join(ch for ch in word if ch.isalnum())
        for particle in _PARTICLES:
            if len(stem) > len(particle) + 1 and stem.endswith(particle):
                stem = stem[: -len(particle)]
                break
        if len(stem) >= 2:
            tokens.append(stem)
    return tokens


# 날짜 표기를 한 모양으로 모은다. `squash()` 뒤에 먹인다(공백이 이미 없다).
#
# **이것은 임계값이 아니라 표기 변환이다** — `2020.10.1.` 과 `2020년 10월 1일` 은 **같은 날**이라
# 고를 여지가 없다(인용 범위 검사가 *"의미의 경계이지 관측값이 아니다"* 로 간 것과 같다).
# **넣은 이유는 실측이다** — 안 넣었더니 값 6개 중 1개(`hsih-07` 의 `2020.10.1.`)가 두 판
# **모두** 거짓 음성이었다. 두 답변 다 `2020년 10월 1일` 로 그 날짜를 정확히 말한다.
#
# **단위 변형(`206개교` ↔ `206개`)은 일부러 안 한다** — 단위는 열거가 안 끝나고
# «어디까지 같은 값으로 볼까» 가 곧 임계값이 된다. 날짜와 성질이 다르다.
# 연·월만 적은 표기(`2020년 10월`)도 안 다룬다 — 이 세트에 없어 근거가 없다
def _normalize_dates(text: str) -> str:
    def canon(match: re.Match[str]) -> str:
        year, month, day = (int(g) for g in match.groups())
        return f"\x1f{year}-{month}-{day}\x1f"

    text = re.sub(r"(\d{4})\.(\d{1,2})\.(\d{1,2})\.?", canon, text)
    return re.sub(r"(\d{4})년(\d{1,2})월(\d{1,2})일", canon, text)


# 필수 사실을 잰다. **유료가 0이고, 두 축의 성질이 다르다.**
#
# `must_values` 는 값·날짜·법령 인용 식별자라 **답변에 그 문자열이 있나** 로 결정적이다.
# 대조는 `squash()` 로 한다 — 인용 정합성과 같은 이유이고, 덤으로 `2020.10.1.` 과
# `2020. 10. 1.` 이 같아진다. **표기 정규화는 안 한다** — `2020년 10월 1일` 로 쓴 답변은
# 미검출이고, 정규화를 넣으면 «어디까지 같은 표기로 볼까» 라는 임계값이 생긴다
# (`.claude/wiki/plan_log/평가-세트.md` 14회차 미결).
#
# **`must_point_tokens_*` 는 점수가 아니라 탐침이다.** 서술을 어휘로 재는 것이라 양쪽으로
# 틀린다 — 낱말이 다 있어도 뜻이 다를 수 있고, 바꿔 쓴 답변은 낱말이 없어도 사실을 담는다.
# **그래서 상한도 하한도 아니다.** 재려는 것은 «이 자[尺]가 조건 사이에서 갈리나» 하나이고,
# 갈리면 그때 LLM 판정을 붙일지 정한다(52회차의 «무료 탐침이 가격을 먼저 셌다» 와 같은 순서).
#
# 라벨이 없으면 **네 칸이 전부 `None`** 이다 — 0 은 «다 틀렸다» 라는 다른 뜻이다.
def score_must(row: AnswerRow, item: QAItem) -> None:
    if not item.must_values and not item.must_points:
        return

    hay = squash(row.answer)
    dated = _normalize_dates(hay)
    row.must_values_total = len(item.must_values)
    row.must_values_hit = sum(
        1 for value in item.must_values if _normalize_dates(squash(value)) in dated
    )

    # **토막에는 날짜 정규화를 안 먹인다** — 서술은 날짜를 통째로 담는 일이 없고,
    # 먹이면 `\x1f` 울타리가 부분 문자열 검사를 깨뜨린다
    tokens = [token for point in item.must_points for token in _content_tokens(point)]
    row.must_point_tokens_total = len(tokens)
    row.must_point_tokens_hit = sum(1 for token in tokens if squash(token) in hay)


# 답변을 문장으로 쪼갠다. **판정에 안 쓰이고 화면에만 쓰인다** — 사람이 읽을 조각을 자르는
# 것뿐이라 경계가 조금 틀려도 아무것도 안 바뀐다. 그래서 임계값이 아니다
def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [part.strip() for part in parts if part.strip()]


# 단정하면 안 되는 것의 **후보**를 뽑는다. **점수를 안 낸다 — 이것이 이 함수의 계약이다.**
#
# 실측(2026-08-29, 저장된 사람 세트 답변 두 판)이 문자열 검사를 기각했다: 금지어가 나온 문
# 넷 중 **둘이 부정문**이었고(*"매년…문서에 없습니다"* · *"의무는 명시되어 있지 않습니다"*),
# 그 둘은 전제가 틀린 질문을 **바로잡은 제일 좋은 답변**이다. 세면 좋은 답변을 벌준다.
#
# **그렇게 되는 것이 우연이 아니라 구조다** — 이 칸은 *질문이 틀린 전제를 깔았을 때* 생기므로
# 금지어가 곧 질의의 낱말이다(다섯 중 셋이 그랬다). 답변이 그 전제를 다루려면 그 낱말을 쓴다.
#
# 그래서 어휘 대조는 **재현율 쪽으로만** 쓴다 — 낱말 하나만 겹쳐도 후보로 올리고, 단정이냐
# 부정이냐는 사람이 읽어 가른다. 오인용 탐침(2026-08-29)이 *"후보 0건은 «오류가 없다» 가
# 아니라 «어휘상 어긋난 자리가 없다»"* 로 간 그 자리와 같은 신분이다.
#
# **부정어 창으로 자동 판정하는 안은 기각했다** — 한국어 부정 표지는 열거가 안 끝나
# «어디까지가 부정문인가» 가 곧 임계값이 되고(단위 정규화를 기각한 그 모양이다), 지금 표본은
# 문 넷뿐이라 그 임계값을 검증할 데이터가 없다. **LLM 판정도 아니다** — ② 지지 판정이 다섯 판
# 연속 포화로 강등된 바로 그 길이라, 갈리는지 모르는 채 호출부터 사게 된다
def score_must_not_claim(row: AnswerRow, item: QAItem) -> None:
    if not item.must_not_claim:
        return

    row.must_not_claim_total = len(item.must_not_claim)
    sentences = _split_sentences(row.answer)
    found: list[dict[str, str]] = []
    for entry in item.must_not_claim:
        tokens = _content_tokens(entry)
        # **문장을 바깥 고리로 도는 것이 요점이다** — 낱말을 바깥에 두면 한 문장이 낱말 수만큼
        # 되풀이돼, 사람이 읽을 목록에 같은 문장이 여러 줄로 뜬다
        for sentence in sentences:
            hay = squash(sentence)
            # **걸린 낱말을 전부 싣는다 — 첫 것만 실었더니 실측에서 신호가 가려졌다.**
            # `hai-05` 의 「설명 대상을 국민으로」는 «설명» 이 답변의 거의 모든 문장에 걸리는데,
            # 정작 가르는 낱말은 «국민» 이다. 첫 것만 싣자 두 판의 목록이 «설명» 으로 똑같아져
            # **읽는 사람이 어느 문장을 봐야 하는지 못 보았다.** 화면용이라 붙여서 싣는다
            hit = " ".join(t for t in tokens if squash(t) in hay)
            if hit:
                found.append({"entry": entry, "tokens": hit, "sentence": sentence})
    # **걸린 문장이 하나도 없어도 빈 목록이지 `None` 이 아니다** — `None` 은 «라벨이 없다»
    # 이고 빈 목록은 «봤는데 후보가 없다» 라, 접으면 리포트가 둘을 못 가른다
    row.must_not_claim_candidates = found


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
            # **아낀 것도 남긴다.** 이 판정은 대상 하나에 호출이 **2회**(본판 + 음성
            # 대조군)라 캐시가 아끼는 양이 다른 갈래의 두 배다 (모듈 docstring)
            self._log_cost("cache_hit", items=len(chunks))
            return restored

        verdict = self._call_api(query, answer, chunks)
        if verdict is None:
            # 경고는 이미 `_call_api` 가 남겼다. 판정을 지어내지 않는다.
            # **실패도 한 줄 남긴다** — `_print()` 의 회계 게이트가 리포트 안에서만 도는데,
            # 로그에 남으면 그 검사를 리포트 밖에서도 할 수 있다
            self._log_cost(
                "failed",
                items=len(chunks),
                detail=self._warnings[-1] if self._warnings else None,
            )
            return SupportVerdict(verdict="failed", reasoning="판정을 얻지 못했습니다")

        # **못 쓸 응답은 캐시에 안 넣는다**
        self._cache.set(key, asdict(verdict))
        self._usage.judged += 1
        return verdict

    # 비용 로그 한 줄. **`stage="judge"` 로 `evaluate` 와 가른다** — 둘 다 판정이지만
    # 저쪽은 *근거가 충분한가*(질의당 1회)이고 이쪽은 *답변이 근거에 실렸는가*(대상당
    # **2회** — 본판 + 음성 대조군)라, 합치면 「판정이 왜 이렇게 비싸지」의 답이 사라진다.
    #
    # **이 파일이 `rag_pipeline` 밖인데도 같은 로그를 쓴다.** 비용은 계층이 아니라 돈의
    # 문제라, 평가 하네스가 쓴 돈도 같은 파일에 모여야 «이번 주에 얼마» 가 성립한다
    def _log_cost(
        self,
        outcome: CostOutcome,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        items: int = 0,
        detail: str | None = None,
    ) -> None:
        append_cost_log(
            stage="judge",
            provider="openai",
            model=self._config.judge_model,
            outcome=outcome,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            items=items,
            detail=detail,
        )

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
        prompt_tokens, completion_tokens = split_tokens(response)
        self._usage.input_tokens += prompt_tokens
        self._usage.output_tokens += completion_tokens
        self._log_cost(
            "call",
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
            items=len(chunks),
        )
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


# 채점자를 만든다. **이름이 비면 `None` 이고, 그것이 이 축의 유일한 스위치다**
# (`get_reranker()`·`get_rewriter()`·`get_evaluator()` 와 같은 모양).
#
# **불리언 인자를 따로 두지 않는다** — `AGENT_MAX_RETRIES` 가 *"불리언을 따로 두면
# `enabled=true` 인데 `0` 인 조합이 에러 없이 생긴다"* 로 고른 그 자리와 같다
def get_judge(
    config: JudgeConfig,
    *,
    api_key: str | None = None,
    cache_dir: Path | None = None,
    cache_enabled: bool = True,
) -> SupportJudge | None:
    if not config.judge_model:
        return None
    return SupportJudge(
        config, api_key=api_key, cache_dir=cache_dir, cache_enabled=cache_enabled
    )


# 회계가 맞나. **지표보다 먼저 읽는 것이 이 함수의 존재 이유다** (모듈 docstring 참고).
# 빈 리스트면 통과이고, 그때만 아래 지표를 실력으로 읽는다
# **`judge_on` 이 게이트의 방향을 가른다** — 켰으면 *«셀 만큼 셌나»* 이고 껐으면
# *«정말 한 번도 안 불렀나»* 다. 기본값을 안 두는 것은 `append_query_log(source=)` 와 같은
# 판단이다: 새 호출부가 빠뜨리면 조용히 틀린 게이트를 타는 대신 `TypeError` 로 터진다
def accounting_problems(
    rows: list[AnswerRow], usage: JudgeUsage, *, expected_generations: int, judge_on: bool
) -> list[str]:
    problems: list[str] = []

    judged = [row for row in rows if not row.abstained]
    controls = [row for row in judged if row.control is not None]
    if judge_on:
        want = len(judged) + len(controls)
        got = usage.api_calls + usage.cache_hits
        if got != want:
            problems.append(
                f"판정 회계가 안 맞습니다: 호출 {usage.api_calls} + 캐시 {usage.cache_hits} "
                f"= {got} 인데 판정 대상은 {want}(본판 {len(judged)} + 대조군 {len(controls)})입니다"
            )
    else:
        # **껐을 때의 게이트는 뒤집힌다.** 「끈 줄 알았는데 절반 돌았다」가 조용히 성립하지
        # 않게 하는 자리이고, `--judge off` 회차의 «유료 0» 은 이 줄이 증명한다.
        # 캐시 히트도 센다 — 무료여도 **판정이 돌았다는 뜻**이라 그 회차는 꺼진 회차가 아니다
        spent = usage.api_calls + usage.cache_hits
        if spent:
            problems.append(
                f"판정을 껐는데 판정이 돌았습니다: 호출 {usage.api_calls} + 캐시 "
                f"{usage.cache_hits} = {spent} (기대 0)"
            )

    generated = sum(1 for row in rows if row.chunk_ids)
    if generated != expected_generations:
        problems.append(
            f"생성 회계가 안 맞습니다: 근거를 받은 질의 {generated} vs 생성 {expected_generations}"
        )

    # **아래 둘은 켰을 때만 뜻이 있다** — 껐으면 `support` 가 전건 `None` 이라 실패도 0이고
    # 대조군도 없다. 조건 없이 두면 «검사가 돌았는데 깨끗하다» 와 «검사가 없었다» 가 섞인다
    if judge_on:
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


# 인용 정합성의 **대조군 둘**을 만든다 — `(무작위 기대 적중, 최대 달성 적중, 그 분모)`.
#
# **왜 있나.** 인용 정합성 하나만으로는 그 수가 좋은지 나쁜지 말할 수 없다(34회차부터의
# 미결이었다). 답변이 근거 k 개 중 c 개를 인용했고 그 k 개 중 G 개가 정답을 담았다면,
# **아무렇게나 골라도** `c·G/k` 는 맞는다. 그 선 위인지 아래인지가 «생성이 근거를 고르나» 다.
#
# **최대(`min(c, G)`)를 함께 내는 이유**는 1.0 이 도달 가능한 값이 아니어서다 — 정답 청크가
# 하나뿐인데 답변이 셋을 인용하면 그 답변의 상한이 이미 1/3 이다. 최대를 안 보이면
# 0.47 이 «절반도 못 맞힌다» 로 읽힌다.
#
# **유료 0 · 결정적이다.** 같은 (답변, 근거, 라벨)이면 늘 같은 수라 `judge_on` 을 안 탄다.
def _citation_baseline(rows: list[AnswerRow]) -> tuple[float, int, int]:
    chance = 0.0
    ceiling = 0
    basis = 0
    for row in rows:
        k = len(row.chunk_ids)
        gold = row.evidence_gold_chunks
        # **못 세는 행은 분모에서도 뺀다** — 여기서 조용히 넘기고 분모는 전체로 두면
        # 기대값만 낮아져 «생성이 잘 고른다» 가 공짜로 나온다. 부르는 쪽이 분모를 대조한다
        if not k or gold is None:
            continue
        chance += row.cited_count * gold / k
        ceiling += min(row.cited_count, gold)
        basis += row.cited_count
    return chance, ceiling, basis


# 필수 사실 블록. **라벨이 없으면 통째로 `None` 이다** — `judge_on=False` 가 지지 칸을
# `None` 으로 내는 것과 같은 규약이고, `0` 으로 내면 «전건 미달» 과 «라벨이 없다» 가 안 갈린다.
#
# **`given_evidence` 가 이 블록의 요점이다.** 근거에 정답 청크가 안 왔으면 답변이 그 사실을
# 담을 방법이 없어, 조건 없이 세면 «생성이 담았나» 가 아니라 **«검색이 물어왔나 × 생성이
# 담았나»** 가 된다. 그 교락은 `.claude/wiki/plan_log/평가-세트.md` 14회차가 별표1 내용을 라벨에서
# 뺀 것과 **같은 미끄러짐**이라, 여기서는 빼는 대신 조건부 칸을 따로 낸다.
#
# **비[比]는 행 평균이 아니라 합의 비다** — 질의마다 필수 사실 수가 달라(1~2개) 행 평균을
# 내면 사실이 하나인 질의가 둘인 질의와 같은 무게를 갖는다
def _must_block(rows: list[AnswerRow]) -> dict[str, Any] | None:
    labeled = [row for row in rows if row.must_values_total is not None]
    if not labeled:
        return None

    def ratio(subset: list[AnswerRow], hit: str, total: str) -> tuple[int, int, float | None]:
        got = sum(getattr(row, hit) or 0 for row in subset)
        out_of = sum(getattr(row, total) or 0 for row in subset)
        return got, out_of, (got / out_of) if out_of else None

    # 근거에 정답 청크가 실제로 온 행. `evidence_gold_chunks` 는 인용 대조군의 분자와 같은 값이다
    with_gold = [row for row in labeled if (row.evidence_gold_chunks or 0) > 0]
    v_hit, v_total, v_recall = ratio(labeled, "must_values_hit", "must_values_total")
    t_hit, t_total, t_cover = ratio(labeled, "must_point_tokens_hit", "must_point_tokens_total")
    gv_hit, gv_total, gv_recall = ratio(with_gold, "must_values_hit", "must_values_total")
    gt_hit, gt_total, gt_cover = ratio(
        with_gold, "must_point_tokens_hit", "must_point_tokens_total"
    )
    return {
        "labeled": len(labeled),
        "values_hit": v_hit,
        "values_total": v_total,
        "values_recall": v_recall,
        "point_tokens_hit": t_hit,
        "point_tokens_total": t_total,
        "points_token_coverage": t_cover,
        "evidence_had_gold": len(with_gold),
        "values_hit_given_evidence": gv_hit,
        "values_total_given_evidence": gv_total,
        "values_recall_given_evidence": gv_recall,
        "point_tokens_hit_given_evidence": gt_hit,
        "point_tokens_total_given_evidence": gt_total,
        "points_token_coverage_given_evidence": gt_cover,
    }


# 단정 금지 후보를 모은다. **비[比]가 한 칸도 없는 것이 이 블록의 계약이다** — 후보에는
# 부정문이 섞여 있어(`score_must_not_claim` 참고) 어떤 분수를 내도 «오류율» 로 읽힌다.
#
# **라벨은 있는데 후보가 0인 질의를 따로 싣는다** — 그것이 «답변이 안 건드렸다» 인지
# «어휘로 못 잡는 종류» 인지는 사람만 아는데, 안 실으면 그 질의가 목록에서 아예 사라져
# 조용해진다(`hbas-01` 의 «구체적 진단 도구 이름» 이 실제로 그 모양이다).
#
# **파생 수를 안 담는다** — 후보 수·후보가 난 질의 수는 `candidates` 에서 바로 세어지므로
# 칸으로 두면 두 번째 진실이 된다(`JudgeUsage.total_tokens` 가 프로퍼티인 것과 같은 잣대)
def _must_not_claim_block(rows: list[AnswerRow]) -> dict[str, Any] | None:
    labeled = [row for row in rows if row.must_not_claim_total is not None]
    if not labeled:
        return None

    candidates: list[dict[str, str]] = []
    for row in labeled:
        for hit in row.must_not_claim_candidates or []:
            candidates.append({"query_id": row.query_id, **hit})
    return {
        "labeled": len(labeled),
        "entries": sum(row.must_not_claim_total or 0 for row in labeled),
        "candidates": candidates,
        "no_candidate_queries": [
            row.query_id for row in labeled if not row.must_not_claim_candidates
        ],
    }


# 채점표. **갈래별로 따로 낸다** — 부정 예시는 기권이 정답이고 긍정은 아니라, 합치면
# 두 정답표가 한 수에 섞여 무엇이 그 수를 만들었는지 못 읽는다.
#
# **`judge_on=False` 면 지지 칸이 `0` 이 아니라 `None` 이다.** `0` 으로 내면 «전건 미지지» 와
# «안 돌렸다» 가 안 갈리는데, 그것이 바로 `EVAL_JUDGES` 주석이 꺼짐 프로필을 오래 막아 둔
# 이유(*"안 돌면 전건 실패인데 표는 멀쩡해 보인다"*)다 — 그 우려를 여기서 갚는다.
# **기본값을 안 둔다**: 새 호출부가 빠뜨리면 조용히 «켜짐» 으로 세는 대신 `TypeError` 다
def summarize(rows: list[AnswerRow], *, judge_on: bool) -> dict[str, Any]:
    def bucket(subset: list[AnswerRow]) -> dict[str, Any]:
        judged = [row for row in subset if not row.abstained]
        supported = sum(1 for row in judged if row.support == "supported") if judge_on else None
        unsupported = (
            sum(1 for row in judged if row.support == "unsupported") if judge_on else None
        )
        failed = sum(1 for row in judged if row.support == "failed") if judge_on else None
        cited_rows = [row for row in judged if row.cited_gold_hits is not None]
        cited_total = sum(row.cited_count for row in cited_rows)
        cited_hits = sum(row.cited_gold_hits or 0 for row in cited_rows)
        chance, ceiling, basis = _citation_baseline(cited_rows)
        # **분모가 다르면 둘 다 `None` 이다** — 대조군 칸이 없는 옛 리포트가 섞이면 분자만
        # 작아져 «무작위가 낮다 = 생성이 잘 고른다» 라는 **틀린 결론이 그럴듯하게** 나온다.
        # 못 재는 것을 못 잰다고 내는 자리이고, `judge_on=False` 가 지지 칸을 `None` 로
        # 내는 것과 같은 규약이다
        usable = basis == cited_total and cited_total > 0
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
            # **대조군 둘. 무료·결정적이라 `judge_on` 을 안 탄다** (지지 칸과 갈리는 자리다)
            "citation_groundedness_chance": (chance / basis) if usable else None,
            "citation_groundedness_ceiling": (ceiling / basis) if usable else None,
            # **조건 둘을 견줄 때 읽는 수다** — 관측값은 `c`(인용 개수)에 지배되어
            # (`top_k` 5→10 에서 0.466→0.433 인데 최대 대비는 0.941→0.982) 조건 간 비교에
            # 못 쓴다. 이쪽은 `c >= G` 인 행에서 `min(c,G) == G` 라 **분모가 인용 개수와
            # 무관**하다(7판 329/330). 뜻은 «근거에 들어온 정답 청크 중 답변이 실제로
            # 인용한 비율» 이고 7판 합이 0.953 이다.
            #
            # **분모가 `ΣG` 가 아니라 `Σmin(c,G)` 인 것이 결정이다** — `_ceiling` 과 글자
            # 그대로 같은 분자라야 「관측/최대」라는 이름이 참이 되고, 이미 잠근 불변식
            # `ceiling >= 관측` 이 그대로 이 칸의 상한(≤1)을 보증한다. 감수 사항은 `c < G` 인
            # 행(7판 330 중 1)에서 `Σmin < ΣG` 라 위 뜻에서 살짝 벗어나는 것이다 —
            # **정답 span 이 여럿인 세트에서는 다시 봐야 한다**(지금 세트는 질의당 하나다).
            #
            # **그래도 통과선이 아니다 — 두 수가 서로 반대 방향으로 속는다.** 관측은 인용을
            # **덜** 달면 오르고(53회차: 0.474 → 0.876), 이쪽은 근거를 **전부** 인용하면
            # `hits == G == min(c,G)` 라 **1.0** 이 된다. 그래서 화면이 둘을 같이 찍는다
            #
            # **`None` 조건이 기존 대조군 둘과 같아야 한다** — 갈리면 셋 중 하나만 나오는
            # 표가 생겨 «어느 것이 못 잰 것인가» 가 안 읽힌다. `ceiling == 0`(근거에 정답이
            # 한 번도 안 왔다)은 분모가 없는 것이라 여기서만 한 겹 더 막는다
            "cited_gold_ceiling": ceiling if usable else None,
            "citation_groundedness_vs_ceiling": (
                (cited_hits / ceiling) if usable and ceiling else None
            ),
            # 인용을 아예 안 한 답변. `check_citations()` 의 `missing` 이다
            "citation_missing": sum(1 for row in judged if row.citation_problem == "missing"),
            "citation_out_of_range": sum(
                1 for row in judged if row.citation_problem == "out_of_range"
            ),
            "must": _must_block(judged),
            # **무료·결정적이라 `judge_on` 을 안 탄다** (인용 정합성·필수 사실과 같은 자리다)
            "must_not_claim": _must_not_claim_block(judged),
        }

    controls = [row for row in rows if row.control is not None]
    # **대조군 블록도 통째로 `None` 이다** — 그 검사는 «채점자가 살아 있나» 를 묻는 것이라,
    # 채점자가 없으면 «0건 누출» 이 아니라 **«물은 적이 없다»** 가 맞다
    control_block: dict[str, Any] | None = None
    if judge_on:
        control_block = {
            "judged": len(controls),
            # **짝을 못 찾아 대조군이 아예 없는 행. 제3의 상태다** — 43회차가 짝 거르기를
            # «완전 일치» 에서 «하나라도 겹치면» 으로 넓히면서 이 자리가 실재하게 됐다.
            # 안 세면 대조군이 줄어든 것이 «supported 0» 으로 보여 **표본 부족이 실력으로
            # 읽힌다** — Cohere 한도 초과가 품질로 읽힌 그 사고와 같은 모양이다.
            # **무효화가 아니라 분모 주석이다**(40회차) — 게이트에 섞으면 헛경보가 된다
            "unpaired": sum(1 for row in rows if not row.abstained and row.control_from is None),
            # **여기가 0이어야 지지 수치를 읽는다**
            "supported": sum(1 for row in controls if row.control == "supported"),
            "unsupported": sum(1 for row in controls if row.control == "unsupported"),
            "failed": sum(1 for row in controls if row.control == "failed"),
        }
    return {
        # **부정이 먼저다** — 이 도구의 첫 지표가 «기권이 정답인 질의에서 기권했나» 이고,
        # 그것이 자[尺] 자신을 검사하는 판이다
        "negative": bucket([row for row in rows if not row.answerable]),
        "positive": bucket([row for row in rows if row.answerable]),
        "shuffled_control": control_block,
    }


# 저장된 리포트에 대조군 칸을 **소급해** 붙인다. **외부 호출 0 · 모델 적재 0.**
#
# 재료는 리포트가 이미 든 원자료(답변 원문과 근거 `chunk_id` 목록)와 그 실행의 컬렉션뿐이다.
# 다시 생성하지 않는 것이 요점이다 — 생성에는 캐시가 없고 온도가 0.2 라 **답변 자체가 달라진다**
# (모듈 docstring 「원자료를 리포트에 싣는다」). 임베더도 안 만든다: 청크 **텍스트만** 읽는다.
#
# **자기 검증이 이 함수의 핵심이다.** 되살린 근거로 인용 네 칸을 다시 계산해 저장된 값과
# 대조한다 — 지도가 한 자리라도 어긋나면 `evidence_gold_chunks` 가 조용히 작아지고, 그러면
# 무작위 기대값이 낮게 나와 *"생성이 근거를 잘 고른다"* 라는 **틀린 결론이 그럴듯하게** 나온다.
# 어긋나면 **아무것도 안 쓴다.** 52회차의 무료 탐침이 전건 히트로 자기를 검증한 그 자리다
def backfill_baseline(path: Path, *, write: bool) -> bool:
    from rag_pipeline.ingestion.indexing import get_store

    payload = json.loads(path.read_text(encoding="utf-8"))
    meta = payload["meta"]
    label, fmt = meta["label"], meta["format"]
    run = load_report(label, fmt)
    config = eval_config(
        qdrant_path=RESULTS_DIR / f"qdrant-{label}-{fmt}",
        embedding=meta.get("embedding_profile") or DEFAULT_EMBEDDING_PROFILE,
    )
    store = get_store(config.indexing, config.embedding)
    try:
        # **컬렉션이 그 실행의 것이 맞는지 먼저 막는다** — 청킹이 바뀌었으면 같은 `chunk_id` 가
        # 다른 글을 담아 계산이 조용히 거짓이 된다
        require_fresh_store(
            store.count(), sum(x["chunks"] for x in run["indexed"]), label=label, fmt=fmt
        )
        texts = {chunk.chunk_id: chunk.text for chunk in store.iter_chunks()}
    finally:
        # **연 쪽이 닫는다** — qdrant 로컬 파일 모드는 디렉터리에 배타 잠금을 건다
        store.close()

    qa = load_qa_set(QA_SET_HUMAN_PATH.parent / meta["qa_set"])
    items = {item.query_id: item for item in qa.items}
    rows = [AnswerRow(**raw) for raw in payload["rows"]]

    unknown: list[str] = []
    missing: list[str] = []
    mismatched: list[str] = []
    abstained = 0
    recomputed = 0
    for raw, row in zip(payload["rows"], rows, strict=True):
        item = items.get(row.query_id)
        if item is None:
            unknown.append(row.query_id)
            continue
        # **기권 행은 건너뛴다 — `score_answers()` 가 건너뛰는 그 자리와 같아야 한다.**
        # 안 갈랐다가 자기 검증에 걸렸다: 기권 행은 인용 칸이 계산된 적이 없어 `None` 인데
        # 다시 돌리면 `0` 이 되고, 그러면 «인용을 다 틀렸다» 가 없던 데서 생긴다
        if row.abstained:
            abstained += 1
            continue
        gaps = [cid for cid in row.chunk_ids if cid not in texts]
        if gaps:
            missing.append(f"{row.query_id} — {gaps[0]}")
            continue
        chunks = [
            RetrievedChunk(chunk_id=cid, document_id="", text=texts[cid]) for cid in row.chunk_ids
        ]
        before = (raw["citation_problem"], raw["cited"], raw["cited_count"], raw["cited_gold_hits"])
        score_citations(row, item, chunks)
        # **소급도 함께 한다** — 저장된 답변 원문만 있으면 되는 계산이라 생성이 0원이다.
        # 자기 검증 대상은 아니다(저장된 값이 아예 없어 견줄 것이 없다)
        score_must(row, item)
        score_must_not_claim(row, item)
        after = (row.citation_problem, row.cited, row.cited_count, row.cited_gold_hits)
        # **네 칸을 다 견준다** — 전부 같은 함수·같은 근거에서 나와야 하는 값이라,
        # 하나라도 어긋나면 되살린 근거가 그때의 근거가 아니라는 뜻이다
        recomputed += 1
        if before != after:
            mismatched.append(f"{row.query_id} — {before} -> {after}")

    print(
        f"{path.name}: 행 {len(rows)} · 기권 {abstained} · "
        f"재계산 {recomputed} · 일치 {recomputed - len(mismatched)}"
    )
    for tag, bad in (
        ("세트에 없는 질의", unknown),
        ("컬렉션에 없는 근거", missing),
        ("재계산 불일치", mismatched),
    ):
        if bad:
            print(f"  ! {tag} {len(bad)}건 — {bad[:3]}")
    if unknown or missing or mismatched:
        print("  → 안 씁니다. 이 리포트의 대조군은 못 만듭니다")
        return False

    summary = summarize(rows, judge_on=meta.get("judge_profile", "off") != "off")
    # **옛 칸이 그대로인지도 본다** — 대조군을 붙이면서 이미 산 수치가 움직이면 그것은
    # 「더하기」가 아니라 「고쳐 쓰기」다. 움직인 칸이 있으면 역시 안 쓴다
    moved = _summary_drift(payload.get("summary") or {}, summary)
    if moved:
        print(f"  ! 옛 요약 칸이 {len(moved)}개 움직였습니다 — {moved[:3]}")
        print("  → 안 씁니다")
        return False

    for raw, row in zip(payload["rows"], rows, strict=True):
        # **키를 더하기만 한다** — 유료로 산 원자료다
        raw["evidence_gold_chunks"] = row.evidence_gold_chunks
        # **필수 사실은 행까지 싣는다** — 요약만 넣으면 그 수의 근거가 파일에 없어,
        # 저장된 행으로 다시 요약하면 `must` 가 `None` 이 된다. 요약과 행이 어긋난 채
        # 그럴듯해 보이는 것이 이 함수의 자기 검증이 원래 잡던 그 모양이다
        raw["must_values_hit"] = row.must_values_hit
        raw["must_values_total"] = row.must_values_total
        raw["must_point_tokens_hit"] = row.must_point_tokens_hit
        raw["must_point_tokens_total"] = row.must_point_tokens_total
        # **단정 금지도 행까지 싣는다** — 바로 위와 같은 이유이고, 이쪽은 요약이 아예
        # 행의 후보 목록을 모아 만든 것이라 안 실으면 저장된 파일에서 되살릴 방법이 없다
        raw["must_not_claim_total"] = row.must_not_claim_total
        raw["must_not_claim_candidates"] = row.must_not_claim_candidates
    payload["summary"] = summary

    block = summary["positive"]
    ground, chance, ceiling = (
        block["citation_groundedness"],
        block["citation_groundedness_chance"],
        block["citation_groundedness_ceiling"],
    )
    if ground is None or chance is None:
        print("  인용 정합성 — 분모 0 (인용한 답변이 없다)")
    else:
        # **최대 대비까지 찍는다** — 백필이 쓴 값을 바로 읽는 자리라, 여기만 빠지면 7판을
        # 소급하고도 조건 비교에 쓸 수를 못 본다
        vs = block["citation_groundedness_vs_ceiling"]
        vs_shown = "—" if vs is None else f"{vs:.3f}"
        print(
            f"  인용 정합성 {ground:.3f} · 무작위 {chance:.3f} · 최대 {ceiling:.3f} · "
            f"최대 대비 {vs_shown}"
        )
    # **필수 사실도 여기서 찍는다** — 소급이 계산한 값을 바로 읽는 자리라,
    # 빠지면 무료로 소급하고도 그 수를 못 본다(바로 위 대조군과 같은 이유)
    _print_must(block["must"])
    _print_must_not_claim(block["must_not_claim"])
    if write:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  남김: {path}")
    else:
        print("  (찍기만 했습니다. 리포트에 넣으려면 --write)")
    return True


# 필수 사실 블록을 찍는다. **`None` 이면 «라벨이 없다» 이지 «0점» 이 아니다.**
#
# **어휘 겹침은 «탐침» 이라고 화면에 못 박는다** — 숫자가 3자리로 나오면 지표로 읽히는데,
# 이것은 상한도 하한도 아니라 «이 자[尺]가 갈리나» 를 보려고 낸 수다(`score_must` 참고)
def _print_must(block: dict[str, Any] | None) -> None:
    if block is None:
        print("    필수 사실 — 라벨 없음")
        return
    values = block["values_recall"]
    shown = "—" if values is None else f"{values:.3f}"
    line = (
        f"    필수 사실 {block['labeled']}건 · "
        f"값 {shown} ({block['values_hit']}/{block['values_total']})"
    )
    cover = block["points_token_coverage"]
    if cover is not None:
        line += f" · 어휘 겹침 {cover:.3f} (탐침 · 통과선 아님)"
    print(line)
    # **근거가 정답을 물어온 행만 따로 낸다** — 안 물어왔으면 담을 방법이 없어,
    # 조건 없는 수는 검색과 생성이 섞인다
    given = block["values_recall_given_evidence"]
    if block["evidence_had_gold"] and given is not None:
        cover = block["points_token_coverage_given_evidence"]
        tail = "" if cover is None else f" · 어휘 겹침 {cover:.3f}"
        print(
            f"      근거에 정답이 온 {block['evidence_had_gold']}건만: 값 {given:.3f} "
            f"({block['values_hit_given_evidence']}/"
            f"{block['values_total_given_evidence']}){tail}"
        )


# 단정 금지 후보를 찍는다. **수치 대신 읽을 목록을 낸다.**
#
# 화면이 «후보» 라고 두 번 말한다 — 한 번은 줄머리에, 한 번은 «오류가 아니다» 로. 여기 뜬
# 문장의 절반쯤은 전제를 바로잡은 **부정문**이고(실측), 그것을 오류로 세면 제일 좋은 답변이
# 벌을 받는다. 반대쪽도 못 박는다: **후보 0 은 «오류가 없다» 가 아니다**
def _print_must_not_claim(block: dict[str, Any] | None) -> None:
    if block is None:
        return
    print(
        f"    단정 금지 {block['labeled']}건({block['entries']}항목) · "
        f"후보 {len(block['candidates'])}문장 — 손으로 읽는다 "
        "(후보는 오류가 아니다 · 후보 0 ≠ 오류 없음)"
    )
    for hit in block["candidates"]:
        sentence = hit["sentence"]
        shown = sentence if len(sentence) <= 90 else sentence[:90] + "…"
        print(f"      {hit['query_id']} [{hit['entry']}] «{hit['tokens']}» {shown}")
    if block["no_candidate_queries"]:
        print(f"      후보 없음: {', '.join(block['no_candidate_queries'])}")


# 두 요약에서 **양쪽에 다 있는** 칸만 견준다. 새로 생긴 칸은 어긋남이 아니다
def _summary_drift(old: dict[str, Any], new: dict[str, Any], *, prefix: str = "") -> list[str]:
    moved: list[str] = []
    for key, was in old.items():
        if key not in new:
            continue
        now = new[key]
        if isinstance(was, dict) and isinstance(now, dict):
            moved += _summary_drift(was, now, prefix=f"{prefix}{key}.")
        elif isinstance(was, float) and isinstance(now, float):
            if abs(was - now) > 1e-9:
                moved.append(f"{prefix}{key}: {was} -> {now}")
        elif was != now:
            moved.append(f"{prefix}{key}: {was} -> {now}")
    return moved


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
    # **`None` 이면 이 회차는 판정을 안 돈다** (모듈 docstring 「언제 켜나」 참고)
    judge = get_judge(
        JudgeConfig(**judge_profile(judge_name)),
        api_key=config.generation.openai_api_key,
    )

    embedder = get_embedder(config.embedding)
    store = get_store(config.indexing, config.embedding)
    reranker = get_reranker(config.rerank)
    rewriter = get_rewriter(config.rewrite)

    rows: list[AnswerRow] = []
    chunks_by_id: dict[str, list[RetrievedChunk]] = {}
    # **채점자와 따로 둔다** — 생성 실패는 판정 축의 사건이 아니라, 채점자를 껐어도 남아야 한다
    generation_warnings: list[str] = []
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
            f"채점자 {judge_name}" + (f"({judge.model})" if judge else " — 안 돌림")
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

            seen = len(judge.warnings) if judge else 0
            try:
                text = generate_answer(item.query, chunks, config.generation)
                if chunks:
                    generations += 1
            except GenerationError as exc:
                # **생성 실패는 기권이 아니다.** 빈 답변으로 적으면 «못 찾았다» 와 섞인다.
                # **경고는 채점자를 껐어도 남겨야 한다** — 생성 실패는 판정 축의 일이 아니다
                text = ""
                generation_warnings.append(f"{item.query_id}: 생성 실패 — {exc}")

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
                # **인용 정합성은 채점자와 무관하게 돈다** — 무료·결정적이라 축이 다르고,
                # 판정을 끈 회차에서 실제로 변별하는 것이 이것 하나다
                score_citations(row, item, chunks)
                # **필수 사실도 채점자와 무관하게 돈다** — 인용 정합성과 같은 자리다
                score_must(row, item)
                score_must_not_claim(row, item)
                if judge is not None:
                    verdict = judge.judge(item.query, text, chunks)
                    row.support = verdict.verdict
                    row.support_reasoning = verdict.reasoning
                    row.unsupported_sentences = list(verdict.unsupported_sentences)
            row.warnings = list(judge.warnings[seen:]) if judge else []
            rows.append(row)

        # **대조군은 본판이 다 끝난 뒤에 돈다** — 짝을 고르려면 모든 행의 근거가 있어야 한다.
        # **채점자를 껐으면 통째로 안 돈다** — 이 검사는 «채점자가 살아 있나» 를 묻는 것이라
        # 채점자가 없으면 물음 자체가 성립하지 않는다(`summarize()` 도 그 칸을 `None` 로 낸다)
        for index, row in enumerate(rows if judge is not None else []):
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
        # **생성 프롬프트 판도 싣는다** — 이 문자열을 고쳐도 `generation_profile` 은 `prod`
        # 그대로라, 안 실으면 두 리포트를 견줄 때 *생성 프롬프트가 달랐다* 를 못 되짚는다.
        # `rewritten` 을 `query_log` 에 실은 이유(*"여기가 유일한 기록"*)와 같은 자리다
        "generation_prompt_version": GENERATION_PROMPT_VERSION,
        "queries": len(rows),
        "generations": generations,
        # **채점자가 없어도 칸은 늘 쓴다** — 빼면 *껐다* 와 *옛 형식이라 칸이 없다* 가
        # 집계에서 안 갈린다(`query_log` 의 «키를 늘 쓴다» 규칙과 같다)
        "judge_usage": asdict(judge.usage if judge else JudgeUsage()),
        "judge_warnings": [*(judge.warnings if judge else []), *generation_warnings],
        "bm25_warnings": list(bm25.warnings),
        "rerank_warnings": list(getattr(reranker, "warnings", []) or []),
        "rewrite_warnings": list(getattr(rewriter, "warnings", []) or []),
    }
    return rows, meta


# 리포트가 저장될 이름. **실행을 가르는 축이 전부 들어가야 한다** — 채점자만 넣고 재작성을
# 빼 두었더니, 같은 라벨에 `--rewrite fuse` 로 한 번 더 돌리면 본판을 **에러 없이 덮었다**.
# `verdicts.py` 가 13회차에 실제로 잃은 사고(luna v1 이 v3 표를 덮었다)와 같은 모양이다.
#
# **`rerank` 와 `top_k` 는 43회차에 같은 이유로 들어왔다.** 그 판이 같은 라벨·같은 채점자로
# `--top-k` 만 5→10 으로 바꿔 두 짝을 돌리는데, 안 담으면 **뒤 짝이 앞 짝을 에러 없이 덮는다**.
# `--tag k5` 로 손으로 막을 수도 있었지만, 그것을 기각하는 문장이 바로 아래에 이미 있었다.
#
# **`qa_set` 도 2026-08-29 에 들어왔다 — 같은 규칙의 두 번째 얼굴이다.** `--qa-set` 은 기본이
# **사람 세트**인데 43회차의 판 넷은 `qa_set.jsonl`(60건)을 명시해 돌린 것이라, 인자를 빠뜨린
# 실행이 **다른 세트의 판을 같은 이름으로 낸다**. 실제로 그 자리를 밟아 43건짜리 판이 나왔고,
# 그것을 살린 뒤 60건으로 다시 돌렸으면 **방금 유료로 산 판을 스스로 덮을 뻔했다.**
# 값은 `Path(qa_set).stem` 이라 새 세트가 생겨도 저절로 갈린다 — 짧은 별명표를 두면
# 그 표에 없는 세트가 조용히 같은 자리를 다툰다
#
# **`generation` 은 2026-08-29 에 같은 이유로 들어왔다.** 축은 44회차에 생겼는데 이 자리가
# 안 따라와, `--generation luna` 로 한 판 돌리면 짝으로 쓸 `prod` 판을 **에러 없이 덮었다**
# — 그 짝이 이 판의 전부인데 덮이면 견줄 것이 사라진다. **기본값일 때 빼는 안은 기각했다**:
# 이름만 보고 *어느 모델이 낸 답변인가* 를 알 수 있어야 하고, `tag` 가 조건부인 것은
# 그것이 **프로필로 안 갈리는 회차**를 위한 것이라 뜻이 다르다. 옛 이름의 판 넷은
# 그대로 두고 새 이름으로만 쌓는다 — 소급해 고치면 43회차 수치의 출처가 흐려진다
#
# **`tag` 는 프로필로도 안 갈리는 회차를 위한 것이다** — 같은 조건을 두 번 돌려 온도 잡음을
# 재는 회차가 그렇다(생성에 캐시가 없어 두 실행의 답변이 다르다). 손으로 지키는 규약은
# 잊는 순간 근거가 사라지므로 이름으로 옮긴다
def report_name(
    *,
    label: str,
    qa_set: str,
    judge: str,
    rerank: str,
    rewrite: str,
    generation: str,
    top_k: int,
    fmt: str,
    tag: str = "",
) -> str:
    suffix = f"-{tag}" if tag else ""
    stem = Path(qa_set).stem
    return f"{label}-answers-{stem}-{judge}-{rerank}-{rewrite}-{generation}-k{top_k}{suffix}-{fmt}.json"


# 회계를 먼저, 지표를 나중에 찍는다 (모듈 docstring 참고)
def _print(rows: list[AnswerRow], meta: dict[str, Any], *, show_wrong: bool) -> None:
    usage = JudgeUsage(**meta["judge_usage"])
    # **리포트만 보고도 어느 게이트를 탔는지 알아야 한다** — 프로필 이름이 그 유일한 근거다
    judge_name = meta.get("judge_profile") or ""
    judge_on = judge_name != "off"
    problems = accounting_problems(
        rows, usage, expected_generations=meta["generations"], judge_on=judge_on
    )

    print("\n[회계 게이트]")
    print(
        f"  채점자 {judge_name or '?'} · 판정 호출 {usage.api_calls} · 캐시 {usage.cache_hits} · "
        f"토큰 {usage.total_tokens} · 생성 {meta['generations']}"
    )
    if problems:
        for line in problems:
            print(f"  ✗ {line}")
        print("  → 아래 지표를 실력으로 읽지 않는다")
    else:
        print("  ✓ 통과")

    summary = summarize(rows, judge_on=judge_on)
    for name, korean in (("negative", "부정 예시"), ("positive", "정답 있음")):
        b = summary[name]
        if not b["queries"]:
            continue
        print(f"\n[{korean}] {b['queries']}건")
        print(f"  기권 {b['abstained']} · 판정 대상 {b['judged']}")
        if b["judged"]:
            if judge_on:
                print(
                    f"  지지 {b['supported']} · 미지지 {b['unsupported']} · 실패 {b['failed']}"
                )
            ground = b["citation_groundedness"]
            shown = "—" if ground is None else f"{ground:.3f}"
            # **관측과 최대 대비를 한 줄에 둔다** — 조건 비교에 읽는 것은 최대 대비인데,
            # 그 수 혼자는 «근거를 전부 인용하면 1.0» 으로 속는다. 관측과 분수(`hits/c`)가
            # 옆에 남아야 그 조작이 보인다(둘은 반대 방향으로 속는다 · `summarize()` 주석)
            vs = b["citation_groundedness_vs_ceiling"]
            ceil_n = b["cited_gold_ceiling"]
            vs_shown = (
                "—"
                if vs is None
                else f"{vs:.3f} ({b['cited_gold_hits']}/{ceil_n})"
            )
            print(
                f"  인용 정합성 {shown} ({b['cited_gold_hits']}/{b['cited_chunks']}) · "
                f"최대 대비 {vs_shown} · "
                f"인용 없음 {b['citation_missing']} · 범위 밖 {b['citation_out_of_range']}"
            )
            # **대조군은 통과선이 아니라 읽는 재료다** — 회계 게이트에 안 섞는다(40회차의
            # «무효화 vs 분모 주석»). 관측이 무작위보다 **낮게** 나올 수도 있고, 그것도 답이다
            chance = b["citation_groundedness_chance"]
            ceiling = b["citation_groundedness_ceiling"]
            if chance is None:
                print("    대조군 — 못 잼 (대조군 칸이 없는 행이 섞였다. --backfill 참고)")
            else:
                print(f"    무작위 {chance:.3f} · 최대 {ceiling:.3f} (통과선 아님)")
            _print_must(b["must"])
            _print_must_not_claim(b["must_not_claim"])

    control = summary["shuffled_control"]
    if control is None:
        # **«0» 이 아니라 «안 돌림» 이다** — 숫자로 내면 *물었는데 깨끗했다* 로 읽힌다
        print("\n[지지 판정] 안 돌림 — 이 회차가 재는 것은 인용 정합성과 기권율이다")
        return

    print(f"\n[음성 대조군] 판정 {control['judged']}건 · 짝 없음 {control['unpaired']}건")
    print(
        f"  supported {control['supported']} (0이어야 한다) · "
        f"unsupported {control['unsupported']} · 실패 {control['failed']}"
    )

    # **회계 게이트와 분리된 줄이다** — 미지지는 회차를 무효화하지 않는다(40회차의 «무효화 vs
    # 분모 주석» 구분). 그리고 **«회귀» 라고 쓰지 않는다**: 2026-08-29 에 실물 거짓 음성이
    # 하나 났고(멀쩡한 인용을 깎았다), 그때 «모델이 근거 없이 답했다» 를 적을 뻔한 것을 가른
    # 것은 근거를 열어 읽은 것 하나였다. `judge_probe` 는 이 방향을 원리적으로 못 잰다
    wrong = sum(1 for row in rows if row.support == "unsupported")
    print(f"\n[지지] 미지지 {wrong}건")
    if wrong:
        print("  → 회귀로 기록하기 전에 근거를 열어 확인한다 (--wrong · 거짓 음성 선례 있음)")

    if show_wrong:
        print("\n[미지지로 판정된 답변]")
        for row in rows:
            if row.support == "unsupported":
                print(f"  {row.query_id} ({row.source_doc}) — {row.support_reasoning}")
                for sentence in row.unsupported_sentences[:3]:
                    print(f"      · {sentence}")


def main() -> int:
    parser = argparse.ArgumentParser(description="생성된 답변의 인용 정합성과 근거 지지를 잰다")
    parser.add_argument("--label", help="읽을 실행 라벨 (그 컬렉션을 연다)")
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
    # **재실행이 아니라 소급이다** — 저장된 리포트에 대조군 칸만 붙인다 (유료 0)
    parser.add_argument(
        "--backfill",
        nargs="+",
        metavar="리포트",
        help="저장된 답변 리포트에 대조군 칸을 소급해 붙인다 (이름 또는 경로 · 유료 0)",
    )
    # **기본이 찍기만 하는 것이다** — 유료로 산 원자료를 건드리는 자리라 한 손잡이를 더 둔다
    parser.add_argument("--write", action="store_true", help="--backfill 을 리포트에 실제로 쓴다")
    args = parser.parse_args()

    if args.backfill:
        ok = True
        for name in args.backfill:
            target = Path(name)
            if not target.exists():
                target = RESULTS_DIR / name
            ok = backfill_baseline(target, write=args.write) and ok
        return 0 if ok else 1

    if not args.label:
        parser.error("--label 이 필요합니다 (또는 --backfill)")

    # 이 채점이 쓴 돈에 그 실행의 이름을 붙인다. **`--backfill` 뒤에 두는 것이 요점이다** —
    # 저쪽은 유료 0이라 붙일 비용이 없다. `setdefault` 라 사람이 준 `COST_LABEL` 이 이긴다.
    # 라벨이 `run_eval` 것과 같으므로 `cost/report.py --label` 이 **검색과 채점의 비용을
    # 한 줄에 모아** 준다 — 지지 판정은 대상당 호출이 2회(본판 + 대조군)라 그 합이 크다
    os.environ.setdefault("COST_LABEL", args.label)

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
            qa_set=meta["qa_set"],
            judge=args.judge,
            rerank=args.rerank,
            rewrite=args.rewrite,
            generation=args.generation,
            top_k=args.top_k,
            fmt=args.fmt,
            tag=args.tag,
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "meta": meta,
            "summary": summarize(rows, judge_on=args.judge != "off"),
            "rows": [asdict(r) for r in rows],
        }
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n남김: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
