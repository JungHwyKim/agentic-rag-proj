"""원본 보관·서빙·삭제. **이 파일이 지키는 것은 셋이다.**

1. **저장 이름이 계산된다** — 매핑 파일 없이 `document_id` 만으로 나온다. 해시 접미사를
   항상 붙이는 것이 그 전제이고, 안 붙이면 예약 문자 치환 뒤 `보고서:1.pdf` 와
   `보고서_1.pdf` 가 같은 파일이 되어 **한쪽의 원문 보기가 남의 파일을 에러 없이 보여준다**
2. **경로 탈출이 막힌다** — 경로 파라미터는 사용자 입력이다
3. **삭제가 색인과 원본을 함께 지운다** — 보이는 것과 실제가 어긋나지 않는다

`app.state` 에 가짜를 꽂고 lifespan 을 안 돌리는 이유는 `test_index_route.py` 와 같다 —
안 그러면 테스트가 `PROJECT_ROOT/.qdrant` 를 열어 개발자의 실제 색인을 건드린다.

**`originals_dir` 은 여기서 안 덮는다** — `tests/api/conftest.py` 의 autouse 픽스처가 이
폴더의 모든 테스트에 걸어 둔다. 손으로 덮게 하면 새 테스트를 쓰면서 잊는 순간 실제 보관함에
파일이 샌다(실제로 `test_index_route.py` 가 그렇게 샜다 — 그쪽 주석 참고).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.api.routes.documents import _stored_name
from backend.app.main import create_app
from rag_pipeline.config import IngestConfig
from rag_pipeline.schemas.document import DocumentMetadata, FileFormat, ParsedDocument


# 삭제 호출만 기록하는 저장소. 실제 벡터는 이 파일의 관심사가 아니다
class FakeStore:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    def delete_by_document_id(self, document_id: str) -> None:
        self.deleted.append(document_id)

    def list_documents(self) -> list[dict]:
        return []

    def iter_chunks(self):  # noqa: ANN201 - Iterator[StoredChunk]
        return iter(())

    def close(self) -> None:
        pass


# 앱을 만들고 가짜 상태를 꽂는다. **originals_dir 은 conftest 의 autouse 픽스처가 이미
# tmp_path 로 돌려놨다** — 여기서 다시 덮지 않는 이유는 그쪽 하나만 보면 되게 하려는 것이다
def _client(originals_dir: Path, bm25=None, answer_cache=None):  # noqa: ANN001
    app = create_app()
    app.state.ingest_config = IngestConfig()
    app.state.store = FakeStore()
    app.state.embedder = object()
    app.state.bm25 = bm25
    app.state.answer_cache = answer_cache
    return TestClient(app), app, originals_dir


def _files(name: str = "안내서.pdf", body: bytes = b"%PDF-1.4 fake") -> dict:
    return {"file": (name, body, "application/octet-stream")}


# ----------------------------------------------------------- 저장 이름


# **해시 접미사를 항상 붙이는 이유가 이 테스트다.** 치환만 하면 둘이 같은 이름이 되고,
# 색인은 다른 문서로 아는데 원문은 하나뿐인 상태가 된다 — 에러가 안 나서 못 알아챈다
def test_sanitized_names_that_would_collide_stay_distinct():
    assert _stored_name("보고서:1.pdf") != _stored_name("보고서_1.pdf")


# 예약 문자와 경로 구분자가 이름에서 사라진다. document_id_for() 가 경로를 떼주지만
# 예약 문자는 안 거르므로 여기서 막는다
@pytest.mark.parametrize("raw", ['보고서:최종?.pdf', "a/b.pdf", "a\\b.pdf", 'q"w|e*.pdf'])
def test_stored_name_has_no_unsafe_characters(raw: str):
    name = _stored_name(raw)

    assert not set(name) & set('<>:"|?*\\/')


# **이름은 document_id 만으로 계산된다.** 같은 값이면 언제 불러도 같은 이름이라
# 매핑 파일이 필요 없다 — 부딪힐 때만 접미사를 붙이는 안을 기각한 근거다
def test_stored_name_is_deterministic():
    assert _stored_name("안내서.pdf") == _stored_name("안내서.pdf")


# 확장자가 없어도 죽지 않는다. rpartition 이 앞을 비우는 경계다
def test_stored_name_without_extension():
    name = _stored_name("확장자없음")

    assert name.startswith("확장자없음__")
    assert "." not in name


# ----------------------------------------------------------- 저장 시점


# 색인에 성공하면 원본이 남는다
def test_successful_index_keeps_the_original(mocker, originals_dir: Path):
    mocker.patch("backend.app.api.routes.documents.index_document")
    client, _, originals = _client(originals_dir)

    response = client.post("/api/v1/documents/index", files=_files())

    assert response.status_code == 204
    saved = list(originals.iterdir())
    assert len(saved) == 1
    assert saved[0].name == _stored_name("안내서.pdf")
    assert saved[0].read_bytes() == b"%PDF-1.4 fake"


# **색인이 실패하면 안 남긴다.** 남기면 목록에 없는 파일이 디스크에 쌓여 화면과 어긋난다
def test_failed_index_keeps_no_original(mocker, originals_dir: Path):
    class _Boom(Exception):
        pass

    mocker.patch(
        "backend.app.api.routes.documents.index_document", side_effect=_Boom("색인 실패")
    )
    client, _, originals = _client(originals_dir)

    with pytest.raises(_Boom):
        client.post("/api/v1/documents/index", files=_files())

    assert not originals.exists() or not list(originals.iterdir())


# **파싱 전용 엔드포인트는 저장하지 않는다.** 색인을 안 하므로 저장하면 목록에 없는
# 원본이 쌓인다 (routes/documents.py 모듈 docstring)
def test_parse_only_upload_keeps_no_original(mocker, originals_dir: Path):
    # response_model 이 ParsedDocument 라 진짜 모양을 돌려줘야 한다 — None 을 주면
    # 라우트가 아니라 응답 검증에서 죽어 이 테스트가 무엇을 재는지 흐려진다
    parsed = ParsedDocument(
        metadata=DocumentMetadata(
            source_name="안내서.pdf", file_format=FileFormat.PDF, size_bytes=13
        )
    )
    mocker.patch("backend.app.api.routes.documents.ingest", return_value=parsed)
    client, _, originals = _client(originals_dir)

    response = client.post("/api/v1/documents/upload", files=_files())

    assert response.status_code == 200
    assert not originals.exists() or not list(originals.iterdir())


# ----------------------------------------------------------- 서빙


# PDF 는 브라우저가 그리므로 inline 이다
def test_pdf_is_served_inline(mocker, originals_dir: Path):
    mocker.patch("backend.app.api.routes.documents.index_document")
    client, _, _ = _client(originals_dir)
    client.post("/api/v1/documents/index", files=_files())

    response = client.get("/api/v1/documents/안내서.pdf/original")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "inline" in response.headers["content-disposition"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.content == b"%PDF-1.4 fake"


# HWPX 는 브라우저가 못 그리므로 내려받기다
def test_hwpx_is_served_as_attachment(mocker, originals_dir: Path):
    mocker.patch("backend.app.api.routes.documents.index_document")
    client, _, _ = _client(originals_dir)
    client.post("/api/v1/documents/index", files=_files("계획.hwpx", b"PK\x03\x04"))

    response = client.get("/api/v1/documents/계획.hwpx/original")

    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]


# 원본이 없는 문서(이 회차 이전에 올린 것)는 404 이고, 안내가 문구에 담긴다
def test_missing_original_returns_404_with_guidance(originals_dir: Path):
    client, _, _ = _client(originals_dir)

    response = client.get("/api/v1/documents/없는문서.pdf/original")

    assert response.status_code == 404
    assert "업로드" in response.json()["detail"]


# **경로 탈출.** 경로 파라미터는 사용자 입력이라 반드시 막혀야 한다 —
# document_id_for() 가 경로를 떼는 것이 1차, resolve() 봉쇄가 2차다
@pytest.mark.parametrize(
    "attack",
    ["../../.env", "..%2F..%2F.env", "....//....//.env", "/etc/passwd", "C:\\Windows\\win.ini"],
)
def test_path_traversal_is_blocked(tmp_path: Path, originals_dir: Path, attack: str):
    outside = tmp_path / "secret.txt"
    outside.write_text("비밀", encoding="utf-8")
    client, _, _ = _client(originals_dir)

    response = client.get(f"/api/v1/documents/{attack}/original")

    assert response.status_code == 404
    assert "비밀" not in response.text


# ----------------------------------------------------------- 삭제


# 삭제는 색인과 원본을 **함께** 지운다
def test_delete_removes_index_and_original(mocker, originals_dir: Path):
    mocker.patch("backend.app.api.routes.documents.index_document")
    client, app, originals = _client(originals_dir, bm25=object())
    client.post("/api/v1/documents/index", files=_files())
    assert list(originals.iterdir())

    response = client.delete("/api/v1/documents/안내서.pdf")

    assert response.status_code == 204
    assert response.headers["HX-Trigger"] == "documents-changed"
    assert app.state.store.deleted == ["안내서.pdf"]
    assert not list(originals.iterdir())


# BM25 는 저장소를 전량 훑은 스냅샷이라 삭제로도 낡는다. 색인 라우트와 같은 계약이다
def test_delete_invalidates_bm25(originals_dir: Path):
    client, app, _ = _client(originals_dir, bm25=object())

    client.delete("/api/v1/documents/안내서.pdf")

    assert app.state.bm25 is None


# **답변 캐시는 삭제에서 안 비운다.** 조회 시점 근거 대조가 *"딛고 선 청크가 사라졌다"* 를
# 잡는데, 삭제가 바로 그 경우라 원리적으로 걸러진다 — 색인 라우트가 clear() 를 부르는
# 이유(새 문서 유입은 사라진 것이 없어 못 잡는다)와 정확히 반대다
def test_delete_leaves_the_answer_cache_alone(originals_dir: Path):
    class _Cache:
        def __init__(self) -> None:
            self.cleared = 0

        def clear(self) -> None:
            self.cleared += 1

    cache = _Cache()
    client, app, _ = _client(originals_dir, answer_cache=cache)

    client.delete("/api/v1/documents/안내서.pdf")

    assert app.state.answer_cache is cache
    assert cache.cleared == 0


# 원본이 없어도 색인 삭제는 돈다 — 이 회차 이전에 올린 문서를 지울 수 있어야 한다
def test_delete_without_original_still_clears_the_index(originals_dir: Path):
    client, app, _ = _client(originals_dir)

    response = client.delete("/api/v1/documents/옛문서.pdf")

    assert response.status_code == 204
    assert app.state.store.deleted == ["옛문서.pdf"]


# 삭제도 경로 탈출이 막혀야 한다. 서빙보다 이쪽이 위험하다 — 지우기 때문이다
def test_delete_path_traversal_is_blocked(tmp_path: Path, originals_dir: Path):
    outside = tmp_path / "secret.txt"
    outside.write_text("비밀", encoding="utf-8")
    client, _, _ = _client(originals_dir)

    response = client.delete("/api/v1/documents/..%2F..%2Fsecret.txt")

    assert response.status_code == 404
    assert outside.exists()
