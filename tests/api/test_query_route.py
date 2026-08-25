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
from rag_pipeline.config import GenerationConfig, IngestConfig
from rag_pipeline.exceptions import GenerationError, IndexingError
from rag_pipeline.schemas.answer import Answer, CacheOrigin
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
def _client(store=None, tmp_path: Path | None = None) -> TestClient:
    app = create_app()
    config = IngestConfig()
    object.__setattr__(config, "_generation", GenerationConfig(openai_api_key=None))
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
    # 질문도 함께 그린다 — 화면이 말풍선 한 쌍을 덧붙이는 구조다
    assert "위약금" in body


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
    made = mocker.patch(
        "backend.app.api.routes.query.get_embedder", return_value="fake-embedder"
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
