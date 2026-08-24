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
    # 기본값이 pymupdf 인 이유는 실측이다(2026-08-18, plan_log/표-경로.md 1회차).
    # 같은 문서를 두 경로로 청킹해 보니 LlamaParse 는 마크다운이 아니라 HTML 을 돌려주고,
    # 그러면 metadata["rows"] 를 못 얻어 splitter 가 헤더 반복을 못 하고 태그 한가운데를
    # 글자 단위로 자른다 — 시행령 PDF 표청크가 24→62 로 불고 그중 16개가 여는 태그 없는
    # 조각이었다. 무과금 pymupdf 쪽이 유효한 마크다운 표에 rows 까지 갖고 있다.
    # llamaparse 경로는 지우지 않았다. HTML→rows 복원이 붙으면 다시 볼 값이다.
    #
    # `hybrid` 는 **승급**이다(2026-08-22, plan_log/표-경로.md 11회차). pymupdf 로 뽑되
    # 빈 칸에 벡터 드로잉이 있는 표만 Vision 으로 올려 **그 빈 칸만** 채운다. 표를 다시
    # 읽는 것이 아니라 메우는 것이라 병합 셀·되붙이기·쪽 경계 결과가 그대로 남는다.
    # 신호가 필요한 이유: 체크 표시가 이미지도 텍스트도 아니라 extract() 로는 원리적으로
    # 못 얻는다(AI가이드 12~17쪽). 기본값이 pymupdf 인 것은 그대로다 — 과금 경로다
    table_pdf_handler: Literal["llamaparse", "pymupdf", "hybrid"] = "pymupdf"
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
    # 쪽 위에 **표시되는** 크기(pt) 기준. 위 픽셀 자[尺]와 서로를 대신하지 못한다 —
    # 12.8x15.2pt 장식 글리프가 115x127px 로 저장돼 픽셀 필터를 그냥 통과한다(실측).
    # 20pt 는 "16.7 이하가 전부 OCR 0~1자"에서, 8:1 은 진짜 내용 최대 5.9:1 과 띠 최소
    # 10.0:1 사이 1.7배 틈에서 나왔다. 두 신호가 각자 다른 것을 잡는다 — 20pt 는 정사각
    # 글리프를, 비율은 크고 납작한 제목 띠를 잡는다. 근거는 plan_log/이미지-경로.md 2회차
    min_image_display_pt: float = 20.0
    max_image_aspect_ratio: float = 8.0

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

    # 연속된 공백·탭을 한 칸으로 줄이고 줄 앞뒤 공백을 없앤다. BPE 토크나이저는 " " 와 "  " 를
    # 다르게 쪼개므로 같은 문장이 다른 벡터가 된다(실측: HWPX 89요소 · PDF 13요소)
    normalize_whitespace: bool = True

    # 쪽마다 되풀이되는 문서 제목·기관명을 지운다. 그대로 두면 그 쪽들에서 나오는 모든 청크에
    # 제목이 딸려 들어가 검색 결과가 엉킨다(실측: AI 가이드에서 25개 쪽의 첫 요소가 동일)
    remove_repeated_headers: bool = True
    # 머리말로 보려면 최소 이만큼의 쪽에 나와야 한다
    header_min_pages: int = 3
    # 그리고 '첫 등장~마지막 등장' 폭이 전체 쪽수의 이 비율 이상이어야 한다.
    # 등장 '횟수' 비율을 안 쓰는 이유: 로더의 쪽 수가 틀리면(같은 문서를 HWPX 97쪽 / PDF 28쪽으로
    # 센다) 분모가 흔들려 판정이 뒤집힌다. 폭 비율은 분자·분모가 함께 부풀어 그 오류에 강하다
    # (실측: 진짜 머리말 HWPX 93.8% / PDF 89.3%, 오탐 후보 29.9~45.4%)
    header_min_span_ratio: float = 0.60


# 정리된 요소를 검색 단위(청크)로 묶고 쪼갤 때 쓰는 설정
class SplitterConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 청크 한 개의 상한. **임시값이고 실측 전이다.**
    # 실측상 텍스트 요소는 제일 긴 것이 554자라 이 상한이 텍스트에서는 거의 발화하지 않는다.
    # 실제로 걸리는 곳은 표다(시행령 안내서의 67행 표가 3,243자). 근거 있는 값으로 바꾸려면
    # 정답 질의 세트가 필요하다 — plan_log/평가-세트.md
    max_chunk_chars: int = 1200

    # 상한을 넘겨 쪼갠 구간에만 주는 겹침. **섹션 경계에는 주지 않는다** —
    # 구조 경계로 자른 뒤 겹침을 얹으면 애써 지킨 의미 단위를 다시 흐린다.
    # 표에도 주지 않는다(행이 중복 색인된다)
    chunk_overlap_chars: int = 150

    # 제목 경로를 청크 본문 맨 앞에 붙인다. 표 청크와 분할 조각이 자기가 어느 절 소속인지
    # 본문만으로도 알 수 있어야 sparse 검색에서 제목 어휘가 걸린다
    include_heading_in_text: bool = True


# 청크 텍스트를 벡터로 만들 때 쓰는 설정. 비용이 붙는 유일한 항목은 토큰이다
class EmbeddingConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ContentRoutingConfig 와 같은 .env 키를 읽는다. 중복이 아니라 각 설정 객체가 독립이라
    # 각자 읽는 것이다 — 임베딩만 끄고 Vision 은 살리는 식의 조합이 가능해야 한다
    openai_api_key: str | None = None

    # 어느 구현체로 임베딩하나. **구현체가 둘이 되어서 여는 설정이다** — 하나뿐일 때는
    # 합법값이 하나뿐인 죽은 설정이라 일부러 안 만들었다(plan_log/embedding.md 3회차).
    # `local` 은 sentence-transformers 경로이고 `requirements-local.txt` 가 필요하다.
    # **키가 없어도 도는 갈래라** tests/conftest.py 의 offline_config 가 이 값을 못 박는다
    embedding_provider: Literal["openai", "local"] = "openai"

    # **임시값이고 실측 전이다.** 모델 간 한국어 검색 품질은 지금 못 잰다 — 정답 질의 세트가
    # 없다(plan_log/평가-세트.md). 싼 쪽에서 시작하고 캐시 salt 로 교체 가능하게 뒀다.
    # `provider="local"` 이면 HuggingFace 리포지터리 이름이다(예: nlpai-lab/KURE-v1)
    embedding_model: str = "text-embedding-3-small"

    # None 이면 모델 기본 차원. text-embedding-3-* 는 이 값으로 차원을 줄일 수 있어
    # "차원은 모델이 정한다"가 반만 맞다 — 설정값이므로 캐시 salt 에 함께 들어간다.
    #
    # **뜻은 provider 를 안 탄다 — "차원을 줄여라" 하나다.** OpenAI 는 할 수 있고 bge-m3
    # 계열은 MRL 미학습이라 못 하므로, `provider="local"` 에서 값이 오면 예외로 거절한다.
    # 앞 N 차원을 잘라 재정규화하는 것은 품질이 **조용히** 나빠지는 길이라 안 한다
    embedding_dimensions: int | None = None

    # 한 번에 보낼 텍스트 개수. 실측상 문서당 청크가 45~102개라 대부분 1회 호출로 끝난다.
    # 최악의 청크가 1,201토큰이므로 100개를 묶어도 요청당 12만 토큰 — 요청 상한에 여유가 있다
    embedding_batch_size: int = 100

    # 문서 하나당 토큰 상한. **호출 수가 아니라 토큰인 이유**: 배치라 청크 500개가 5회 호출로
    # 끝나 max_vision_calls_per_doc 를 그대로 베끼면 아무것도 안 막는다. 돈은 토큰이다.
    # 실측 최대가 51,341토큰(학교기록물, 청크 102개)이라 그 약 4배로 잡았다. 그 위는 400청크급
    # 문서인데, 이 코퍼스에서는 사고를 의심할 구간이다
    max_embedding_tokens_per_doc: int = 200_000

    # 같은 텍스트를 두 번 임베딩하지 않는다. 콘텐츠 캐시와 수명이 달라 디렉터리를 가른다
    embedding_cache_enabled: bool = True
    embedding_cache_dir: Path = PROJECT_ROOT / ".cache" / "embedding"


# 벡터를 어디에 저장하고 어떤 컬렉션에 넣을지 결정하는 설정
class IndexingConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # qdrant 로컬 파일 모드의 저장 경로. **서버가 아니라 파일인 이유**: 기본 pytest 가 키도
    # 서버도 없이 전부 통과해야 한다는 규약을 도커 전제로는 지킬 수 없다.
    # 테스트는 conftest 의 offline_config 가 tmp_path 로 덮어쓴다
    qdrant_path: Path = PROJECT_ROOT / ".qdrant"

    # 컬렉션 이름의 앞부분. 실제 이름은 여기에 모델·차원을 덧붙여 파생한다 —
    # 근거는 ingestion/indexing/qdrant_store.py 의 collection_name_for()
    collection_name: str = "documents"

    # 한 번에 보낼 point 수. 문서당 청크가 45~102개(embedding 실측)라 대부분 1회로 끝난다
    upsert_batch_size: int = 128


# 여러 검색 경로의 후보를 어디까지 모으고 어떻게 합칠지 정하는 설정. 과금 항목이 없다
class RetrievalConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 각 경로(dense·BM25)에서 뽑아 RRF 로 합칠 후보 수. **임시값이고 실측 전이다** —
    # Phase 2 의 dense vs hybrid 측정 뒤에 재판정한다(plan_log/검색-생성.md 2회차).
    # 50 은 새로 지어낸 수가 아니라 schemas/retrieval.py docstring 이 이미 쓴 수이고,
    # 문서당 청크가 45~104개이므로 대략 한 문서치다.
    # **두 경로에 같은 값을 쓴다** — 한쪽만 길면 RRF 가 긴 쪽에 유리해져, 기울기가 숫자가
    # 아니라 구조에서 나오고 원인을 못 짚는다
    candidate_k: int = 50

    # RRF 의 순위 완충 상수. score = Σ 1/(rrf_k + rank) 의 그 값이다.
    # **우리가 고른 수가 아니라 RRF 원 논문 값이라** 분포의 틈을 찾을 필요가 없었다.
    # 크면 상위권 순위차를 뭉개고 작으면 1등을 과대평가한다
    rrf_k: int = 60

    # top_k 는 여기 없다. 지금 retrieve() 인자이자 GenerationConfig.generation_top_k 에서
    # 오는데, 셋째 출처를 만들면 어느 것이 이겼는지 호출부마다 달라진다


# 검색된 청크로 답변을 만들 때 쓰는 설정. 질의당 LLM 1회가 비용의 전부다
class GenerationConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ContentRoutingConfig·EmbeddingConfig 와 같은 .env 키를 읽는다. 중복이 아니라 각 설정
    # 객체가 독립이라 각자 읽는 것이다 — 생성만 끄고 임베딩은 살리는 조합이 가능해야 한다
    openai_api_key: str | None = None

    # **임시값이고 실측 전이다.** 답변 품질을 재는 자[尺]가 아직 없다 — evaluation/ 의 60건
    # 세트는 gold_spans 가 정답 "문자열"이라 검색을 재지 생성을 못 잰다(plan_log/backend.md).
    # Vision 과 같은 모델을 쓰는 것은 우연이 아니라 싼 쪽에서 시작한다는 같은 판단이다
    generation_model: str = "gpt-4.1-mini"

    # 답변 길이 상한. 한국어는 청크에서 문자 ≈ 토큰이라(CLAUDE.md) 대략 이만큼의 글자다
    generation_max_tokens: int = 1024

    # 근거에서 벗어나지 않는 것이 목적이라 낮게 둔다. 0 이 아닌 이유는 표·목록을 그대로
    # 베끼는 대신 질문에 맞게 추리는 여지를 남기기 위해서다
    generation_temperature: float = 0.2

    # 컨텍스트에 실을 청크 수. **임시값이다** — baseline 이 hit@1 0.150 / @5 0.400 /
    # @10 0.483 / @20 0.617 이라 늘리면 오르는 것은 알지만, 대가(컨텍스트 토큰·답변 품질)를
    # 재는 쪽이 없어 값을 못 정한다. plan_log/indexing.md 의 top_k 미결과 한 줄이다
    generation_top_k: int = 5


# 비슷한 질문에 이전 답변을 되돌려줄 때 쓰는 설정. **과금을 줄이는 쪽이라 켜는 것이 비용이
# 아니라 틀리는 것이 비용이다** — 임계값이 헐거우면 다른 질문에 옛 답을 준다
class AnswerCacheConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 캐시 컬렉션 이름의 앞부분. 실제 이름은 IndexingConfig.collection_name 과 **같은 함수**
    # (qdrant_store.collection_name_for)로 파생해 모델·차원이 뒤에 붙는다. 고정 이름을 쓰면
    # 임베딩 모델을 바꿨을 때 옛 모델 벡터가 든 캐시에 새 모델 질의 벡터로 유사도를 재게 된다
    answer_cache_collection_name: str = "qa_cache"

    # 이 값 이상이면 "같은 질문"으로 본다. **기본값이 없는 것이 설계다.**
    # None 이면 get_answer_cache() 가 None 을 돌려줘 캐시가 통째로 꺼진다 — 이 저장소의
    # 임계값은 전부 분포의 틈에서 나왔는데(표 되붙이기 12pt, 표시 면적비 0.08 vs 1.00)
    # 이 축은 아직 안 쟀다. 근거 없는 수를 기본값으로 두면 그 사실이 조용히 묻힌다.
    # 재는 방법은 plan_log/시맨틱-캐시.md 의 "임계값의 근거" 절
    answer_cache_threshold: float | None = None


# 적재 파이프라인 전체 설정
class IngestConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    max_upload_size_mb: int = 50
    # Excel 시트당 읽을 최대 행 수. 거대한 시트가 메모리를 잡아먹는 것을 막는다
    max_excel_rows: int = 5000

    # 글자 크기를 보고 문단을 제목(TITLE)으로 승격한다. PDF·HWPX 는 제목 표시를 파일에 안 남겨
    # 이게 없으면 splitter 가 청크를 끊을 경계를 못 얻는다(판정 근거는 loader/document/headings.py).
    # ContentRoutingConfig 가 아니라 여기 있는 이유: 저쪽은 '어떤 핸들러를 쓰고 외부 API 비용을
    # 어디서 끊나'의 집합인데 제목 판정은 둘 다 아니다. 같은 성격인 max_excel_rows 옆이 맞다
    detect_headings: bool = True

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

    # 청킹 단계 설정. 같은 lazy 형태라 테스트에서 _splitter 로 덮어쓸 수 있다
    @property
    def splitter(self) -> SplitterConfig:
        if not hasattr(self, "_splitter"):
            object.__setattr__(self, "_splitter", SplitterConfig())
        return self._splitter  # type: ignore[attr-defined]

    # 임베딩 단계 설정. 같은 lazy 형태라 테스트에서 _embedding 으로 덮어쓸 수 있다
    @property
    def embedding(self) -> EmbeddingConfig:
        if not hasattr(self, "_embedding"):
            object.__setattr__(self, "_embedding", EmbeddingConfig())
        return self._embedding  # type: ignore[attr-defined]

    # 색인 단계 설정. 같은 lazy 형태라 테스트에서 _indexing 으로 덮어쓸 수 있다
    @property
    def indexing(self) -> IndexingConfig:
        if not hasattr(self, "_indexing"):
            object.__setattr__(self, "_indexing", IndexingConfig())
        return self._indexing  # type: ignore[attr-defined]

    # 검색 단계 설정. 같은 lazy 형태라 테스트·평가에서 _retrieval 로 덮어쓸 수 있다.
    # **적재 설정 객체에 붙는 것이 어색해 보이지만 generation 이 이미 그렇다** — 검색만 밖에
    # 두면 이유 없는 비대칭이 되고, offline_config 픽스처도 둘로 갈린다
    @property
    def retrieval(self) -> RetrievalConfig:
        if not hasattr(self, "_retrieval"):
            object.__setattr__(self, "_retrieval", RetrievalConfig())
        return self._retrieval  # type: ignore[attr-defined]

    # 생성 단계 설정. 같은 lazy 형태라 테스트에서 _generation 으로 덮어쓸 수 있다.
    # **그 덮어쓰기가 offline_config 의 실키 차단이다** — GenerationConfig() 가 .env 를 직접
    # 읽으므로, lazy 가 아니면 픽스처가 키를 지운 객체를 꽂을 자리 자체가 없다
    @property
    def generation(self) -> GenerationConfig:
        if not hasattr(self, "_generation"):
            object.__setattr__(self, "_generation", GenerationConfig())
        return self._generation  # type: ignore[attr-defined]


    # 답변 캐시 설정. 같은 lazy 형태라 테스트에서 _answer_cache 로 덮어쓸 수 있다.
    # **generation 과 같은 이유로 lazy 여야 한다** — AnswerCacheConfig() 가 .env 를 직접
    # 읽으므로, 안 덮으면 개발자 머신의 임계값이 픽스처에 실려 테스트가 사람마다 달라진다
    @property
    def answer_cache(self) -> AnswerCacheConfig:
        if not hasattr(self, "_answer_cache"):
            object.__setattr__(self, "_answer_cache", AnswerCacheConfig())
        return self._answer_cache  # type: ignore[attr-defined]


# 기본 설정 인스턴스를 만들어 반환. 테스트에서는 직접 생성해 값을 덮어쓴다
def default_config() -> IngestConfig:
    return IngestConfig()
