"""웹 검색 도구 — Tavily.

**「미구성」이 예외가 아니라 값으로 나오는지**가 계약 시절부터의 이 파일의 뼈대다. 예외로
내면 Router 가 이 도구만 다르게 다뤄야 하고, 호출부가 구현과 함께 바뀐다.

구현이 들어오며 잰 것이 둘 더 늘었다.

- **키를 막는 핀이 실제로 막는가** — 안 잠그면 개발자 `.env` 의 실키로 기본 pytest 가
  Tavily 를 부른다. 2026-08-29 에 실제로 그 일이 났고 크레딧 2건이 나갔다
- **응답을 `RetrievedChunk` 로 옮기는 규칙** — 점수 칸을 안 채우는 것까지가 규칙이다
"""

from __future__ import annotations

import pytest

from rag_pipeline.agent.tools import web_search as web_search_module
from rag_pipeline.agent.tools.web_search import NOT_CONFIGURED, WebSearchTool
from rag_pipeline.config import WebSearchConfig
from rag_pipeline.schemas.agent import ToolInput


# Tavily SDK 를 흉내 낸다. **응답이 dict 인 것은 짐작이 아니라 소스로 확인한 모양이다**
# (tavily-python 0.8.0 의 `search()` 가 `response.json()` 을 돌려주고 `results` 를
# setdefault 한다). 문자열로 지어냈으면 테스트가 오해를 그대로 검증했을 것이다
class FakeTavily:
    def __init__(self, results: list[dict] | None = None, raises: Exception | None = None):
        self.results = results if results is not None else []
        self.raises = raises
        self.calls: list[dict] = []

    def search(self, **kwargs):
        self.calls.append(kwargs)
        if self.raises is not None:
            raise self.raises
        return {"query": kwargs.get("query"), "results": self.results}


def _result(url: str = "https://example.go.kr/a", **over) -> dict:
    base = {
        "title": "행정정보 데이터세트 안내",
        "url": url,
        "content": "데이터세트는 이관 시 그 특성이 변할 수 있다.",
        "score": 0.93,
    }
    base.update(over)
    return base


def _config(**over) -> WebSearchConfig:
    return WebSearchConfig(tavily_api_key="test-key", **over)


# ---- 미구성 (계약 시절부터 있던 둘) ----


# 키가 없으면 실패를 값으로 돌려준다. 예외가 아니다
def test_returns_not_configured():
    out = WebSearchTool(config=WebSearchConfig(tavily_api_key=None)).run(
        ToolInput(query="2026년 최신 지침")
    )

    assert out.success is False
    assert out.error == NOT_CONFIGURED


# 사유가 예외 이름으로 시작하면 안 된다 — *아직 안 만들었다* 와 *부르다 죽었다* 가 갈려야 한다
def test_reason_is_not_an_exception_name():
    out = WebSearchTool(config=WebSearchConfig(tavily_api_key=None)).run(ToolInput(query="q"))

    assert "Error" not in out.error
    assert "NotImplemented" not in out.error


# SDK 가 안 깔린 머신도 같은 사유다 — 선택 갈래라 그것이 정상이지 오류가 아니다
def test_missing_sdk_is_the_same_reason(mocker):
    mocker.patch.object(web_search_module, "_make_client", return_value=None)

    out = WebSearchTool(config=_config()).run(ToolInput(query="질문"))

    assert out.success is False
    assert out.error == NOT_CONFIGURED


# ---- 핀이 살아 있는지 ----


# **이 테스트는 `_make_client` 를 모킹하면 안 된다** — 모킹하면 핀이 없어도 통과한다.
# `tests/api/test_warmup.py` 가 `warm_up` 을 모킹하면 안 되는 것과 같은 이유다.
# 재는 것은 「설정을 실제로 만들었을 때 키가 안 실리는가」 하나다.
#
# **`delenv` 로 짜면 이 테스트가 빨개진다** — pydantic-settings 가 `.env` 파일을 따로
# 읽어서, 환경변수를 지워도 파일이 키를 채운다 (tests/agent/conftest.py 참고)
def test_conftest_pin_actually_blocks_the_real_key():
    assert not WebSearchConfig().tavily_api_key


# ---- 응답을 근거 청크로 옮기는 규칙 ----


# 성공 경로. 신원·본문·제목이 제자리에 간다
def test_results_become_chunks(mocker):
    fake = FakeTavily([_result()])
    mocker.patch.object(web_search_module, "_make_client", return_value=fake)

    out = WebSearchTool(config=_config()).run(ToolInput(query="데이터세트 이관"))

    assert out.success is True
    (chunk,) = out.data
    assert chunk.chunk_id == "web:https://example.go.kr/a"
    assert chunk.document_id == "https://example.go.kr/a"
    assert chunk.text.startswith("데이터세트는")
    assert chunk.metadata["source_name"] == "행정정보 데이터세트 안내"
    assert chunk.metadata["url"] == "https://example.go.kr/a"
    assert chunk.metadata["web_score"] == 0.93


# **코퍼스 근거와 가르는 유일한 칸이다.** 없으면 웹에서 온 것과 색인에서 온 것이 안 갈린다
def test_origin_marks_the_chunk_as_web(mocker):
    mocker.patch.object(web_search_module, "_make_client", return_value=FakeTavily([_result()]))

    (chunk,) = WebSearchTool(config=_config()).run(ToolInput(query="q")).data

    assert chunk.metadata["origin"] == "web"


# **점수 칸은 안 채운다** — 코퍼스 검색 경로의 산물이라 웹 점수를 담으면 평가 지표가
# 두 척도를 한 칸에서 읽는다
def test_ranking_score_fields_stay_empty(mocker):
    mocker.patch.object(web_search_module, "_make_client", return_value=FakeTavily([_result()]))

    (chunk,) = WebSearchTool(config=_config()).run(ToolInput(query="q")).data

    assert chunk.dense_score is None
    assert chunk.bm25_score is None
    assert chunk.rerank_score is None
    assert chunk.fused_score == 0.0


# 0건은 실패가 아니다 — 못 찾은 것과 도구가 죽은 것은 다르다
def test_no_results_is_not_a_failure(mocker):
    mocker.patch.object(web_search_module, "_make_client", return_value=FakeTavily([]))

    out = WebSearchTool(config=_config()).run(ToolInput(query="없는 것"))

    assert out.success is True
    assert out.data == []
    assert out.error is None


# 신원이나 본문이 없는 항목은 버린다 — 인용 번호가 가리킬 곳이 없거나 근거가 아니다
@pytest.mark.parametrize("broken", [{"url": ""}, {"content": ""}])
def test_items_without_identity_or_body_are_dropped(mocker, broken):
    results = [_result(**broken), _result(url="https://example.go.kr/b")]
    mocker.patch.object(web_search_module, "_make_client", return_value=FakeTavily(results))

    (chunk,) = WebSearchTool(config=_config()).run(ToolInput(query="q")).data

    assert chunk.document_id == "https://example.go.kr/b"


# ---- 설정이 요청에 닿는가 ----


# 설정값이 그대로 SDK 로 간다. 안 실리면 `WEB_SEARCH_DEPTH` 가 죽은 설정이 된다
def test_config_reaches_the_request(mocker):
    fake = FakeTavily([])
    mocker.patch.object(web_search_module, "_make_client", return_value=fake)

    WebSearchTool(config=_config(web_search_max_results=3, web_search_depth="advanced")).run(
        ToolInput(query="질문")
    )

    (call,) = fake.calls
    assert call["query"] == "질문"
    assert call["max_results"] == 3
    assert call["search_depth"] == "advanced"


# metadata 가 설정을 이긴다 — Router 가 같은 도구를 다른 폭으로 부를 수 있어야 한다
def test_metadata_overrides_max_results(mocker):
    fake = FakeTavily([])
    mocker.patch.object(web_search_module, "_make_client", return_value=fake)

    WebSearchTool(config=_config(web_search_max_results=5)).run(
        ToolInput(query="질문", metadata={"max_results": 2})
    )

    assert fake.calls[0]["max_results"] == 2


# 정수가 아니거나 0 이하면 설정값으로 돌아간다
@pytest.mark.parametrize("bad", ["3", 0, -1, None])
def test_bad_metadata_falls_back_to_config(mocker, bad):
    fake = FakeTavily([])
    mocker.patch.object(web_search_module, "_make_client", return_value=fake)

    WebSearchTool(config=_config(web_search_max_results=5)).run(
        ToolInput(query="질문", metadata={"max_results": bad})
    )

    assert fake.calls[0]["max_results"] == 5


# ---- 계약 ----


# SDK 가 던져도 예외가 밖으로 안 나간다. `BaseTool.run()` 이 값으로 바꾼다
def test_sdk_exception_becomes_failed_output(mocker):
    fake = FakeTavily(raises=RuntimeError("429 Too Many Requests"))
    mocker.patch.object(web_search_module, "_make_client", return_value=fake)

    out = WebSearchTool(config=_config()).run(ToolInput(query="q"))

    assert out.success is False
    assert "429" in out.error
    assert out.data is None
