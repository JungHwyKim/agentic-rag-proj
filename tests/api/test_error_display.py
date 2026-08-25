"""실패가 화면에 보이는가 — 그리고 **공개 API 의 JSON 은 그대로인가.**

4회차까지 색인 실패도 질의 실패도 화면에서 완전히 조용했다. htmx 가 4xx/5xx 에 기본적으로
swap 을 안 해서, 사용자는 `색인하는 중…` 이 떴다 사라지는 것만 보고 사이드바는 그대로였다.

**여기서 재는 것은 축이 하나다 — `HX-Request` 헤더.** 있으면 HTML 조각, 없으면 지금까지의
JSON 이다. 그래서 모든 실패 경로를 **쌍으로** 확인한다: 헤더를 붙였을 때와 안 붙였을 때.
한쪽만 보면 "화면은 고쳤는데 공개 API 를 깨뜨렸다" 가 안 잡힌다.

**415·413 이 이 파일의 요점이다.** 그 둘은 `RagPipelineError` 가 아니라 `HTTPException`
이라(`routes/documents.py` 가 직접 던진다) 다른 핸들러로 간다. `RagPipelineError` 쪽만
고쳤으면 나머지 테스트가 전부 통과하는데 업로드가 제일 자주 낼 오류만 여전히 안 보인다.

`app.state` 에 가짜를 꽂고 lifespan 을 안 돌리는 이유는 `test_index_route.py` 와 같다 —
안 그러면 테스트가 `PROJECT_ROOT/.qdrant` 를 열어 개발자의 실제 색인을 건드린다.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app.core.config import Settings, get_settings
from backend.app.main import create_app
from rag_pipeline.config import GenerationConfig, IngestConfig
from rag_pipeline.exceptions import EmbeddingError, IndexingError

# htmx 가 모든 요청에 붙이는 헤더. 이 한 줄이 두 갈래를 가른다
HTMX = {"HX-Request": "true"}


# 색인 라우트가 넘겨받는 저장소. 이 파일에서는 실제로 쓰이지 않는다
class FakeStore:
    def iter_chunks(self):  # noqa: ANN201 - Iterator[StoredChunk]
        return iter(())

    def close(self) -> None:
        pass


# 앱을 만들고 가짜 상태를 꽂는다. lifespan 은 일부러 안 돌린다 (모듈 docstring 참고)
def _client() -> TestClient:
    app = create_app()
    config = IngestConfig()
    # 생성 설정의 키를 지운다 — offline_config 가 하는 것과 같은 이유로, 안 지우면
    # 개발자 머신의 실키가 실린다
    object.__setattr__(config, "_generation", GenerationConfig(openai_api_key=None))
    app.state.ingest_config = config
    app.state.store = FakeStore()
    app.state.embedder = object()
    app.state.bm25 = None
    app.state.answer_cache = None
    return TestClient(app)


def _files(name: str = "안내서.docx", body: bytes = b"PK\x03\x04 not really a docx") -> dict:
    return {"file": (name, body, "application/octet-stream")}


# ---------------------------------------------------------------- 색인 실패


# 화면에서 온 색인 실패는 HTML 조각으로 돌아온다. **상태코드는 503 그대로다** —
# 200 으로 낮추면 htmx 가 편하게 그리지만 그것이 frontend.md 1회차가 스트리밍을 기각한 자리다
def test_index_failure_renders_html_for_htmx(mocker):
    mocker.patch(
        "backend.app.api.routes.documents.index_document",
        side_effect=EmbeddingError("키 없음", user_message="임베딩 API 키가 설정되지 않았습니다."),
    )

    response = _client().post("/api/v1/documents/index", files=_files(), headers=HTMX)

    assert response.status_code == 503
    assert "text/html" in response.headers["content-type"]
    assert "임베딩 API 키가 설정되지 않았습니다." in response.text
    assert 'class="error"' in response.text


# **같은 실패인데 헤더가 없으면 지금까지와 똑같은 JSON 이다.** 이 단언이 없으면 화면을
# 고치면서 공개 API 를 조용히 바꿔도 아무도 모른다
def test_index_failure_stays_json_without_htmx_header(mocker):
    mocker.patch(
        "backend.app.api.routes.documents.index_document",
        side_effect=EmbeddingError("키 없음", user_message="임베딩 API 키가 설정되지 않았습니다."),
    )

    response = _client().post("/api/v1/documents/index", files=_files())

    assert response.status_code == 503
    assert "application/json" in response.headers["content-type"]
    assert response.json()["detail"] == "임베딩 API 키가 설정되지 않았습니다."


# ------------------------------------------------- 415·413 (다른 핸들러를 탄다)


# **이 파일의 요점.** 415 는 _reject_unsupported_extension() 이 던지는 HTTPException 이라
# RagPipelineError 핸들러를 안 탄다. StarletteHTTPException 핸들러가 없으면 여기서만 JSON 이다
def test_unsupported_extension_renders_html_for_htmx():
    response = _client().post(
        "/api/v1/documents/index",
        files={"file": ("보고서.exe", b"MZ\x00\x00", "application/octet-stream")},
        headers=HTMX,
    )

    assert response.status_code == 415
    assert "text/html" in response.headers["content-type"]
    assert ".exe" in response.text
    assert 'class="error"' in response.text


# 헤더가 없으면 415 도 JSON 그대로다 (test_index_route.py 의 같은 단언과 짝이다)
def test_unsupported_extension_stays_json_without_htmx_header():
    response = _client().post(
        "/api/v1/documents/index",
        files={"file": ("보고서.exe", b"MZ\x00\x00", "application/octet-stream")},
    )

    assert response.status_code == 415
    assert "application/json" in response.headers["content-type"]
    assert ".exe" in response.json()["detail"]


# 413 도 같은 핸들러다. _save_upload() 가 스트리밍 중 누적 검사로 던진다 —
# 상한을 1MB 로 낮춰 작은 파일로 넘긴다
def test_too_large_upload_renders_html_for_htmx(mocker):
    mocker.patch(
        "backend.app.api.routes.documents.get_settings",
        return_value=Settings(max_upload_size_mb=1),
    )

    response = _client().post(
        "/api/v1/documents/index",
        files=_files(body=b"x" * (2 * 1024 * 1024)),
        headers=HTMX,
    )

    assert response.status_code == 413
    assert "text/html" in response.headers["content-type"]
    assert 'class="error"' in response.text


# ---------------------------------------------------------------- 질의 실패


# /query 도 똑같이 조용했다 — composer 가 event.detail.successful 일 때만 분기해서,
# 503 이 나면 말풍선도 오류도 안 떴다. 4회차 기록에 없던 것이다
def test_query_failure_renders_html_for_htmx(mocker):
    mocker.patch(
        "backend.app.api.routes.query.answer_question",
        side_effect=IndexingError(
            "컬렉션 없음", user_message="아직 색인된 문서가 없어 검색할 수 없습니다."
        ),
    )

    response = _client().post("/api/v1/query", data={"query": "질문"}, headers=HTMX)

    assert response.status_code == 503
    assert "text/html" in response.headers["content-type"]
    assert "아직 색인된 문서가 없어 검색할 수 없습니다." in response.text


# ---------------------------------------------------------- 문구의 출처


# **오류 문구는 여기서 새로 짓지 않는다.** 조각이 그리는 것은 _STATUS_BY_EXCEPTION 이
# 내보내는 user_message 그대로다. 템플릿이 문장을 덧붙이기 시작하면 같은 메시지의 두 번째
# 진실이 생긴다
@pytest.mark.parametrize(
    "message",
    ["임베딩 API 키가 설정되지 않았습니다.", "아직 색인된 문서가 없어 검색할 수 없습니다."],
)
def test_fragment_carries_user_message_verbatim(mocker, message: str):
    mocker.patch(
        "backend.app.api.routes.documents.index_document",
        side_effect=EmbeddingError("내부 사정", user_message=message),
    )

    response = _client().post("/api/v1/documents/index", files=_files(), headers=HTMX)

    assert message in response.text


# 성공은 여전히 204 이고 본문이 없다.
#
# **이 테스트의 이름을 한 번 잘못 지었다.** 처음에는
# `..._that_clears_the_slot` 이었는데, htmx 는 204 에 swap 을 **아예 안 해서**
# (`htmx.min.js` 의 `responseHandling` 에 `{code:"204", swap:false}`) 빈 본문이 화면을
# 비우지 않는다. 서버 쪽 사실(204 · 본문 없음)만 재면서 화면 동작을 재는 것처럼 이름을
# 붙였던 것이다 — 지우는 것은 `chat.html` 의 `hx-on::after-request` 한 줄이고
# `test_pages.py:test_success_clears_the_error_slot` 이 그쪽을 잠근다
def test_successful_index_returns_204_with_no_body(mocker):
    mocker.patch("backend.app.api.routes.documents.index_document")

    response = _client().post("/api/v1/documents/index", files=_files(), headers=HTMX)

    assert response.status_code == 204
    assert response.text == ""
    assert response.headers["HX-Trigger"] == "documents-changed"


# get_settings 를 갈아끼운 위 413 테스트가 캐시를 오염시키지 않는지 — 이 저장소의
# get_settings 는 매번 새로 만들지만, lru_cache 가 붙는 순간 위 테스트가 옆 테스트를 깬다
def test_settings_are_not_cached_between_tests():
    assert get_settings().max_upload_size_mb == Settings().max_upload_size_mb
