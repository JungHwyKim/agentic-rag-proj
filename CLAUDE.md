# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

한국어 문서(PDF/DOCX/PPTX/XLSX/HWPX/이미지)를 파싱해 통일 스키마로 만드는 RAG 적재 파이프라인. 현재 1단계(loader)까지 구현됐다.

## 명령어

Windows + uv + Python 3.11.9. `.venv` 는 프로젝트 루트에 있다.

```powershell
# 서버 실행 → http://127.0.0.1:8000/docs
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload

# 테스트 전체
.\.venv\Scripts\python.exe -m pytest

# 단일 테스트
.\.venv\Scripts\python.exe -m pytest tests/ingestion/loader/document/test_pdf.py::test_pdf_table_routed_to_llamaparse

# 린트
.\.venv\Scripts\python.exe -m ruff check .

# 의존성 설치
uv pip install -r requirements.txt
```

기본 테스트는 **API 키 없이 전부 통과**해야 한다. `pyproject.toml` 의 `addopts = "-m 'not integration'"` 이 외부 호출 테스트를 기본 실행에서 뺀다. `-m integration` 으로 돌리면 **LlamaParse/OpenAI에 실제 과금이 발생**하므로 사용자 허락 없이 실행하지 않는다.

## 아키텍처 — 2단계 라우팅

진입점은 `rag_pipeline/ingestion/pipeline.py:ingest()` 하나이며, HTTP를 전혀 모른다. 스크립트나 노트북에서도 그대로 쓸 수 있다.

```
detect_format()            매직바이트로 실제 포맷 판별 (확장자 불신)
  ↓                        loader/detect.py — zip/ole는 내부 구조까지 봐야 갈린다
get_loader()               1차 라우팅: FileFormat → 문서 로더
  ↓                        loader/registry.py 의 _FACTORIES 분기표
BaseDocumentLoader.load()  템플릿 메서드 (loader/document/base.py)
  ├ detect_regions()       ← 포맷별 로더가 구현하는 유일한 추상 메서드
  ├ route_all()            2차 라우팅: ContentType → 콘텐츠 핸들러
  └ DocumentMetadata 조립  LoadContext의 비용 카운터·경고를 옮겨 담는다
```

### 지켜야 할 경계

- **문서 로더**(`loader/document/*`)는 "어디에 무엇이 있는가"만 `ContentRegion` 목록으로 답한다. OCR/Vision/LlamaParse를 직접 부르지 않는다.
- **콘텐츠 핸들러**(`loader/content/*`)만 "어떻게 텍스트로 만드는가"를 안다. 표를 LlamaParse로 볼지, 이미지를 Vision으로 볼지는 `loader/content/router.py` 와 설정으로만 바뀐다.
- 모든 출력은 `ParsedDocument(metadata, elements)` 로 수렴한다 (`schemas/document.py`).

이 경계를 지키지 않고 로더 안에서 OCR을 부르기 시작하면 설계가 무너진다.

### 폴백 사슬 — 외부 API 실패가 적재를 멈추면 안 된다

- **표**: `table_llamaparse` (blob이 있고 `table_pdf_handler="llamaparse"` 일 때) → 실패·키없음이면 `table_native` 로 내려간다
- **이미지**: `classify()` → SKIP(로고·아이콘 크기 필터) / OCR / Vision → Vision 실패 시 classify가 이미 돌린 OCR 결과를 재사용한다

외부 API 핸들러는 **예외를 던지지 않고 빈 리스트를 반환**하는 게 규약이다. 이 규약을 깨면 라우터의 폴백이 동작하지 않는다.

### 비용 통제

- `LoadContext` 가 `vision_calls` / `llamaparse_calls` / `ocr_applied` / `warnings` 를 누적해 `DocumentMetadata` 로 노출한다
- `max_vision_calls_per_doc` 상한과 `ResultCache`(내용 해시 키)로 재업로드 시 재과금을 막는다
- hybrid 이미지 판정(`loader/content/classify.py`)이 무료·로컬인 Tesseract를 먼저 돌리는 이유는 유료 Vision 호출을 아끼기 위해서다. 200페이지 스캔본을 전부 Vision에 보내면 감당이 안 된다

### backend ↔ rag_pipeline 분리

- `backend/app/api/routes/documents.py` 는 업로드 수신 / 확장자 화이트리스트 1차 검증 / UUID 임시저장 / 정리만 한다. 파싱은 전부 `ingest()` 가 한다
- 로더는 동기·CPU 바운드라 `run_in_threadpool` 로 넘긴다
- `backend/app/main.py` 의 `_STATUS_BY_EXCEPTION` 이 `RagPipelineError` 를 HTTP 상태코드로 매핑하고 `user_message`(한국어)를 그대로 응답에 내보낸다. 새 예외를 만들면 이 매핑에 추가해야 500이 안 난다
- 설정도 둘로 나뉜다: `backend/app/core/config.py`(웹) vs `rag_pipeline/config.py`(파싱). 둘 다 `PROJECT_ROOT/.env` 를 읽는다

## 코드 컨벤션

- **모든 class/def 위에 한 줄 한국어 설명 주석**을 단다. 코드베이스 전체가 이 형태다
- 모듈 docstring에는 *왜* 그렇게 했는지를 적는다. 특히 실측으로 얻은 수치는 근거와 함께 남긴다
- 모든 모듈 상단에 `from __future__ import annotations`
- ruff: line-length 100, `E,F,I,UP,B,SIM` 선택, E501 무시. 외부 API 예외를 삼키는 `except Exception` 에는 `# noqa: BLE001` 과 이유 주석을 함께 단다

## 테스트 패턴

- `tests/conftest.py` 가 샘플 파일을 **코드로 생성**한다(docx/pptx/xlsx/pdf/png/hwpx). 바이너리를 저장소에 커밋하지 않는 방침
- `offline_config` 픽스처 = 키 없음 + `table_pdf_handler="pymupdf"` + `image_handler="ocr"` + 캐시 끔. 단위 테스트의 기본값
- `IngestConfig.content` 는 lazy 프로퍼티라 테스트에서 `object.__setattr__(config, "_content", ContentRoutingConfig(...))` 로 덮어쓴다
- **모킹 이음새는 각 API 핸들러의 모듈 레벨 `_make_client()`** 다. `mocker.patch.object(image_vision, "_make_client", return_value=fake)` 형태로 쓴다. 이 함수를 인라인화하면 외부 API 테스트가 전부 깨진다
- 실제 한컴 파일 회귀 테스트(`tests/ingestion/loader/document/test_hwpx_real.py`)는 `tests/fixtures/` 에 파일이 있을 때만 돌고 없으면 조용히 skip한다

## 알고 있어야 할 함정

- **LlamaParse SDK**: `llama-parse` 와 `llama-cloud-services` 는 둘 다 deprecated다. `llama-cloud>=1.0` 의 `Client.parsing.parse()` 를 쓴다
- **OCR 한국어**: `ocr_psm=6` 을 쓴다(기본 3은 한글 한 줄을 세로로 오인식). `preserve_interword_spaces=1` 이 없으면 "주식회사"가 "주 식 회 사"로 쪼개져 임베딩·검색이 망가진다. 다만 `image_to_data` 는 이 옵션을 무시하므로 **신뢰도는 `image_to_data`, 최종 텍스트는 `image_to_string`** 으로 용도를 분리해 뒀다 — 합치지 말 것
- **이미지 전처리**: 600px 미만일 때만 확대한다. 이미 큰 이미지를 늘리면 보간 흐림 때문에 인식률이 떨어진다(실측: 원본 7토큰 → 미세 업스케일 후 0토큰)
- `.hwp`/`.doc`/`.ppt` 는 감지는 되지만 파싱하지 않는다. `UnsupportedLegacyLoader` 가 변환 안내와 함께 415로 거부한다. 정확한 안내를 주려면 업로드 자체는 받아야 하므로 `SUPPORTED_EXTENSIONS` 에는 포함돼 있다
- Tesseract는 외부 설치가 필요하다(5.5.0 + 한국어 traineddata). PATH에 없으면 `.env` 의 `TESSERACT_CMD` 로 지정한다
- zip bomb 방어는 `loader/detect.py:MAX_UNCOMPRESSED_BYTES`(500MB) 에 있다

## 새 포맷 추가

1. `schemas/document.py` 의 `FileFormat` 에 추가
2. `loader/detect.py` 의 `_ZIP_MARKERS` / `_OLE_MARKERS` / `_IMAGE_MAGICS` 중 해당하는 곳에 시그니처 추가
3. `loader/document/` 에 `BaseDocumentLoader` 상속 클래스를 만들고 `detect_regions()` 만 구현
4. `loader/registry.py` 의 `_FACTORIES` 와 `SUPPORTED_EXTENSIONS` 에 등록

## 현재 상태

1단계(loader) 완료. 다음은 `cleaner/` 이고, 삽입 지점이 `pipeline.py:ingest()` 안에 주석으로 표시돼 있다(`clean → split → embed → index`). `frontend/` 는 비어 있고 qdrant는 아직 설치하지 않았다.
