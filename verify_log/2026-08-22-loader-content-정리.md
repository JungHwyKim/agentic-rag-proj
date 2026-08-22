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
