"""질의·문서목록 엔드포인트. **키 없이 통과해야 한다.**

store 는 가짜를 app.state 에 직접 꽂는다. TestClient 를 `with` 없이 쓰면 lifespan 이 안 돌아
진짜 저장소가 안 열리는데, 그것이 여기서는 의도다 — 안 그러면 테스트가 PROJECT_ROOT/.qdrant
를 열어 개발자의 실제 색인을 건드리고 배타 잠금까지 잡는다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from rag_pipeline.config import (
    AgentConfig,
    EvaluatorConfig,
    GenerationConfig,
    IngestConfig,
    RewriteConfig,
)
from rag_pipeline.exceptions import GenerationError, IndexingError
from rag_pipeline.schemas.answer import (
    Answer,
    CacheOrigin,
    CitationTrace,
    EvaluatorVerdict,
    RetryTrace,
    RewriteTrace,
)
from rag_pipeline.schemas.retrieval import RetrievedChunk


# list_documents 와 열거만 가진 가짜 저장소.
# **iter_chunks() 는 Protocol 에 있어 빼면 안 된다** — 질의 라우트가 첫 질의에 BM25 를 지으며
# 이걸 부르고, 없으면 AttributeError 가 그대로 500 이 된다. list_documents 와 달리
# 이쪽은 계약이라 관대하게 넘어가지 않는다 (rag_pipeline/ingestion/indexing/base.py)
class FakeStore:
    def __init__(self, documents=None, error: Exception | None = None) -> None:
        self._documents = documents or []
        self._error = error
        self.closed = False

    def list_documents(self) -> list[dict]:
        if self._error is not None:
            raise self._error
        return self._documents

    def iter_chunks(self):  # noqa: ANN201 - Iterator[StoredChunk]. 여기서는 늘 비어 있다
        return iter(())

    def close(self) -> None:
        self.closed = True


# list_documents 가 아예 없는 저장소. Protocol 만 지키는 두 번째 구현체를 흉내낸다 —
# 그래서 Protocol 에 있는 iter_chunks() 는 있고 없는 list_documents() 만 없다
class ProtocolOnlyStore:
    def iter_chunks(self):  # noqa: ANN201 - Iterator[StoredChunk]
        return iter(())

    def close(self) -> None:
        pass


# 앱을 만들고 가짜 상태를 꽂는다. lifespan 은 일부러 안 돌린다 (모듈 docstring 참고)
def _client(store=None, tmp_path: Path | None = None, retries: int = 0) -> TestClient:
    app = create_app()
    config = IngestConfig()
    object.__setattr__(config, "_generation", GenerationConfig(openai_api_key=None))
    # **판정도 못 박는다.** 라우트가 질의마다 `get_evaluator()` 를 부르므로 안 박으면
    # 개발자 `.env` 의 `EVALUATOR_MODEL` 이 픽스처에 실린다 — `_generation` 핀과 같은 이유이고,
    # CLAUDE.md 가 임베딩 provider 에서 실제로 밟았다고 적어 둔 그 함정이다
    object.__setattr__(
        config, "_evaluator", EvaluatorConfig(evaluator_model=None, openai_api_key=None)
    )
    # **재작성기도 같은 이유로 못 박는다.** 라우트가 질의마다 `get_rewriter()` 를 부르므로
    # 안 박으면 개발자 `.env` 의 `REWRITE_MODEL` 이 픽스처에 실린다 — 그리고 이 저장소는
    # 2026-08-27 에 그 줄을 실제로 채웠다(`fuse`). 위 `_evaluator` 핀과 글자 그대로 같은
    # 자리이고, **핀은 배선을 넣은 회차의 몫이다** — 다음 회차로 미루면 그 사이에 도는
    # 기본 pytest 가 실키를 문다
    object.__setattr__(
        config, "_rewrite", RewriteConfig(rewrite_model=None, openai_api_key=None)
    )
    # **재시도 루프도 못 박는다.** 라우트가 `cfg.agent.agent_max_retries` 로 **어느 진입점을
    # 부를지** 를 정하므로, 안 박으면 개발자 `.env` 한 줄이 이 파일의 모든 테스트를 루프
    # 경로로 보낸다 — 그러면 `answer_question` 을 모킹한 테스트들이 **모킹이 안 먹은 채로**
    # 돈다. 위 두 핀과 같은 자리이고, 갈리는 것은 «과금» 이 아니라 «다른 함수를 잰다» 다
    object.__setattr__(config, "_agent", AgentConfig(agent_max_retries=retries))
    app.state.ingest_config = config
    app.state.store = store if store is not None else FakeStore()
    app.state.embedder = object()
    return TestClient(app)


def _chunk(text: str = "제12조 …", **metadata) -> RetrievedChunk:  # noqa: ANN003
    meta = {"source_name": "계약서_v2.pdf", "page": 4, **metadata}
    return RetrievedChunk(
        chunk_id="계약서_v2.pdf:0007",
        document_id="계약서_v2.pdf",
        text=text,
        metadata=meta,
        dense_score=0.812,
    )


# 답변 본문과 소스 태그가 HTML 조각으로 온다. JSON 이 아니다 — HTMX 가 그대로 DOM 에 넣는다
def test_answer_fragment_has_text_and_source(mocker):
    mocker.patch(
        "backend.app.api.routes.query.answer_question",
        return_value=Answer(query="위약금", text="20% 입니다 [1]", sources=[_chunk()]),
    )

    response = _client().post("/api/v1/query", data={"query": "위약금"})

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "20% 입니다 [1]" in body
    assert "계약서_v2.pdf" in body
    assert "p.4" in body
    # **질문 말풍선은 이 조각에 없다** — 화면이 보내는 즉시 그리므로(이 응답은 3~6초 뒤다)
    # 서버가 또 그리면 두 벌이 된다 (routes/query.py · chat.html 의 #composer-js)
    assert '<div class="bubble user">' not in body
    # 대신 서버가 받은 질문이 숨은 채로 실려 온다. cp949 로 깨져 도착한 질의를 가르는
    # 유일한 방법이라(CLAUDE.md 함정 절), 이것이 없으면 화면이 깨진 질의를 멀쩡하게 보여준다
    assert 'data-echo="위약금"' in body


# 발췌가 조각 안에 함께 실려 온다. 서버 왕복 없이 펴려면 여기 있어야 한다
def test_answer_fragment_embeds_excerpt(mocker):
    mocker.patch(
        "backend.app.api.routes.query.answer_question",
        return_value=Answer(query="q", text="답", sources=[_chunk("제12조 본문 전체")]),
    )

    body = _client().post("/api/v1/query", data={"query": "q"}).text

    assert "제12조 본문 전체" in body
    assert "excerpt" in body


# **근거 0건이어도 200 이다.** 실패가 아니라 정상 경로이고, 소스 영역만 안 그린다
def test_no_sources_renders_without_source_row(mocker):
    mocker.patch(
        "backend.app.api.routes.query.answer_question",
        return_value=Answer(query="q", text="문서에서 찾지 못했습니다.", sources=[]),
    )

    response = _client().post("/api/v1/query", data={"query": "q"})

    assert response.status_code == 200
    assert "문서에서 찾지 못했습니다." in response.text
    assert 'class="sources"' not in response.text


# 빈 질의는 파이프라인을 아예 안 부른다
def test_blank_query_does_not_call_pipeline(mocker):
    called = mocker.patch("backend.app.api.routes.query.answer_question")

    response = _client().post("/api/v1/query", data={"query": "   "})

    assert response.status_code == 200
    called.assert_not_called()


# 색인이 없으면 IndexingError -> 503 + 한국어 메시지. **이 경로가 /query 로 처음 살아났다**
def test_indexing_error_maps_to_503(mocker):
    mocker.patch(
        "backend.app.api.routes.query.answer_question",
        side_effect=IndexingError(
            "컬렉션이 없습니다", user_message="아직 색인된 문서가 없어 검색할 수 없습니다."
        ),
    )

    response = _client().post("/api/v1/query", data={"query": "질문"})

    assert response.status_code == 503
    assert response.json()["detail"] == "아직 색인된 문서가 없어 검색할 수 없습니다."


# 생성 실패도 503 이다. 매핑에 빠져 있으면 여기서 500 이 난다
def test_generation_error_maps_to_503(mocker):
    mocker.patch(
        "backend.app.api.routes.query.answer_question",
        side_effect=GenerationError("키 없음", user_message="API 키가 설정되지 않았습니다."),
    )

    response = _client().post("/api/v1/query", data={"query": "질문"})

    assert response.status_code == 503
    assert response.json()["detail"] == "API 키가 설정되지 않았습니다."


# 문서 목록이 저장소 값으로 그려진다
def test_document_list_rendered():
    store = FakeStore(
        [
            {"document_id": "안내서.pdf", "source_name": "안내서.pdf", "chunk_count": 104},
            {"document_id": "시행령.hwpx", "source_name": "시행령.hwpx", "chunk_count": 45},
        ]
    )

    body = _client(store).get("/api/v1/documents").text

    assert "안내서.pdf" in body
    assert "104청크" in body
    assert "2개 문서" in body


# **비어 있는 것은 오류가 아니다.** 첫 실행에서 사이드바가 비는 것은 정상이다
def test_empty_document_list_is_ok():
    response = _client(FakeStore([])).get("/api/v1/documents")

    assert response.status_code == 200
    assert "아직 색인된 문서가 없습니다" in response.text


# list_documents 가 없는 저장소가 끼어도 화면은 산다 — 목록만 빈다.
# Protocol 을 안 넓힌 결정의 대가를 여기서 흡수한다
def test_store_without_list_documents_degrades_gracefully():
    response = _client(ProtocolOnlyStore()).get("/api/v1/documents")

    assert response.status_code == 200
    assert "아직 색인된 문서가 없습니다" in response.text


# 저장소가 터지면 503 이다. 목록 조회도 같은 매핑을 탄다
def test_document_list_error_maps_to_503():
    store = FakeStore(error=IndexingError("boom", user_message="저장소를 확인할 수 없습니다."))

    response = _client(store).get("/api/v1/documents")

    assert response.status_code == 503


# embedder 는 첫 질의에 만든다. startup 에서 만들면 **키 없는 환경에서 서버가 아예 안 뜬다** —
# 화면도 문서 목록도 못 보게 된다
def test_embedder_is_created_lazily(mocker):
    app = create_app()
    config = IngestConfig()
    object.__setattr__(config, "_generation", GenerationConfig(openai_api_key=None))
    app.state.ingest_config = config
    app.state.store = FakeStore()
    app.state.embedder = None
    # **이음새는 `core/singletons.py` 다** — getter 가 그리로 옮겨갔다(적재 라우트도
    # 같은 인스턴스를 써야 하는데 routes 끼리 import 하면 순환이라서다)
    made = mocker.patch(
        "backend.app.core.singletons.get_embedder", return_value="fake-embedder"
    )
    mocker.patch(
        "backend.app.api.routes.query.answer_question",
        return_value=Answer(query="q", text="답", sources=[]),
    )
    client = TestClient(app)

    # 문서 목록만 봐서는 안 만든다
    client.get("/api/v1/documents")
    made.assert_not_called()

    client.post("/api/v1/query", data={"query": "질문"})
    assert made.call_count == 1
    assert app.state.embedder == "fake-embedder"

    # 두 번째 질의는 다시 안 만든다
    client.post("/api/v1/query", data={"query": "또 질문"})
    assert made.call_count == 1


# lifespan 이 저장소를 열고 **반드시 닫는다.** 안 닫으면 다음 실행이 잠금으로 통째로 죽는다
def test_lifespan_opens_and_closes_store(mocker, tmp_path: Path):
    store = FakeStore()
    mocker.patch("backend.app.main.get_store", return_value=store)

    app = create_app()
    with TestClient(app) as client:
        assert app.state.store is store
        # 열자마자 클라이언트를 만들지 않는다 — 색인이 없어도 서버는 떠야 한다
        assert client.get("/api/v1/health").status_code == 200

    assert store.closed is True


# heading_path 구분자를 이중 이스케이프하지 않는다. Jinja2 표현식 안에서 `&gt;` 로 조인하면
# `&` 가 한 번 더 escape 돼 화면에 `머리말 &gt; 보호` 라는 글자가 그대로 뜬다(실제로 그랬다)
def test_heading_path_not_double_escaped(mocker):
    mocker.patch(
        "backend.app.api.routes.query.answer_question",
        return_value=Answer(
            query="q",
            text="답",
            sources=[_chunk(heading_path=["Ⅱ. 세부", "업무역할별"])],
        ),
    )

    body = _client().post("/api/v1/query", data={"query": "q"}).text

    assert "Ⅱ. 세부 &gt; 업무역할별" in body
    assert "&amp;gt;" not in body


# **소스 태그가 전부 발췌보다 앞에 온다.** 버튼과 발췌를 쌍으로 감싸던 옛 구조로 되돌아가면
# 태그가 세로로 쌓이는데 **화면을 안 보면 아무 신호도 없다** — 그 구조에서는 이 순서가 깨진다
def test_source_tags_come_before_excerpts(mocker):
    mocker.patch(
        "backend.app.api.routes.query.answer_question",
        return_value=Answer(query="q", text="답", sources=[_chunk(), _chunk(), _chunk()]),
    )

    body = _client().post("/api/v1/query", data={"query": "q"}).text

    assert body.rindex("source-tag") < body.index('class="excerpt"')


# 버튼과 발췌가 1:1 이어야 onclick 의 인덱스가 제 짝을 찾는다.
# 한쪽 루프만 고치면 엉뚱한 발췌가 펴지는데 예외는 안 난다
def test_excerpt_count_matches_sources(mocker):
    mocker.patch(
        "backend.app.api.routes.query.answer_question",
        return_value=Answer(query="q", text="답", sources=[_chunk(), _chunk(), _chunk()]),
    )

    body = _client().post("/api/v1/query", data={"query": "q"}).text

    assert body.count('class="excerpt"') == 3
    assert body.count('class="source-tag"') == 3


# CSS 가 긴 파일명을 잘라내므로 **원본은 title 에 남아야 한다.**
# 안 남기면 실데이터에서 어느 문서인지 화면으로 알 길이 없다
def test_source_tag_carries_full_name_in_title(mocker):
    long_name = "국가아동권리보장원+권리지원부_제3차+아동정책기본계획+아동용+안내서.pdf"
    mocker.patch(
        "backend.app.api.routes.query.answer_question",
        return_value=Answer(
            query="q", text="답", sources=[_chunk(source_name=long_name)]
        ),
    )

    body = _client().post("/api/v1/query", data={"query": "q"}).text

    assert f'title="{long_name}"' in body


# 문서 목록 조각이 사이드바 바닥 문구까지 함께 갱신한다(out-of-band).
# 빠지면 목록은 늘어나는데 "N개 문서" 가 옛 값에 멈춘다 — 조용히 어긋나는 자리다
def test_document_fragment_updates_count_out_of_band():
    store = FakeStore(
        [
            {"document_id": "a.pdf", "source_name": "a.pdf", "chunk_count": 10},
            {"document_id": "b.pdf", "source_name": "b.pdf", "chunk_count": 20},
        ]
    )

    body = _client(store).get("/api/v1/documents").text

    assert 'hx-swap-oob="true"' in body
    assert 'id="doc-count"' in body
    assert "2개 문서" in body


# 업로드 엔드포인트가 그대로 산다. 라우터를 더하면서 기존 경로를 밀어내지 않았는지 본다
def test_existing_routes_survive():
    response = _client().get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# 화면 라우터에는 api_prefix 가 안 붙는다 — 사람이 여는 주소이지 API 가 아니다
@pytest.mark.parametrize("path", ["/api/v1/", "/api/v1"])
def test_page_is_not_under_api_prefix(path: str):
    assert _client().get(path).status_code == 404


# --- 캐시 배지 ----------------------------------------------------------------


# 캐시에서 온 답이면 배지에 **원 질문**이 그대로 실린다. 임계값이 살짝 헐거워도
# 오답이 아니라 사용자가 알아볼 수 있는 오류가 되는 것이 이 표시의 목적이다
def test_cached_answer_renders_badge_with_original_question(mocker):
    mocker.patch(
        "backend.app.api.routes.query.answer_question",
        return_value=Answer(
            query="유예기간 언제까지죠?",
            text="6개월입니다.",
            sources=[_chunk()],
            cached_from=CacheOrigin(
                query="유예기간이 언제까지인가요?",
                created_at=datetime.now(UTC) - timedelta(days=3),
                score=0.964,
            ),
        ),
    )

    body = _client().post("/api/v1/query", data={"query": "유예기간 언제까지죠?"}).text

    assert "유예기간이 언제까지인가요?" in body
    assert "3일 전" in body
    # 진짜 값인데 흐림·클릭 불가를 물려받으면 안 된다
    badge = body.split("badge-row", 1)[1].split("</div>", 1)[0]
    assert "is-placeholder" not in badge


# 캐시가 아니면 배지 줄 자체가 없다. 빈 배지가 남으면 사용자는 캐시가 켜져 있다고 읽는다
def test_fresh_answer_has_no_cache_badge(mocker):
    mocker.patch(
        "backend.app.api.routes.query.answer_question",
        return_value=Answer(query="질문", text="답변", sources=[_chunk()]),
    )

    body = _client().post("/api/v1/query", data={"query": "질문"}).text

    assert "badge-row" not in body


# --- 판정 배지 ------------------------------------------------------------------


# 배지의 조각 하나를 꺼낸다. 캐시 배지도 같은 `badge-row` 라 **클래스로 갈라야** 한다
def _verdict_badge(body: str) -> str:
    marker = 'class="badge-row verdict-'
    assert marker in body
    return body[body.index(marker) :].split("</div>", 1)[0]


# 불충분이면 배지가 뜨고 **모델 사유는 title 에만** 실린다. 배지에 펼치면 길이가 모델
# 마음대로라 레이아웃이 무너지고 답변보다 판정이 먼저 읽힌다
def test_insufficient_verdict_renders_badge_with_reason_in_title(mocker):
    mocker.patch(
        "backend.app.api.routes.query.answer_question",
        return_value=Answer(
            query="질문",
            text="답변",
            sources=[_chunk()],
            verdict=EvaluatorVerdict(
                verdict="insufficient", reasoning="근거가 기입용 빈 서식이다"
            ),
        ),
    )

    body = _client().post("/api/v1/query", data={"query": "질문"}).text

    badge = _verdict_badge(body)
    assert "verdict-insufficient" in badge
    assert "충분하지 않다고 판정했습니다" in badge
    # 사유는 title 에만 있다 — 본문 텍스트로 새면 안 된다
    assert 'title="근거가 기입용 빈 서식이다"' in badge
    assert badge.count("근거가 기입용 빈 서식이다") == 1
    # **판정이 답변을 바꾸지 않는다**는 계약이 문구에도 보여야 한다
    assert "그대로 생성된" in badge


# 충분이면 줄 자체가 없다. `cached_from` 배지와 같은 규칙이다 —
# 빈 배지가 남으면 사용자는 판정이 켜져 있다고 읽는다
def test_sufficient_verdict_draws_no_badge(mocker):
    mocker.patch(
        "backend.app.api.routes.query.answer_question",
        return_value=Answer(
            query="질문",
            text="답변",
            sources=[_chunk()],
            verdict=EvaluatorVerdict(verdict="sufficient", reasoning="충분하다"),
        ),
    )

    body = _client().post("/api/v1/query", data={"query": "질문"}).text

    assert "verdict-" not in body


# 판정을 안 돌렸으면(꺼짐 또는 캐시 히트) 역시 줄이 없다
def test_no_verdict_draws_no_badge(mocker):
    mocker.patch(
        "backend.app.api.routes.query.answer_question",
        return_value=Answer(query="질문", text="답변", sources=[_chunk()]),
    )

    body = _client().post("/api/v1/query", data={"query": "질문"}).text

    assert "verdict-" not in body


# **실패는 불충분과 갈려야 한다.** 같은 클래스·같은 문구면 *판정이 불충분이라고 말한 것* 과
# *판정을 못 한 것* 이 화면에서 안 갈리는데, 그 둘을 가르려고 제3의 상태를 만들었다
def test_failed_verdict_is_distinct_from_insufficient(mocker):
    mocker.patch(
        "backend.app.api.routes.query.answer_question",
        return_value=Answer(
            query="질문",
            text="답변",
            sources=[],
            verdict=EvaluatorVerdict(verdict="failed", reasoning="판정을 얻지 못했습니다"),
        ),
    )

    body = _client().post("/api/v1/query", data={"query": "질문"}).text

    badge = _verdict_badge(body)
    assert "verdict-failed" in badge
    assert "verdict-insufficient" not in badge
    assert "판정하지 못했습니다" in badge


# 진짜 값인데 자리표시의 흐림·클릭 불가를 물려받으면 안 된다 (캐시 배지와 같은 가드)
def test_verdict_badge_is_not_a_placeholder(mocker):
    mocker.patch(
        "backend.app.api.routes.query.answer_question",
        return_value=Answer(
            query="질문",
            text="답변",
            sources=[],
            verdict=EvaluatorVerdict(verdict="insufficient", reasoning=""),
        ),
    )

    body = _client().post("/api/v1/query", data={"query": "질문"}).text

    assert "is-placeholder" not in _verdict_badge(body)


# --- 재작성 표시 ----------------------------------------------------------------


# 재작성 줄 하나를 꺼낸다. `badge-row` 와 클래스가 달라 갈린다
def _rewrite_row(body: str) -> str:
    if "rewrite-row" not in body:
        return ""
    return body.split("rewrite-row", 1)[1].split("</div>", 1)[0]


# **다시 쓴 질의를 사용자가 볼 수 있어야 한다.** 재작성이 엉뚱하게 좁혔을 때 알아볼 수 있는
# 자리가 화면에 이것 하나다 — 캐시 배지가 원 질문을 보여주는 것과 같은 기준이다
def test_rewrite_row_shows_the_rewritten_query(mocker):
    rewritten = "2026 중등 기초학력 보장 사업의 지원 방식 및 운영 방법 안내"
    mocker.patch(
        "backend.app.api.routes.query.answer_question",
        return_value=Answer(
            query="지원 방식 알려줘",
            text="답변",
            sources=[_chunk()],
            rewrite=RewriteTrace(query=rewritten, mode="fuse"),
        ),
    )

    body = _client().post("/api/v1/query", data={"query": "지원 방식 알려줘"}).text

    assert rewritten in body
    # **fuse 는 원 질의 갈래가 살아 있다** — "바꿔 검색했다"고 쓰면 거짓이다
    assert "도 함께 검색했습니다" in _rewrite_row(body)
    # 원 질의는 화면이 그린 말풍선에 그대로 남아 둘을 나란히 볼 수 있다.
    # 여기서 확인할 수 있는 것은 서버가 받은 질문이 그대로 되돌아온다는 것이다
    assert 'data-echo="지원 방식 알려줘"' in body
    # 긴 문장은 CSS 가 자르므로 전문은 title 에 담는다 (캐시 배지·소스 태그와 같은 규칙)
    assert f'title="{rewritten}"' in body


# **replace 는 문구가 달라야 한다.** 원 질의 갈래를 갈아끼운 것이라 "도 함께" 가 거짓이다 —
# 같은 문구를 두 모드에 쓰면 화면이 둘을 못 가르고, 그것이 이 축의 뜻을 지운다
def test_replace_mode_says_it_replaced(mocker):
    mocker.patch(
        "backend.app.api.routes.query.answer_question",
        return_value=Answer(
            query="q",
            text="답변",
            sources=[_chunk()],
            rewrite=RewriteTrace(query="다시 쓴 질의", mode="replace"),
        ),
    )

    row = _rewrite_row(_client().post("/api/v1/query", data={"query": "q"}).text)

    assert "로 바꿔 검색했습니다" in row
    assert "도 함께 검색했습니다" not in row


# 재작성이 안 걸렸으면 줄 자체가 없다. 빈 줄이 남으면 사용자는 재작성이 켜져 있다고 읽는다 —
# `cached_from`·`verdict` 배지와 같은 규칙이다
def test_no_rewrite_draws_no_row(mocker):
    mocker.patch(
        "backend.app.api.routes.query.answer_question",
        return_value=Answer(query="질문", text="답변", sources=[_chunk()]),
    )

    body = _client().post("/api/v1/query", data={"query": "질문"}).text

    assert "rewrite-row" not in body


# **질의마다 새로 만든다.** embedder·BM25·리랭커와 갈리는 지점이고 이유는 가중치다 —
# Evaluator 에는 올릴 것이 없고, 싱글턴으로 두면 `warnings`·`usage` 가 서버 수명 내내 쌓인다
def test_evaluator_is_built_per_query(mocker):
    made = mocker.patch(
        "backend.app.api.routes.query.get_evaluator", return_value="fake-evaluator"
    )
    asked = mocker.patch(
        "backend.app.api.routes.query.answer_question",
        return_value=Answer(query="질문", text="답변", sources=[]),
    )

    client = _client()
    client.post("/api/v1/query", data={"query": "질문"})
    client.post("/api/v1/query", data={"query": "또 질문"})

    assert made.call_count == 2
    assert asked.call_args.kwargs["evaluator"] == "fake-evaluator"


# **재작성기도 질의마다 새로 만든다** — Evaluator 와 같은 자리이고 근거도 같다(가중치 없음 ·
# `warnings`·`usage` 누적). 딸린 `ResultCache` 는 경로 + 불리언뿐이라 축이 아니다
def test_rewriter_is_built_per_query(mocker):
    made = mocker.patch(
        "backend.app.api.routes.query.get_rewriter", return_value="fake-rewriter"
    )
    asked = mocker.patch(
        "backend.app.api.routes.query.answer_question",
        return_value=Answer(query="질문", text="답변", sources=[]),
    )

    client = _client()
    client.post("/api/v1/query", data={"query": "질문"})
    client.post("/api/v1/query", data={"query": "또 질문"})

    assert made.call_count == 2
    assert asked.call_args.kwargs["rewriter"] == "fake-rewriter"


# **`.env` 의 `REWRITE_MODEL` 이 실제로 라우트까지 닿아야 한다.** 이 배선이 끊기면 설정을
# 켜도 아무 일이 안 일어나는데 **에러가 안 난다** — 2026-08-27 에 실제로 그 상태였고,
# 그때 `.env` 두 줄은 읽는 곳이 없는 죽은 줄이었다
def test_config_rewrite_reaches_the_route(mocker):
    asked = mocker.patch(
        "backend.app.api.routes.query.answer_question",
        return_value=Answer(query="질문", text="답변", sources=[]),
    )
    client = _client()
    object.__setattr__(
        client.app.state.ingest_config,
        "_rewrite",
        RewriteConfig(rewrite_model="gpt-4.1-mini", rewrite_mode="fuse", openai_api_key="sk-x"),
    )

    client.post("/api/v1/query", data={"query": "질문"})

    rewriter = asked.call_args.kwargs["rewriter"]
    assert rewriter is not None
    assert rewriter.mode == "fuse"


# 모델 이름이 없으면 `get_evaluator()` 가 None 을 돌려주고 **안 넘기는 것과 같다** —
# bm25·cache·reranker 와 같은 규약이라 라우트에 스위치가 없다
def test_rewriter_off_passes_none(mocker):
    asked = mocker.patch(
        "backend.app.api.routes.query.answer_question",
        return_value=Answer(query="질문", text="답변", sources=[]),
    )

    _client().post("/api/v1/query", data={"query": "질문"})

    assert asked.call_args.kwargs["rewriter"] is None


# 모델 이름이 없으면 `get_evaluator()` 가 None 을 돌려주고 **안 넘기는 것과 같다** —
# bm25·cache·reranker 와 같은 규약이라 라우트에 스위치가 없다
def test_evaluator_off_passes_none(mocker):
    asked = mocker.patch(
        "backend.app.api.routes.query.answer_question",
        return_value=Answer(query="질문", text="답변", sources=[]),
    )

    _client().post("/api/v1/query", data={"query": "질문"})

    assert asked.call_args.kwargs["evaluator"] is None


# --- 리랭커 --------------------------------------------------------------------


# **embedder 와 같은 축이다** — 로컬 갈래는 cross-encoder 가중치를 올리므로 startup 에서
# 만들면 그 시간만큼 서버가 안 뜬다. 첫 질의에 만들고 그 뒤로는 재사용한다
def test_reranker_is_created_lazily(mocker):
    app = create_app()
    config = IngestConfig()
    object.__setattr__(config, "_generation", GenerationConfig(openai_api_key=None))
    app.state.ingest_config = config
    app.state.store = FakeStore()
    app.state.embedder = object()
    app.state.reranker = None
    made = mocker.patch(
        "backend.app.api.routes.query.get_reranker", return_value="fake-reranker"
    )
    asked = mocker.patch(
        "backend.app.api.routes.query.answer_question",
        return_value=Answer(query="q", text="답", sources=[]),
    )
    client = TestClient(app)

    # 문서 목록만 봐서는 안 만든다
    client.get("/api/v1/documents")
    made.assert_not_called()

    client.post("/api/v1/query", data={"query": "질문"})
    assert made.call_count == 1
    assert asked.call_args.kwargs["reranker"] == "fake-reranker"

    # 두 번째 질의는 다시 안 만든다
    client.post("/api/v1/query", data={"query": "또 질문"})
    assert made.call_count == 1


# **`None` 이 정상값이라 그것도 기억해야 한다.** 꺼진 상태를 "아직 안 만들었다"로 읽으면
# 질의마다 다시 만드는데, 로컬 갈래에서는 그 자리가 곧 모델 적재다
def test_disabled_reranker_is_not_rebuilt_every_query(mocker):
    app = create_app()
    config = IngestConfig()
    object.__setattr__(config, "_generation", GenerationConfig(openai_api_key=None))
    app.state.ingest_config = config
    app.state.store = FakeStore()
    app.state.embedder = object()
    app.state.reranker = None
    made = mocker.patch("backend.app.api.routes.query.get_reranker", return_value=None)
    asked = mocker.patch(
        "backend.app.api.routes.query.answer_question",
        return_value=Answer(query="q", text="답", sources=[]),
    )
    client = TestClient(app)

    client.post("/api/v1/query", data={"query": "질문"})
    client.post("/api/v1/query", data={"query": "또 질문"})

    assert made.call_count == 1
    assert asked.call_args.kwargs["reranker"] is None


# --- 인용 배지 ------------------------------------------------------------------


# 배지 조각 하나를 꺼낸다. 캐시·판정 배지도 같은 `badge-row` 라 클래스로 갈라야 한다
def _citation_badge(body: str) -> str:
    marker = 'class="badge-row citation-row"'
    assert marker in body
    return body[body.index(marker) :].split("</div>", 1)[0]


# 범위 밖 번호면 배지가 뜨고 **근거 개수와 그 번호가 함께 읽힌다** — 둘 중 하나만으로는
# 사용자가 무엇이 어긋났는지 못 본다
def test_out_of_range_citation_renders_badge(mocker):
    mocker.patch(
        "backend.app.api.routes.query.answer_question",
        return_value=Answer(
            query="위약금",
            text="20% 입니다 [3]",
            sources=[_chunk()],
            citation=CitationTrace(problem="out_of_range", numbers=[3], source_count=2),
        ),
    )

    body = _client().post("/api/v1/query", data={"query": "위약금"}).text

    badge = _citation_badge(body)
    assert "2개인데" in badge
    assert "[3]" in badge
    # **답변은 그대로다** — 검사가 본문을 안 고친다(query.py). 배지만 보면 눌렸다고 읽힌다
    assert "그대로 생성된 것입니다" in badge
    assert "20% 입니다 [3]" in body


# 인용이 아예 없으면 문구가 갈린다. 한 문구로 뭉치면 «없는 근거를 가리킨다» 가 거짓이 된다
def test_missing_citation_renders_a_different_sentence(mocker):
    mocker.patch(
        "backend.app.api.routes.query.answer_question",
        return_value=Answer(
            query="위약금",
            text="20% 입니다",
            sources=[_chunk()],
            citation=CitationTrace(problem="missing", numbers=[], source_count=1),
        ),
    )

    body = _client().post("/api/v1/query", data={"query": "위약금"}).text

    badge = _citation_badge(body)
    assert "출처 표시가 없습니다" in badge
    assert "없는 근거를 가리킵니다" not in badge


# **맞게 인용하면 줄 자체를 안 그린다.** 늘 그리면 정상이 소음이 되고, 사용자는
# 배지를 읽지 않게 된다 — 판정 배지가 *충분* 을 안 그리는 것과 같은 규칙이다
def test_valid_citation_draws_no_badge(mocker):
    mocker.patch(
        "backend.app.api.routes.query.answer_question",
        return_value=Answer(
            query="위약금", text="20% 입니다 [1]", sources=[_chunk()], citation=None
        ),
    )

    body = _client().post("/api/v1/query", data={"query": "위약금"}).text

    assert "citation-row" not in body
# ── 서버 상태등 ────────────────────────────────────────────────────────────────
#
# **등이 상태를 재는 대신 만들면 안 된다.** `embedder_for()` 를 부르는 순간 키가 없는 환경에서
# 상태 조회가 예외로 죽고, 있는 환경에서는 등이 KURE 2.27GB 를 올린다 — 등이 아니라 부하다
def test_status_says_idle_before_the_first_query():
    client = _client()
    # lifespan 이 심는 첫 질의 전 상태. `_client()` 는 편의로 임베더를 세워 두므로 되돌린다
    client.app.state.embedder = None

    body = client.get("/api/v1/status").text

    assert "status-idle" in body
    assert "status-ready" not in body
    # 조회가 임베더를 만들지 않았다
    assert client.app.state.embedder is None


# 임베더가 서 있으면 초록이다. 색이 셋인 이유는 워밍업 기본값이 꺼짐이어서, 첫 질의 전
# `embedder is None` 이 **정상**이기 때문이다 — 둘로 접으면 평소가 경고색이 된다
def test_status_says_ready_once_the_embedder_stands():
    client = _client()

    body = client.get("/api/v1/status").text

    assert "status-ready" in body
    assert "status-idle" not in body


# 워밍업이 실패한 채로도 임베더가 설 수 있다(리랭커만 죽은 경우). 그때도 사유는 보여준다 —
# 사유가 있는데 안 보이면 등이 절반만 말한다. 다만 **title 에만** 담는다(길이가 예외 마음대로다)
def test_status_carries_the_warmup_reason_in_the_title():
    client = _client()
    client.app.state.warmup_errors = ["reranker: boom"]

    body = client.get("/api/v1/status").text

    assert 'title="reranker: boom"' in body


# ---------------------------------------------------------------------------
# 재시도 루프 배선 — 어느 진입점을 부르나
# ---------------------------------------------------------------------------


# **0 이면 글자 그대로 예전 경로다.** 이것이 깨지면 루프를 끌 방법이 없어진다
def test_retries_off_calls_the_single_shot_entry(mocker):
    single = mocker.patch(
        "backend.app.api.routes.query.answer_question",
        return_value=Answer(query="q", text="답", sources=[_chunk()]),
    )
    loop = mocker.patch("backend.app.api.routes.query.answer_question_with_retries")

    assert _client(retries=0).post("/api/v1/query", data={"query": "q"}).status_code == 200

    assert single.call_count == 1
    assert loop.call_count == 0


# 켜면 반대쪽을 부른다. **인자 목록은 그대로다** — 갈리는 것이 함수 하나뿐이라
# 어느 쪽으로 가든 같은 것을 넘긴다
def test_retries_on_calls_the_loop_entry(mocker):
    single = mocker.patch("backend.app.api.routes.query.answer_question")
    loop = mocker.patch(
        "backend.app.api.routes.query.answer_question_with_retries",
        return_value=Answer(
            query="q",
            text="답",
            sources=[_chunk()],
            retry=RetryTrace(count=1, stop_reason="answered"),
        ),
    )

    assert _client(retries=2).post("/api/v1/query", data={"query": "q"}).status_code == 200

    assert loop.call_count == 1
    assert single.call_count == 0
    assert loop.call_args.kwargs["store"] is not None
    assert "evaluator" in loop.call_args.kwargs


# 재시도 줄은 **실제로 다시 찾아갔을 때만** 그린다. 배지 셋과 같은 규칙이다
def test_retry_row_is_drawn_when_it_actually_retried(mocker):
    mocker.patch(
        "backend.app.api.routes.query.answer_question_with_retries",
        return_value=Answer(
            query="q",
            text="답",
            sources=[_chunk()],
            retry=RetryTrace(count=2, stop_reason="retries_exhausted"),
        ),
    )

    body = _client(retries=3).post("/api/v1/query", data={"query": "q"}).text

    assert "retry-row" in body
    assert "2회 다시 찾았습니다" in body
    assert "재시도를 다 썼습니다" in body


# **성공한 재시도가 화면에 남아야 한다.** `answered` 를 빼면 실패한 회차만 보여서
# 루프가 손해만 보는 것처럼 읽힌다 — 계획이 여기서 틀렸다
def test_a_successful_retry_is_still_drawn(mocker):
    mocker.patch(
        "backend.app.api.routes.query.answer_question_with_retries",
        return_value=Answer(
            query="q",
            text="답",
            sources=[_chunk()],
            retry=RetryTrace(count=1, stop_reason="answered"),
        ),
    )

    body = _client(retries=3).post("/api/v1/query", data={"query": "q"}).text

    assert "retry-row" in body
    assert "1회 다시 찾았습니다" in body
    assert "근거를 채우고 멈췄습니다" in body


# 0회는 안 그린다. 첫 검색에서 «충분» 이 나온 회차는 단발과 화면상 구별할 것이 없다
def test_zero_retries_draws_no_row(mocker):
    mocker.patch(
        "backend.app.api.routes.query.answer_question_with_retries",
        return_value=Answer(
            query="q",
            text="답",
            sources=[_chunk()],
            retry=RetryTrace(count=0, stop_reason="answered"),
        ),
    )

    body = _client(retries=3).post("/api/v1/query", data={"query": "q"}).text

    assert "retry-row" not in body


# 단발 경로(`retry is None`)도 안 그린다. 캐시 히트·루프 꺼짐과 화면이 할 일이 같다
def test_single_shot_draws_no_retry_row(mocker):
    mocker.patch(
        "backend.app.api.routes.query.answer_question",
        return_value=Answer(query="q", text="답", sources=[_chunk()]),
    )

    body = _client(retries=0).post("/api/v1/query", data={"query": "q"}).text

    assert "retry-row" not in body
