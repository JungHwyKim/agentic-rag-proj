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


# 문서 목록은 화면이 실린 뒤 따로 불러오고, **색인이 끝나면 다시 불러온다**.
# documents-changed 는 색인 라우트가 HX-Trigger 로 보내는 이벤트다 (routes/documents.py)
def test_documents_loaded_via_htmx(client: TestClient):
    body = client.get("/").text

    assert 'hx-get="/api/v1/documents"' in body
    assert "load, documents-changed from:body" in body


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


# 업로드는 더 이상 목업이 아니다. index_document() 가 store 를 인자로 받게 되면서
# startup 싱글턴과의 충돌이 풀렸다 (plan_log/backend.md 3회차).
# 파일을 고르는 즉시 보내므로 트리거가 change 이고, 별도 전송 버튼이 없다
def test_upload_posts_to_index_endpoint(client: TestClient):
    body = client.get("/").text

    assert "파일 업로드" in body
    assert 'hx-post="/api/v1/documents/index"' in body
    assert 'hx-encoding="multipart/form-data"' in body
    assert 'hx-trigger="change from:#upload-input"' in body


# 히스토리는 자리만 두고 비활성이다 — rDBMS 가 붙을 자리를 화면에서 잃지 않기 위해서다
def test_history_is_placeholder(client: TestClient):
    body = client.get("/").text

    assert "위약금 조항 문의" in body
    assert 'aria-disabled="true"' in body
    assert "저장은 아직 준비 중입니다" in body


# 재검색 표시가 **실물이 됐다**(2026-08-27). 유사질문 배지·판정 배지가 걸어간 길과 같다 —
# 붙기 전에는 모양만 있었고, 붙은 뒤에는 값이 있을 때만 그린다.
#
# **여기서 재는 것은 «가짜가 안 남았는가» 다.** 모형 문구가 실물 옆에 남으면 재작성에 관한
# 줄이 둘이 되고 그중 하나가 매 답변에 거짓을 그린다 — 그리고 흐린 줄이라 아무도 안 읽는다
def test_rewrite_row_is_real_not_a_placeholder():
    from backend.app.core.templates import TEMPLATES_DIR

    fragment = (TEMPLATES_DIR / "_answer.html").read_text(encoding="utf-8")

    # 모형이 그리던 가짜 진행 표시. 남아 있으면 안 된다
    assert "재검색 중 (1 / 3)" not in fragment
    assert "준비 중인 기능입니다" not in fragment
    # 값이 있을 때만 그린다 — 조건 없이 그리면 재작성이 꺼져 있어도 줄이 남는다
    assert "{% if answer.rewrite %}" in fragment
    row = fragment.split("{% if answer.rewrite %}", 1)[1].split("{% endif %}", 1)[0]
    assert "is-placeholder" not in row
    # **mode 로 문구가 갈려야 한다** — fuse 는 원 질의도 함께 돌았다
    assert "도 함께 검색했습니다" in row
    assert "로 바꿔 검색했습니다" in row


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


# 업로드 실패를 그릴 자리가 화면에 있고, 폼이 거기를 겨눈다.
#
# **성공 204 는 본문이 비어 있어 이 자리를 저절로 비운다** — 그래서 hx-swap 이 none 이 아니라
# innerHTML 이어야 한다. none 으로 되돌리면 오류가 그려질 자리가 없어져 다시 조용해진다
def test_upload_form_targets_the_error_slot(client: TestClient):
    body = client.get("/").text

    assert 'id="upload-error"' in body
    assert 'hx-target="#upload-error"' in body
    assert 'hx-swap="innerHTML"' in body


# **비대칭은 여전히 의도다** — 업로드 폼은 조건 없이 비우고, composer 는 실패했을 때 질문
# 글자를 **되돌려 놓는다.** file input 을 안 비우면 같은 파일을 다시 골라도 change 가 안 떠서
# 재시도도 재색인도 막히고, 질문 글자는 실패 때 남아야 한다.
#
# **composer 쪽은 속성에서 스크립트로 옮겨갔다** — 입력 예약이 생기면서 상태 기계가 한 곳에
# 있어야 했다. 그래서 재는 것도 문자열이 아니라 규칙이다: 보낼 때 비우고 실패하면 되돌린다
def test_upload_resets_unconditionally_but_composer_restores_on_failure():
    from backend.app.core.templates import TEMPLATES_DIR

    chat = (TEMPLATES_DIR / "chat.html").read_text(encoding="utf-8")
    upload_form = chat.split('<form id="upload-form"')[1].split("</form>")[0]
    composer_js = chat.split('<script id="composer-js">')[1].split("</script>")[0]

    # reset 이 **조건 밖**에 있어야 한다 — 핸들러 맨 앞이라는 것이 그 증거다
    assert 'hx-on::after-request="this.reset();' in upload_form
    # composer 는 보낼 때 비우고, 실패했을 때만 되돌린다
    assert "input.value = '';" in composer_js
    assert "if (!event.detail.successful) input.value = sent;" in composer_js


# **성공했을 때 오류 자리를 손으로 비운다.** htmx 는 204 에 swap 을 아예 안 하므로
# (`htmx.min.js` 의 `responseHandling` 에 `{code:"204", swap:false}`) `hx-swap="innerHTML"`
# 만으로는 안 지워진다 — 실패 뒤 정상 파일로 재시도해 성공해도 빨간 줄이 남는다
def test_success_clears_the_error_slot():
    from backend.app.core.templates import TEMPLATES_DIR

    chat = (TEMPLATES_DIR / "chat.html").read_text(encoding="utf-8")
    upload_form = chat.split('<form id="upload-form"')[1].split("</form>")[0]

    assert "event.detail.successful" in upload_form
    assert "getElementById('upload-error').innerHTML = ''" in upload_form


# 204 에 기대지 않는다는 것을 htmx 쪽에서도 잠근다. 벤더링한 파일을 갈아끼웠을 때
# 이 전제가 바뀌면 여기서 알린다 — 바뀌면 위 한 줄이 불필요해지거나 반대로 필수가 된다
def test_vendored_htmx_never_swaps_on_204():
    from backend.app.core.templates import TEMPLATES_DIR

    htmx = (TEMPLATES_DIR.parent / "static" / "htmx.min.js").read_text(encoding="utf-8")

    assert '{code:"204",swap:false}' in htmx


# htmx 는 4xx/5xx 를 기본적으로 swap 하지 않는다 — 이 리스너가 그것을 뒤집는다.
# 없으면 서버가 오류 조각을 보내도 화면에는 아무것도 안 뜬다 (main.py · plan_log/backend.md 5회차)
def test_error_swap_listener_present(client: TestClient):
    body = client.get("/").text

    assert 'id="error-swap"' in body
    assert "htmx:beforeSwap" in body
    assert "shouldSwap = true" in body


# **isError 는 안 건드린다.** false 로 만들면 htmx:responseError 가 안 떠서 콘솔 진단이
# 사라진다 — 그리는 것과 실패로 남기는 것은 다른 축이다
def test_error_swap_keeps_the_error_signal():
    from backend.app.core.templates import TEMPLATES_DIR

    chat = (TEMPLATES_DIR / "chat.html").read_text(encoding="utf-8")
    script = chat.split('<script id="error-swap">')[1].split("</script>")[0]

    assert "isError" not in script


# 진행 바 자리와 배선이 화면에 실린다. **두 구간이 갈려 있는 것이 요점이다** —
# 업로드는 실제 퍼센트, 색인은 무한 애니메이션 + 경과 시간이다
def test_upload_progress_bar_present(client: TestClient):
    body = client.get("/").text

    assert 'id="upload-progress"' in body
    assert 'id="upload-bar"' in body
    assert 'id="upload-progress-js"' in body
    assert "htmx:xhr:progress" in body


# **퍼센트를 지어내지 않는다.** 색인 구간은 서버가 신호를 안 주므로 시간만 센다 —
# 여기에 가짜 진행률이 들어오면(예: 타이머로 퍼센트를 올리는 코드) 이 단언이 걸린다
def test_indexing_phase_shows_time_not_a_fake_percentage():
    from backend.app.core.templates import TEMPLATES_DIR

    chat = (TEMPLATES_DIR / "chat.html").read_text(encoding="utf-8")
    script = chat.split('<script id="upload-progress-js">')[1].split("</script>")[0]
    # 자르는 앵커를 `function stop(` 까지만 잡는다 — 인자까지 묶으면 시그니처가 바뀌는 순간
    # 잔여 전체가 구간으로 들어와 **이 가드가 엉뚱한 데서 터진다**(실제로 한 번 그랬다)
    indexing = script.split("function enterIndexing()")[1].split("function stop(")[0]
    tick = script.split("function tick()")[1].split("}")[0]

    # 무한 애니메이션으로 넘어가고, **퍼센트를 계산하지 않는다**.
    # (width = '100%' 는 바를 꽉 채우는 것이지 진행률 수치가 아니다)
    assert "is-indeterminate" in indexing
    assert "Math.round" not in indexing
    assert "d.total" not in indexing

    # 라벨에 들어가는 것은 퍼센트가 아니라 경과 시간이다.
    # **문구를 만드는 곳은 여기가 아니다** — 답변 생성 표시와 같은 것을 쓴다 (#elapsed-js)
    assert "%" not in tick
    assert "window.formatElapsed(startedAt)" in tick
    assert "색인하는 중…" in tick
    assert "'초'" in chat.split('<script id="elapsed-js">')[1].split("</script>")[0]


# 업로드 구간은 반대로 **실제 수치**여야 한다. lengthComputable 을 안 보면
# total 이 0 인 요청에서 NaN% 가 뜬다
def test_upload_phase_uses_real_bytes():
    from backend.app.core.templates import TEMPLATES_DIR

    chat = (TEMPLATES_DIR / "chat.html").read_text(encoding="utf-8")
    script = chat.split('<script id="upload-progress-js">')[1].split("</script>")[0]

    assert "lengthComputable" in script
    assert "d.loaded / d.total" in script


# **답변 생성 표시는 질문마다 #messages 안에서 태어난다.**
#
# 한때 입력줄 아래의 `#pending` 한 줄이었는데 **한 번도 뜬 적이 없다** — htmx 는 hx-indicator 가
# 없으면 htmx-request 를 요청을 낸 엘리먼트 자신에 붙이는데 그 줄은 form 의 형제였다. 그리고
# 그것을 잠갔다고 믿은 테스트는 **그 문자열이 응답에 있는지만** 쟀다(같은 함정이 6회차에도 있었다).
# 그래서 여기서는 문자열이 아니라 **자리와 동작**을 잠근다 — 죽은 규약이 되살아나지 않는 것까지
def test_generating_note_is_born_inside_messages(client: TestClient):
    body = client.get("/").text

    # 뜬 적 없는 규약이 되살아나면 안 된다
    assert 'id="pending"' not in body
    assert "htmx-indicator" not in body

    js = body.split('<script id="composer-js">')[1].split("</script>")[0]
    assert "bubble assistant generating" in js
    assert "messages.appendChild" in js
    assert "답변을 만드는 중…" in js


# **전송 중에 입력칸을 안 잠근다.** hx-disabled-elt 가 켜던 CSS 가 커서를 not-allowed 로 바꾸고
# 캐럿을 없앴는데, 그것이 3~6초 구간의 유일한 피드백이었다. 연타 방지는 예약 상태 기계가 맡는다
def test_composer_does_not_disable_input_while_sending(client: TestClient):
    body = client.get("/").text
    composer = body.split('<form class="composer"')[1].split("</form>")[0]

    assert "hx-disabled-elt" not in composer
    # htmx 자체 큐(queue last)는 값이 아니라 "폼을 다시 읽어라" 를 담아 빈 질문을 낸다
    assert 'hx-sync="this:drop"' in composer


# 예약 상태에서 잠기는 자리가 하나 남는데, **거기서도 커서 모양이 바뀌면 안 된다.**
# 브라우저는 disabled 입력에 화살표를 주므로 cursor 를 명시해야 없앤 것이 안 되살아난다
def test_stylesheet_never_swaps_the_cursor(client: TestClient):
    css = client.get("/static/app.css").text

    assert "cursor: not-allowed;" not in css
    assert ".composer input:disabled { opacity: .5; cursor: text; }" in css


# **상태등은 「문서」 제목 줄에 있다** — 사이드바 맨 위가 제일 먼저 읽히는 자리이고, 등이
# 답하는 물음(«서버가 도는가»)은 문서 목록보다 앞선다. 한때 사이드바 바닥에 뒀는데 사용자가
# 위로 올려 달라고 했다(2026-08-28)
def test_server_status_polls_in_the_document_panel_head(client: TestClient):
    body = client.get("/").text

    # 제목 줄 안이다 — 업로드 버튼보다 앞이고, 사이드바 바닥(doc-count)보다 훨씬 앞이다
    assert (
        body.index('<div class="panel-head">')
        < body.index('id="server-status"')
        < body.index('<form id="upload-form"')
        < body.index('id="doc-count"')
    )
    tag = body.split('<div id="server-status"')[1].split(">")[0]
    assert 'hx-get="/api/v1/status"' in tag
    assert "every 10s" in tag


# **입력칸을 비우는 자리는 submit 이 아니라 `htmx:beforeRequest` 다.**
#
# htmx 는 [폼 값 수집 → configRequest → **검증** → beforeRequest → 전송] 순으로 돌고
# (`htmx.min.js` 의 `b=C.errors; if(b&&b.length>0){... "htmx:validation:halted" ...; return}`),
# 이 스크립트의 submit 리스너는 htmx 것보다 **먼저** 등록된다(`htmx.min.js` 가 defer 라
# 나중에 process 한다). 그래서 submit 에서 비우면 htmx 가 **빈 질문을 읽고** `required` 검증에
# 걸려 **요청이 아예 안 나가는데, 화면에는 「만드는 중」만 영원히 남고 에러가 없다.**
# 2026-08-28 에 실제로 그렇게 만들었고 사용자가 «답변을 안 준다» 로 물고 왔다.
#
# **브라우저가 없어 동작으로는 못 잰다.** 대신 비우는 코드가 **어느 리스너 안에 있는지**를
# 잠근다 — 이 회귀가 나려면 그 줄이 submit 으로 돌아가야 한다
def test_input_is_cleared_only_after_htmx_read_the_form(client: TestClient):
    js = client.get("/").text.split('<script id="composer-js">')[1].split("</script>")[0]
    submit_body = js.split("form.addEventListener('submit'")[1].split("});")[0]
    before_request = js.split("'htmx:beforeRequest'")[1].split("});")[0]

    assert "input.value = ''" not in submit_body
    assert "input.value = ''" in before_request
    # 말풍선도 같은 이유로 beforeRequest 다 — 검증에 걸린 요청에는 아예 안 그려야 한다
    assert "bubble user" in before_request
    # submit 이 하는 일은 예약 하나뿐이고, htmx 리스너가 **뒤에** 붙어 있어 여기서 끊어야 한다
    assert "reserve(text)" in submit_body
    assert "stopImmediatePropagation" in submit_body


# **예약된 질의는 `afterRequest` 콜스택 밖에서 보내야 한다.**
#
# htmx 는 `htmx:afterRequest` 를 **먼저** 쏘고 그 다음에 in-flight 잠금을 푼다
# (`htmx.min.js` 의 `m = function(){u.xhr=null; ...}` 가 `onload` 끝에서 불린다). 그래서 그
# 핸들러 안에서 곧바로 `requestSubmit()` 하면 아직 «도는 중» 이라 `hx-sync="this:drop"` 이
# 예약된 질의를 **조용히 떨군다** — 예약도 스탑도 되는데 **자동 발사만 안 되고 에러가 없다.**
# 2026-08-28 에 사용자가 그 상태를 물고 왔다.
#
# 브라우저가 없어 동작으로는 못 잰다. 대신 **발사가 콜스택 밖으로 미뤄졌는지**를 잠근다
def test_reserved_query_is_dispatched_outside_the_htmx_callstack(client: TestClient):
    js = client.get("/").text.split('<script id="composer-js">')[1].split("</script>")[0]

    # 보내는 자리가 하나이고 그 하나가 미뤄져 있어야 한다 — 미루지 않은 맨 호출이
    # 하나라도 남아 있으면 그것이 곧 이 회귀다
    assert js.count("form.requestSubmit()") == 1
    assert "setTimeout(function () { form.requestSubmit(); }, 0)" in js


# 문서 이름이 곧 원문 링크다. **urlencode 가 빠지면** 한국어·공백·`#` 이 든 파일명에서
# 주소가 깨진다 — `#` 뒤는 조각으로 잘려 서버에 도달조차 안 한다
def test_document_name_links_to_original():
    from backend.app.core.templates import TEMPLATES_DIR

    fragment = (TEMPLATES_DIR / "_documents.html").read_text(encoding="utf-8")

    assert "/original" in fragment
    assert "doc.document_id | urlencode" in fragment
    assert 'target="_blank"' in fragment
    assert 'rel="noopener"' in fragment


# 삭제 버튼은 확인을 거친다. hx-confirm 은 htmx 2.0.4 내장이라 JS 가 0줄이다
def test_delete_button_confirms_before_deleting():
    from backend.app.core.templates import TEMPLATES_DIR

    fragment = (TEMPLATES_DIR / "_documents.html").read_text(encoding="utf-8")

    assert "hx-delete=" in fragment
    assert "hx-confirm=" in fragment
    # 되돌릴 수 없다는 것이 문구에 있어야 한다
    assert "되돌릴 수 없습니다" in fragment


# 출처 태그 **옆에** 원문 링크가 붙는다. 태그 자체에는 발췌 토글 onclick 이 이미 있어
# 한 버튼에 동작 둘을 얹지 않는다
def test_source_tag_has_a_separate_original_link():
    from backend.app.core.templates import TEMPLATES_DIR

    fragment = (TEMPLATES_DIR / "_answer.html").read_text(encoding="utf-8")
    tag = fragment.split('<button class="source-tag"')[1].split("</button>")[0]

    assert "source-open" in fragment
    assert "/original" in fragment
    # 원문 링크가 태그 버튼 **안**에 있으면 안 된다
    assert "/original" not in tag


# **`#page=` 는 PDF 일 때만이다.** HWPX 는 로더가 세는 쪽 수가 문서에 따라 크게 부풀어
# (신뢰가능 AI 가이드 실제 28쪽 → 97쪽) 엉뚱한 쪽으로 보낸다 (CLAUDE.md 함정 절)
def test_page_deep_link_is_pdf_only():
    from backend.app.core.templates import TEMPLATES_DIR

    fragment = (TEMPLATES_DIR / "_answer.html").read_text(encoding="utf-8")
    link = fragment.split('class="source-open"')[1].split("</a>")[0]

    assert "#page=" in link
    assert ".pdf" in link


# 원본 용량은 상한을 안 만든 대신 보여주는 것이다. 문서가 없으면 줄 자체가 비어야 한다
def test_sidebar_shows_originals_size():
    from backend.app.core.templates import TEMPLATES_DIR

    fragment = (TEMPLATES_DIR / "_documents.html").read_text(encoding="utf-8")

    assert "originals_mb" in fragment
    assert "MB" in fragment


# 인용 줄도 **값이 있을 때만** 그린다 — 배지 셋이 이미 쓰는 규칙이다. 조건 없이 그리면
# 맞게 인용한 답변에도 줄이 남아, 사용자가 «늘 뜨는 것» 으로 읽고 그때부터 안 읽는다.
#
# **문구가 problem 에서 갈려야 한다**: 인용이 아예 없는데 «없는 근거를 가리킵니다» 는
# 거짓이고, 범위 밖인데 «출처 표시가 없습니다» 도 거짓이다 — rewrite 줄이 mode 로
# 갈리는 것과 같은 자리다
def test_citation_row_is_conditional_and_branches_on_problem():
    from backend.app.core.templates import TEMPLATES_DIR

    fragment = (TEMPLATES_DIR / "_answer.html").read_text(encoding="utf-8")

    assert "{% if answer.citation %}" in fragment
    row = fragment.split("{% if answer.citation %}", 1)[1].split("</div>", 1)[0]
    assert "is-placeholder" not in row
    assert "없는 근거를 가리킵니다" in row
    assert "출처 표시가 없습니다" in row
    # **danger 를 안 쓴다** — 거짓 경고가 날 수 있어(generation/citations.py) 빨강으로
    # 말하면 멀쩡한 답변이 고장 난 것으로 읽힌다
    assert "error" not in row
