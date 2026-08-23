# loader/content 감사 후속 — 계약 위반 2건 수정과 죽은 코드 3건 삭제

- 검증일: 2026-08-22
- 대상: 감사 결과([2026-08-22-loader-content-감사-모델대조.md](2026-08-22-loader-content-감사-모델대조.md))를 코드에 반영
- 비용: **외부 API 호출 없음.** `_make_client()` 를 갈아끼운 가짜 클라이언트와
  `pytesseract` 몽키패치만 썼다. `-m integration` 미실행

## 1. 확인하려 한 것

"규칙이 도는가"다. 임계값을 정하는 검증이 아니다.

1. `image_vision` 이 이제 `table_vision` 과 **같은 결과**를 내는가 (C2 수정)
2. Tesseract 예외가 `route()` 밖으로 안 새는가 (N9 수정)
3. 죽은 코드 3건을 지워도 아무것도 안 깨지는가

## 2. 가상 데이터

무엇을 고립시켰나: **외부 호출이 성공한 뒤 응답만 어긋나는 축.** 호출 실패(예외)는 기존
테스트가 이미 덮고 있어 새로 안 만들었다.

```python
# 응답 모양이 어긋나는 두 경우 — 호출은 성공했으므로 과금은 이미 됐다
SimpleNamespace(choices=[])                      # choices 가 비었다
SimpleNamespace(choices=[SimpleNamespace()])     # message 가 없다

# Tesseract 내부 오류
class _FakeTesseractError(RuntimeError): ...
mocker.patch.object(image_ocr.pytesseract, "image_to_data", side_effect=_FakeTesseractError("boom"))
mocker.patch.object(image_ocr.pytesseract, "image_to_string", side_effect=_FakeTesseractError("boom"))

# 크기 필터(100x100)를 통과하는 입력
Image.new("RGB", (400, 200), "white")  # PNG 로 저장
```

| 입력 | 기대 | 실측 | 판정 |
|---|---|---|---|
| `image_vision.handle()` + `choices=[]` | `[]` 반환, 예외 없음 | `[]` | 통과 |
| 〃 — 장부 | `vision_calls == 1` (과금됐으므로) | 1 | 통과 |
| 〃 — 경고 | "해석하지 못해" 남음 | 남음 | 통과 |
| `image_vision.handle()` + `message` 없음 | 위와 동일 | 동일 | 통과 |
| `run_ocr(ctx=ctx)` + Tesseract 오류 | `char_count=0`, 경고 남음 | 그대로 | 통과 |
| `run_ocr()` **ctx 없이** + 오류 | 예외 없음, `char_count=0` (경고는 못 남김) | 그대로 | 통과 |
| `extract_text(ctx=ctx)` + 오류 | `""`, 경고 남음 | 그대로 | 통과 |
| `image_ocr.handle()` + 양쪽 오류 | `[]`, 예외 없음 | `[]` | 통과 |

### 수정 전후 대조 — 같은 스크립트, 같은 입력

감사 때 결함을 증명한 `scratchpad/verify_key.py` 를 그대로 다시 돌렸다.

| | 수정 전 | 수정 후 |
|---|---|---|
| `image_vision` 과금/장부 | 1 / **0** | 1 / **1** |
| `image_vision` 예외 | **`IndexError` 가 `handle()` 밖으로 샘** | 없음 (`[]` 반환) |
| `image_vision` 경고 | **없음** | "Vision 응답을 해석하지 못해 OCR로 폴백했습니다: IndexError" |
| `table_vision` (대조군) | 1 / 1, 예외 없음 | 변화 없음 |

스크립트의 최종 판정줄이 `C2 는 거짓` 으로 바뀌는데, 그 스크립트는 **두 핸들러의 비대칭을
찾도록** 짜여 있다 — 비대칭이 사라졌다는 뜻이다.

## 3. 실제 데이터

**없음.** 이번 변경은 실패 경로만 건드려 실파일 파싱 산출이 바뀌지 않는다. 기준선 대조를
안 돌린 이유이기도 하다(차이 0 이 기대값이라 정보가 없다).

다만 감사 대상 하나가 **다른 작업자에 의해 바뀌었다** — `table_vision.py` 가 13:41 에
13,260→15,656 bytes (`61f174f8b15c`→`8d32b2088e51`, `_attempt()` 재시도 추가). 이번 수정은
그 파일을 안 건드렸다.

## 4. 테스트 스위트

- ruff   : `All checks passed!`
- pytest : `599 passed, 1 skipped, 7 deselected in 64.45s` (감사 시점 기준선 577)
- skip 사유 : 실파일 픽스처 없는 회귀 테스트 1건 (기존과 같음)

**+22 를 전부 이번 작업으로 세면 안 된다.** 이 작업트리에 다른 작업자가 동시에 있었다 —
`evaluation/` 쪽 16건이 그쪽이고, **이번 작업이 더한 것은 `test_handler_failure_paths.py` 6건**
이다. 중간에 `tests/evaluation/test_qa_set.py` 2건이 실패했는데 그쪽이 `evaluation/qa_set.py`
를 14:01 에 고치는 중이어서였고(제 편집과 동시), 이후 사라졌다. 제 영역만 따로 돌린
`tests/ingestion/loader|cleaner|splitter` 는 그때도 **305 passed, 1 skipped** 로 깨끗했다.

## 5. 판정

- **C2 · N9 — 의도한 변화.** 수정 전후 대조가 2절에 있다
- **죽은 코드 3건 — 의도한 변화.** `ContentHandler`·`content/__init__` 재수출·`OcrResult.text`
  삭제 후 599 통과. `base.py` 의 `Protocol`·`Element` import 도 미사용이 돼 함께 지웠고
  ruff 가 확인했다
- **`tests/evaluation` 실패 2건 — 이번 작업과 무관.** 위 4절 근거

## 6. 재현

```powershell
$env:PYTHONIOENCODING="utf-8"
.\.venv\Scripts\python.exe -m pytest -q tests/ingestion/loader/content/test_handler_failure_paths.py
.\.venv\Scripts\python.exe <scratchpad>\verify_key.py   # 수정 전후 대조용
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest -q
```

## 7. 재지 못한 것

- **실제 Tesseract 오류로는 재현 안 했다.** 몽키패치로만 확인했다. 진짜
  `pytesseract.TesseractError` 를 유발하는 손상 이미지를 못 만들었다
- **실파일 회귀 대조를 안 돌렸다.** 실패 경로만 바뀌어 차이 0 이 기대값이라 정보가 없다.
  틀렸다면 이 판단이 틀린 것이다
- **N4·N6·N8 은 확정만 하고 안 고쳤다** — 아래 task_log 7절 참조
- **N3(`table_vision.py:54` 주석)는 손대지 않았다.** 그 파일을 다른 작업자가 편집 중이다


---

# 2회차 — 2026-08-23 · 남겨 둔 N6·N8 을 닫았다

- 검증일: 2026-08-23
- 대상: 1회차 7절과 `task_log/2026-08-22-loader-content-정리.md` §7 이 남긴 N6·N8.
  `task_log/2026-08-23-loader-content-남은셋.md`
- 비용: **외부 API 호출 없음.** 키를 `None` 으로 두고 이미지·표 경로만 돌렸다. `-m integration` 미실행

## 1. 확인하려 한 것

둘이고, 각각 다른 데이터가 답한다.

1. **N8 — 라우터의 설정 출처가 실제로 `ctx` 하나가 됐는가.** 규칙이 도는가를 묻는 것이라
   가상 데이터다. 실파일로는 **이 축을 만들 수조차 없다** — `document/base.py:load()` 가
   `ContentRouter` 와 `LoadContext` 에 같은 객체를 넣으므로 둘이 갈리는 입력이 실재하지 않는다
2. **N6 — 여는 횟수가 줄면서 산출은 그대로인가.** 횟수는 가상으로 세고, **산출 무변경은
   실파일로** 확인해야 한다. 합성 이미지 하나로는 "안 바뀐다"를 말할 수 없다

## 2. 가상 데이터

무엇을 고립시켰나: **생성 시점 설정과 `ctx.config` 가 어긋난 상황**(축 1)과 **리전 하나를
라우팅할 때의 `open_image` 호출 수**(축 2). 네트워크·과금 없음.

```python
import io
from PIL import Image
from rag_pipeline.config import ContentRoutingConfig
from rag_pipeline.ingestion.loader.content import image_ocr
from rag_pipeline.ingestion.loader.content.base import ContentRegion, ContentType, LoadContext
from rag_pipeline.ingestion.loader.content.router import ContentRouter

# 크기 필터(기본 100x100)를 통과하는 흰 PNG. 글자가 없어 OCR 결과는 빈 문자열이다
buf = io.BytesIO(); Image.new("RGB", (400, 200), "white").save(buf, format="PNG")
BLOB = buf.getvalue()

def reg():
    return ContentRegion(content_type=ContentType.IMAGE, order=0, source_format="pdf",
                         blob=BLOB, blob_kind="png")

def cfg(**kw):
    d = {"openai_api_key": None, "llama_cloud_api_key": None,
         "image_handler": "ocr", "cache_enabled": False}
    d.update(kw)
    return ContentRoutingConfig(**d)

# 축 1 — 생성자는 설정을 안 받고, 판정은 ctx 것만 쓴다
try:
    ContentRouter(cfg()); axis1a = "받는다"
except TypeError:
    axis1a = "TypeError"
axis1b = ContentRouter().route(reg(), LoadContext(config=cfg(image_handler="skip")))
axis1c = ContentRouter().route(
    ContentRegion(content_type=ContentType.TABLE, order=0, source_format="pdf",
                  rows=[["a", "b"]], blob=b"%PDF-x", blob_kind="pdf"),
    LoadContext(config=cfg(table_pdf_handler="hybrid")))[0].metadata["table_source"]

# 축 2 — open_image 호출 수. `drop_image=True` 가 N6 이전 동작(넘긴 이미지를 버린다)
def count_opens(mode, drop_image):
    calls = {"n": 0}
    real_open, real_handle = image_ocr.open_image, image_ocr.handle
    image_ocr.open_image = lambda b: (calls.__setitem__("n", calls["n"] + 1), real_open(b))[1]
    if drop_image:
        image_ocr.handle = lambda region, ctx, *, precomputed=None, image=None: \
            real_handle(region, ctx, precomputed=precomputed, image=None)
    try:
        ContentRouter().route(reg(), LoadContext(config=cfg(image_handler=mode)))
    finally:
        image_ocr.open_image, image_ocr.handle = real_open, real_handle
    return calls["n"]
```

기대값은 돌리기 전에 적었다.

| 입력 | 기대 | 실측 | 판정 |
|---|---|---|---|
| `ContentRouter(config)` 호출 | `TypeError` | `TypeError` | 통과 |
| `ctx` 가 `image_handler="skip"` → 이미지 리전 | 빈 목록 | 빈 목록 | 통과 |
| `ctx` 가 `table_pdf_handler="hybrid"` · `mark_cells` 빈 목록 → 승급 안 함 | `native` | `native` | 통과 |
| `open_image` 호출 수 (`image_handler="ocr"`) | 2 → 1 | 2 → 1 | 통과 |
| `open_image` 호출 수 (`image_handler="hybrid"`) | 2 → 1 | 2 → 1 | 통과 |
| 깨진 `blob` + 성한 `image` → 여는 시도 안 함 | 경고 0건 | 경고 0건 | 통과 |
| 깨진 `blob` + `image` 없음 → 예전대로 스스로 연다 | 경고 1건 | 경고 1건 | 통과 |
| 같은 이미지를 재사용 vs 다시 열기 | 산출 같음 | 산출 같음 | 통과 |

**앞의 셋은 실파일이 만들 수 없는 입력이다.** 설정 출처가 갈린 상황은 실제 경로에 없고,
`ContentRouter(config)` 는 호출 자체가 사라졌다.

## 3. 실제 데이터

**N6 이 파싱 산출을 바꾸는지**만 본다. 같은 프로세스에서 두 번 파싱하되 두 번째는
`image_ocr.handle` 을 감싸 `image=` 를 버리게 했다(= N6 이전 동작). 요소 수와
`order|type|page|text` 전체의 sha256 을 대조한다.

| 파일 | bytes | sha256(12) |
|---|---:|---|
| 2025년 학교기록물 관리 지침.hwpx | 8,919,632 | c20e8eb1cb60 |
| 2026+중등+기초학력+보장+주친+계획.hwpx | 729,765 | b8072ec78b3c |
| 2026+중등+기초학력+보장+주친+계획.pdf | 1,074,313 | a7654b7523e7 |
| [DNA+_2019-5]_…실용가이드.hwpx | 980,726 | 4972e568948b |
| [DNA+_2019-5]_…실용가이드.pdf | 1,097,567 | 331465567f8e |
| [DNA+_2019-5]_…실용가이드_fixed.hwpx | 1,192,672 | d5911340c27e |
| 「2025년 학교기록물 관리 지침」 (1).pdf | 1,158,573 | c50409732078 |
| 국가아동권리보장원…아동용+안내서.pdf | 4,857,477 | c8dc2d532a4d |
| 시행령 개정안 안내서(정보시스템의무사항알림).hwpx | 3,743,407 | c48d65c7872c |
| 시행령 개정안 안내서(정보시스템의무사항알림).pdf | 1,156,767 | 6eab0a32d09e |
| 시행령 개정안 안내서(정보시스템의무사항알림)_가로쪽.hwpx | 3,675,316 | ed3006c0480e |

| 항목 | N6 전 | N6 후 | 판정 |
|---|---|---|---|
| 요소 수 · 텍스트 해시가 같은 파일 | — | **11 / 11** | 차이 0 |

파일별 값(요소 수 / 해시 앞 12자)은 양쪽이 글자까지 같다 — 예: 학교기록물 HWPX
`492 / 88bde30b9483`, 시행령 PDF `440 / 09f0a5671b33`, AI가이드 `_fixed` HWPX
`879 / 24b795bc892b`.

## 4. 테스트 스위트

- ruff   : `All checks passed!`
- pytest : `648 passed, 2 skipped, 7 deselected in 76.07s` (1회차 599)
- skip 사유 : `test_splitter_real.py:151` 2건 — 두 문서(`…실용가이드.pdf`,
  `…실용가이드_fixed.hwpx`)에 **쪼개진 표가 없어서** 스스로 건너뛴다. 픽스처 없음이 아니다

**+49 는 이번 작업 것이 아니다.** 이번 변경이 더한 테스트는 **0건**이다(기존 20건의 호출
형태와 4건의 `mark_cells` 타입만 바꿨다). 늘어난 것은 동시에 돌던 다른 세션들의 몫이고,
그중에는 `tests/fixtures/` 에 새로 들어온 `…실용가이드_fixed.hwpx` 로 열린 실파일 회차도 있다.

중간에 `test_splitter_real.py::test_merging_removes_tiny_chunks[…_fixed.hwpx]` 가 한 번
실패했다. 그 세션이 **그 픽스처를 방금 추가하고 기대값을 잠그는 중**이어서였고(테스트 파일
mtime 09:34), 몇 분 뒤 같은 파일만 따로 돌리니 `13 passed, 2 skipped` 였다. 제 변경이
그 경로를 안 지난다는 것은 위 3절이 따로 보인다.

## 5. 판정

- **N8 — 의도한 변화.** 2절 앞의 셋. 다만 **회귀 수정이 아니다** — 모든 생성 지점이 같은
  객체를 넘기고 있어 오늘은 갈릴 수가 없었다. 고친 것은 *갈릴 수 있는 구조*이지 증상이 아니다
- **N6 — 의도한 변화.** 여는 횟수 2→1(두 모드 모두), 실파일 11/11 산출 동일
- **`mark_cells` 타입 불일치 — 이번 작업 것이 아니다.** 계획 중에 찾았으나 다른 세션이
  16:35 에 `content/base.py` 선언과 `router.py` 비교 연산 양쪽을 닫았다. 이번에 한 것은
  `test_router.py` 승급 테스트 넷이 int 대신 자리 목록을 쓰게 한 것뿐이다
- **pytest +49 — 이번 작업과 무관.** 4절 근거

## 6. 재현

```powershell
$env:PYTHONIOENCODING="utf-8"
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest -q tests/ingestion/loader/content/test_router.py
# 2절 코드를 그대로 돌리면 가상 축 둘이 재현된다 (스크래치패드, 저장소에 안 남긴다)
# 3절 A/B 는 같은 프로세스에서 image_ocr.handle 을 감싸 image= 를 버리고 두 번 파싱한다
```

## 7. 재지 못한 것

- **디코드 시간을 안 쟀다.** 잰 것은 `open_image` **호출 수** 하나다. "두 번째 디코드가
  사라진다"는 PIL 이 `load()` 한 픽셀을 들고 있다는 성질에서 따라오는 것이지 이번에 잰
  값이 아니다. 이미지가 많은 문서에서 벽시계로 재려 했으나 **동시에 다른 세션이 CPU 를
  쓰고 있어** 의미 있는 값이 안 나온다고 보고 접었다
- **N8 이 실제로 막은 사고는 없다.** 갈릴 수 있는 입력이 실재하지 않으므로 "이 수정이
  무엇을 구했나"는 잴 수 없다. 잰 것은 구조뿐이다
- **N1·N3·N4 와 `classify` SKIP 경고는 여전히 안 쟀다.** 1회차 7절과 같은 이유이고,
  N1 은 여기에 하나 더 있다 — 그것이 짚는 세 파일을 다른 세션이 편집 중이었다
- **동시 세션의 편집이 이 수치에 섞였을 가능성**을 완전히 배제하지 못한다. 4절처럼 근거를
  대 갈랐지만, 같은 작업 트리에서 잰 값이라는 사실 자체는 남는다

