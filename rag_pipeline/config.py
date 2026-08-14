"""파이프라인 설정. .env 에서 읽되 코드에서 직접 넘겨 덮어쓸 수도 있다."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# 프로젝트 루트 (rag_pipeline/ 의 부모). cwd와 무관하게 .env를 찾기 위해 사용
PROJECT_ROOT = Path(__file__).resolve().parents[1]


# 콘텐츠 유형별로 어떤 핸들러를 쓸지, 외부 API 비용을 어디서 끊을지 결정하는 설정
class ContentRoutingConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- 외부 API 키 (없으면 자동으로 로컬 폴백) ---
    openai_api_key: str | None = None
    llama_cloud_api_key: str | None = None

    # --- 표 라우팅 ---
    # PDF/이미지의 표를 무엇으로 파싱할지. llamaparse 실패 시 항상 pymupdf로 폴백된다
    table_pdf_handler: Literal["llamaparse", "pymupdf"] = "llamaparse"
    # LlamaParse 파싱 티어: fast(규칙기반) < cost_effective < agentic < agentic_plus
    llamaparse_tier: str = "cost_effective"
    llamaparse_version: str = "latest"
    llamaparse_timeout_sec: float = 120.0

    # --- 이미지 라우팅 ---
    # hybrid: 글자 위주면 OCR, 그림 위주면 Vision / vision·ocr: 강제 / skip: 이미지 무시
    image_handler: Literal["hybrid", "vision", "ocr", "skip"] = "hybrid"
    vision_model: str = "gpt-4.1-mini"
    # 문서 하나당 Vision 호출 상한. 비용 폭주 방지
    max_vision_calls_per_doc: int = 30
    # 이 크기(px) 미만 이미지는 로고·아이콘·구분선으로 보고 건너뛴다
    min_image_width: int = 100
    min_image_height: int = 100

    # --- OCR ---
    ocr_lang: str = "kor+eng"
    # Tesseract 실행파일 경로. PATH에 있으면 비워둔다
    tesseract_cmd: str | None = None
    # 페이지 분할 모드. 기본값 3(자동)은 한글 한 줄을 세로로 오인식하는 일이 잦다.
    # 6(균일한 텍스트 블록)이 문서 스캔본에서 눈에 띄게 정확하다
    ocr_psm: int = 6
    # 한글은 이 옵션이 없으면 음절마다 공백이 끼어든다.
    # "주 식 회 사 한 국" -> "주식회사 한국" 으로 교정되며, 임베딩·검색 품질에 직결된다
    ocr_preserve_spaces: bool = True
    # hybrid 판정 기준: OCR 문자 수와 평균 신뢰도가 둘 다 이 값 이상이면 글자 위주로 본다
    ocr_min_chars: int = 10
    ocr_min_confidence: float = 60.0

    # --- PDF ---
    # 페이지 텍스트가 이 글자 수 미만이면 스캔본으로 보고 페이지 전체를 이미지로 처리.
    # 너무 높이면 제목만 있는 표지·간지 페이지까지 OCR로 보내 비용을 낭비한다.
    # 진짜 스캔본은 추출 글자가 0에 가깝다
    pdf_scan_text_threshold: int = 10
    # 스캔 페이지 렌더링 해상도
    pdf_render_dpi: int = 300

    # --- 캐시 ---
    # 같은 표·이미지를 재업로드해도 외부 API를 다시 호출하지 않게 한다
    cache_enabled: bool = True
    cache_dir: Path = PROJECT_ROOT / ".cache" / "content"


# 로더가 뽑은 텍스트를 어디까지 정리할지 결정하는 설정
class CleanerConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 제어문자(Cc)와 보이지 않는 서식문자(Cf)를 지운다. PDF 는 글머리 기호를 U+0001·U+0007
    # 같은 제어 코드포인트로 떨어뜨려 본문 한가운데 박아 넣는다(실측: AI 가이드 PDF 243개).
    # 표 마크다운의 행 구분자인 \n 과 탭은 남기므로 표가 깨지지 않는다
    remove_control_chars: bool = True


# 적재 파이프라인 전체 설정. 이후 splitter/embedding 설정이 여기 추가된다
class IngestConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    max_upload_size_mb: int = 50
    # Excel 시트당 읽을 최대 행 수. 거대한 시트가 메모리를 잡아먹는 것을 막는다
    max_excel_rows: int = 5000

    # 콘텐츠 라우팅 설정은 별도 객체로 분리해 핸들러들에게만 넘긴다
    @property
    def content(self) -> ContentRoutingConfig:
        if not hasattr(self, "_content"):
            object.__setattr__(self, "_content", ContentRoutingConfig())
        return self._content  # type: ignore[attr-defined]

    # 정리 단계 설정. content 와 같은 lazy 형태라 테스트에서 _cleaner 로 덮어쓸 수 있다
    @property
    def cleaner(self) -> CleanerConfig:
        if not hasattr(self, "_cleaner"):
            object.__setattr__(self, "_cleaner", CleanerConfig())
        return self._cleaner  # type: ignore[attr-defined]


# 기본 설정 인스턴스를 만들어 반환. 테스트에서는 직접 생성해 값을 덮어쓴다
def default_config() -> IngestConfig:
    return IngestConfig()
