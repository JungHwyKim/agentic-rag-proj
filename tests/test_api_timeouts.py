"""외부 API 클라이언트에 타임아웃이 실제로 먹는지 본다. 키 없이 돌고 네트워크를 안 탄다.

## 왜 이 테스트가 있나

**안 걸면 SDK 기본이 먹는데 그 값이 조용히 위험하다.** 실측(2026-09-03) — `openai 3.0.0` 은
`connect=5.0 · read=600 · max_retries=2` 라 최악이 **1800초**이고, `cohere 7.1.0` 은 **300초**다.
로더·질의가 uvicorn 스레드풀에서 도니 그동안 그 스레드가 다른 요청을 못 받는다.
**아무 에러도 안 나고 로그도 안 남는다** — 그냥 느려진다.

## 두 가지를 갈라 잠근다

- **값이 먹는가** — `read` 는 설정값이 되고 `connect` 는 5초로 남는가. 맨 float 을 주면
  connect 까지 덮이므로(실측) 이 둘을 함께 봐야 «조인다»가 «연결 실패가 12배 느려진다»로
  뒤집히는 것을 잡는다
- **빠진 클라이언트가 없는가** — `ast` 로 `rag_pipeline/` 전체를 훑는다. **파일별로 잠그면
  새로 생긴 클라이언트가 조용히 빠진다**(`auto_test_check.UNMAPPED_BY_DESIGN` · `_hookutil`
  자식 인코딩 검사와 같은 모양이다). 면제는 사유와 함께 아래 표에 적는다
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import httpx
import pytest

from rag_pipeline.config import OPENAI_CONNECT_TIMEOUT_SEC

# 생성자에서 타임아웃을 받아야 하는 클라이언트. 이름은 호출식의 마지막 이름 조각이다
_CLIENT_CALLS = frozenset({"OpenAI", "ClientV2"})

# 생성자에 안 주는 것이 맞는 클라이언트와 그 사유.
# **비워 두면 안 된다** — 사유 없이 빠지면 다음 사람이 «깜빡한 것»과 못 가른다
_TIMEOUT_ELSEWHERE = {
    # SDK 가 생성자에서 안 받는다. `search(timeout=...)` 로 준다(기본이 이미 60초지만
    # 그 60은 우연이라 우리 쪽에서 못 박는다 — WebSearchConfig 주석)
    "TavilyClient": "search() 인자로 준다",
    # llama_cloud 는 `parse(timeout=...)` 로 준다. 원래부터 값이 있던 유일한 갈래다
    "Client": "parse() 인자로 준다",
}

# `_make_client(api_key, timeout)` 을 가진 OpenAI 갈래 모듈들
_OPENAI_MODULES = [
    "rag_pipeline.generation.answer",
    "rag_pipeline.generation.evaluate",
    "rag_pipeline.ingestion.embedding.openai_api",
    "rag_pipeline.ingestion.loader.content.image_vision",
    "rag_pipeline.ingestion.loader.content.table_vision",
    "rag_pipeline.retrieval.reranker.openai_llm",
    "rag_pipeline.retrieval.rewrite",
]


# 읽기는 설정값이 되고 **연결은 5초로 남아야 한다.** 맨 float 을 주면 connect 까지 덮인다
@pytest.mark.parametrize("module_name", _OPENAI_MODULES)
def test_openai_client_gets_the_read_timeout_and_keeps_connect(module_name: str) -> None:
    module = importlib.import_module(module_name)

    client = module._make_client("sk-테스트", 12.5)

    assert client is not None
    assert client.timeout == httpx.Timeout(12.5, connect=OPENAI_CONNECT_TIMEOUT_SEC)
    assert client.timeout.connect == 5.0


# cohere 는 SDK 기본이 300초라 여기도 구멍이었다. 이쪽은 생성자가 맨 float 을 받는다
def test_cohere_client_gets_the_timeout() -> None:
    cohere_api = importlib.import_module("rag_pipeline.retrieval.reranker.cohere_api")

    client = cohere_api._make_client("co-테스트", 12.5)

    assert client is not None
    assert client._client_wrapper.get_timeout() == 12.5


# 키가 없으면 여전히 None 이다 — 타임아웃 인자가 그 갈래를 안 건드려야 한다
@pytest.mark.parametrize("module_name", _OPENAI_MODULES)
def test_no_key_still_returns_none(module_name: str) -> None:
    module = importlib.import_module(module_name)

    assert module._make_client(None, 12.5) is None


# 호출식의 마지막 이름 조각. `OpenAI(...)` 도 `cohere.ClientV2(...)` 도 같게 본다
def _callee_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


# **새로 생긴 클라이언트가 조용히 빠지는 것을 막는다.** 파일별로 잠그면 그 일이 난다
def test_every_client_construction_passes_a_timeout() -> None:
    root = Path(__file__).resolve().parents[1] / "rag_pipeline"
    missing: list[str] = []

    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _callee_name(node)
            if name not in _CLIENT_CALLS or name in _TIMEOUT_ELSEWHERE:
                continue
            if not any(keyword.arg == "timeout" for keyword in node.keywords):
                missing.append(f"{path.relative_to(root)}: {name}()")

    assert not missing, (
        "생성자에 timeout 을 안 주는 클라이언트가 있습니다 — "
        f"{missing}. 주거나, 다른 자리에서 준다면 _TIMEOUT_ELSEWHERE 에 사유를 적으세요"
    )
