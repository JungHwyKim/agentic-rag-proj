"""업로드 엔드포인트 테스트."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app


# 테스트용 클라이언트
@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


# 업로드한 파일을 파싱해 ParsedDocument JSON 을 돌려줘야 한다
def test_upload_docx(client: TestClient, docx_file: Path) -> None:
    with docx_file.open("rb") as handle:
        response = client.post(
            "/api/v1/documents/upload",
            files={"file": ("연간보고서.docx", handle, "application/octet-stream")},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["metadata"]["source_name"] == "연간보고서.docx"
    assert body["metadata"]["file_format"] == "docx"
    assert body["elements"][0]["text"] == "연간 보고서"


# 표는 마크다운으로 직렬화되어 내려와야 한다
def test_upload_returns_markdown_table(client: TestClient, xlsx_file: Path) -> None:
    with xlsx_file.open("rb") as handle:
        response = client.post(
            "/api/v1/documents/upload",
            files={"file": ("매출.xlsx", handle, "application/octet-stream")},
        )

    assert response.status_code == 200
    assert "| 월 | 금액 |" in response.json()["elements"][0]["text"]


# 화이트리스트에 없는 확장자는 415 로 막아야 한다
def test_rejects_unlisted_extension(client: TestClient, tmp_path: Path) -> None:
    path = tmp_path / "malware.exe"
    path.write_bytes(b"MZ\x90\x00")

    with path.open("rb") as handle:
        response = client.post(
            "/api/v1/documents/upload",
            files={"file": ("malware.exe", handle, "application/octet-stream")},
        )

    assert response.status_code == 415
    assert "지원하지 않습니다" in response.json()["detail"]


# 확장자는 허용 목록에 있지만 내용이 알 수 없는 바이너리면 415
def test_rejects_unknown_content(client: TestClient, unknown_binary_file: Path) -> None:
    with unknown_binary_file.open("rb") as handle:
        response = client.post(
            "/api/v1/documents/upload",
            files={"file": ("mystery.pdf", handle, "application/pdf")},
        )

    assert response.status_code == 415
    assert "지원하지 않는" in response.json()["detail"]


# 크기 상한을 넘으면 413
def test_rejects_oversized_file(client: TestClient, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MAX_UPLOAD_SIZE_MB", "1")

    path = tmp_path / "big.txt"
    path.write_bytes(b"a" * (2 * 1024 * 1024))

    with path.open("rb") as handle:
        response = client.post(
            "/api/v1/documents/upload",
            files={"file": ("big.txt", handle, "text/plain")},
        )

    assert response.status_code == 413
    assert "너무 큽니다" in response.json()["detail"]


# 확장자를 위조해도 내용 기준으로 라우팅되어야 한다
def test_spoofed_extension_routed_by_content(
    client: TestClient, docx_file: Path, tmp_path: Path
) -> None:
    import shutil

    spoofed = tmp_path / "fake.hwpx"
    shutil.copy(docx_file, spoofed)

    with spoofed.open("rb") as handle:
        response = client.post(
            "/api/v1/documents/upload",
            files={"file": ("fake.hwpx", handle, "application/octet-stream")},
        )

    assert response.status_code == 200
    assert response.json()["metadata"]["file_format"] == "docx"


# 헬스체크
def test_health(client: TestClient) -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
