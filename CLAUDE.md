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

진입점은 `rag_pipeline/ingestion/pipeline.py` 에 둘 있고 **둘 다 HTTP를 전혀 모른다.** 스크립트나 노트북에서도 그대로 쓸 수 있다.

- `ingest()` — 파일 하나를 `ParsedDocument` 로 만든다. 하는 일은 단계를 순차로 합성하는 것뿐이다 — 로더 뒤에 붙는 단계들은 `ParsedDocument` 를 받아 `ParsedDocument` 를 돌려주므로 떼었다 붙였다 할 수 있다
- `index_document()` — 그 위에 청킹→임베딩→색인을 잇는다. 검색 가능한 벡터를 만드는 전 구간은 이쪽이다

**둘을 합치지 않는 이유는 아래 "색인 단계" 절에 있다.** `ingest()` 의 반환이 곧 backend 의 `response_model` 이라는 것이 첫째이고, 기본 `pytest` 가 임베딩을 안 부르는 상태가 유지되어야 한다는 것이 둘째다.

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

- **표**: `table_llamaparse` (blob이 있고 `table_pdf_handler="llamaparse"` 일 때) → 실패·키없음이면 `table_native` 로 내려간다. **기본값은 `pymupdf` 라 이 경로가 꺼져 있다** — LlamaParse 가 표를 마크다운이 아니라 HTML 로 돌려줘 `rows` 를 못 얻고, 그러면 splitter 가 헤더 반복을 못 하고 태그 한가운데를 자른다(실측: 시행령 PDF 표청크 24→62, 그중 16개가 여는 태그 없는 조각). 근거와 되살릴 조건은 `plan_log/표-경로.md`
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

### 임베딩 단계(embedding)

청크 텍스트를 벡터로 만든다. 진입점은 `embedding/__init__.py:get_embedder()` 하나이고
`list[str]` 을 받아 `list[list[float]]` 를 돌려준다. `Chunk` 를 안 받는 이유는 질의
임베딩과 대칭을 지키기 위해서다 — 상위가 `[c.text for c in chunks]` 를 넘긴다.

- **여기서는 예외를 던진다. 폴백 규약을 일부러 안 따르는 유일한 자리다.** "외부 API 는 빈
  결과 + `warn()`" 은 **내려갈 층이 있어서** 성립하는 규약이다(LlamaParse→PyMuPDF,
  Vision→OCR). 임베딩에는 없다 — 빈 벡터를 돌려주면 그 청크는 영원히 검색되지 않는데 아무도
  모른다. 그래서 `len(반환) == len(입력)` 이거나 `EmbeddingError` 다
- **상한은 호출 수가 아니라 토큰이다.** `max_vision_calls_per_doc` 를 베끼면 아무것도 못
  막는다 — 배치라 청크 500개가 5회 호출로 끝난다. `tiktoken` 으로 **보내기 전에** 센다
- **캐시 salt 에 모델명과 차원이 둘 다 들어간다**(`embed:{model}:{dim}`). 빠지면 모델을
  바꿔도 옛 벡터가 나오는데 에러가 안 난다. 차원이 salt 에 있는 이유는 `text-embedding-3-*`
  가 `dimensions` 로 차원을 줄일 수 있어 **차원이 설정값**이기 때문이다
- **`provider` 설정은 일부러 없다.** 구현체가 하나면 합법값이 하나뿐인 죽은 설정이다
  (`table_pdf_handler` 에 두 값이 있는 건 두 경로가 실재해서다). 추상화(`Embedder`
  Protocol + `get_embedder()`)만 두고 설정은 로컬 구현체가 생길 때 함께 더한다
- **벡터는 단위 길이다**(실측 0.9998~1.0005, `dimensions=256` 축소본도 0.9999). indexing 이
  Cosine 대신 더 싼 Dot 을 쓸 수 있고 재정규화도 필요 없다
- **두 벡터를 원소별로 비교하지 않는다.** 호출 간 비트 재현성은 보장되지 않는다 — 같은
  텍스트가 한 번은 1e-6 넘게 달랐고 한 번은 정확히 같았다(배치 구성이 다르면 ~1e-4 로
  흔들린다). 판정은 코사인으로 한다. 특히 **순서 검증을 값 비교로 쓰면 자리 밀림과 잡음을
  구분하지 못한다** — 자기 짝과의 유사도가 남과의 유사도보다 높은가로 본다
- **`get_embedder()` 도 `ingest()` 안에서 안 불린다.** splitter 와 같은 이유이고 한 칸 밀린
  같은 상황이다 — 벡터를 소비할 곳(indexing)이 없다. 부수 효과가 중요하다: 기본 `pytest` 가
  이 단계를 아예 안 불러서 "키 없으면 예외"가 기본 테스트를 안 깬다. **두 결정이 서로를
  지탱하므로 한쪽만 되돌리면 안 된다**

### 색인 단계(indexing)

벡터를 저장하고 찾는다. 진입점은 `indexing/__init__.py:get_store()` 하나이고, 파일 하나를
끝까지 색인하는 전 구간 흐름은 `pipeline.py:index_document()` 다. 반환값이 아니라
**부수 효과**가 목적인 첫 단계다.

- **여기서도 예외를 던진다.** embedding 과 같은 이유이고 한 칸 밀린 같은 상황이다 —
  저장 못 한 청크는 영원히 검색되지 않는데 아무도 모른다. `IndexingError` 하나로 받는다
- **`document_id` 는 원본 파일명에서 뽑는다**(`records.py:document_id_for`). splitter·embedding
  이 열어두고 기다린 미결이 여기서 닫혔다. 내용 해시로 잡으면 **문서를 고치는 순간 키가 바뀌어**
  재색인이 옛 청크를 못 찾고 문서가 통째로 두 벌 남는다 — 막으려던 일이 그대로 일어난다.
  `unicodedata.normalize("NFC", ...)` 가 필수다: 한글 파일명을 macOS 는 NFD, Windows 는 NFC 로
  저장해 정규화 없이는 눈에 같은 이름이 다른 키가 된다
- **`VectorRecord.id` 는 `chunk_id` 가 아니라 `uuid5(chunk_id)` 다.** qdrant 가 point ID 로
  **unsigned int 또는 UUID 만** 받아 문자열은 `ValueError` 로 거절한다(실측). uuid5 는
  결정론적이라 재색인이 갱신이 된다. `_NAMESPACE` 를 바꾸면 기존 색인 전체가 새 point 가 된다
- **컬렉션 이름에 모델명과 차원이 들어간다**(`{collection_name}_{model}_{dim}`). 차원만
  검사하면 `-small` 기본 1536 과 `dimensions=1536` 으로 줄인 `-large` 를 못 가른다 — 크기가
  같고 벡터 공간이 달라 에러 없이 검색 품질만 나빠진다. 이름에 넣으면 혼입이 검사 통과 여부가
  아니라 **구조적으로** 불가능해진다. `embed:{model}:{dim}` salt 와 같은 판단이다
- **거리 척도는 Dot 고정이고 설정이 아니다.** 벡터가 단위 길이라는 실측이 근거다
- **재색인은 `delete_by_document_id()` → `upsert()` 다.** upsert 만으로는 문서가
  `A B C D E` 에서 `A B D` 로 줄었을 때 C·E 가 남아 계속 검색된다. **레코드가 0개여도
  delete 는 부른다** — 빈 문서로 바뀐 경우를 건너뛰면 옛 청크가 통째로 남는다
- **`store.close()` 가 선택이 아니다.** qdrant 로컬 파일 모드는 저장 디렉터리에 배타 잠금을
  걸어, 안 닫으면 두 번째 색인이 `RuntimeError` 로 죽는다. `index_document()` 가
  `try/finally` 로 푼다
- **비용 카운터·호출 상한·캐시가 없고 그게 맞다.** 그 규약은 유료 외부 API 것이고 로컬 파일
  모드에는 호출당 과금이 없다. `ResultCache` 도 안 쓴다 — qdrant 자체가 저장소라 캐시를
  얹으면 진실이 두 곳이 된다. 관리형 qdrant 로 가면 셋 다 필요해진다
- **`index_document()` 는 `ingest()` 를 안 바꾼다.** `ingest()` 반환이 곧 공개 응답
  스키마인 것이 첫째 이유이고, **둘째가 더 중요하다** — 기본 `pytest` 가 `ingest()` 만 불러
  임베딩이 호출되지 않으므로 "키 없으면 예외"가 테스트를 안 깬다. 합치면 그 지탱이 무너진다
- **`search(filters=...)` 와 backend 색인 엔드포인트는 일부러 없다.** 필터로 쓸 축이
  `document_id` 뿐이고 `page` 는 못 쓴다(아래 HWPX 쪽 수 함정). 근거는 `plan_log/indexing.md`

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
- `offline_config` 픽스처 = 키 없음 + `table_pdf_handler="pymupdf"` + `image_handler="ocr"` + 캐시 끔. 단위 테스트의 기본값. **임베딩 설정도 여기서 키를 지운다** — `ingest()` 가 임베딩을 안 부르지만 `EmbeddingConfig()` 는 `.env` 를 읽으므로, 안 지우면 개발자 머신의 실키가 픽스처에 실린다. **색인 설정은 키가 아니라 저장 위치를 덮는다** — `IndexingConfig()` 의 기본 `qdrant_path` 가 `PROJECT_ROOT/.qdrant` 라 안 덮으면 테스트가 개발자의 실제 색인을 건드리고 테스트끼리 상태를 물려받는다. 그래서 이 픽스처가 `tmp_path` 를 받는다
- `IngestConfig.content` · `cleaner` · `splitter` · `embedding` 은 lazy 프로퍼티라 테스트에서 `object.__setattr__(config, "_content", ContentRoutingConfig(...))` 형태로 덮어쓴다
- **모킹 이음새는 각 API 핸들러의 모듈 레벨 `_make_client()`** 다. `mocker.patch.object(image_vision, "_make_client", return_value=fake)` 형태로 쓴다. 이 함수를 인라인화하면 외부 API 테스트가 전부 깨진다
- 실파일 회귀 테스트는 `tests/fixtures/` 에 파일이 있을 때만 돌고 없으면 조용히 skip한다. 지금 다섯이다 — `loader/document/test_hwpx_real.py`(한컴 파싱 실측), `cleaner/test_real.py`(PDF 제어문자·반복 머리말 실측), `splitter/test_splitter_real.py`(요소→청크 병합률·상한), `embedding/test_embedding_real.py`(청크 토큰 분포·문자/토큰 비율), `indexing/test_indexing_real.py`(청크 수백 개에서의 자리 밀림·실제 한글 파일명). 합성 문서로는 "이 규칙이 실제로 필요한가"를 못 보여주기 때문에 따로 둔다
- **테스트 파일 이름은 저장소 전체에서 유일해야 한다.** `tests/` 에 `__init__.py` 가 없어 basename 이 곧 모듈명이라, 같은 이름이 두 폴더에 있으면 pytest 가 collection error 로 죽는다. 영역 테스트만 돌릴 때는 안 나고 전체에서만 난다. 그래서 실파일 테스트가 `test_hwpx_real.py` · `test_splitter_real.py` 처럼 영역 이름을 달고 있다

## 알고 있어야 할 함정

- **LlamaParse SDK**: `llama-parse` 와 `llama-cloud-services` 는 둘 다 deprecated다. `llama-cloud>=1.0` 의 `Client.parsing.parse()` 를 쓴다. **응답의 `markdown` 은 문자열이 아니다** — `pages` 를 담은 pydantic 모델이고 성공한 페이지에만 `markdown: str` 이, 실패한 페이지에는 `error` 가 들어온다. 문자열로 알고 `.strip()` 을 부르다 `AttributeError` 로 적재 전체가 멈춘 적이 있다(2026-08-18, 유료 표 경로 첫 실행). **가짜 응답을 문자열로 만들면 테스트가 이 오해를 그대로 검증한다** — 그래서 `test_router.py` 의 가짜는 실제 `pages` 모양이다
- **PDF 는 한 표를 여러 리전으로 쪼갠다**: 시행령 안내서 4쪽의 표 하나를 PDF 경로가 **4개 표 리전**으로 잡았다(실측). splitter 는 각 리전의 첫 행을 헤더로 반복하므로, 실제 열 이름(`단계|실행항목|세부내용 및 방법|담당`)은 4개 중 첫 청크에만 남고 나머지 셋은 **데이터 행이 헤더 자리**에 온다. 같은 표를 HWPX 판은 하나로 잡아 `part 0/2·1/2` 로 쪼개며 양쪽에 같은 헤더를 넣는다. 표 청킹이 막으려던 "열 이름 없는 행"이 **로더 단계에서 이미 만들어지므로** splitter 를 고쳐도 안 없어진다
- **같은 문서라도 포맷이 청크 수를 가른다**: 시행령 안내서가 HWPX 45청크 vs PDF 104청크로 2.3배 차이다(실측). 요소 수는 285 vs 435 로 1.5배뿐이라 요소가 잘아진 것만으로는 설명이 안 된다. 어느 쪽이 검색에 유리한지는 **아직 못 잰다** — 정답 질의 세트가 없다. 반대로 가로쪽 판은 세로판과 요소·청크가 완전히 같아, 가로 쪽 기하 보정은 청킹을 바꾸지 않는다
- **OCR 한국어**: `ocr_psm=6` 을 쓴다(기본 3은 한글 한 줄을 세로로 오인식). `preserve_interword_spaces=1` 이 없으면 "주식회사"가 "주 식 회 사"로 쪼개져 임베딩·검색이 망가진다. 다만 `image_to_data` 는 이 옵션을 무시하므로 **신뢰도는 `image_to_data`, 최종 텍스트는 `image_to_string`** 으로 용도를 분리해 뒀다 — 합치지 말 것
- **이미지 전처리**: 600px 미만일 때만 확대한다. 이미 큰 이미지를 늘리면 보간 흐림 때문에 인식률이 떨어진다(실측: 원본 7토큰 → 미세 업스케일 후 0토큰)
- **HWPX 쪽 번호**: `hp:tbl` 의 `hp:sz height` 를 믿으면 안 된다. 쪽을 넘기는 표는 이 값이 **시작 쪽에 남은 공간으로 잘려서** 저장돼 있다(실측: 43행 표가 선언 34206 / 실제 98326). 실제 높이는 `hp:cellSz height` 를 `hp:cellSpan rowSpan` 으로 나눠 복원해야 나온다. vertpos만 보고 쪽을 세면 쪽 넘긴 표 뒤로 쪽 번호가 전부 밀린다
- **HWPX 레이아웃 표**: 한글은 제목·인용을 그릴 때 표를 쓴다. 내용 든 칸이 2개 이하이고 9칸 이하인 표는 `_is_layout_box` 가 본문 문단으로 강등한다(`metadata["layout_table"]` 로 표시). 그림이 든 상자는 캡션-그림 관계가 끊기므로 표로 남기고, **표가 든 상자도 남긴다** — 강등하면 안쪽 표가 문단으로 뭉개진다. 세는 것은 `_table_rows()` 의 격자가 아니라 **실제 `tc` 목록**이다. 격자는 병합 칸을 여러 칸으로 펴므로 그걸 세면 9칸·2칸의 뜻이 달라진다(실측: 그렇게 뒀더니 시행령 안내서 표가 9→13개)
- **HWPX 병합 셀**: 병합에 덮인 자리는 `hp:tc` 가 **아예 안 나온다.** `hp:cellAddr` 를 안 보고 `tc` 를 순서대로 담으면 그만큼 값이 왼쪽으로 밀려, `지침확인`(실행항목)이 `단계` 열에 들어간 채 색인된다 — 실측으로 시행령 안내서 HWPX 표 173행 중 127행, 세 문서 합쳐 373행이 그 상태였다. `_table_rows()` 가 `cellAddr`·`cellSpan` 으로 격자를 채워 편다. **세로 연장은 값을 되풀이하고 가로 연장은 빈 칸으로 둔다** — splitter 가 표를 행 단위로만 쪼개서, 아래로 이어진 행은 다른 청크로 갈 수 있지만 옆으로 이어진 칸은 늘 같은 행에 남기 때문이다. PDF 경로도 같은 규칙을 쓴다(아래)
- **PDF 병합 셀**: PyMuPDF 는 병합에 덮인 자리를 `row.cells[j] is None` 으로 내놓고 `extract()` 는 같은 자리에 빈 문자열을 준다. **두 소스를 겹쳐 봐야 "병합에 덮인 자리"와 "그냥 빈 칸"이 갈린다** — `extract()` 만 보면 구분이 안 되고, "PDF 는 병합 정보가 없다"고 잘못 결론 내기 쉽다(실제로 그 전제로 유료 승급을 검토한 적이 있다). `pdf.py:_fill_merged_cells()` 가 채운다. **가로가 세로를 이긴다** — 위에 앵커가 있는 `None` 칸 661개 중 **226개(34.2%)가 왼쪽 칸이 이 열까지 뻗은 가로 병합이기도 해서**(실측 PDF 5개·칸 3,503개), 위만 보고 되풀이하면 그 226칸에 엉뚱한 값이 들어간다. 그래서 가로를 먼저 판정하고 아닐 때만 세로로 채운다
- **PDF 는 한 표를 여러 리전으로 쪼갠다**: `find_tables()` 가 시행령 4쪽의 표 하나를 5개로 나눠 내놓았다. splitter 는 각 조각의 첫 행을 헤더로 되풀이하므로 실제 열 이름이 첫 조각에만 남는다. `pdf.py:_merge_split_tables()` 가 **세로 간격 12pt 미만 · 좌우 끝 ±2pt 일치 · 열 수 동일**을 모두 만족할 때만 되붙인다. 임계값 12 는 관측값이 아니라 **분포의 틈**에서 나왔다 — 실측(PDF 5개·리전 103개·인접쌍 36개)으로 조각난 표는 간격 6.8, 별개 표·상자는 전부 18.8 이상이라 2.8배 틈이 있다. **이 되붙이기를 외부 API 로 대신할 수 없다** — 핸들러가 리전 하나의 blob 만 보내 LlamaParse 는 조각 하나밖에 못 본다
- **HWPX 가로 쪽**: 한컴은 가로 쪽에서도 `hp:pagePr` 의 `width`/`height` 에 물리 용지(세로) 치수를 그대로 넣고 방향은 `landscape` 속성으로만 표시한다. **이름과 반대로 `NARROWLY` 가 가로, `WIDELY` 가 세로다**(실측: 시행령 개정안 안내서 가로쪽 판 — 두 섹션 모두 width=59528 height=84188 인데 WIDELY 섹션의 최상위 문단 vertpos 가 69898 까지 올라가 71435=height-여백 에 육박한다. 가로였다면 46775 를 넘을 수 없다). 그래서 가로 섹션의 본문 높이는 `width` 에서 얻는다. 안 가르면 53% 과대평가해 쪽 넘긴 표를 한 쪽으로 세고 이후 쪽 번호가 밀린다
- **HWPX 쪽 수를 분모로 쓰지 말 것** (미해결): 로더가 세는 HWPX 쪽 수가 문서에 따라 크게 부풀어 있다. 실측 — 신뢰가능 AI 가이드는 PDF 판이 28쪽(파일 구조에서 직접 확인)인데 HWPX 판을 97쪽으로 센다. 본문 분량은 20,895자 vs 21,843자로 거의 같다. 쪽당 요소 수 중앙값이 PDF 19 / HWPX 5 이고 요소가 1개뿐인 쪽이 21개다. 섹션은 14개뿐이라 `start_section()` 만으로는 설명이 안 되고 `vertpos` 감소 휴리스틱의 과다 발화로 보인다. 시행령 문서는 사람이 확인한 26쪽과 맞으므로 일률적으로 깨진 것은 아니다. **쪽 수로 비율을 계산하는 판정(머리말 등)을 만들 때 이 값을 분모로 쓰면 문서마다 결과가 뒤집힌다** — `cleaner/headers.py` 가 등장 횟수 비율 대신 걸친 폭을 쓰는 이유다
- **요소는 청크가 아니다 — 아주 잘다**: 실측으로 요소 자수 중앙값이 PDF 23 / HWPX 15 / 시행령 40 이고, **1200자를 넘는 텍스트 요소가 세 문서 전부 0개**다. PDF 는 PyMuPDF 블록 단위라 707개 중 349개(49%)가 20자 미만이다. 그래서 청크 상한은 텍스트에서 사실상 발화하지 않고 **큰 표에서만** 걸린다(시행령의 67행 표 3,243자). 청킹을 "자르는 일"로 설계하면 20자짜리 청크가 절반이 된다
- **한국어 청크는 문자 ≈ 토큰이다**: cl100k_base(`text-embedding-3-*`)로 잰 청크의 문자/토큰 비율이 중앙값 1.03~1.09 / 최소 0.90 이다. 한글 낱글자는 UTF-8 바이트로 쪼개져 글자당 2~3토큰이지만, 청크에는 공백·숫자·영문·마크다운 표가 섞여 있어 희석된다. **그래서 `max_chunk_chars` 를 문자로 두는 것이 토큰 상한까지 지켜준다** — 실측 최대 청크가 1,201토큰으로 모델 입력 상한 8191 의 15% 다. 낱글자 비율(2~3토큰)을 그대로 적용해 상한을 잡으면 청크가 3배로 잘아진다
- **HWPX 제목은 스타일로 못 찾는다**: 한컴이 서식을 문단 스타일이 아니라 `charPr` 로 직접 박는 문서가 흔하다. 실측으로 신뢰가능 AI 가이드 HWPX 의 `Contents/header.xml` 에는 스타일이 `Normal` **하나뿐**이었다(글자 높이는 240~2617 로 살아 있다). 그래서 `headings.py` 가 글자 크기를 쓴다. 판정 규칙 셋(문서별 최빈값 / 배율 아닌 부등호 / `min` 이지 `max` 가 아님)은 그 파일 docstring에 근거와 함께 있다 — **되돌리면 강조가 제목이 되거나 제목이 통째로 사라진다**
- **PDF 제어문자**: PDF는 글머리 기호를 제어 코드포인트로 떨어뜨린다(실측: 신뢰가능 AI 가이드 PDF에서 U+0001 192개 / U+0007 47개 / U+0008 4개, `작성 \x07김동현 수석` 처럼 본문 한가운데에 박혀 있다). 같은 문서의 HWPX 판은 0개다 — 정리 단계가 실제로 필요한 쪽은 PDF 경로다
- **`\n` 은 지우면 안 된다**: 실측 560개가 전부 표 요소에 있는 마크다운 표의 행 구분자다. 같이 지우면 `| 구 분 | 추진내용 || --- |` 처럼 한 줄로 뭉개져 표가 통째로 깨진다
- **Cf 제거의 감수 사항**: ZWJ(U+200D)도 Cf라서 이모지 결합(가족 이모지 등)이 쪼개진다. 한국 공문서 코퍼스에는 사실상 안 나와 예외로 두지 않았다. 살려야 하는 코퍼스가 생기면 `cleaner/characters.py` 의 `_KEEP` 에 추가한다
- `.hwp`/`.doc`/`.ppt` 는 감지는 되지만 파싱하지 않는다. `UnsupportedLegacyLoader` 가 변환 안내와 함께 415로 거부한다. 정확한 안내를 주려면 업로드 자체는 받아야 하므로 `SUPPORTED_EXTENSIONS` 에는 포함돼 있다
- Tesseract는 외부 설치가 필요하다(5.5.0 + 한국어 traineddata). PATH에 없으면 `.env` 의 `TESSERACT_CMD` 로 지정한다
- zip bomb 방어는 `loader/detect.py:MAX_UNCOMPRESSED_BYTES`(500MB) 에 있다
- **qdrant 저장 경로는 한 번에 한 프로세스만 연다**: 로컬 파일 모드가 디렉터리에 배타 잠금을 걸어, 두 번째 클라이언트는 `RuntimeError: Storage folder ... is already accessed by another instance` 로 죽는다. 서버(`uvicorn`)를 띄운 채 스크립트로 색인하면 이것에 걸린다 — 한쪽을 내리거나 `QDRANT_PATH` 를 갈라야 한다. `.qdrant/` 는 `.gitignore` 에 있다

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
4. 합성 지점을 정한다. **`ingest()` 에 끼우는 것이 기본값이 아니다** — 그 반환은 곧 공개 HTTP 응답 스키마라 바꾸는 순간 API 결정이 된다. splitter·embedding·indexing 셋 다 `ingest()` 밖에 있고, 대신 `pipeline.py:index_document()` 라는 **두 번째 진입점**을 만들어 이었다. 새 단계도 소비자가 어디에 있는지부터 본다
5. 외부 API를 부르는 단계라면 `tests/conftest.py` 의 `offline_config` 에 그것을 끄는 설정을 넣는다. 기본 테스트는 키 없이 통과해야 한다. **`ingest()` 에 안 끼운 단계도 마찬가지다** — 설정 객체가 `.env` 를 직접 읽으므로 안 지우면 개발자 머신의 실키가 픽스처에 실린다
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
