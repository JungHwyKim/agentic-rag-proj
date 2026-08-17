# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

한국어 문서(PDF/DOCX/PPTX/XLSX/HWPX/이미지/텍스트·CSV·마크다운)를 파싱해 통일 스키마로 만드는 RAG 적재 파이프라인.

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

## 아키텍처

진입점은 `rag_pipeline/ingestion/pipeline.py:ingest()` 하나이며, HTTP를 전혀 모른다. 스크립트나 노트북에서도 그대로 쓸 수 있다. `ingest()` 가 하는 일은 단계를 순차로 합성하는 것뿐이다 — 로더 뒤에 붙는 단계들은 `ParsedDocument` 를 받아 `ParsedDocument` 를 돌려주므로 떼었다 붙였다 할 수 있다.

### 로더의 2단계 라우팅

로더 **안에서** 라우팅이 두 번 일어난다(파이프라인 단계 수와는 무관한 이야기다).

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

### 정리 단계(cleaner)

로더가 뽑은 텍스트를 검색에 쓸 수 있게 다듬는다. 진입점은 `cleaner/__init__.py:clean()` 하나다.

- **`ParsedDocument` → `ParsedDocument` 를 지킨다.** 여기서 다른 타입을 내놓으면 `ingest()` 의 순차 합성이 깨지고 단계를 떼었다 붙였다 할 수 없게 된다
- 지우는 기준은 코드포인트 목록이 아니라 **유니코드 범주**다 — Cc(제어)와 Cf(서식). `\n` 과 `\t` 는 남기고, `\r` 은 지우지 않고 `\n` 으로 바꾼다(그냥 지우면 앞뒤 줄이 서로 붙는다)
- 표는 `text` 와 `metadata["rows"]` 를 **함께** 고친다. 한쪽만 고치면 같은 표의 두 표현이 어긋난다
- **안 바뀌었으면 원본 객체를 그대로 돌려주는 게 규약이다** (`strip_control_chars` → `_clean_element` → `clean` 세 층 모두). 상위가 `is` 비교로 변경 여부를 판단하기 때문이다. 무조건 새 객체를 만들도록 고치면 깨끗한 문서에서도 요소가 통째로 복사되고 `cleaned_elements` 카운트가 조용히 부풀어 오른다
- 요소를 지우는 규칙(반복 머리말 제거)이 있어도 **원본 `order` 를 유지한다.** 지운 자리의 번호를 메우지 않는다 — `order` 의 계약상 용도는 정렬 하나뿐이라 연속성이 필요 없고, `elements` 에는 이미 빈틈이 있다(핸들러가 요소를 0개 만든 자리). 번호를 다시 매기면 앞뒤 단계가 참조하던 값이 조용히 달라진다
- 요소를 지울 때는 **무엇을 지웠는지**를 `removed_texts` 에 남긴다. 개수(`removed_elements`)만으로는 오탐을 알아차릴 수 없다. `warnings` 와 섞지 않는다 — 정상 제거는 파싱 '문제'가 아니라서, 같은 칸에 넣으면 파싱 경고를 보는 쪽이 노이즈에 묻힌다

### 청킹 단계(splitter)

정리된 요소를 검색 단위로 만든다. 진입점은 `splitter/__init__.py:split()` 하나이고
`ParsedDocument` 를 받아 `list[Chunk]`(`schemas/chunk.py`)를 돌려준다 — **타입을 바꾸는 첫
단계**라 여기서 순차 합성 사슬이 끝난다.

- **주 작업은 자르기가 아니라 병합이다.** 실측으로 요소가 잘아서(아래 함정 절) 제목이 나올
  때까지 이어 붙인다. 크기 하한은 두지 않는다 — 섹션이 짧아도 다음 섹션과 합치지 않는다
- 병합 경계는 `ElementType.TITLE` 이다. 그 경계를 만드는 것은 **로더**이고
  (`loader/document/headings.py`), splitter 는 포맷을 모른다
- 표는 텍스트 청커에 안 태운다. `metadata["rows"]` 를 행 그룹으로 나누되 **모든 조각에 헤더
  행을 반복해 넣는다.** 행 하나만 검색됐을 때 열 이름이 없으면 숫자만 남아 뜻을 잃는다
- 겹침은 **상한 초과로 쪼갠 구간에만** 준다. 섹션 경계와 표에는 주지 않는다(표는 행이 중복 색인된다)
- 제목 머리는 본문 앞에 붙이되 **예산에서 미리 빼고 자른다.** 자른 뒤에 붙이면 상한을 선언해
  놓고 넘긴다(실측: 1200 상한에 최대 1258자)
- **`split()` 은 아직 `ingest()` 안에서 불리지 않는다.** 누락이 아니라 결정이다 —
  `ingest()` 의 반환 타입이 곧 `backend` 의 `response_model` 이고 청크를 소비할 곳이 없다.
  근거는 `plan_log/splitter.md` 의 "합성 지점" 절
- `chunk_id` 는 `f"{document_key}:{chunk_index:04d}"`. `document_key` 를 안 주면 파싱 결과
  해시를 쓴다 — 저장소에 `document_id` 가 없고 "재업로드 = 중복"인지는 indexing 결정이라,
  그 결정을 splitter 가 앞질러 내리지 않기 위해서다

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
- `IngestConfig.content` · `cleaner` · `splitter` 는 lazy 프로퍼티라 테스트에서 `object.__setattr__(config, "_content", ContentRoutingConfig(...))` 형태로 덮어쓴다
- **모킹 이음새는 각 API 핸들러의 모듈 레벨 `_make_client()`** 다. `mocker.patch.object(image_vision, "_make_client", return_value=fake)` 형태로 쓴다. 이 함수를 인라인화하면 외부 API 테스트가 전부 깨진다
- 실파일 회귀 테스트는 `tests/fixtures/` 에 파일이 있을 때만 돌고 없으면 조용히 skip한다. 지금 셋이다 — `loader/document/test_hwpx_real.py`(한컴 파싱 실측), `cleaner/test_real.py`(PDF 제어문자·반복 머리말 실측), `splitter/test_splitter_real.py`(요소→청크 병합률·상한). 합성 문서로는 "이 규칙이 실제로 필요한가"를 못 보여주기 때문에 따로 둔다
- **테스트 파일 이름은 저장소 전체에서 유일해야 한다.** `tests/` 에 `__init__.py` 가 없어 basename 이 곧 모듈명이라, 같은 이름이 두 폴더에 있으면 pytest 가 collection error 로 죽는다. 영역 테스트만 돌릴 때는 안 나고 전체에서만 난다. 그래서 실파일 테스트가 `test_hwpx_real.py` · `test_splitter_real.py` 처럼 영역 이름을 달고 있다

## 알고 있어야 할 함정

- **LlamaParse SDK**: `llama-parse` 와 `llama-cloud-services` 는 둘 다 deprecated다. `llama-cloud>=1.0` 의 `Client.parsing.parse()` 를 쓴다
- **OCR 한국어**: `ocr_psm=6` 을 쓴다(기본 3은 한글 한 줄을 세로로 오인식). `preserve_interword_spaces=1` 이 없으면 "주식회사"가 "주 식 회 사"로 쪼개져 임베딩·검색이 망가진다. 다만 `image_to_data` 는 이 옵션을 무시하므로 **신뢰도는 `image_to_data`, 최종 텍스트는 `image_to_string`** 으로 용도를 분리해 뒀다 — 합치지 말 것
- **이미지 전처리**: 600px 미만일 때만 확대한다. 이미 큰 이미지를 늘리면 보간 흐림 때문에 인식률이 떨어진다(실측: 원본 7토큰 → 미세 업스케일 후 0토큰)
- **HWPX 쪽 번호**: `hp:tbl` 의 `hp:sz height` 를 믿으면 안 된다. 쪽을 넘기는 표는 이 값이 **시작 쪽에 남은 공간으로 잘려서** 저장돼 있다(실측: 43행 표가 선언 34206 / 실제 98326). 실제 높이는 `hp:cellSz height` 를 `hp:cellSpan rowSpan` 으로 나눠 복원해야 나온다. vertpos만 보고 쪽을 세면 쪽 넘긴 표 뒤로 쪽 번호가 전부 밀린다
- **HWPX 레이아웃 표**: 한글은 제목·인용을 그릴 때 표를 쓴다. 내용 든 칸이 2개 이하이고 9칸 이하인 표는 `_is_layout_box` 가 본문 문단으로 강등한다(`metadata["layout_table"]` 로 표시). 그림이 든 상자는 캡션-그림 관계가 끊기므로 표로 남긴다
- **HWPX 가로 쪽**: 한컴은 가로 쪽에서도 `hp:pagePr` 의 `width`/`height` 에 물리 용지(세로) 치수를 그대로 넣고 방향은 `landscape` 속성으로만 표시한다. **이름과 반대로 `NARROWLY` 가 가로, `WIDELY` 가 세로다**(실측: 시행령 개정안 안내서 가로쪽 판 — 두 섹션 모두 width=59528 height=84188 인데 WIDELY 섹션의 최상위 문단 vertpos 가 69898 까지 올라가 71435=height-여백 에 육박한다. 가로였다면 46775 를 넘을 수 없다). 그래서 가로 섹션의 본문 높이는 `width` 에서 얻는다. 안 가르면 53% 과대평가해 쪽 넘긴 표를 한 쪽으로 세고 이후 쪽 번호가 밀린다
- **HWPX 쪽 수를 분모로 쓰지 말 것** (미해결): 로더가 세는 HWPX 쪽 수가 문서에 따라 크게 부풀어 있다. 실측 — 신뢰가능 AI 가이드는 PDF 판이 28쪽(파일 구조에서 직접 확인)인데 HWPX 판을 97쪽으로 센다. 본문 분량은 20,895자 vs 21,843자로 거의 같다. 쪽당 요소 수 중앙값이 PDF 19 / HWPX 5 이고 요소가 1개뿐인 쪽이 21개다. 섹션은 14개뿐이라 `start_section()` 만으로는 설명이 안 되고 `vertpos` 감소 휴리스틱의 과다 발화로 보인다. 시행령 문서는 사람이 확인한 26쪽과 맞으므로 일률적으로 깨진 것은 아니다. **쪽 수로 비율을 계산하는 판정(머리말 등)을 만들 때 이 값을 분모로 쓰면 문서마다 결과가 뒤집힌다** — `cleaner/headers.py` 가 등장 횟수 비율 대신 걸친 폭을 쓰는 이유다
- **요소는 청크가 아니다 — 아주 잘다**: 실측으로 요소 자수 중앙값이 PDF 23 / HWPX 15 / 시행령 40 이고, **1200자를 넘는 텍스트 요소가 세 문서 전부 0개**다. PDF 는 PyMuPDF 블록 단위라 707개 중 349개(49%)가 20자 미만이다. 그래서 청크 상한은 텍스트에서 사실상 발화하지 않고 **큰 표에서만** 걸린다(시행령의 67행 표 3,243자). 청킹을 "자르는 일"로 설계하면 20자짜리 청크가 절반이 된다
- **HWPX 제목은 스타일로 못 찾는다**: 한컴이 서식을 문단 스타일이 아니라 `charPr` 로 직접 박는 문서가 흔하다. 실측으로 신뢰가능 AI 가이드 HWPX 의 `Contents/header.xml` 에는 스타일이 `Normal` **하나뿐**이었다(글자 높이는 240~2617 로 살아 있다). 그래서 `headings.py` 가 글자 크기를 쓴다. 판정 규칙 셋(문서별 최빈값 / 배율 아닌 부등호 / `min` 이지 `max` 가 아님)은 그 파일 docstring에 근거와 함께 있다 — **되돌리면 강조가 제목이 되거나 제목이 통째로 사라진다**
- **PDF 제어문자**: PDF는 글머리 기호를 제어 코드포인트로 떨어뜨린다(실측: 신뢰가능 AI 가이드 PDF에서 U+0001 192개 / U+0007 47개 / U+0008 4개, `작성 \x07김동현 수석` 처럼 본문 한가운데에 박혀 있다). 같은 문서의 HWPX 판은 0개다 — 정리 단계가 실제로 필요한 쪽은 PDF 경로다
- **`\n` 은 지우면 안 된다**: 실측 560개가 전부 표 요소에 있는 마크다운 표의 행 구분자다. 같이 지우면 `| 구 분 | 추진내용 || --- |` 처럼 한 줄로 뭉개져 표가 통째로 깨진다
- **Cf 제거의 감수 사항**: ZWJ(U+200D)도 Cf라서 이모지 결합(가족 이모지 등)이 쪼개진다. 한국 공문서 코퍼스에는 사실상 안 나와 예외로 두지 않았다. 살려야 하는 코퍼스가 생기면 `cleaner/characters.py` 의 `_KEEP` 에 추가한다
- `.hwp`/`.doc`/`.ppt` 는 감지는 되지만 파싱하지 않는다. `UnsupportedLegacyLoader` 가 변환 안내와 함께 415로 거부한다. 정확한 안내를 주려면 업로드 자체는 받아야 하므로 `SUPPORTED_EXTENSIONS` 에는 포함돼 있다
- Tesseract는 외부 설치가 필요하다(5.5.0 + 한국어 traineddata). PATH에 없으면 `.env` 의 `TESSERACT_CMD` 로 지정한다
- zip bomb 방어는 `loader/detect.py:MAX_UNCOMPRESSED_BYTES`(500MB) 에 있다

## 새 포맷 추가

1. `schemas/document.py` 의 `FileFormat` 에 추가
2. `loader/detect.py` 의 `_ZIP_MARKERS` / `_OLE_MARKERS` / `_IMAGE_MAGICS` 중 해당하는 곳에 시그니처 추가
3. `loader/document/` 에 `BaseDocumentLoader` 상속 클래스를 만들고 `detect_regions()` 만 구현
4. `loader/registry.py` 의 `_FACTORIES` 와 `SUPPORTED_EXTENSIONS` 에 등록

## 새 파이프라인 단계 추가

cleaner가 실제로 밟은 경로다.

1. `rag_pipeline/ingestion/<stage>/` 에 모듈을 만든다. 공개 진입 함수는 하나이고 HTTP를 모른다
2. `config.py` 에 `<Stage>Config` 를 만들고 `IngestConfig` 에 lazy 프로퍼티로 붙인다
3. `.env.example` 에 밖으로 열 설정을 추가한다
4. `pipeline.py:ingest()` 의 예약 주석 자리를 실제 호출로 바꾼다 — **다만 그 단계의 출력을 쓸 곳이 있을 때만이다.** splitter 는 이 칸을 일부러 비워뒀다(위 "청킹 단계" 절). 소비자 없이 끼우면 업로드마다 결과를 만들어 버리는 셈이고, `ingest()` 의 반환은 곧 공개 HTTP 응답 스키마라 바꾸는 순간 API 결정이 된다
5. 외부 API를 부르는 단계라면 `tests/conftest.py` 의 `offline_config` 에 그것을 끄는 설정을 넣는다. 기본 테스트는 키 없이 통과해야 한다
6. `.claude/hooks/auto_test_check.py` 의 매핑 표에 그 단계의 테스트 경로를 추가한다. 빠뜨리면 **그 단계만 자동 테스트가 안 도는데 아무 에러도 안 난다** — 그래서 `tests/hooks/test_auto_test_check.py` 가 검사한다. 매핑을 넣거나, 부분 실행이 무의미한 이유를 `UNMAPPED_BY_DESIGN` 에 적거나 둘 중 하나여야 통과한다

## 기록

성공한 코드는 저장소에 남지만 **기각된 대안·되돌린 시도·아직 못 정한 것은 안 남는다.** 기록의 값어치는 대부분 거기서 나온다. 시점에 따라 둘로 나뉜다.

| | 언제 | 어디에 | 스킬 |
|---|---|---|---|
| 설계 논의 | 만들기 **전** — 무엇을 만들지 정하는 동안 | `plan_log/<기능>.md` (기능당 한 파일, 회차 누적) | `plan-log` |
| 작업 회고 | 만든 **뒤** — 무엇을 만들었고 왜 그렇게 골랐나 | `task_log/YYYY-MM-DD-<주제>.md` | `task-log` |
| 검증 수치 | **재는 동안** — 검증·테스트에서 얼마가 나왔나 | `verify_log/YYYY-MM-DD-<주제>.md` | `verify-log` |

앞의 둘은 산문이고 셋째는 수치다. 가르는 기준은 **떼어냈을 때 문장이 성립하는가** 하나다 —
`pytest 337 passed` 는 `verify_log/` 고, `큰 글씨와 마커가 겹치는 문단이 0/74 라 마커 승격을
기각했다` 는 떼면 문장이 죽으므로 `task_log/` 다. `verify_log/` 는 **가상 데이터와 실제
데이터를 갈라 적는다** — 가상은 "규칙이 도는가"에만 답하고 임계값을 못 정한다(합성 문서는
만든 사람의 기대를 그대로 갖고 있어 자기 확인이 된다). 반대로 실파일에 우연히 없는 경계값은
가상만 만들 수 있다. `regression/baseline.json` 과도 다르다 — 저쪽은 승인된 **기대값 하나**다.

**`verify_log/` 에는 훅이 없다.** 차단 게이트를 셋으로 늘리면 `SKIP_*=1` 이 습관이 되므로,
`task-log` 2단계가 `verify-log` 를 부르게 해서 기존 게이트 하나가 둘 다 강제한다.

앞의 둘은 기록 없이 세션을 끝내려 하면 Stop 훅이 세션당 한 번 막는다(`.claude/hooks/{plan,task}_log_check.py`, 공용부는 `_hookutil.py`). 건너뛰려면 `SKIP_PLANLOG=1` / `SKIP_TASKLOG=1`. 색인은 각 폴더의 `INDEX.md`.

설계 쪽은 검사가 무장되는 조건이 하나 더 있다 — **`plan-log` 스킬을 호출해야** "이 세션은 설계 논의"라는 표시가 생기고, 그래야 Stop 검사가 돈다. 안 부르면 검사 자체가 없다. 그래서 확인 요청으로 시작한 대화가 규칙·형식을 바꾸는 논의로 번지면 **알아챈 그 시점에** 부른다. 늦게 부르는 것과 안 부르는 것은 다르다.

두 훅이 "기록했는가"를 판정하는 기준은 **이번 세션에 그 폴더에 썼는가**(PostToolUse 마커)이지 git 변경이 아니다. git 으로 보면 지난 세션의 미커밋 기록이 이번 세션을 통과시켜, **기록이 밀릴수록 검사가 헐거워진다** — 실제로 그 상태로 무력화돼 있던 것을 2026-08-17 에 발견했다. 기록 대상은 제품 코드에 더해 **하네스 중 동작이 있는 것**(`.claude/hooks/`·`settings.json`)까지다. `.claude/skills/**` 와 이 문서는 일부러 뺐다(문서 갱신은 `task-log` 5단계 몫이고, 문서마다 회고를 요구하면 `SKIP_TASKLOG=1` 이 습관이 된다). 경계는 `task_log_check.py` 의 `is_recordable()` 이 답한다.

## 자동 검증

`auto_test_check.py` 가 파일을 고칠 때마다 **그 영역의 테스트만** 돌리고(PostToolUse), 세션을 끝낼 때 **전체 pytest** 를 한 번 돌린다(Stop). 영역 매핑과 그 이유는 훅 파일 안에 있다. 건너뛰려면 `SKIP_AUTOTEST=1`.

`-m integration` 은 어느 훅도 자동으로 돌리지 않는다. `paid_test_check.py` 가 아예 막고, 풀려면 `ALLOW_PAID_TESTS=1` 로 사용자가 직접 승인해야 한다.

## 이 문서에 쓰지 않는 것

이 문서의 값어치는 **코드를 읽어도 알 수 없는 것**에서 나온다 — 실측값, 함정, 왜 그 값인지, 무엇을 버렸는지. 코드가 이미 말할 수 있는 것을 여기 옮겨 적으면 다음 커밋에 곧바로 거짓이 된다. 그래서 아래 셋은 쓰지 않는다.

- **진행 단계 스냅샷** ("현재 N단계까지 구현됐다", "다음은 X") — 아래 "어디까지 왔나"처럼 출처를 가리킨다
- **줄번호** (`pipeline.py:31-35`) — 한 줄만 밀려도 조용히 거짓이 되고 아무도 안 알려준다. 파일과 심볼 이름으로 가리킨다
- **코드에서 바로 읽히는 것** (설정 항목 나열, 함수 시그니처) — 대신 *왜 그 값인지*를 쓴다

## 어디까지 왔나

여기에 현재 상태를 적지 않는다. 적는 순간 낡기 때문이다. 각각의 출처는 이렇다.

- **구현된 단계** — `rag_pipeline/ingestion/` 아래 디렉터리가 전부다
- **예약된 다음 단계** — `pipeline.py:ingest()` 안의 주석
- **어떻게 여기까지 왔나** — `task_log/INDEX.md`
- **아직 논의 중인 설계** — `plan_log/INDEX.md`
