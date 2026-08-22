# loader/content 코드 품질 감사 4회 — Opus 5 vs Fable 5 오탐률 대조

- 검증일: 2026-08-22
- 대상: `rag_pipeline/ingestion/loader/content/` 10파일 1,251줄. 코드 변경 없음(읽기 전용 감사)
- 설계 근거: `plan_log/하네스-확장.md` 6회차
- 비용: **외부 API 호출 없음.** 감사 에이전트 4회는 Claude 호출이고 LlamaParse/OpenAI 과금
  경로는 안 탔다. 재현 스크립트도 `_make_client()` 를 갈아끼워 실제 SDK 를 안 부른다.
  `-m integration` 미실행

## 1. 확인하려 한 것

둘이다.

1. **규칙이 도는가** — 답안지의 C1·C2 주장이 실제로 성립하는가 (실행 검증)
2. **임계값이 아니라 비율** — 같은 입력·같은 프롬프트에서 두 모델이 **의도된 설계를 몇 번
   삭제 권장하는가**(오탐률). 진성 발견의 recall 은 사람 라벨링이 없어 못 잰다

답안지(`scratchpad/answer-key.md`)는 **4회 리포트를 보기 전에 확정**했다. 사후 합리화를
막기 위한 순서다.

## 2. 가상 데이터

무엇을 고립시켰나: **OpenAI 가 `choices=[]` 를 돌려준 단 한 축.** 두 Vision 핸들러에 같은
가짜 응답을 주고 예외·카운터·경고가 어떻게 갈리는지만 본다. 네트워크·과금 없음.

```python
class EmptyChoices:
    """OpenAI 가 choices 를 비워 돌려준 상황. 콘텐츠 필터·상류 장애에서 실제로 나온다."""
    choices: list = []


class FakeClient:
    def __init__(self):
        self.calls = 0
        outer = self
        class _C:
            class completions:
                @staticmethod
                def create(**kw):
                    outer.calls += 1
                    return EmptyChoices()
        self.chat = _C()


# 모듈 레벨 _make_client 를 갈아끼운다 — contracts.md 6절의 모킹 이음새
setattr(module, "_make_client", lambda _k: fake)

# image_vision 입력: 800x600 흰 PNG
buf = io.BytesIO(); Image.new("RGB", (800, 600), "white").save(buf, format="PNG")

# table_vision 입력: 400x300 단일 쪽 PDF + 빈 칸이 있는 격자
doc = pymupdf.open(); doc.new_page(width=400, height=300); pdf = doc.tobytes()
rows = [["a", "b"], ["", ""]]
```

| 입력 | 기대 | 실측 | 판정 |
|---|---|---|---|
| `image_vision.handle()` + `choices=[]` — 예외 | `handle()` 밖으로 안 샘 | **`IndexError` 가 샘** | **계약 위반** |
| `image_vision.handle()` + `choices=[]` — 장부 | 과금 1 = `vision_calls` 1 | 과금 1 / **장부 0** | **계약 위반** |
| `image_vision.handle()` + `choices=[]` — 경고 | `ctx.warn()` 남음 | **없음** | **계약 위반** |
| `table_vision.handle()` + `choices=[]` — 예외 | 안 샘 | 안 샘 (`[]` 반환) | 통과 |
| `table_vision.handle()` + `choices=[]` — 장부 | 과금 1 = 장부 1 | 과금 1 / 장부 1 | 통과 |
| `table_vision.handle()` + `choices=[]` — 경고 | 남음 | "표 Vision 응답을 해석하지 못해…" | 통과 |

기계적 확인(같은 스크립트 묶음):

| 항목 | 기대 | 실측 | 판정 |
|---|---|---|---|
| `content/` 안 `_make_client` 정의 수 | 3 | 3 | 통과 |
| `# noqa: BLE001` 개수 | 7 | 7 | 통과 |
| 캐시 salt 분리 (`vision:` / `table_vision:`) | 각 1 | 각 1 | 통과 |
| `max_vision_calls_per_doc` 참조 파일 수 | 2 | 2 | 통과 |
| 미사용 인자(ruff ARG) | 2 (`table_native`·`text_native` 의 `ctx`) | 2 | 통과 |
| `handle()` 정의 수 | 5 | **6** | **답안지 오류 — 정정** |
| `image_to_data` 출현 | 1 | **2** (1건은 독스트링) | **답안지 오류 — 정정** |
| `ContentHandler` AST 참조 (정의 제외) | — | **0** | C1 성립 |

## 3. 실제 데이터

여기서 "실제 데이터"는 `tests/fixtures/` 가 아니라 **감사 대상 소스 자체**다. 저장소에
커밋돼 있지만 곧 바뀌므로 신원을 남긴다.

| 파일 | bytes | sha256(12) | 줄 |
|---|---:|---|---:|
| table_vision.py | 13,260 | 61f174f8b15c | 288 |
| image_vision.py | 10,667 | 4a7fb2c63340 | 257 |
| base.py | 5,417 | b1148dde2260 | 134 |
| classify.py | 5,422 | d1998a050832 | 123 |
| image_ocr.py | 5,391 | c035a94ed5a5 | 145 |
| table_llamaparse.py | 5,044 | 0e98a2ed9e1c | 125 |
| router.py | 4,355 | a985b744b589 | 97 |
| table_native.py | 1,206 | b71e2321e46f | 38 |
| text_native.py | 934 | abd7562bddcc | 27 |
| __init__.py | 413 | 28307da15be7 | 17 |
| **합계** | **52,109** | — | **1,251** |

### 오탐률 — 이 대조에서 유일하게 깨끗한 축

답안지 A 항목 12개(의도된 설계) 중 **삭제·통합을 권고한 건수**.

| 항목 | Opus 1 | Opus 2 | Fable 1 | Fable 2 |
|---|---:|---:|---:|---:|
| **오탐** | **0** | **0** | **0** | **0** |
| 근거를 달아 **명시적으로** 회피한 A 항목 수 | 10 | 6 | 3 | 5 |

네 회차 모두 `_make_client` 3벌을 "중복이 아니라 `contracts.md` 6절의 모킹 이음새"로
정확히 기각했다. 캐시 salt 분리·Vision 상한 공유·OCR 이원화·classify 두 필터도
지적되지 않았다.

### 사전 등록 정답(C) 적중

| | Opus 1 | Opus 2 | Fable 1 | Fable 2 |
|---|---|---|---|---|
| C1 `ContentHandler` | 권장 | 권장 | **보류** | 권장(낮음) |
| C2 `image_vision` 카운터·try | Critical | Critical | Critical | Critical |
| C2 를 **실행으로 재현** | **함** | 안 함 | 안 함 | 안 함 |
| `contracts.md` 가 이 함수를 3(a) 모범으로 지목 → reference 낡음 | **짚음** | 안 짚음 | 안 짚음 | 안 짚음 |

**4/4 전부 C2 를 Critical 로 잡았다.**

### 답안지 밖 진성 발견 — 교집합·차집합

개수로 순위를 매기지 않는다(정답 총수를 모른다). 목록으로 낸다.

| # | 발견 | Opus1 | Opus2 | Fable1 | Fable2 | 독립 검증 |
|---|---|:-:|:-:|:-:|:-:|---|
| N1 | `ContentRegion.source_format` — 쓰기 33곳 / **읽기 0곳** | ● | ● | | | **참** |
| N2 | `content/__init__.py` 재수출 5개 — 패키지 경로 소비자 **0** (+순환) | ● | ● | | | **참** |
| N3 | `table_vision.py:54` 주석이 없는 상수를 가리킴 | ● | ● | | | 미검증 |
| N4 | `test_pdf.py:64` LlamaParse 가짜가 아직 **문자열** | ● | | | | 미검증 |
| N5 | `image_vision` 의 "응답 모양 이상" 폴백 테스트 부재 | ● | ● | ● | ● | 미검증 |
| N6 | 이미지 디코드·`preprocess` 2회 중복 | ● | ● | | ● | 미검증 |
| N7 | `OcrResult.text` — 읽는 곳 0 | **✗** | ● | ● | ● | **참** |
| N8 | `ContentRouter.config` 가 `LoadContext.config` 와 중복 | | ● | | | 미검증 |
| N9 | `image_ocr` 의 Tesseract 예외 미포착 | | | | ● | 미검증 |

- 교집합(4/4): C1 · C2 · N5
- Opus 합집합 10 / Fable 합집합 6
- **Opus 만**: N1 · N2 · N3 · N4 · N8 (5건)
- **Fable 만**: N9 (1건)

### 회차 간 차이 — n=2 가 실제로 값을 한 지점

| 항목 | 같은 모델 두 회차 |
|---|---|
| `OcrResult.text` (N7) | **Opus 1 은 틀린 근거로 기각**("테스트가 값을 직접 본다"), **Opus 2 는 삭제 권장**. Fable 은 두 회차 다 찾음(보류) |
| N8 | Opus 2 만 |
| N4 | Opus 1 만 |
| C2 실행 재현 | Opus 1 만 |
| N6 | Fable 2 만 (Fable 1 없음) |

**N7 의 실제 판정**: `test_run_ocr_is_metrics_only` 가 읽는 것은 `metrics.char_count` 이고
`.text` 가 아니다. 저장소 전체 `.text` 히트 3건은 전부 검색 결과 타입(`retrieval.py`·
indexing 테스트)이라 무관하다 — **Opus 1 의 기각 사유가 사실이 아니다.**

## 4. 테스트 스위트

- ruff   : `All checks passed!`
- pytest : `577 passed, 1 skipped, 7 deselected in 62.87s` (감사 전과 동일 — 코드 미변경)
- skip 사유 : 실파일 픽스처가 없는 회귀 테스트 1건 (기존과 같음)
- deselected 7 : `-m integration` (과금 경로, 의도적 제외)

## 5. 판정

- **C2 (`image_vision._call_vision`) — 회귀가 아니라 미수정 계약 위반.** 2026-08-18 사고 후
  `table_llamaparse` 를 고쳤고 `table_vision` 은 처음부터 맞게 짰는데 `image_vision` 만
  안 따라갔다. 실행으로 두 실패 모드를 다 재현했다(2절). **고쳐야 한다**
- **C1 (`ContentHandler`) — 삭제 대상 확정.** AST 참조 0
- **N1 · N2 · N7 — 삭제 대상 확정.** grep·AST 로 독립 검증
- **N3~N6 · N8 · N9 — 확인 필요.** 이번 검증에서 독립 확인을 안 했다
- **오탐률 0/12 × 4회 — 두 모델을 못 가른다.** 답안지가 이 축에서 변별력을 못 냈다.
  A 항목 함정이 전부 모듈 docstring·`CLAUDE.md`·`references/` 에 명시돼 있어 **읽기만
  하면 안 걸리는** 종류였다는 뜻이고, 그건 저장소 문서화 상태에 대한 결과이지 모델에
  대한 결과가 아니다
- **진성 발견 차집합은 Opus 5 / Fable 1 로 갈렸다 — 다만 단정하지 않는다.** n=2 이고,
  같은 모델 두 회차가 N7 에서 정반대로 갈린 것이 그 이유다. **회차 간 차이가 모델 간
  차이보다 작다는 근거가 없다**

## 6. 재현

```powershell
# 실행 검증 (외부 API 없음)
$env:PYTHONIOENCODING="utf-8"
.\.venv\Scripts\python.exe <scratchpad>\verify_key.py
.\.venv\Scripts\python.exe <scratchpad>\verify_mech.py

# 독립 검증한 발견 셋
rg -n "source_format" rag_pipeline backend tests --type py      # 쓰기 33 / 읽기 0
rg -n "from rag_pipeline.ingestion.loader.content import" -t py # 전부 서브모듈
rg -nE "(metrics|ocr|result|precomputed)\.text\b" -t py         # OcrResult.text 읽기 0

# 스위트
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest -q
```

감사 프롬프트 원문과 답안지는 스크래치패드의 `audit-prompt.md` · `answer-key.md` 다.
4회 모두 이 프롬프트를 **문자 그대로** 썼다.

## 7. 재지 못한 것

- **진성 발견의 recall.** 정답 총수를 사람이 라벨링하지 않았다. 각 모델이 놓친 것이
  몇 개인지 모른다 — 그래서 개수 순위를 안 매기고 목록으로만 냈다
- **분산.** n=2 는 분산 추정이 아니다. 회차 간 차이가 있다는 것만 보였고 그 크기를 못 잰다
- **비용.** 4회의 실제 토큰·요금을 못 쟀다. Claude Code 플랜 소비라 API 요율로 환산이 안 된다
- **N3~N6 · N8 · N9 독립 검증.** 6건은 리포트 주장 그대로다. 고치기 전에 각각 확인해야 한다
- **다른 단계로의 일반화.** 이 결과는 `loader/content/` 한 디렉터리 것이다
- **오염 가능성을 배제하지 못했다.** 답안지(`answer-key.md`)를 감사 에이전트가 **읽을 수 있는
  스크래치패드**에 뒀다. "에이전트에 주지 않는다"고 설계했지만 도달 가능한 곳에 놔둔 것은
  다른 얘기다. Fable 2 는 전사가 남아 있어 확인했다 — 1차 리포트는 138줄에서 끝나고
  `answer-key` 접근은 전부 148줄 이후(재개 후)라 **채점에 쓴 리포트는 오염되지 않았다.**
  **Opus 1 · Opus 2 · Fable 1 은 전사가 0바이트로 정리돼 검증할 수 없다** — 네 리포트
  어디에도 답안지 문구나 사전등록 프레이밍이 없다는 정황뿐이고 증거가 아니다.
  다음에 같은 대조를 하면 **답안지를 에이전트가 못 읽는 곳에 둔다**
- **재개된 회차의 자기 채점은 반영하지 않았다.** Fable 2 가 이후 다섯 번 재개되며 답안지와
  이 문서를 읽고 자기 채점·재검증·diff 를 냈다. 독립적이지 않아 기록에 안 넣는다
- **감사 이후 대상이 하나 바뀌었다.** `table_vision.py` 가 13:41 에 13,260→15,656 bytes
  (`61f174f8b15c`→`8d32b2088e51`, `_attempt()` 추가)로 바뀌었다. 위 3절 신원표는 12:31
  기준이다. **이 문서의 발견은 영향받지 않는다** — 카운터 위치(해석 앞)·`_make_client`
  모듈 레벨·`:54` 주석 셋 다 그대로임을 재확인했다
