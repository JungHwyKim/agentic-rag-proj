"""웹 계층 설정. 파싱 관련 설정은 rag_pipeline.config 가 따로 들고 있다."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from rag_pipeline.config import PROJECT_ROOT, EmbeddingConfig

# 그림자 색인이 쓰는 임베딩 설정. **2026-08-25 에 갈아타기 전의 기본값 그대로다.**
#
# 설정이 아니라 상수인 것이 요점이다 — 고를 수 있게 열면 합법값이 하나뿐인 축이 하나 더
# 생기고, 그것이 `Settings.shadow_index_enabled` 주석이 말하는 그 잣대다. 켜고 끄는 것만
# 설정이고 **무엇을 쌓을지는 이 회차가 정했다.**
#
# `openai_api_key` 를 안 적는다 — `EmbeddingConfig()` 가 `.env` 에서 읽는다. 키가 없으면
# 그림자만 실패하고 주 색인은 그대로 성공한다(pipeline.py `_index_shadow`)
SHADOW_EMBEDDING = EmbeddingConfig(
    embedding_provider="openai",
    embedding_model="text-embedding-3-small",
)


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

    # 색인할 때마다 **갈아타기 전 임베더로 한 벌 더** 넣을지.
    #
    # **모델 축이 아니라 스위치 하나다.** `SHADOW_EMBEDDING` 이 갈아타기 전 기본값으로
    # 못 박혀 있고 설정으로 못 고른다 — "N개 모델을 고른다" 로 열면 합법값이 하나뿐인 축이
    # 또 생긴다(다중 임베더 융합이 그래서 죽었다). **걷는 것은 이 불리언을 끄는 것 하나다.**
    #
    # **왜 켜 두나**: 지금 정답 질의 세트가 tests/fixtures/ 문서 4개에만 붙어 있어, 운영에
    # 올라오는 문서로는 두 모델을 견줄 수가 없다. 그림자는 **나중에 견주고 싶어졌을 때
    # 재색인 없이 되게 해 주는 보험**이고, 지금 옛 494 point 가 원본이 없어 못 되살리는 것이
    # 그 보험이 막으려는 상황이다. 걷을 조건은 **새 문서에 정답 질의가 붙을 때**다
    # (plan_log/embedding.md 5회차 미결)
    shadow_index_enabled: bool = True

    # 실사용 질의를 한 줄씩 남길지, 그리고 어디에.
    #
    # **기본값이 켜짐인 것이 요점이다.** 이 로그가 있어야 답변 캐시를 켤지 정할 수 있는데
    # (`ANSWER_CACHE_MODE` · plan_log/시맨틱-캐시.md 6회차), 꺼둔 채로는 아무것도 안 쌓여
    # 그 판단이 영영 안 선다. 관측은 켜져 있어야 관측이다.
    #
    # **불리언을 따로 두는 이유**: 경로를 빈 문자열로 끄게 하면 `Path("")` 가 `.` 이 되어
    # 조용히 저장소 루트에 쓴다. `shadow_index_enabled` 가 같은 클래스에서 이미 쓰는 모양이다.
    #
    # 상한도 자동 정리도 없다 — `originals_dir` 과 같은 판단이고, `data/` 는 gitignore 다.
    # 남는 것이 사용자 질문 문자열이라 **테스트가 실제 경로를 밟으면 안 된다**:
    # `tests/api/conftest.py` 의 autouse 픽스처가 `originals_dir` 과 함께 덮는다
    query_log_enabled: bool = True
    query_log_path: Path = PROJECT_ROOT / "data" / "queries.jsonl"

    # 바이트 단위 업로드 상한
    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024


# 설정 인스턴스를 만들어 반환. 앱 기동 시 한 번만 호출한다
def get_settings() -> Settings:
    return Settings()
