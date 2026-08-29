"""유료 API 호출을 한 줄씩 JSONL 로 남긴다. **「이번 실측이 얼마짜리였나」의 유일한 근거다.**

## 왜 단계마다 있는 `usage` 로는 안 되나

`EmbeddingUsage`·`RerankUsage`·`RewriteUsage`·`EvaluatorUsage`·`JudgeUsage` 다섯이 이미
인스턴스에 비용을 쌓는다. 그런데 **읽는 곳이 `evaluation/` 의 리포트 둘뿐이다.** backend 질의
라우트·손으로 돌리는 프로브 스크립트·`-m integration` 은 아무도 안 읽어 비용이 0으로 사라지고,
`generation/answer.py` 는 **함수라 인스턴스 자체가 없어** 카운터를 둘 자리가 없다.

그래서 `verify_log/` 에 `생성 LLM 2회` 처럼 **횟수만** 적힌 줄이 남았다 — 규율이 없어서가
아니라 셀 것이 없어서다. **함수형 단계가 인스턴스 usage 를 못 갖는데 로그는 가질 수 있다는
것이** 호출 지점마다 한 줄을 고른 값어치다.

## 왜 `backend` 가 아니라 여기인가

`backend/app/core/query_log.py` 는 *실사용* 반복률을 재려고 일부러 backend 에 있다 —
`run_eval` 이 한 실행에 60~105건을 쌓으면 그 분모가 오염되기 때문이다. **비용은 반대다.**
실사용이든 평가든 테스트든 **똑같이 나가는 돈**이라, 오히려 전부 세야 한다. 그래서 진입점
아래 최상위 모듈에 산다.

## 무엇을 남기고 무엇을 안 남기나

**남기는 것은 셋이다** — 성공한 유료 호출(`call`) · 캐시로 아낀 것(`cache_hit`) · 실패해서
폴백한 것(`failed`). 성공만 남기면 「얼마 썼나」에만 답하고 **「얼마 아꼈나」와 「한도에
막혔나」를 못 낸다.** 뒤엣것이 특히 중요하다: Cohere 1차 실행에서 60건 중 30건이 429 로
떨어진 것을 지표 여덟 개가 전부 품질 저하로 보이게 했고, 가른 것은 `rerank_calls` 가 30이라는
사실 하나였다(`verify_log/2026-08-27-리랭커-cohere.md`). 저장소가 그 사고에 이미 두 번
답했는데(`run_eval` 의 `usage` 블록 · `answer_eval._print()` 의 회계 게이트) **둘 다 그 리포트
안에서만 산다.** 여기가 그것을 모든 경로로 꺼내는 자리다.

**로컬·무료 갈래는 안 부른다.** 로컬 임베더(KURE)를 남기면 문서 하나 색인에 수백 줄이 쌓여
상한 없는 파일이 폭주한다. 회계 게이트(`호출 + 캐시히트 == 대상`)는 **유료 갈래를 켠 실행**
에서만 쓰므로 유료만 남겨도 성립한다.

**금액 칸이 없다.** 단가가 바뀌면 이미 쌓인 줄이 조용히 거짓이 된다. 관측(토큰·호출)만
남기고 판정(금액)은 `cost/report.py` 가 가격표를 읽어 한다 — `verdict`·`citation` 이 답변을
안 바꾸고 흔적만 싣는 것과 같은 잣대다.

**키를 늘 쓴다.** 안 쓴 칸은 빼지 않고 `null`·0 으로 채운다. 빼면 *"그 단계가 그 값을 안
낸다"* 와 *"옛 형식이라 칸이 없다"* 가 집계에서 안 갈린다(`query_log` 의 `verdict` 규칙 그대로).
**0과 `null` 도 다르다** — `search_units` 는 Cohere 말고는 `null`(그 단위가 없다)이고,
토큰은 0(그 API 가 토큰을 안 쓴다)이다.

## 실패는 삼킨다 — `OSError` 만

쓰기가 실패해도 호출은 그대로 돌아간다. 로그 때문에 유료 호출이 죽으면 관측이 신뢰성을
깎는다. **삼키는 것은 `OSError` 뿐이다**: 그 밖의 예외(직렬화 불가 등)는 이 모듈의 버그라
조용히 넘기면 로그가 영원히 안 쌓이는 것을 아무도 모른다. `query_log` · `ResultCache.set()`
이 이미 같은 규약이다.

## 설정을 왜 호출마다 새로 만드나

`CostLogConfig()` 는 매번 `.env` 를 읽는다. **메모이즈하지 않는 것이 결정이다** —
`get_settings()` 에 `lru_cache` 가 없는 것과 같은 이유이고, 캐시하면 `tests/conftest.py` 의
autouse 핀이 **첫 테스트의 `tmp_path` 에 굳어** 나머지 테스트가 남의 임시 폴더에 쓴다
(에러가 안 난다). 유료 호출은 네트워크 왕복이라 이 읽기가 비용에서 보이지 않는다.

## `run_id` 는 프로세스당 하나다

모듈 적재 때 한 번 만든다. `uvicorn` 은 한 프로세스라 질의 여럿이 같은 `run_id` 를 갖는데,
그때 질의를 가르는 것은 `at` 이다. 실행 하나를 이름으로 부르려면 `COST_LABEL` 을 준다.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import UTC, datetime
from typing import Literal

from rag_pipeline.config import CostLogConfig

__all__ = ["CostOutcome", "CostStage", "append_cost_log", "run_id", "split_tokens"]

# 어느 단계가 돈을 썼나. **`generate` 와 `web_search` 가 여기 있는 것이 요점이다** —
# 둘 다 호출 캐시가 없어(생성은 답변 캐시가 위층에 있고, 웹 검색은 `WebSearchConfig` 가
# 일부러 안 만들었다) **이 로그 말고는 흔적이 원리적으로 남지 않는다**
CostStage = Literal[
    "embedding",
    "rerank",
    "rewrite",
    "evaluate",
    "generate",
    "judge",
    "vision",
    "llamaparse",
    "web_search",
]

# 그 호출이 어떻게 끝났나. 셋을 가르는 이유는 모듈 docstring 참고
CostOutcome = Literal["call", "cache_hit", "failed"]

# 한 줄 쓰기를 직렬화한다. 적재 쪽 무료 OCR 이 워커 8로 도는데 유료는 직렬이라
# (`route_all()`) 실제 경합은 적지만, `ask()` 가 `run_in_threadpool` 을 쓰므로 창구는 하나여야 한다
_LOCK = threading.Lock()

# 이 프로세스의 실행 식별자. **적재 때 한 번 만든다** (모듈 docstring 참고)
_RUN_ID = uuid.uuid4().hex[:12]


# 이 프로세스의 `run_id`. 리포트가 실행을 묶는 축이라 밖에서도 읽을 수 있어야 한다
def run_id() -> str:
    return _RUN_ID


# OpenAI 응답에서 (입력, 출력) 토큰을 꺼낸다. **규칙을 구현체가 갖지 않는다** —
# `rerank_cache_salt_for()` 가 밟은 길 그대로다. 리랭커·재작성·판정·지지판정 넷이 같은
# 다섯 줄을 되풀이하는데, 구현체마다 다시 짜면 **한쪽이 빠뜨려도 아무 에러가 안 난다**.
#
# **모양을 안 믿는다.** `getattr` 로만 읽고 정수가 아니면 0 이다 — 가짜 응답을 쓰는 테스트가
# 많고, 여기서 `AttributeError` 가 새면 폴백이 아니라 500 이 된다(각 구현체의 `_content_of()`
# 가 이미 같은 이유로 그렇게 돼 있다).
#
# **`total_tokens` 로 되돌아가지 않는다.** 입력만 주고 출력을 안 주는 응답이 있으면 그 몫은
# 0 으로 남는 것이 맞다 — 합계를 입력에 몰아넣으면 단가가 싼 쪽으로 금액이 **조용히**
# 기운다. 임베딩만은 예외라 그쪽이 자기 자리에서 따로 처리한다(출력 토큰이 없는 API 다)
def split_tokens(response: object) -> tuple[int, int]:
    usage = getattr(response, "usage", None)
    prompt = getattr(usage, "prompt_tokens", None)
    completion = getattr(usage, "completion_tokens", None)
    return (
        prompt if isinstance(prompt, int) else 0,
        completion if isinstance(completion, int) else 0,
    )


# 유료 호출 하나를 로그에 한 줄 붙인다. **실패해도 아무것도 안 던진다** (모듈 docstring 참고).
#
# 부르는 자리는 각 구현체의 `self._usage.api_calls += 1` / `cache_hits += 1` / 폴백 **바로
# 옆**이다. 카운터가 이미 그 자리에 있으므로 두 번째 진실이 안 생긴다.
#
# `search_units` 가 `int | None` 인 것은 **0과 없음이 다르기 때문**이다 — Cohere 는 검색
# 단위로 과금하고 나머지는 그 단위가 아예 없다. `items` 는 토큰을 안 주는 API(LlamaParse·
# Tavily)와 로컬 리랭커에서 **유일한 규모 지표**라 0이 기본값이지 `None` 이 아니다
def append_cost_log(
    *,
    stage: CostStage,
    provider: str,
    model: str,
    outcome: CostOutcome,
    input_tokens: int = 0,
    output_tokens: int = 0,
    search_units: int | None = None,
    items: int = 0,
    detail: str | None = None,
    config: CostLogConfig | None = None,
) -> bool:
    cfg = config or CostLogConfig()
    if not cfg.cost_log_enabled:
        return False

    record = {
        "at": datetime.now(UTC).isoformat(),
        "run_id": _RUN_ID,
        # 비어 있으면 `null` 이다. 빈 문자열로 두면 "이름을 안 줬다"와 "이름이 빈 문자열"이
        # 집계에서 안 갈린다
        "label": cfg.cost_label or None,
        "stage": stage,
        "provider": provider,
        "model": model,
        "outcome": outcome,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "search_units": search_units,
        "items": items,
        "detail": detail,
    }
    line = json.dumps(record, ensure_ascii=False) + "\n"

    try:
        with _LOCK:
            cfg.cost_log_path.parent.mkdir(parents=True, exist_ok=True)
            with cfg.cost_log_path.open("a", encoding="utf-8") as handle:
                handle.write(line)
    except OSError:
        return False
    return True
