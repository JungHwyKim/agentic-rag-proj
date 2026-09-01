# loader/content — 계약 위반 둘을 닫고 죽은 코드 셋을 지웠다

- 작업일: 2026-08-22
- 검증 방법: `ruff check .` · `pytest -q` · 가짜 클라이언트/몽키패치 재현.
  **외부 API 호출 없음**(`-m integration` 미실행)

## 1. 무엇을 바꿨나

- `rag_pipeline/ingestion/loader/content/image_vision.py`
  응답 해석부를 `try` 안으로 넣고 카운터를 그 앞으로 올렸다. 모듈 docstring 에 왜 그런지를 남겼다
- `rag_pipeline/ingestion/loader/content/image_ocr.py`
  Tesseract 호출 둘을 감쌌다. `OcrResult.text` 를 지웠다. `run_ocr`·`extract_text` 에
  선택 인자 `ctx` 를 더해 경고를 남길 수 있게 했다
- `rag_pipeline/ingestion/loader/content/base.py`
  `ContentHandler` Protocol 과 그 때문에 남아 있던 `Protocol`·`Element` import 를 지웠다
- `rag_pipeline/ingestion/loader/content/__init__.py`
  패키지 레벨 재수출과 `__all__` 을 지웠다. 순환 고리가 함께 사라졌다
- `tests/ingestion/loader/content/test_handler_failure_paths.py` (신규)
  "호출은 성공하고 응답만 어긋나는" 경로 6건
- `.claude/skills/rag-code-review/references/contracts.md`
  3(a)의 예시가 실제로는 규약을 어기고 있었다 — 지목 대상을 고치고 교훈을 남겼다

## 2. 설계 결정

### OCR 예외는 저수준에서 삼키고, 경고는 `ctx` 가 있는 곳에서만 남긴다

`run_ocr()`·`extract_text()` 는 `LoadContext` 를 안 받는다. `classify()` 도 마찬가지다.
그래서 세 갈래였다.

- **호출부마다 `try`** — 경고는 남지만 감사에서 N6 으로 지적한 "가드가 여러 곳에 흩어지는"
  패턴을 그대로 반복한다. 실제로 `router.py:69-72` 와 `image_ocr.py:146` 에 같은 경고
  문자열이 글자 그대로 복제돼 있는 상태를 이미 확인했다
- **저수준에서 조용히 삼키기** — 가드는 한 곳이지만 경고가 통째로 사라진다
- **저수준 흡수 + 선택 인자 `ctx`** ← 골랐다

가른 근거는 **어느 경로가 사용자에게 보이는가** 하나다. `DocumentMetadata.warnings` 로
나가는 경로는 `handle()` 뿐이고 거기는 `ctx` 를 갖고 있다. `classify()` 경로는
`Decision.reason` 이 "글자 0자, 신뢰도 0"으로 흔적을 남기고 `test_classify.py` 가 그 값을
계약으로 검사한다. 그래서 `ctx` 를 **선택 인자**로 두면 두 경로가 각자 맞는 방식으로
기록된다.

### N9 를 `contracts.md` 2절 위반으로 쓰지 않았다

폭주한 서브에이전트가 이걸 "C2와 동급 Critical"로 올리라고 했는데 받지 않았다. 2절은
**유료 외부 API** 규약이고 *"내려갈 층이 있어서"* 성립한다(LlamaParse→PyMuPDF,
Vision→OCR). OCR 은 로컬·무료이고 **사슬의 맨 아래**라 아래에 층이 없다. 위험의 크기는
같아도 **근거가 다르다** — 계약 위반이 아니라 계약 밖 강인화다.

이 구분을 안 남기면 다음 사람이 "OCR 도 폴백 규약 대상"으로 일반화한다. `image_ocr.py`
모듈 docstring 에 그대로 적었다.

### `source_format` 은 이번에 안 지웠다

감사가 확정한 죽은 코드 넷 중 하나인데(쓰기 33곳 / 읽기 0곳) 뺐다. 로더 8파일·테스트
4파일 33곳을 건드려 **리스크 성격이 다르고**, 같은 커밋에 섞이면 회귀가 났을 때 원인을
못 가른다. 나머지 셋은 각각 한두 파일이라 함께 넣었다.

### `contracts.md` 는 예시만 바꾸지 않았다

3(a)가 `image_vision._call_vision()` 을 모범으로 **이름까지 대며** 인용했는데 그 함수가
규약을 어긴 유일한 자리였다. 예시를 `table_vision` 으로 갈아끼우고 끝낼 수도 있었지만,
그러면 **같은 일이 또 일어난다.** 교훈은 예시가 아니라 절차다 — "외부 API 핸들러가 여럿이면
리뷰 때 나란히 대조한다. reference 가 예시로 든 함수라고 검사에서 빼지 않는다"를 함께 적었다.

## 3. 지표

전문은 [verify_log/2026-08-22-loader-content-정리.md](../verify_log/2026-08-22-loader-content-정리.md) 에 있다.

- 수정 전후 대조 — `image_vision` 과금/장부 **1 / 0 → 1 / 1**, 예외 샘 → 안 샘
- pytest 577 → **599 passed, 1 skipped** · ruff 통과 (이번 작업이 더한 것은 **6건**,
  나머지 16건은 동시 작업자의 `evaluation/`)

## 4. 실패 분석

### 답안지를 에이전트가 읽을 수 있는 곳에 뒀다

감사 설계에서 "제외 목록을 에이전트에 주지 않는다"를 정해 놓고, 그 답안지를 **에이전트도
읽을 수 있는 스크래치패드**에 뒀다. 주지 않는 것과 도달 못 하게 하는 것은 다르다. 실제로
Fable 2회차가 재개된 뒤 `answer-key.md` 를 읽었다.

전사를 확인해 1차 리포트(138줄)는 접근(148줄 이후) **전**이라 채점은 무사했지만, 나머지 세
회차는 전사가 0바이트로 정리돼 **검증 자체가 불가능**해졌다. 다음에 같은 대조를 하면
답안지를 에이전트가 못 읽는 곳에 둔다 — `plan_log/하네스-확장.md` 6회차에 적었다.

### 회차 하나가 진짜를 틀린 근거로 버렸다

Opus 1회차가 `OcrResult.text` 를 *"테스트가 값을 직접 본다"* 며 기각했다. 확인해보니
`test_run_ocr_is_metrics_only` 가 읽는 것은 `metrics.char_count` 이고 `.text` 가 아니다.
같은 모델의 2회차는 같은 항목을 삭제 권장했다. **n=2 로 돌린 것이 이걸 잡았다** — 1회씩만
돌렸으면 이 차이가 그대로 "모델 차이"로 읽혔을 것이다.

### 중간 상태에서 훅이 막았고, 그게 맞았다

`OcrResult` 필드를 지우고 생성자를 아직 안 고친 사이에 PostToolUse 훅이 테스트 실패로
막았다. 편집 묶음의 절반만 적용된 상태였고, 훅이 없었으면 그대로 다음 작업으로 넘어갔을
것이다. 되돌린 것은 없다.

## 5. 회귀 테스트

`tests/ingestion/loader/content/test_handler_failure_paths.py` 6건.

| 4절/감사 항목 | 막은 테스트 |
|---|---|
| C2 — 응답 해석부가 `try` 밖 | `test_vision_bad_response_shape_falls_back` (`choices=[]` · `message` 없음 두 경우) |
| C2 — 카운터가 해석 뒤 | 같은 테스트의 `vision_calls == 1` 단언 |
| N9 — Tesseract 예외가 샘 | `test_run_ocr_survives_tesseract_error` · `test_extract_text_survives_tesseract_error` |
| N9 — `ctx` 없는 경로 | `test_run_ocr_without_ctx_is_silent` |
| N9 — 사슬 전체 | `test_ocr_handler_returns_empty_instead_of_raising` |

**죽은 코드 3건은 전용 테스트로 막지 않았다.** 지운 것이라 막을 대상이 없다 — 되살아나면
ruff 나 기존 577건이 잡는다.

## 6. 재현

```powershell
$env:PYTHONIOENCODING="utf-8"
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest -q tests/ingestion/loader/content/test_handler_failure_paths.py
```

## 7. 남은 것

감사가 확정했지만 이번에 안 고친 것들이다. 전부 **참으로 확인**만 해뒀다.

| # | 무엇 | 왜 미뤘나 |
|---|---|---|
| N1 | `ContentRegion.source_format` — 쓰기 33곳 / 읽기 0곳 | 로더 8파일을 건드려 별도 커밋. 2절 참조 |
| N3 | `table_vision.py:54` 주석이 없는 상수를 가리킴 | **그 파일을 다른 작업자가 편집 중** |
| N4 | `test_pdf.py:66` LlamaParse 가짜가 아직 문자열 (`test_router.py:61` 은 `pages` 모양) | 감사 범위 밖(테스트 파일)이라 판정을 넘김 |
| N6 | `open_image` 가 `router.py:69`·`image_ocr.py:146` 두 곳 — 이중 디코드 + 경고 문자열 복제 | 핸들러 시그니처가 늘어 별도 판단 |
| N8 | `document/base.py:28`·`:43` 이 같은 `self.config.content` 를 각각 넘김 | 테스트 21곳 churn |
| — | `classify` SKIP 이 `ctx.warn()` 없이 `[]` 반환 — 장식 필터에 걸린 이미지가 흔적을 안 남긴다 | cleaner 의 `removed_texts` 같은 자리가 필요한지 미정 |

그리고 **이 작업트리에 다른 작업자가 동시에 있다.** `table_vision.py`·`router.py`·
`evaluation/` 이 이번 세션 중에도 바뀌었다. 커밋할 때 내 변경만 골라 담아야 한다.
