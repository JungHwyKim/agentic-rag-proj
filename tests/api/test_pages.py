"""채팅 화면. 저장소를 못 읽어도 화면 자체는 떠야 한다."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app


# 화면은 app.state 를 안 읽는다 — 문서 목록은 HTMX 가 따로 불러온다
@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


# 화면이 뜬다. 저장소 상태를 하나도 안 꽂았는데도 200 이어야 한다 —
# 이것이 문서 목록을 서버 렌더에 안 싣고 HTMX 로 미룬 이유다
def test_chat_page_renders_without_store(client: TestClient):
    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


# 화면 구성안의 세 자리가 다 있다
def test_page_has_three_regions(client: TestClient):
    body = client.get("/").text

    assert "문서" in body
    assert "대화 히스토리" in body
    assert "질문을 입력하세요" in body


# 문서 목록은 화면이 실린 뒤 따로 불러온다
def test_documents_loaded_via_htmx(client: TestClient):
    body = client.get("/").text

    assert 'hx-get="/api/v1/documents"' in body
    assert 'hx-trigger="load"' in body


# 질의는 POST /api/v1/query 로 가고 결과를 대화 끝에 덧붙인다
def test_composer_posts_to_query(client: TestClient):
    body = client.get("/").text

    assert 'hx-post="/api/v1/query"' in body
    assert 'hx-swap="beforeend"' in body


# **htmx 는 CDN 이 아니라 벤더링이다** — 오프라인에서 화면이 죽으면 안 된다
def test_htmx_is_vendored(client: TestClient):
    body = client.get("/").text

    assert "/static/htmx.min.js" in body
    assert "unpkg.com" not in body
    assert "cdn." not in body

    served = client.get("/static/htmx.min.js")
    assert served.status_code == 200
    assert len(served.content) > 10_000


# 업로드 버튼은 이번 회차에서 목업이다. 눌리면 index_document() 가 자기 store 를 열어
# startup 싱글턴과 충돌하므로 **비활성이어야 한다** (plan_log/backend.md 미결)
def test_upload_button_is_disabled(client: TestClient):
    body = client.get("/").text

    assert "파일 업로드" in body
    assert "disabled" in body


# 히스토리는 자리만 두고 비활성이다 — rDBMS 가 붙을 자리를 화면에서 잃지 않기 위해서다
def test_history_is_placeholder(client: TestClient):
    body = client.get("/").text

    assert "위약금 조항 문의" in body
    assert 'aria-disabled="true"' in body
    assert "저장은 아직 준비 중입니다" in body


# 재검색 표시는 화면에 있되 아무것도 판정하지 않는다 — Phase 4~5 가 붙을 자리다.
# **유사질문 배지는 여기서 빠졌다** — 답변 캐시가 실제로 붙어 placeholder 가 아니게 됐다
def test_rewrite_row_still_placeholder_in_answer_fragment():
    from backend.app.core.templates import TEMPLATES_DIR

    fragment = (TEMPLATES_DIR / "_answer.html").read_text(encoding="utf-8")

    assert "재검색 중" in fragment
    # 흐리게 + 클릭 불가로 표시해야 진짜 기능으로 오인되지 않는다
    assert "is-placeholder" in fragment


# 캐시 배지는 **cached_from 이 있을 때만** 그린다. 조건 없이 그리면 캐시가 꺼져 있어도
# 배지가 남아, 사용자가 "이전 답변"으로 읽는다
def test_cache_badge_is_conditional_in_answer_fragment():
    from backend.app.core.templates import TEMPLATES_DIR

    fragment = (TEMPLATES_DIR / "_answer.html").read_text(encoding="utf-8")

    assert "{% if answer.cached_from %}" in fragment
    # 배지가 placeholder 흐림을 물려받으면 진짜 값인데도 클릭이 막힌다
    badge = fragment.split("{% if answer.cached_from %}", 1)[1].split("{% endif %}", 1)[0]
    assert "is-placeholder" not in badge


# 문서 수 문구가 들어갈 자리가 사이드바 **맨 아래**에 있어야 한다. 자리가 없으면
# _documents.html 의 out-of-band swap 이 갈 곳을 못 찾아 **조용히 아무 일도 안 한다**
def test_sidebar_has_document_count_slot(client: TestClient):
    body = client.get("/").text

    assert 'id="doc-count"' in body
    # 히스토리 뒤에 와야 예시화면처럼 바닥에 붙는다
    assert body.index("대화 히스토리") < body.index('id="doc-count"')


# 파일을 직접 열었을 때 이유를 말해주는 배너. **id 가 앵커다** —
# backend/preview.py 가 이 id 로 script 를 지우는데, 바뀌면 preview.html 에도 배너가 뜬다
# (정작 직접 열라고 만든 파일에 "직접 열지 말라"가 뜬다)
def test_file_protocol_warning_present(client: TestClient):
    body = client.get("/").text

    assert 'id="file-warning"' in body
    assert "location.protocol === 'file:'" in body
    assert "preview.html" in body


# **배너 스타일은 인라인이어야 한다.** CSS 가 안 왔다는 것을 알리는 배너인데
# 그 스타일을 app.css 에 두면 배너까지 스타일 없이 떠서 존재 이유가 무너진다
def test_file_warning_is_inline_styled(client: TestClient):
    from backend.app.core.templates import TEMPLATES_DIR

    chat = (TEMPLATES_DIR / "chat.html").read_text(encoding="utf-8")
    script = chat.split('<script id="file-warning">')[1].split("</script>")[0]

    assert "style=" in script
    assert "class=" not in script


# 서버로 받은 화면에는 Jinja 주석이 한 글자도 안 남아야 한다.
# 사용자가 본 깨진 화면의 정체가 바로 이 주석이 글자로 찍힌 것이었다
def test_rendered_page_has_no_jinja_comments(client: TestClient):
    body = client.get("/").text

    assert "{#" not in body
    assert "#}" not in body


# CSS 도 벤더링 없이 우리 것 하나다
def test_stylesheet_served(client: TestClient):
    assert "/static/app.css" in client.get("/").text
    assert client.get("/static/app.css").status_code == 200
