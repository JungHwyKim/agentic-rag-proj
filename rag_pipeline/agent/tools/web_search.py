"""웹 검색 도구 — Tavily.

한때 이 파일은 **계약만 있고 구현이 없었다.** 그때 적어 둔 「다시 볼 조건」이
*"사용자가 코퍼스 밖 질문에 답하기를 원할 때. 그때 정할 것은 제공자(Tavily·Serper) 와
기본 pytest 가 키 없이 통과하도록 만들 모킹 이음새"* 였고, 2026-08-29 에 그 조건이 왔다.

## 「미구성」이 여전히 예외가 아니라 값이다

키가 없거나 SDK 가 안 깔려 있으면 `_make_client()` 가 `None` 을 돌려주고 이 도구는
`NOT_CONFIGURED` 를 **값으로** 내려보낸다. 구현이 들어와도 그 계약이 안 바뀌는 것이 요점이다
— 빈 계약 시절의 테스트 둘이 그대로 살아 있고, 그 둘이 *아직 안 만들었다* 와 *부르다 죽었다*
를 사람 눈에 갈라 준다. 그래서 사유 문자열이 **예외 이름으로 시작하지 않는다.**

## 결과를 `RetrievedChunk` 로 맞춘다

전용 스키마(`WebResult`)를 안 만들었다. 이 모양이면 `agent/context.py:build_context()` ·
`generation/prompts.py:build_evidence_block()` · `generation/citations.py` 가 **한 줄도 안
바뀐 채** 웹 근거를 먹는다. 전용 스키마로 가면 근거 블록 모양이 두 벌이 되는데, `context.py`
가 *"세 번째 모양이 끼면 한 곳만 고쳐져도 에러가 안 난다"* 로 막아 둔 자리가 바로 거기다.

대가는 웹 결과에 뜻이 없는 칸이 생긴다는 것이고, 그것을 `metadata["origin"] == "web"` 하나가
가른다.

**점수 칸(`dense_score`·`rewrite_dense_score`·`fused_score`·`rerank_score`)은 안 채운다.**
그 넷은 전부 코퍼스 검색 경로의 산물이라, Tavily 의 relevance 를 거기 담으면
`evaluation/` 의 지표와 `rerank_diff.py` 가 **두 척도를 한 칸에서 읽는데 에러가 안 난다.**
Tavily 점수는 `metadata["web_score"]` 로 간다. 순서를 다시 매길 필요도 없다 —
`context.py` 가 *"다시 정렬하지 않는다"* 이고, 여기서는 Tavily 가 준 순서가 그 순서다.

## SDK 응답 모양은 **소스로 확인했다** (tavily-python 0.8.0, 2026-08-29)

`TavilyClient.search()` 는 `dict` 를 돌려주고 **`results` 키가 반드시 있다**
(`response_dict.setdefault("results", [])`). 그 dict 는 `response.json()` 이라 항목도 평범한
dict 다. 200 이 아니면 SDK 가 **예외를 던지고**, 그것은 `BaseTool.run()` 이 값으로 바꾼다.

이것을 짐작하지 않고 판 이유는 적재 쪽 선례다 — LlamaParse 응답의 `markdown` 을 문자열로
알고 `.strip()` 을 부르다 **적재 전체가 멈춘 적이 있다**(2026-08-18). 가짜 응답을 문자열로
지어냈으면 테스트가 그 오해를 그대로 검증했을 것이다.

**감수 사항**: 0.8.0 의 `search_depth` 는 합법값이 넷(`basic`·`advanced`·`fast`·
`ultra-fast`)인데 `WebSearchConfig` 는 둘만 연다. 뒤 둘의 크레딧·품질을 안 재 봐서이고,
**합법값을 못 적을 설정은 안 만든다**는 쪽을 골랐다.

## 호출 캐시가 없다

이유는 `rag_pipeline/config.py` 의 `WebSearchConfig` 주석에 있다 — 이 저장소의 캐시 셋은
*같은 입력이면 같은 출력* 을 전제하는데 웹 검색은 그것이 거짓이다.
"""

from __future__ import annotations

from typing import Any

from rag_pipeline.agent.tools.base import BaseTool
from rag_pipeline.config import WebSearchConfig
from rag_pipeline.cost_log import CostOutcome, append_cost_log
from rag_pipeline.schemas.agent import ToolInput, ToolOutput
from rag_pipeline.schemas.retrieval import RetrievedChunk

__all__ = ["NOT_CONFIGURED", "WebSearchTool"]

# 사람이 읽을 사유. **예외 이름으로 시작하지 않는다** (모듈 docstring 참고)
NOT_CONFIGURED = "웹 검색 도구가 아직 구성되지 않았습니다 (제공자·키 미설정)"


# 모킹 이음새. **모듈 레벨 함수여야 한다** — 테스트가
# `mocker.patch.object(web_search, "_make_client", ...)` 로 갈아끼운다. 인라인화하거나
# 메서드로 옮기면 그 테스트가 전부 깨진다(`generation/evaluate.py` 와 같은 자리).
# **키 없음과 SDK 없음이 같은 None 인 것이 결정이다** — 둘 다 사용자가 할 일은
# 「구성해라」 하나라 갈라 봐야 화면에 쓸 데가 없다
def _make_client(api_key: str | None) -> Any | None:
    if not api_key:
        return None
    try:
        from tavily import TavilyClient
    except ImportError:  # 선택 갈래다 — 안 깔려 있어도 기본 pytest 가 통과해야 한다
        return None
    return TavilyClient(api_key=api_key)


# 코퍼스 밖을 찾는 도구. 키가 없으면 미구성을 값으로 돌려준다
class WebSearchTool(BaseTool):
    name = "web_search"
    description = "색인된 문서 밖의 정보를 웹에서 찾는다"

    # 설정을 안 넘기면 호출 시점에 만든다 — `RetrieverTool` 의 `config` 인자와 같은 모양이다
    def __init__(self, *, config: WebSearchConfig | None = None) -> None:
        self._config = config

    # 한 번 검색한다. **0건은 실패가 아니다** — 못 찾은 것과 도구가 죽은 것은 다르다
    def _run(self, tool_input: ToolInput) -> ToolOutput:
        config = self._config or WebSearchConfig()
        # 클라이언트를 __init__ 에서 안 만든다. 호출 시점에 부르는 것이 모킹 이음새를
        # 살려 두는 자리다 (`ingestion/embedding/openai_api.py` 와 같은 이유)
        client = _make_client(config.tavily_api_key)
        if client is None:
            return ToolOutput(success=False, error=NOT_CONFIGURED)

        # **여기가 이 저장소에서 흔적이 제일 안 남는 유료 호출이다.** 이 도구에는
        # 호출 캐시가 **일부러** 없고(`WebSearchConfig` 주석 — 웹 검색은 *같은 입력이면
        # 같은 출력* 이 거짓이라 내용 해시 키가 안 선다) `usage` 카운터도 없다. 그래서
        # `.cache/` 를 훑는 과거 복구가 이 갈래만은 **한 건도** 못 되살린다.
        # 그 주석이 스스로 적어 둔 대가 — *"테스트·탐색을 되풀이하면 크레딧이 그만큼
        # 나간다"* — 를 세는 유일한 자리가 아래 두 줄이다
        try:
            response = client.search(
                query=tool_input.query,
                max_results=_resolve_max_results(tool_input, config),
                search_depth=config.web_search_depth,
                timeout=config.tavily_timeout_sec,
            )
        except Exception as exc:  # noqa: BLE001 - 다시 던진다. 값으로 바꾸는 것은 run() 몫이다
            # **`run()` 이 예외를 값으로 바꿔 삼키므로 여기서 안 남기면 실패가 사라진다.**
            # 크레딧이 나갔는지는 예외 종류에 달렸지만, 안 남기면 «도구가 죽었다» 와
            # «한도에 막혔다» 를 나중에 못 가른다
            _log_cost(config, "failed", detail=f"{type(exc).__name__}: {exc}")
            raise

        chunks = _to_chunks(response)
        # **0건도 성공이다** — 크레딧은 결과 수와 무관하게 나갔다
        _log_cost(config, "call", items=len(chunks))
        return ToolOutput(success=True, data=chunks)


# 비용 로그 한 줄. **`model` 칸에 검색 깊이를 싣는다** — Tavily 에는 모델이 없고 크레딧을
# 가르는 축이 `basic`/`advanced` 라(`WebSearchConfig` 주석: *"`advanced` 는 같은 질의에
# 크레딧을 더 쓴다"*), 그 자리가 곧 다른 갈래의 모델 이름과 같은 뜻이다. 비워 두면
# 리포트가 두 깊이를 한 줄로 합쳐 **비싼 회차를 싼 단가로 계산한다.**
#
# 토큰 칸은 안 채운다 — 0 은 *"안 썼다"* 가 아니라 *"이 API 는 토큰으로 안 판다"* 이고,
# 그것을 `provider` 가 가른다 (Cohere 리랭킹과 같은 모양이다)
def _log_cost(
    config: WebSearchConfig,
    outcome: CostOutcome,
    *,
    items: int = 0,
    detail: str | None = None,
) -> None:
    append_cost_log(
        stage="web_search",
        provider="tavily",
        model=config.web_search_depth,
        outcome=outcome,
        items=items,
        detail=detail,
    )


# metadata 의 max_results 를 쓰되 정수가 아니면 설정값으로 돌아간다.
# **`RetrieverTool._resolve_top_k()` 와 같은 모양이다** — Router 가 같은 도구를 상황에 따라
# 다른 폭으로 부를 수 있어야 하는데, `ToolInput` 에 필드를 더하면 **모든 도구가 웹 전용
# 칸을 갖게 된다**
def _resolve_max_results(tool_input: ToolInput, config: WebSearchConfig) -> int:
    value = tool_input.metadata.get("max_results")
    if isinstance(value, int) and value > 0:
        return value
    return config.web_search_max_results


# 응답을 근거 청크로 옮긴다. **URL 이나 본문이 없는 항목은 버린다** — 신원이 없으면 인용
# 번호가 가리킬 곳이 없고, 본문이 없으면 근거가 아니라 제목 한 줄이다
def _to_chunks(response: Any) -> list[RetrievedChunk]:
    # `search()` 가 setdefault 로 보장하지만, 그 보장은 **이 SDK 버전의 것**이다.
    # 값을 못 얻었을 때 죽는 대신 0건으로 내려가는 쪽을 고른다
    results = response.get("results") or [] if isinstance(response, dict) else []

    chunks: list[RetrievedChunk] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        text = str(item.get("content") or "")
        if not url or not text:
            continue
        chunks.append(
            RetrievedChunk(
                # 코퍼스 청크와 신원이 안 겹치게 접두어를 단다 — `chunk_id` 는
                # `evidence_unchanged_at` 의 앞뒤 대조가 읽는 값이다
                chunk_id=f"web:{url}",
                document_id=url,
                text=text,
                metadata={
                    # `build_evidence_block()` 이 이 값을 `[1] (…)` 로 그린다
                    "source_name": str(item.get("title") or url),
                    # 코퍼스 근거와 가르는 **유일한** 칸이다 (모듈 docstring 참고)
                    "origin": "web",
                    "url": url,
                    "web_score": item.get("score"),
                },
            )
        )
    return chunks
