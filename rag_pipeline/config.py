"""파이프라인 설정. .env 에서 읽되 코드에서 직접 넘겨 덮어쓸 수도 있다."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 프로젝트 루트 (rag_pipeline/ 의 부모). cwd와 무관하게 .env를 찾기 위해 사용
PROJECT_ROOT = Path(__file__).resolve().parents[1]


# `.env` 의 **빈 값**을 `None` 으로 읽는다. temperature 처럼 «안 보낸다» 가 합법 상태인
# 설정에 붙인다.
#
# **이것이 없으면 `.env` 로는 그 상태를 아예 못 만든다** — 2026-08-29 실측으로 확인했다.
# 줄을 지우거나 주석 처리하면 **코드 기본값**(0.2 · 0.0)이 살아나고, 빈 값·`null`·`None` 은
# 전부 `ValidationError` 다. 그래서 `gpt-5.6-luna` 로 바꾸면 `temperature=0.2` 가 그대로
# 나가 **400** 이 났다(`Unsupported value: 'temperature' does not support 0.2`).
# **한때 문서가 «줄을 주석 처리한다» 로 적어 두었는데 그 처방은 틀렸다** — 기본값이 `None`
# 이 아닌 한 주석 처리는 «안 보냄» 이 아니라 «기본값을 쓴다» 다.
#
# **기본값을 `None` 으로 내리는 안은 기각했다** — 0.2 는 *"근거를 벗어나지 않되 베끼지는
# 않는다"*, 0.0 은 *"판정은 재현이 값어치"* 로 고른 값이라, 기본을 지우면 그 근거가 사라진다.
# 빈 값을 쓰는 쪽은 `.env` 를 **쓴 사람이 명시**한 것이라 그 사고가 없다
# **아무 `float | None` 에나 붙이지 않는다. 조건이 둘이다** — ① 「안 보냄」이 합법 상태이고
# ② **기본값이 `None` 이 아니라서** 줄을 지우는 것으로는 그 상태를 못 만들 때.
#
# 둘 다 아닌 자리에 붙이면 뜻이 같아 보이는데 **축이 하나 는다.** 실제 사례 둘이 저장소에 있다 —
# `ANSWER_CACHE_THRESHOLD` 는 **기본값이 이미 `None`** 이라 주석 처리로 꺼지므로 ②가 아니고,
# `TAVILY_API_KEY` 는 **빈 값이 곧 「꺼짐」**이라(`''` 가 falsy) ①이 아니다. 뒤엣것에 붙이면
# *꺼짐* 이 *안 보냄* 으로 바뀌면서 합법값이 하나 늘어난다.
def _blank_means_none(value: object) -> object:
    if isinstance(value, str) and not value.strip():
        return None
    return value


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

    # 무료 OCR 을 몇 갈래로 동시에 돌리나. **`route_all()` 의 병렬 구간에만 쓴다** —
    # 유료(Vision·LlamaParse)는 일부러 직렬이고 그 이유는 `loader/content/router.py` 에 있다.
    #
    # **8은 한계값이 아니라 무릎이다.** 실측(2026-08-24, 47쪽 전면 스캔본)으로 워커를 늘릴 때
    # 직전 대비 -102.1/-62.1/-23.8/**-9.5**초이고, 16이 42.3초로 8(51.8초)보다 18% 빠르지만
    # 같은 uvicorn 이 질의도 받는다. 근거는 `verify_log/2026-08-24-ocr-병렬화.md`.
    #
    # **전에는 일부러 안 열었다** — 그때 근거가 *"합법값이 실제로 갈리는 두 번째 경로가
    # 생긴 뒤에 연다"* 였다. 2026-08-26 에 그 경로가 생겼다: **발열**이다. Tesseract 는
    # 서브프로세스라 `TORCH_NUM_THREADS` 가 안 먹고 GPU 로도 못 옮겨서, 적재 쪽 CPU 부하를
    # 낮출 손잡이가 이것 하나뿐이다. 낮추면 그만큼 느려지는 것이 위 실측의 대가다
    ocr_max_workers: int = 8

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
    # `local` 은 sentence-transformers 경로다.
    #
    # **기본값이 `local` 인 것이 이 설정의 위험한 쪽이다** — 로컬 갈래는 **키가 없어도 돌아서**
    # "키를 지운다" 라는 지금까지의 방어가 안 통한다. 예전에는 `torch` 가 기본 설치에 없는 것이
    # 두 번째 겹이었는데 **본체로 옮기면서 그 겹이 사라졌다.** 그래서 지금은
    # `tests/conftest.py` 의 `offline_config` 가 이 값을 못 박는 것이 **유일한 방어**다
    embedding_provider: Literal["openai", "local"] = "local"

    # **실측으로 고른 값이다.** 같은 정답 세트 60건에서 `hit@10` 이 KURE-v1 **0.8500** vs
    # `text-embedding-3-small` **0.4833** 이고(miss 9 vs 31, `MRR` 0.5078 vs 0.2530),
    # 그래서 2026-08-25 에 갈아탔다 — 전에 여기 적혀 있던 *"임시값이고 실측 전"* 은 끝났다.
    # 수치는 `verify_log/2026-08-25-융합-되돌리기.md`, 판단은 `plan_log/embedding.md` 5회차.
    # `provider="local"` 이라 HuggingFace 리포지터리 이름이다
    embedding_model: str = "nlpai-lab/KURE-v1"

    # 로컬 모델을 어느 장치에 올리나. **`provider="openai"` 면 안 쓴다.**
    #
    # **기본이 `cpu` 인 것은 재현성이다** — cu 빌드를 깐 머신에서만 GPU 로 가면 같은 명령이
    # 다른 벡터를 내고 *"baseline 은 재현될 때만 기준선"* 이 무너진다. 그래서 자동 감지
    # (`cuda if available`)를 **일부러 안 한다** — 조용히 갈리는 것이 이 축의 사고다.
    # GPU 로 가려면 이 값을 명시로 바꾸고 PyPI 밖 `--index-url` 로 cu 빌드 torch 를 깐다
    # (PyPI 휠은 CPU 빌드다).
    #
    # **캐시 salt·컬렉션 이름에는 안 들어간다 — 이제 실측이다.** 2026-08-26 에 CUDA 머신이
    # 생겨 예약해 뒀던 측정을 했다: 실제 청크 414개를 두 장치로 인코딩해 `1-cos` 최대
    # **1.354e-09** · 성분 최대 절대차 5.728e-06 · 노름 양쪽 1.0000000 이다(합성 입력
    # 200개에서는 `1-cos` 가 정확히 0이었다 — 실제 청크에서는 0이 아니지만 float32 오차
    # 수준인 것은 같다). 그래서 **넣지 않는 것이 확정**이고, `verify_log/2026-08-26-
    # 스레드-발열-GPU.md` 가 근거다.
    #
    # 넣었다면 GPU 로 옮기는 순간 임베딩 캐시가 전량 미스가 되고 컬렉션이 갈려 옛 색인이
    # 고아가 된다 — **얻는 것 없이** 그 대가만 치르는 셈이었다.
    # `rerank_device` 는 반대로 salt 에 **들어간다**: 저쪽은 순위가 같다는 것만 확인했지
    # 점수가 같다는 것은 안 봤고, *같다는 것을 확인한 것*과 *다를 수 없다는 것*은 다르다
    embedding_device: str = "cpu"

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


# 후보 풀을 다시 정렬할 때 쓰는 설정. **RetrievalConfig 와 갈라 둔 것은 과금 축이 다르기
# 때문이다** — 저쪽은 과금 항목이 없어 `EVAL_SETTINGS["retrieval"]` 이 통째로 고정하는데,
# 섞으면 리랭커를 안 켠 dense·hybrid 실행에도 모델 이름과 과금 설정이 실린다
class RerankConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ContentRoutingConfig·EmbeddingConfig·GenerationConfig 와 같은 .env 키를 읽는다.
    # 중복이 아니라 각 설정 객체가 독립이라 각자 읽는 것이다
    openai_api_key: str | None = None

    # Cohere 갈래의 키. `openai_api_key` 와 **별개 필드인 것이 요점이다** — 한 칸을 돌려쓰면
    # provider 를 바꿀 때 남의 키를 물고 401 을 폴백으로 삼킨다(경고만 남고 에러가 안 난다)
    cohere_api_key: str | None = None

    # 어느 구현체로 다시 정렬하나. **구현체가 셋이라 여는 설정이다** — `embedding_provider`
    # 와 같은 잣대이고, 하나뿐일 때는 합법값이 하나뿐인 죽은 설정이라 안 만든다.
    # `local` 은 sentence-transformers CrossEncoder, `openai` 는 LLM 채점기(listwise),
    # `cohere` 는 전용 리랭킹 API 다 — **뒤 둘은 같은 '유료'가 아니라 종류가 다르다.**
    # **키가 없어도 도는 갈래라** tests/conftest.py 의 offline_config 가 이 값을 못 박는다
    rerank_provider: Literal["openai", "local", "cohere"] = "openai"

    # 무슨 모델로. **기본값이 없는 것이 설계다** — 비어 있으면 `get_reranker()` 가 None 을
    # 돌려줘 리랭커가 통째로 꺼진다(`answer_cache_threshold` 와 같은 모양).
    # 여기서 켜는 순간 **질의당 LLM 호출이 1회에서 2회가 되므로**, 값을 하는지 재기 전에
    # 과금이 먼저 붙는 것을 막는다. 재는 방법은 plan_log/검색-생성.md 5회차.
    # `provider="openai"` 면 `gpt-4.1-mini` 꼴, `local` 이면 HuggingFace 리포지터리 이름이다
    rerank_model: str | None = None

    # 로컬 구현체를 어느 장치에 올리나. **`embedding_device` 와 이름 규칙·기본값을 맞춘다** —
    # 같은 함정이라서다. 안 못 박으면 CPU 와 GPU 가 미세하게 다른 점수를 내, 같은 명령이
    # 머신마다 다른 순위를 만들고 비교표가 에러 없이 흔들린다. provider="openai" 면 안 쓴다
    rerank_device: str = "cpu"

    # 후보를 몇 개까지 먹이나. **`candidate_k` 와 같은 수지만 축이 다르다** — 저쪽은 각 경로가
    # *뽑는* 폭이고 이쪽은 리랭커가 *보는* 폭이다. 지금 같은 값인 것은 우연이 아니라
    # "풀 전부를 먹인다"가 결정이어서다: 20으로 자르면 실측으로 후보 #30·#26 에 있던 정답을
    # 버린다(verify_log/2026-08-24-리랭커-여지.md). 갈라야 할 근거가 나오면 그때 값이 갈린다
    rerank_candidates: int = 50

    # 응답 길이 상한. 후보당 `{"i": N, "score": M}` 한 줄이라 후보 수에 선형이다.
    # **`openai` 갈래만 쓴다** — 아래 `rerank_max_tokens_per_doc` 과 이름이 닮았지만 축이 다르다
    rerank_max_tokens: int = 4096

    # `cohere` 갈래가 문서 하나를 몇 토큰까지 보내나. **SDK 기본값과 같은 값을 명시로 박는다** —
    # 이 코퍼스의 청크는 최대 약 1,200자·토큰이라(CLAUDE.md 의 *문자 ≈ 토큰*) 안 잘리지만,
    # SDK 기본값이 내려가면 **조용히 잘리기 시작하고 그것은 에러가 아니라 수치로만 드러난다.**
    # 자르면 안 되는 이유는 `openai_llm` 이 *청크를 안 자른다* 를 정한 것과 같다 —
    # 정답 span 이 마크다운 표의 행에 흩어져 있어 앞부분만 보면 헤더만 보인다.
    # **과금 단위와 헷갈리지 말 것**: 과금은 500 토큰마다 별개 문서로 세므로 이 값과 무관하다
    rerank_max_tokens_per_doc: int = 4096

    # 점수는 재현돼야 한다. 생성(0.2)과 달리 0 인 이유는 **추릴 여지를 남길 데가 없어서**다 —
    # 여기서 모델이 하는 일은 문장을 쓰는 것이 아니라 순서를 매기는 것 하나뿐이다
    rerank_temperature: float = 0.0

    # 같은 (질의, 후보 집합) 을 두 번 채점하지 않는다. 콘텐츠·임베딩 캐시와 수명이 달라
    # 디렉터리를 가른다. **평가를 여러 번 돌리는 것이 공짜가 되는 자리다**
    rerank_cache_enabled: bool = True
    rerank_cache_dir: Path = PROJECT_ROOT / ".cache" / "rerank"


# 1차 검색 결과를 보고 질의를 다시 쓰는 설정(PRF). RerankConfig·EvaluatorConfig 와 같은
# 모양인 이유도 같다: 켜지는 순간 질의당 LLM 호출이 하나 늘어, 값을 하는지 재기 전에 과금이
# 먼저 붙는 것을 막는 자리가 똑같다 (plan_log/검색-생성.md 22회차)
class RewriteConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 다른 설정 객체와 같은 .env 키를 읽는다. 중복이 아니라 각자 독립이라 각자 읽는 것이다
    openai_api_key: str | None = None

    # 무슨 모델로. **비어 있으면 `get_rewriter()` 가 None 을 돌려줘 통째로 꺼진다** —
    # `rerank_model`·`evaluator_model` 과 글자 그대로 같은 모양이고, 저장소에는 꺼진 채 커밋된다
    rewrite_model: str | None = None

    # 재작성 질의의 검색 결과를 원 질의와 어떻게 합치나. **합법값 둘이 실재해 죽은 설정이
    # 아니다** — `rerank_provider` 와 같은 잣대다.
    #
    # `fuse` 는 원 질의 갈래를 남기고 재작성 갈래를 RRF 에 더한다(7회차 로드맵).
    # `replace` 는 재작성 질의로만 검색한다 — **21회차 프로브가 잰 것이 이쪽이다**
    # (verify_log/2026-08-27-라벨-전수검수-재작성프로브.md 7절: *"프로브는 질의 문자열만
    # 바꾼 것"*). 즉 `sih-07` 102위 → 2위 는 `replace` 의 수치이고 `fuse` 의 것이 아니다.
    # 둘을 다 만든 이유가 그것이다 — 어느 쪽이 나은지를 추측이 아니라 측정으로 답한다
    rewrite_mode: Literal["fuse", "replace"] = "fuse"

    # 1차 검색 결과 몇 개를 재작성기에 보여주나. **10 은 21회차가 실제로 잰 폭이다** —
    # 그 회차가 "1차 상위 10 에 필요한 낱말이 있나"를 6건에서 확인했고, 겨냥하는 최악의
    # 질의(`sih-07`, 정답 102위)조차 상위 10 에 어휘가 다 있었다. 정답 청크를 못 건져도
    # **어휘는 이미 건져 놨다**는 것이 PRF 를 고른 근거라, 이 값이 그 근거의 폭이다
    rewrite_context_k: int = 10

    # 재작성은 재현돼야 한다. **`float | None` 이고 `None` 은 *보내지 않는다* 는 뜻이다** —
    # gpt-5 계열이 `temperature=0.0` 을 400 으로 거부하기 때문이고, `1.0` 을 대신 보내지
    # 않는 이유는 그것이 *0.0 이 재현을 위한 값이었다* 는 사실을 지우기 때문이다
    # (EvaluatorConfig.evaluator_temperature 와 같은 자리 · CLAUDE.md).
    # **`.env` 로 그 상태를 만들려면 줄을 주석 처리한다** — 빈 값은 파싱하다 죽는다
    rewrite_temperature: float | None = 0.0

    # 응답 길이 상한. 출력이 질문 문장 하나(`{"query": "..."}`)라 후보 수에 안 실린다 —
    # 후보당 한 줄을 받는 `rerank_max_tokens`(4096)와 축이 다르다
    rewrite_max_tokens: int = 256

    # 같은 (질의, 1차 후보) 를 두 번 다시 쓰지 않는다. 콘텐츠·임베딩·리랭킹 캐시와 수명이
    # 달라 디렉터리를 가른다. **여기가 두 모드를 견주는 것을 공짜로 만드는 자리다** —
    # 재작성 호출은 mode 보다 앞에서 일어나므로 fuse·replace 가 같은 항목을 읽는다
    rewrite_cache_enabled: bool = True
    rewrite_cache_dir: Path = PROJECT_ROOT / ".cache" / "rewrite"


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
    # 베끼는 대신 질문에 맞게 추리는 여지를 남기기 위해서다.
    #
    # **`None` 은 «인자를 안 보낸다» 는 뜻이다** — gpt-5 계열이 기본값(1) 말고는 400 이라
    # 그 모델로 가려면 이 값을 비워야 한다(`EvaluatorConfig.evaluator_temperature` 와 같은
    # 자리 · CLAUDE.md). **`1.0` 을 대신 적지 않는다** — 그건 기본값을 명시하는 것뿐이면서
    # *0.2 가 위 이유로 고른 값이었다* 는 사실을 지운다. **`.env` 에서는 줄을 주석 처리한다**
    # — 빈 값은 `''` 를 float 로 파싱하다 죽는다
    generation_temperature: float | None = 0.2

    # **`.env` 에서는 빈 값(`GENERATION_TEMPERATURE=`)이 «안 보냄» 이다** — 줄을 지우면
    # 위 기본값 0.2 가 살아나 gpt-5 계열에서 400 이 난다 (`_blank_means_none` 참고)
    _blank_temperature = field_validator("generation_temperature", mode="before")(
        _blank_means_none
    )

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

    # 무엇을 "같은 질문"으로 보나. **임계값과 다른 축이라 따로 둔다.**
    #
    # `semantic` 은 질문 벡터의 유사도를 재고 `answer_cache_threshold` 가 그 자[尺]다.
    # `exact` 는 **정규화한 문자열이 같은가**만 본다 — `cache_point_id_for()` 가 NFC + 공백
    # 정규화 uuid5 라 같은 질문이 늘 같은 자리이고, 그래서 자[尺]가 아예 필요 없다.
    #
    # **`threshold=1.0` 으로 흉내 내지 않는 이유**: 임계값은 *같은 벡터인가*를 재는 자인데
    # 완전 일치가 묻는 것은 *같은 문자열인가*다. 한 자로 둘을 재면 어느 쪽이 발화했는지
    # 코드가 못 말하고, 부동소수라 보장도 없다(실측 노름 0.999562~1.000417).
    #
    # **`exact` 는 임계값 없이도 켜진다** — 이 축에는 정할 값이 없기 때문이다.
    # 기본값이 `semantic` 이라 `.env` 를 안 건드리면 지금까지와 글자 그대로 같다
    # (임계값이 비어 있어 캐시가 통째로 꺼진 상태). 근거는 plan_log/시맨틱-캐시.md 6회차
    answer_cache_mode: Literal["semantic", "exact"] = "semantic"

    # 이 값 이상이면 "같은 질문"으로 본다. **기본값이 없는 것이 설계다.**
    # None 이면 get_answer_cache() 가 None 을 돌려줘 캐시가 통째로 꺼진다 — 이 저장소의
    # 임계값은 전부 분포의 틈에서 나왔는데(표 되붙이기 12pt, 표시 면적비 0.08 vs 1.00)
    # 이 축은 아직 안 쟀다. 근거 없는 수를 기본값으로 두면 그 사실이 조용히 묻힌다.
    # 재는 방법은 plan_log/시맨틱-캐시.md 의 "임계값의 근거" 절
    answer_cache_threshold: float | None = None


# 검색된 근거가 질문에 답하기에 충분한지 판정할 때 쓰는 설정. **켜는 축은 모델 이름 하나다** —
# 비면 get_evaluator() 가 None 을 돌려주고 그것이 곧 꺼진 상태다. RerankConfig 와 같은 모양인
# 이유도 같다: 켜지는 순간 질의당 LLM 호출이 1회에서 2회가 되므로, 값을 하는지 재기 전에
# 과금이 먼저 붙는 것을 막는 자리가 똑같다 (plan_log/검색-생성.md 11회차)
class EvaluatorConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # GenerationConfig 와 같은 .env 키를 읽는다. 중복이 아니라 각 설정 객체가 독립이라 각자
    # 읽는 것이다 — 판정만 끄고 생성은 살리는 조합이 가능해야 한다
    openai_api_key: str | None = None

    # 판정에 쓸 모델. **비면 통째로 꺼진다** — RERANK_MODEL 과 글자 그대로 같은 모양이고
    # 저장소에는 꺼진 채 커밋된다. 켜는 것은 혼동행렬을 재고 나서 .env 에 이름이 들어가는 순간이다
    evaluator_model: str | None = None

    # 판정은 재현이 값어치라 0 이다. 생성이 0.2 인 것과 갈리는 이유는 출력의 모양이다 —
    # 저쪽은 표·목록을 질문에 맞게 추리는 여지가 필요하지만 이쪽 출력은 세 상태 중 하나다.
    #
    # **None 은 "그 인자를 아예 안 보낸다"는 뜻이다.** gpt-5 계열은 0.0 을 400 으로 거부하고
    # 기본값(1)만 받는다(2026-08-27 luna 실측). 1.0 을 보내는 것으로 흉내 내지 않는 이유는
    # 그게 기본값을 명시하는 것뿐이면서 **0.0 이 재현을 위한 값이었다는 사실을 코드에서
    # 지워 버리기** 때문이다. 기본값이 0.0 그대로인 것이 요점이다 — gpt-4.1-mini 경로는
    # 글자 그대로 안 바뀐다.
    #
    # **`.env` 에서 None 을 만들려면 줄을 주석 처리한다.** `EVALUATOR_TEMPERATURE=` 처럼
    # 빈 값을 두면 `float | None` 이 `''` 를 파싱하다 ValidationError 로 죽는다
    # (`EMBEDDING_DIMENSIONS` 가 `.env.example` 에서 주석인 것과 같은 이유다)
    evaluator_temperature: float | None = 0.0

    # 위 `generation_temperature` 와 같은 자리다. **여기가 먼저 있었는데 결함도 먼저
    # 있었다** — 문서가 「줄을 주석 처리하면 안 보낸다」로 적어 둔 것이 틀렸고,
    # 주석 처리하면 기본값 0.0 이 살아나 gpt-5 계열에서 그대로 400 이었다 (2026-08-29 실측)
    _blank_temperature = field_validator("evaluator_temperature", mode="before")(
        _blank_means_none
    )

    # verdict 한 줄 + 사유 한두 문장. **임시값이다** — 사유가 잘리는지는 첫 혼동행렬의
    # reasoning 을 사람이 읽고 정한다.
    # **API 인자 이름은 `max_completion_tokens` 다** — gpt-5 계열이 `max_tokens` 를 400 으로
    # 거부한다. 설정 이름을 안 따라 바꾸는 것은 이게 우리 축의 이름이라서다: 바꾸면 `.env` 의
    # `EVALUATOR_MAX_TOKENS` 가 `extra="ignore"` 를 타고 **에러 없이** 죽는다
    evaluator_max_tokens: int = 512

    # 같은 (질의, 후보) 짝을 다시 물으면 API 를 안 탄다. 채점 재실행이 무료여야 실패 버킷을
    # 읽고 프롬프트를 고치는 반복이 성립한다. 프롬프트를 고치면 salt 가 갈려 자동으로 미스가 된다
    evaluator_cache_enabled: bool = True
    evaluator_cache_dir: Path = PROJECT_ROOT / ".cache" / "evaluator"


# 재시도 루프(⚑Checkpoint A) 설정. **켜는 축이 수 하나뿐이다.**
#
# **`0` 이 곧 꺼짐이다.** `agent_loop_enabled` 를 따로 두지 않은 것은 결정이다 — 리랭커가
# 「켜는 데 세 줄」로 물린 자리와 같은 모양이 되고, 두 설정이 어긋난 조합(`enabled=true` 인데
# `max_retries=0`)이 **에러 없이** 생긴다. 대가는 «검색 한 번 + 생성» 루프를 못 쓰는 것인데,
# 그 조합은 `answer_question()` 과 관측상 같아 잃는 것이 없다.
#
# **저장소 커밋본은 꺼진 채다** — `RERANK_MODEL`·`REWRITE_MODEL`·`EVALUATOR_MODEL` 과 같은
# 규칙이고 이유도 같다. 켜는 순간 질의당 LLM 호출이 회차 수만큼 늘어(회차마다 판정 1회 +
# 마지막에 생성 1회), 값을 하는지 재기 전에 과금이 먼저 붙는 것을 막는다
class AgentConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 근거가 불충분할 때 질의를 승급해 다시 찾아갈 최대 횟수. **0 이면 루프를 아예 안 돈다.**
    # 3 은 스펙(`retrieval/IMPLEMENTATION_SPEC.md` ⚑Checkpoint A)의 값이고 실측으로 고른
    # 수가 아니다 — 실사용 회차 분포를 보고 정할 자리인데 아직 표본이 없다
    agent_max_retries: int = 0


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

    # 리랭커 설정. 같은 lazy 형태라 테스트·평가에서 _rerank 로 덮어쓸 수 있다.
    # **덮어쓰기가 두 겹이어야 한다** — 키를 지우는 것만으로는 부족하다. 로컬 갈래는 키가
    # 없어도 돌아 모델을 내려받으므로, offline_config 가 provider 와 model 을 함께 못 박는다
    @property
    def rerank(self) -> RerankConfig:
        if not hasattr(self, "_rerank"):
            object.__setattr__(self, "_rerank", RerankConfig())
        return self._rerank  # type: ignore[attr-defined]

    # 질의 재작성 설정. 같은 lazy 형태라 테스트·평가에서 _rewrite 로 덮어쓸 수 있다.
    # **evaluator 와 같은 이유로 lazy 여야 한다** — RewriteConfig() 가 .env 를 직접 읽으므로,
    # 안 덮으면 개발자 머신의 REWRITE_MODEL 이 픽스처에 실려 **기본 pytest 가 과금 경로로
    # 간다.** 로컬 갈래가 없어 가중치 적재는 없지만 대신 돈이다
    @property
    def rewrite(self) -> RewriteConfig:
        if not hasattr(self, "_rewrite"):
            object.__setattr__(self, "_rewrite", RewriteConfig())
        return self._rewrite  # type: ignore[attr-defined]

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

    # 판정 단계 설정. 같은 lazy 형태라 테스트에서 _evaluator 로 덮어쓸 수 있다.
    # **generation·answer_cache 와 같은 이유로 lazy 여야 한다** — EvaluatorConfig() 가 .env 를
    # 직접 읽으므로, 안 덮으면 개발자 머신의 모델 이름이 픽스처에 실려 **기본 pytest 가 과금
    # 경로로 간다.** 켜는 축이 이름 하나뿐이라 그 한 줄이 곧 스위치다
    @property
    def evaluator(self) -> EvaluatorConfig:
        if not hasattr(self, "_evaluator"):
            object.__setattr__(self, "_evaluator", EvaluatorConfig())
        return self._evaluator  # type: ignore[attr-defined]

    # 재시도 루프 설정. 같은 lazy 형태라 테스트에서 _agent 로 덮어쓸 수 있다.
    # **evaluator 와 같은 이유로 lazy 여야 한다** — AgentConfig() 가 .env 를 직접 읽으므로,
    # 안 덮으면 개발자 머신에서 켜 둔 회차 수가 픽스처에 실려 **기본 pytest 가 루프를 돈다**
    @property
    def agent(self) -> AgentConfig:
        if not hasattr(self, "_agent"):
            object.__setattr__(self, "_agent", AgentConfig())
        return self._agent  # type: ignore[attr-defined]


# 프로세스 전역인 것만 여기 온다. **`EmbeddingConfig`·`RerankConfig` 로 가르지 않는 이유**가
# 이 클래스의 존재 이유다 — OpenMP 스레드 풀은 프로세스에 **하나뿐**이라 둘로 가르면
# `EMBEDDING_THREADS=4` 와 `RERANK_THREADS=8` 을 동시에 만족시킬 수 없다. 나중에 부른 쪽이
# 이기고, **설정한 값과 다른 수로 도는 상태가 에러 없이** 생긴다 — 설정 클래스가 전부
# `extra="ignore"` 라 오타가 조용히 안 먹는 것과 같은 종류의 사고다.
#
# 임베더와 리랭커가 **같은 풀을 공유한다**는 것이 실측이다(2026-08-26, 16논리/8물리 머신):
# `at::get_num_threads()` 8 · `omp_get_max_threads()` 8 · `OMP_NUM_THREADS` 미설정
class RuntimeConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # torch 가 쓸 CPU 스레드 수(intra-op). **None 이면 torch 기본값을 안 건드린다** —
    # 그 기본값은 물리 코어 수다.
    #
    # **기본을 낮추지 않는 것이 결정이다.** `verify_log/` 의 모든 벽시계와
    # `regression/baseline.json` 이 8스레드에서 잰 값이라, 기본을 바꾸면 과거 수치가
    # **조용히 비교 불가**가 된다. `embedding_device` 가 자동 감지를 안 하는 것과 같은
    # 잣대이고, 발열을 줄이려면 사용자가 `.env` 에서 명시로 내린다.
    #
    # **GPU 로 갔다고 이 축이 죽지 않는다** — CPU 에 남는 갈래가 셋이다:
    # ① 재현성 때문에 일부러 `cpu` 를 못 박은 `evaluation/config.py` 의 프로필
    # ② GPU 없는 머신 ③ VRAM 이 모자라 CPU 로 내릴 때
    torch_num_threads: int | None = None


# 에이전트 도구 계층이 쓰는 웹 검색 설정. **`IngestConfig` 에 안 붙는다** — 소비자가
# `agent/tools/web_search.py` 하나이고, `RetrievalConfig` 을 `RetrieverTool` 이 직접 받는
# 것과 같은 자리다. 그래서 `offline_config` 가 이 객체를 못 덮고, **개발자 `.env` 의 실키를
# 막는 것은 `tests/agent/conftest.py` 의 autouse 픽스처다**(그쪽 docstring 에 이유가 있다).
#
# **자리가 파일 끝인 것은 설계가 아니라 조율이다** — 2026-08-29 에 다른 세션이
# `EvaluatorConfig` 뒤에 `AgentConfig` 를 넣는 중이어서 비켰다. 묶음으로는 `AgentConfig`
# 옆이 맞다
class WebSearchConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 켜는 축은 이 키 하나다. **비면 `_make_client()` 가 None 을 돌려줘 도구가 통째로 꺼진다**
    # — `rerank_model`·`evaluator_model`·`rewrite_model` 이 빈 값으로 꺼지는 것과 글자 그대로
    # 같은 모양이고, **저장소 커밋본은 꺼진 채다.** 여기서 켜는 순간 질의당 크레딧이 나간다
    tavily_api_key: str | None = None

    # 몇 건을 받아오나. `generation_top_k`(5)와 나란한 수로 뒀다 — **임시값이고 실측 전이다.**
    # 웹 근거가 답변에 실리는 경로가 아직 없어(Router 가 이 도구를 안 고른다) 잴 자[尺]가 없다
    web_search_max_results: int = 5

    # 검색 깊이. **합법값 둘이 크레딧을 갈라 죽은 설정이 아니다** — `rerank_provider`·
    # `rewrite_mode` 와 같은 잣대이고, 구현체가 Tavily 하나뿐이라 `WEB_SEARCH_PROVIDER` 를
    # **안 만든 것**이 그 잣대의 반대쪽 사례다. `advanced` 는 같은 질의에 크레딧을 더 쓴다
    web_search_depth: Literal["basic", "advanced"] = "basic"

    # **호출 캐시 설정이 없는 것이 결정이다.** 이 저장소의 캐시 셋(embedding·rerank·rewrite)은
    # 전부 *같은 입력이면 같은 출력* 이 참이라 내용 해시만으로 키가 서는데, 웹 검색은 그것이
    # **거짓**이다 — 결과가 시간에 따라 변하는 것이 이 도구의 존재 이유다. TTL 없이 붙이면
    # «최신을 찾는 도구가 옛 답을 준다» 가 에러 없이 생기고, TTL 값은 재야 정해지는데 잴
    # 자[尺]가 없다(이 저장소의 임계값은 전부 분포의 틈에서 나왔다).
    # 대가는 **테스트·탐색을 되풀이하면 크레딧이 그만큼 나간다**는 것이다


# 유료 API 호출을 한 줄씩 남기는 비용 로그 설정.
#
# **`IngestConfig` 에 안 붙는다.** 단계가 아니라 **가로지르는** 것이라서다 — 적재(Vision·
# LlamaParse)도 검색(리랭킹·재작성)도 생성도 평가(지지 판정)도 도구(웹 검색)도 같은 파일에
# 쓴다. `RuntimeConfig` 이 홀로 사는 것과 같은 자리이고, 소비자가 하나인 `WebSearchConfig`
# 와는 반대 이유로 그렇다(저쪽은 소비자가 하나라서, 이쪽은 소비자가 전부라서).
#
# **기본이 켜짐인 것이 결정이다.** 유료 실측은 다시 돌리는 것 자체가 비용이라, "켜는 걸
# 잊은 회차"는 그 수치를 영영 못 얻는다. 대가는 테스트가 개발자 파일에 쓰는 것인데 그쪽은
# `tests/conftest.py` 의 **autouse 핀**이 막는다 — `Settings.query_log_enabled` 가 이미 밟은
# 길이고, 거기서 배운 것은 **핀이 배선보다 먼저 들어가야 한다**는 것이다.
#
# **금액 설정이 여기 없는 것도 결정이다.** 가격표는 `cost/pricing.py` 가 갖고 로그는 관측만
# 남긴다 — 단가가 바뀌면 이미 쌓인 줄이 조용히 거짓이 되기 때문이다
class CostLogConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    cost_log_enabled: bool = True
    cost_log_path: Path = PROJECT_ROOT / "data" / "cost.jsonl"

    # 이 실행에 붙일 이름. `run_eval --label` · `answer_eval --label` 이 그대로 넘겨 리포트
    # JSON 과 조인된다. **비어 있어도 된다** — 그때는 `run_id` 만으로 실행이 갈린다
    cost_label: str = ""


# 기본 설정 인스턴스를 만들어 반환. 테스트에서는 직접 생성해 값을 덮어쓴다
def default_config() -> IngestConfig:
    return IngestConfig()
