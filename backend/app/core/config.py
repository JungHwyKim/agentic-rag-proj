"""웹 계층 설정. 파싱 관련 설정은 rag_pipeline.config 가 따로 들고 있다."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from rag_pipeline.config import PROJECT_ROOT


# FastAPI 앱 동작에 필요한 설정
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Agentic RAG API"
    api_prefix: str = "/api/v1"
    # 업로드 크기 상한. 스트리밍 중 누적 검사로 메모리 폭발을 막는다
    max_upload_size_mb: int = 50

    # 색인에 성공한 업로드 원본을 남겨두는 곳.
    #
    # **파싱 설정이 아니라 웹 계층 설정인 이유**: 업로드의 수명은 backend 의 몫이고
    # rag_pipeline 은 파일이 어디서 왔는지 몰라야 한다. 그래서 원본 보관·서빙·삭제가
    # 전부 이쪽에 있고 파이프라인은 한 줄도 안 바뀐다.
    #
    # **상한도 자동 정리도 없다.** 지금 값을 정하면 관측 없는 숫자가 되고, 자동 삭제는
    # 사용자가 모르는 사이 원문만 사라지는 상태(색인은 남아 목록엔 보인다)를 만든다.
    # 대신 사이드바가 총 용량을 보여주고 삭제 버튼을 손에 쥐어 준다
    # (plan_log/backend.md 7회차 "안 하기로 한 것")
    originals_dir: Path = PROJECT_ROOT / "original_files"

    # 바이트 단위 업로드 상한
    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024


# 설정 인스턴스를 만들어 반환. 앱 기동 시 한 번만 호출한다
def get_settings() -> Settings:
    return Settings()
