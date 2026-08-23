# loader/content — 감사가 남긴 N6·N8 을 닫았다. 나머지는 남의 편집과 겹쳐 미뤘다

- 작업일: 2026-08-23
- 검증 방법: `ruff check .` · `pytest -q` · 가상 축 2종(설정 출처 · `open_image` 호출 수) ·
  실파일 11개 A/B 대조. **외부 API 호출 없음**(`-m integration` 미실행, 키를 `None` 으로 둠)

## 1. 무엇을 바꿨나

- `rag_pipeline/ingestion/loader/content/router.py`
  생성자를 없애 `ContentRouter()` 를 무상태로 만들고, 설정을 `ctx.config` 에서만 읽는다(N8).
  이미 연 이미지를 `image_ocr.handle(..., image=image)` 로 넘긴다 — OCR 경로와 Vision 실패
  폴백 경로 둘 다(N6)
- `rag_pipeline/ingestion/loader/content/image_ocr.py`
  `handle()` 에 선택 인자 `image` 를 더했다. `None` 일 때만 `open_image()` 로 연다
- `rag_pipeline/ingestion/loader/document/base.py`
  `ContentRouter(self.config.content)` → `ContentRouter()` 한 줄
- `tests/ingestion/loader/content/test_router.py`
  호출 20곳 + 승급 테스트 넷의 `mark_cells` 를 int 에서 **자리 목록**으로

**이번에 만든 것은 없다.** 전부 `task_log/2026-08-22-loader-content-정리.md` §7 "남은 것"
표에 있던 항목이다.

## 2. 설계 결정

### 핸드오프를 그대로 적용하지 않고 트리와 먼저 대조했다

핸드오프는 7건을 "전부 확정, 판단 유보 없음" 으로 넘겼다. 실제로는 **4건이 이미 적용돼
있었고**, 그중 둘은 핸드오프 diff **보다 나은** 형태였다 — `image_ocr` 이 Tesseract 호출뿐
아니라 `preprocess` 까지 `try` 안에 넣고 선택 인자 `ctx` 로 경고를 남긴다. 핸드오프대로
덮어썼으면 **`preprocess` 가 던지는 경로가 다시 열리고 경고가 사라졌다.**

기준선도 낡아 있었다. 핸드오프는 `577 passed` 를 기준으로 삼았는데 실제로는 599 였고,
작업이 끝날 무렵에는 648 이었다. 그 수치로 "회귀 없음"을 판정했으면 근거가 통째로 틀린다.

**핸드오프는 대조하기 전까지 가설이다.** 이걸 `plan_log/하네스-확장.md` "확정된 것" 에 올렸다.

### N8 을 핸드오프 형태로 안 하고 생성자를 통째로 없앴다

핸드오프안은 메서드만 `ctx.config` 로 바꾸고 `self.config = config` 대입은 "하위 호환용
시그니처 유지" 로 남기라고 했다. 받지 않았다 — 그러면 **읽는 곳이 0인 속성**이 새로 생기는데,
**같은 배치의 N1 이 정확히 그 이유로 `source_format` 을 지우는 항목**이다. 한 커밋 안에서
같은 기준이 정반대로 적용된다.

대신 `__init__` 을 지워 `ContentRouter()` 로 만들었다. 값은 셋이다 — 출처가 하나가 되고,
라우터가 무상태가 되고, `ContentRouter(config)` 라는 **호출 형태 자체가 없어져** 되돌아갈
길이 막힌다. 값은 호출부 21곳(제품 1 · 테스트 20)이다.

**다만 이것은 회귀 수정이 아니다.** `document/base.py:load()` 가 `ContentRouter` 와
`LoadContext` 에 `self.config.content` **같은 객체**를 넘기고 테스트 생성 지점도 전부
그렇다 — 오늘은 갈릴 수 있는 입력이 실재하지 않는다. 고친 것은 *갈릴 수 있는 구조*이고,
그래서 검증에서 이 축은 **가상 데이터로만** 만들 수 있었다.

### N1 을 같은 배치에 안 넣었다 — 근거는 주제가 아니라 mtime 이다

`source_format` 제거는 35곳(선언 1 · 제품 19 · 테스트 15)을 짚는데, 그중
`pdf.py`·`content/base.py`·`test_table_vision.py` 셋이 **그때 busy 이던 다른 세션의 편집
대상**이었다(마지막 변경 16:35~16:45). 브랜치가 갈린 게 아니라 **같은 디스크**라 저쪽이
파일을 다시 쓰면 내 편집이 **에러 없이** 사라진다 — git 충돌로도 안 드러난다.

반대로 N6·N8 이 짚는 넷(`router.py`·`image_ocr.py`·`document/base.py`·`test_router.py`)은
마지막 변경이 전부 14:13 이전이었다. **가르는 선이 우연히 깨끗해서** 범위를 주제가 아니라
mtime 으로 정했다. 저쪽 회고도 같은 결론에 다른 근거로 도달해 있었다("로더 8파일을 건드려
별도 커밋").

### N6 을 "성능 개선" 으로 팔지 않았다

여는 횟수가 리전당 2→1 로 준다는 것은 쟀지만(가상), **디코드 시간은 안 쟀다.** "두 번째
디코드가 사라진다"는 PIL 이 `load()` 한 픽셀을 들고 있다는 성질에서 따라오는 것이지 이번에
잰 값이 아니다. 벽시계로 재려 했으나 **동시에 다른 세션이 CPU 를 쓰고 있어** 접었다.

그래서 이 변경의 확인된 값어치는 둘로만 적는다 — 여는 횟수 2→1, 그리고 **산출이 안 바뀐다는
것**(실파일 11개 전부 요소 수·텍스트 해시 동일). 뒤엣것이 더 중요하다. 산출이 바뀌었으면
호출 수가 줄어도 받을 수 없는 변경이다.

`image` 를 **필수 인자로 안 만든 이유**는 라우터를 거치지 않고 `handle()` 을 직접 부르는
경로가 실재하기 때문이다(`test_handler_failure_paths.py`). 필수로 만들면 그 테스트가 깨지고,
깨지는 쪽이 폴백 규약을 지키는 쪽이다.

## 3. 지표

전문은 [verify_log/2026-08-22-loader-content-정리.md](../verify_log/2026-08-22-loader-content-정리.md)
**2회차**에 있다. 1회차와 같은 주제를 이어서 재는 것이라 파일을 나누지 않았다 — 이 회고만
`task_log/` 에서 갈라져 있다(세션도 결정도 다르다).

- `open_image` 호출 : 리전당 **2 → 1** (`image_handler` 가 `ocr`·`hybrid` 둘 다)
- 실파일 A/B : 요소 수·텍스트 해시가 **11 / 11 동일** (N6 전후)
- pytest **648 passed, 2 skipped, 7 deselected** · ruff 통과.
  **+49 는 이번 것이 아니다** — 이번 변경이 더한 테스트는 0건이다
- E2E : 안 함

## 4. 실패 분석

### 훅이 보여준 실패를 내 코드 탓으로 볼 뻔했다

작업 초반 `auto_test_check` 훅이 `table_vision.py:327` 의 `len(region.mark_cells)` 로 17건
실패를 보여줬다. 그런데 곧바로 전체를 돌리니 **599 passed 로 초록**이었다. 훅이 보여준 것은
다른 세션이 `mark_cells` 를 개수에서 자리 목록으로 옮기던 **중간 상태**였고, 내가 명령을
돌린 시점에는 이미 지나가 있었다.

여기서 두 번째로 틀릴 뻔했다 — 그 낡은 출력을 쫓아가 "`base.py` 선언과 `router.py` 비교는
아직 int 다" 를 찾아 **Critical 로 계획에 넣었는데**, 계획을 확정하려고 다시 재보니 그
세션이 16:35 에 양쪽을 이미 닫아 뒀다. 내가 한 것은 `test_router.py` 가 실제 생산자 타입을
덮게 한 것뿐이다.

교훈은 **"훅 출력도 시점이 있다"** 이고, 이 저장소에서는 **동시 세션이 있으면 어떤 관측도
찍은 시각과 함께 봐야 한다.** 계획 문서에 파일 mtime·해시를 적어 둔 이유가 이것이다.

### 검증 도중 남의 테스트가 빨간색이었고, 그것도 내 것이 아니었다

전체 스위트에서 `test_splitter_real.py::test_merging_removes_tiny_chunks[…_fixed.hwpx]` 가
한 번 실패했다(`8 <= 6`). 그 세션이 **그 픽스처를 방금 넣고 기대값을 잠그는 중**이었고
(테스트 파일 mtime 09:34), 몇 분 뒤 같은 파일만 돌리니 `13 passed, 2 skipped` 였다.

**"내 변경이 아니다" 를 시각만으로 주장하지 않았다.** 실파일 11개 A/B 로 산출이 안 바뀐다는
것을 따로 보였다(3절). 시각은 정황이고 A/B 가 근거다.

### 되돌린 것은 없다

## 5. 회귀 테스트

새로 만든 테스트는 **없다.** 이번 변경은 기존 계약을 그대로 두고 형태만 바꾼 것이라 막을
새 대상이 없다 — 되살아나면 아래가 잡는다.

| 2절/4절 항목 | 막는 테스트 |
|---|---|
| `ContentRouter(config)` 로 되돌아가기 | `test_router.py` 20곳이 `ContentRouter()` 로 부른다. 생성자를 되살려도 인자를 **요구**하는 순간 전부 깨진다 |
| 라우터가 `ctx.config` 아닌 곳을 보기 | 같은 파일 — 설정을 `ctx` 에만 실어 보내므로 다른 출처를 보면 판정이 안 바뀐다 |
| `mark_cells` 비교를 `> 0` 으로 되돌리기 | `test_hybrid_falls_back_when_escalation_fails` — hybrid · blob · 비어있지 않은 **자리 목록**을 모두 만족해 비교 연산에 실제로 도달한다 |
| `image=` 를 필수 인자로 만들기 | `test_handler_failure_paths.py` — `handle()` 을 `image` 없이 직접 부른다 |
| N6 이 산출을 바꾸기 | **막는 테스트가 없다.** 실파일 A/B 로 한 번 확인했을 뿐이고, `tests/fixtures/` 가 gitignore 라 회귀로 못 박았다. `regression/baseline.json` 이 그 자리인데 이번엔 안 돌렸다 |

## 6. 재현

```powershell
$env:PYTHONIOENCODING="utf-8"
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest -q tests/ingestion/loader/content/test_router.py
```

## 7. 남은 것

`task_log/2026-08-22-loader-content-정리.md` §7 표에서 이번에도 안 닫은 것들이다.
그 표의 N6·N8 줄은 이 기록으로 닫혔다.

| # | 무엇 | 왜 미뤘나 |
|---|---|---|
| N1 | `ContentRegion.source_format` — 쓰기 34곳 / 읽기 0곳 | 짚는 세 파일을 다른 세션이 편집 중이었다. **그 세션이 idle 이 되면 따로 한다** |
| N3 | `table_vision.py` 주석이 없는 상수를 가리킴 | 같은 이유. 1회차에서도 같은 이유로 미뤘다 |
| N4 | `test_pdf.py` 의 LlamaParse 가짜가 아직 문자열 | 1회차와 같음 |
| — | `classify` SKIP 이 `ctx.warn()` 없이 `[]` 반환 | cleaner 의 `removed_texts` 같은 자리가 필요한지 미정 |

그리고 **N6 을 회귀로 못 박지 못했다**(5절). `regression/baseline.json` 대조를 한 번
돌려두면 그 자리가 채워진다 — 이번엔 다른 세션이 로더를 고치는 중이라 기준선이 흔들려
안 돌렸다.
