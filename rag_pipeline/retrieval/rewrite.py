"""질의 재작성. **1차 검색 결과를 보고** 질의를 다시 쓴다(pseudo-relevance feedback).

`retrieve()` 안에서 1차 검색 뒤·합류 앞에 선다. 밖이 아니라 안인 이유는 리랭커와 같다 —
`answer_question()` 에 붙이면 `run_eval` 이 `retrieve()` 를 직접 부르므로 **평가가 그 경로를
안 밟아 재작성을 영원히 못 잰다**(`.claude/wiki/plan_log/검색-생성.md` 7회차 기각 항목).

## 왜 코퍼스가 아니라 1차 검색 결과를 보나

이 코퍼스에서 재작성이 버는 것은 **도메인 낱말**이다. 그리고 그 낱말이 두 종류다 —
`실행방안`·`권역별 교육`·`관리기준표` 같은 **코퍼스 고유어**는 모델이 못 지어내고,
`손상`·`벌칙`(← 파기·처벌) `시청각`(← 사진·영상)은 **평범한 유의어 확장**이라 코퍼스를
안 봐도 나온다. PRF 가 맡는 것은 앞쪽 절반이다.

**PRF 의 고전적 실패(query drift)가 이 코퍼스에서는 반증됐다** — 겨냥하는 최악의 질의
`sih-07` 은 정답이 **102위인데도 상위 10 에 필요한 낱말이 다 있다.** 정답 청크를 못 건져도
어휘는 이미 건져 놨다는 것이 이 설계의 근거다(21회차, 6건 실측).

대안이던 **제목 경로 전량**(231종·19,695자)은 질의마다 실려야 하고 **코퍼스와 함께 는다.**
1차 상위 10 은 코퍼스 크기와 무관하다.

## 왜 질문 문장 하나인가 — 낱말 목록이 아니라

21회차 프로브가 잰 `honest` 가 정확히 이 모양이라, **모델이 낸 문장과 사람이 낸 문장을
나란히 놓을 수 있다.** 「상한은 사람이 낸 값」과의 간격을 재는 것이 첫 측정의 목적이고,
출력 모양이 다르면 그 비교가 성립하지 않는다.

원 질의에 낱말만 덧붙이는 안은 기각했다 — 그러면 `rewrite_mode="replace"` 안에 원 질의가
살아 있어 **두 모드를 가른 이유가 흐려진다.**

## 실패는 예외가 아니라 폴백이다

`None` 을 돌려주고, 그러면 `retrieve()` 가 1차 pool 을 그대로 쓴다 — **오늘과 글자 그대로
같은 경로다.** 내려갈 층이 실재하는 것이 이 규약의 조건이고 여기는 있다(리랭커가 RRF 순서로
내려가는 것과 같은 자리, `reranker/base.py`).

**반쯤 쓰지 않는다.** 응답이 손상됐을 때 앞 몇 글자를 건져 쓰면 *재작성이 나빴나 응답이
반쪽이었나* 를 못 가르고, 지금은 이 단계를 **재는 중**이라 그 오염이 곧 판정 오염이다.

## 왜 Protocol 을 안 만드나

**두 번째 구현체가 없다.** `answer_cache` 가 하나뿐이라 추상을 안 만든 것과 같은 잣대이고,
리랭커가 Protocol 을 가진 것은 구현체가 셋이라서다(`reranker/__init__.py`).
*"합법값이 하나뿐인 추상은 죽은 추상"* 이 여기서도 그대로다.

## 캐시 salt 에 `rewrite_mode` 가 없다

재작성 호출은 `(질의, 1차 pool)` 만 보고 **mode 는 그 뒤에서 갈린다.** salt 에 넣으면
`fuse`·`replace` 두 프로필이 캐시를 못 나눠 써, *"둘 다 만들어 재본다"* 를 고른 근거
(두 번째 프로필이 사실상 무료)가 통째로 사라진다. `rerank_cache_salt_for()` 가 `device` 를
**싣는** 것과 방향이 반대인데 이유가 같다 — **출력을 가르는 것만 salt 에 넣는다.**

## 다시 쓴 질의를 부르는 쪽이 어떻게 아나

`retrieve()` 의 반환은 **청크 목록**이다. 거기에 문자열을 끼우려면 반환 타입을 넓혀야 하고,
그러면 `evaluation/run_eval.py` 를 포함한 모든 호출부가 함께 바뀐다 — 재작성 하나를 화면에
보이자고 검색의 공개 계약을 넓히는 것은 값이 안 맞는다.

그래서 **인스턴스가 답한다**: `last_rewritten` 이 마지막 `rewrite()` 의 결과다.
`warnings`·`usage` 가 이미 그 자리이고(리랭커도 같다), 라우트가 재작성기를 **질의마다 새로
만들기** 때문에 요청끼리 섞이지 않는다.

**호출마다 맨 앞에서 `None` 으로 되돌린다.** 안 되돌리면 폴백으로 내려간 질의가 **앞 질의의
재작성을 자기 것으로 보고**하는데, 그것은 에러가 아니라 화면과 로그에 조용히 실린다 —
재작성기를 여러 질의에 걸쳐 재사용하는 `run_eval` 이 정확히 그 모양이다.

## 모킹 이음새

`_make_client()` 는 **모듈 레벨 함수**여야 한다. 테스트가
`mocker.patch.object(rewrite, "_make_client", ...)` 로 갈아끼운다. 클라이언트를 `__init__`
에서 만들거나 이 함수를 메서드로 옮기면 외부 API 테스트가 전부 깨진다.

## 호출 인자는 `generation/evaluate.py` 를 따른다

`max_completion_tokens` 이고, `temperature` 는 **`None` 이면 인자 자체를 안 싣는다.**
`reranker/openai_llm.py` 는 luna 실측(2026-08-27) 이전 코드라 `max_tokens` 를 쓰고 있어
gpt-5 계열에서 400 이다 — 새 코드가 따라갈 쪽이 아니다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from rag_pipeline.config import RewriteConfig
from rag_pipeline.cost_log import CostOutcome, append_cost_log, split_tokens
from rag_pipeline.ingestion.loader.content.base import ResultCache, content_hash
from rag_pipeline.schemas.retrieval import RetrievedChunk

__all__ = ["QueryRewriter", "RewriteUsage", "get_rewriter"]

# 프롬프트를 고치면 **반드시 올린다** — 안 올리면 옛 응답이 새 프롬프트의 답인 척 돌아오는데
# 에러가 아니라 수치로만 드러난다 (.claude/wiki/plan_log/표-경로.md 14회차가 값을 치른 자리다)
_PROMPT_VERSION = "v1"

_SYSTEM = (
    "당신은 한국어 공문서 검색을 위해 질문을 다시 쓰는 도우미입니다. "
    "사용자 질문과 1차 검색으로 찾은 문서 조각들을 받습니다. "
    "그 조각에 실제로 쓰인 표현을 골라내고, 동시에 질문의 일상어를 공문서가 쓸 법한 말로 "
    "바꿔서, 검색이 잘 되는 질문 한 문장으로 다시 쓰세요. "
    "조각에 없는 사실을 지어내지 말고, 질문이 묻는 것을 바꾸지 마세요. "
    '출력은 설명 없이 JSON 객체 하나입니다: {"query": "다시 쓴 질문"}'
)


# 다시 쓰는 동안 쌓이는 비용. 질의당으로 읽으려면 호출부가 reset() 한다.
# **`RerankUsage` 와 같은 모양인 것이 의도다** — 리포트의 두 축이 모양이 갈리면 비교표를
# 손으로 맞춰야 한다
@dataclass
class RewriteUsage:
    # 실제로 나간 외부 API 호출 수. **성공한 뒤에만** 오른다
    api_calls: int = 0
    # 그 호출들이 쓴 토큰. **입력과 출력을 가른다** — 단가가 서로 달라 합계 하나로는
    # 금액을 원리적으로 못 낸다 (`RerankUsage` 와 같은 모양을 지킨다)
    input_tokens: int = 0
    output_tokens: int = 0
    # 캐시로 아낀 질의 수
    cache_hits: int = 0

    # 합계는 **파생이지 필드가 아니다** (`RerankUsage.total_tokens` 참고)
    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    # 질의 하나를 끝내고 다음으로 넘어갈 때 부른다
    def reset(self) -> None:
        self.api_calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_hits = 0


# OpenAI 클라이언트를 만든다. 키가 없으면 None.
# **모듈 레벨이어야 한다** — 테스트의 모킹 이음새다 (모듈 docstring 참고)
def _make_client(api_key: str | None) -> Any | None:
    if not api_key:
        return None
    try:
        from openai import OpenAI
    except ImportError:
        return None
    return OpenAI(api_key=api_key)


# 1차 결과를 번호 붙인 프롬프트로. **청크를 안 자른다** — 이 코퍼스는 도메인 낱말이
# 마크다운 표의 행에 흩어져 있어 앞 N 자만 실으면 헤더만 보인다(`openai_llm.py` 와 같은 근거).
# 자를 N 을 정할 분포의 틈도 없다
def _build_prompt(query: str, context: list[RetrievedChunk]) -> str:
    blocks = [f"[{index}]\n{chunk.text}" for index, chunk in enumerate(context)]
    body = "\n\n".join(blocks)
    return f"질문: {query}\n\n1차 검색으로 찾은 조각 {len(context)}개:\n\n{body}"


# 응답 본문에서 다시 쓴 질의를 꺼낸다. **못 얻으면 None** — 그것이 곧 1차 pool 로 내려간
# 것이고, 부분 성공을 여기서 만들지 않는 것이 이 함수의 요점이다
def _parse_query(content: str | None) -> str | None:
    if not content:
        return None
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return None

    if not isinstance(payload, dict):
        return None
    rewritten = payload.get("query")
    if not isinstance(rewritten, str):
        return None
    text = rewritten.strip()
    return text or None


# 캐시 키. **질의·1차 후보 신원·모델·프롬프트가 들어가고 mode 는 안 들어간다**
# (모듈 docstring 참고). 후보를 순서까지 포함해 싣는 이유는 프롬프트의 번호가 순서에서
# 나오기 때문이다 — 집합만 실으면 다른 프롬프트의 답을 재사용한다
def _cache_key(query: str, context: list[RetrievedChunk], *, salt: str) -> bytes:
    ids = "\n".join(chunk.chunk_id for chunk in context)
    return f"{salt}\n{query}\n{ids}".encode()


# 1차 검색 결과를 보고 질의를 한 번 다시 쓴다
class QueryRewriter:
    # 클라이언트를 여기서 만들지 않는다. _make_client() 를 호출 시점에 부르는 것이
    # 테스트의 모킹 이음새다
    def __init__(self, config: RewriteConfig) -> None:
        self._config = config
        self._usage = RewriteUsage()
        self._warnings: list[str] = []
        self._last_rewritten: str | None = None
        self._cache = ResultCache(config.rewrite_cache_dir, enabled=config.rewrite_cache_enabled)

    # 폴백이 일어났을 때 쌓인다. 부르는 쪽이 읽는다 — `Reranker.warnings` 와 같은 자리다
    @property
    def warnings(self) -> list[str]:
        return self._warnings

    # 지금까지 쌓인 비용. 질의당으로 읽으려면 호출부가 reset() 한다
    @property
    def usage(self) -> RewriteUsage:
        return self._usage

    # 마지막 `rewrite()` 가 실제로 검색에 쓴 질의. **폴백이면 None** 이고, 그 None 이 곧
    # "원 질의로만 검색했다"는 뜻이다 (모듈 docstring 참고). 캐시 히트도 값이 있다 —
    # 재작성이 걸린 것은 같고 갈리는 것은 과금뿐이다
    @property
    def last_rewritten(self) -> str | None:
        return self._last_rewritten

    # 어떻게 합치나. `retrieve()` 가 이것을 보고 갈래를 짠다 — **설정을 호출부가 다시 읽지
    # 않는 것이 요점이다.** 읽게 두면 재작성기와 합류가 서로 다른 모드로 도는 상태가
    # 에러 없이 생긴다
    @property
    def mode(self) -> str:
        return self._config.rewrite_mode

    # 1차 결과 몇 개를 보나. 위 `mode` 와 같은 이유로 여기서 답한다
    @property
    def context_k(self) -> int:
        return max(self._config.rewrite_context_k, 0)

    # 캐시 salt. **`rewrite_mode` 를 안 싣는다** (모듈 docstring 참고)
    @property
    def _salt(self) -> str:
        return f"rewrite:{self._config.rewrite_model}:{_PROMPT_VERSION}"

    # 다시 쓴 질의. **예외를 안 던진다** — 실패는 1차 pool 을 그대로 쓰는 폴백이다
    def rewrite(self, query: str, context: list[RetrievedChunk]) -> str | None:
        # **맨 앞에서 지운다.** 아래 어느 갈래로 빠져나가도 앞 질의의 값이 안 남는다
        # (모듈 docstring 참고)
        self._last_rewritten = None
        text = query.strip()
        # 보여줄 것이 없으면 PRF 가 성립하지 않는다. 호출을 아끼는 것이 아니라 **뜻이 없다**
        if not text or not context:
            return None

        pool = context[: self.context_k]
        if not pool:
            return None

        key = content_hash(_cache_key(text, pool, salt=self._salt))
        cached = self._cache.get(key)
        rewritten = cached.get("query") if isinstance(cached, dict) else None
        if isinstance(rewritten, str) and rewritten.strip():
            self._usage.cache_hits += 1
            # **아낀 것도 남긴다** — 재작성은 질의당 약 +2초를 무는 유료 호출이라
            # (2026-08-27 실측) 캐시가 얼마나 먹었는지가 곧 실사용 지연의 절반이다
            self._log_cost("cache_hit", items=len(pool))
            self._last_rewritten = rewritten.strip()
            return self._last_rewritten

        rewritten = self._call_api(text, pool)
        if rewritten is None:
            # 경고는 남기고 질의는 안 건드린다. 부르는 쪽이 1차 pool 을 그대로 쓴다.
            # **폴백도 한 줄 남긴다** — 안 남기면 «재작성이 값을 못 했다» 와
            # «재작성이 안 돌았다» 가 로그에서 안 갈린다(`run_eval` 이 경고를 싣는 이유와 같다)
            self._log_cost(
                "failed",
                items=len(pool),
                detail=self._warnings[-1] if self._warnings else None,
            )
            return None

        # **못 쓸 응답은 캐시에 안 넣는다** (표-경로.md 13회차가 고친 그 실수다)
        self._cache.set(key, {"query": rewritten})
        self._last_rewritten = rewritten
        return rewritten

    # 실제로 보낸다. 실패·손상은 전부 None 으로 바꿔 폴백에 맡긴다
    def _call_api(self, query: str, pool: list[RetrievedChunk]) -> str | None:
        config = self._config
        client = _make_client(config.openai_api_key)
        if client is None:
            self._warnings.append("OPENAI_API_KEY 가 없어 질의 재작성을 건너뜁니다")
            return None

        # **`max_completion_tokens` 는 분기가 없다** — gpt-5 계열이 `max_tokens` 를 400 으로
        # 거부하는데 `gpt-4.1-mini` 는 둘 다 받는다. 갈리는 것은 temperature 하나라,
        # **None 이면 인자 자체를 안 싣는다** (모듈 docstring 참고)
        kwargs: dict[str, Any] = {
            "model": config.rewrite_model,
            "messages": [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": _build_prompt(query, pool)},
            ],
            "max_completion_tokens": config.rewrite_max_tokens,
            "response_format": {"type": "json_object"},
        }
        if config.rewrite_temperature is not None:
            kwargs["temperature"] = config.rewrite_temperature

        try:
            response = client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - 폴백이 있는 갈래다 (모듈 docstring 참고)
            self._warnings.append(
                f"질의 재작성 호출에 실패해 원 질의로만 검색합니다: {type(exc).__name__}: {exc}"
            )
            return None

        rewritten = _parse_query(self._content_of(response))
        if rewritten is None:
            # **부분 성공을 만들지 않는다** — 반쯤 쓴 질의는 판정을 오염시킨다
            self._warnings.append("질의 재작성 응답에서 질의를 못 얻어 원 질의로만 검색합니다")
            return None

        # 카운터는 성공한 뒤에만 올린다 (embedding·rerank 와 같은 규약)
        self._usage.api_calls += 1
        prompt_tokens, completion_tokens = split_tokens(response)
        self._usage.input_tokens += prompt_tokens
        self._usage.output_tokens += completion_tokens
        self._log_cost(
            "call",
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
            items=len(pool),
        )
        return rewritten

    # 비용 로그 한 줄. **카운터가 오르는 자리 옆에 둔다** — 두 번째 진실이 안 생긴다
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
            stage="rewrite",
            provider="openai",
            model=self._config.rewrite_model,
            outcome=outcome,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            items=items,
            detail=detail,
        )

    # 응답에서 본문을 꺼낸다. 모양이 틀려도 예외를 안 내고 None 으로 떨어뜨린다 —
    # 여기서 AttributeError 가 새면 폴백이 아니라 500 이 된다
    @staticmethod
    def _content_of(response: Any) -> str | None:
        choices = getattr(response, "choices", None) or []
        if not choices:
            return None
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None)
        return content if isinstance(content, str) else None


# 설정에 맞는 재작성기를 만든다. **모델 이름이 없으면 None** — 그것이 곧 꺼진 상태다.
#
# `get_reranker()`·`get_evaluator()` 와 같은 모양이고 이유도 같다: 켜지는 순간 질의당 LLM
# 호출이 하나 늘어, 값을 하는지 재기 전에 과금이 먼저 붙는 것을 막는다.
# **저장소 커밋본은 꺼진 채다** — 켜는 것은 `.env` 결정이다
def get_rewriter(config: RewriteConfig | None = None) -> QueryRewriter | None:
    cfg = config or RewriteConfig()
    if not cfg.rewrite_model:
        return None
    return QueryRewriter(cfg)
