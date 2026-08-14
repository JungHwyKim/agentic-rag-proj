"""웹 계층 설정. 파싱 관련 설정은 rag_pipeline.config 가 따로 들고 있다."""

from __future__ import annotations

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
    # 프론트엔드 개발 서버에서 호출할 수 있도록 허용할 오리진
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:5173"]

    # 바이트 단위 업로드 상한
    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024


# 설정 인스턴스를 만들어 반환. 앱 기동 시 한 번만 호출한다
def get_settings() -> Settings:
    return Settings()
