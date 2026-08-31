# LlamaParse 응답을 문자열로 알고 있던 버그 — 가짜가 코드와 똑같이 틀려서 살아남았다

- 작업일: 2026-08-18
- 검증 방법: `pytest` 전체 + 실파일 6개 무과금 청킹 덤프. **무과금 부분은 외부 API 호출 없음.**
  유료 패스는 사용자 승인을 받고 돌렸고, 수정 전 시도에서 Vision 9회 · LlamaParse 1회가
  실제로 나갔다

## 1. 무엇을 바꿨나

- `rag_pipeline/ingestion/loader/content/table_llamaparse.py`
  `result.markdown` 을 문자열로 보고 `.strip()` 을 부르던 곳. 실제로는 `pages` 를 담은
  pydantic 모델이라 `AttributeError` 로 죽었다. 응답 해석을 `_markdown_of()` 로 떼고
  **해석 구간까지 `try` 안으로** 넣었다
- `tests/ingestion/loader/content/test_router.py`
  가짜 응답이 `SimpleNamespace(markdown="문자열")` 이었다. 실제 shape 으로 바꾸고
  다중 페이지·모양 붕괴 두 축을 추가했다

## 2. 설계 결정

### 응답 해석을 `try` 밖이 아니라 **안**에 둔다

원래는 `client.parsing.parse()` 호출만 감싸고 있었다. 그래서 호출은 성공하고 **응답
해석에서 난 예외가 `handle()` 밖으로 새어** 라우터의 `table_native` 폴백이 발동할 기회
자체가 없었다. 실제로 그 `AttributeError` 가 `ingest()` 까지 올라가 적재 전체를 멈췄다.
CLAUDE.md 가 못 박은 "외부 API 핸들러는 예외를 던지지 않고 빈 리스트를 반환" 규약이
**호출 실패에만 적용되고 응답 해석에는 안 걸려 있던 것**이다.

버린 안: 해석부를 그대로 두고 `_markdown_of()` 안에서만 방어하기. 그러면 다음에 다른 필드를
꺼낼 때 같은 구멍이 다시 생긴다. 경계는 함수 하나가 아니라 **`handle()` 이 예외를 안
내보낸다**는 계약이어야 한다.

### 카운터를 해석보다 **먼저** 올린다

`ctx.llamaparse_calls += 1` 을 `_markdown_of()` 앞으로 옮겼다. 해석이 실패해도 그 호출은
이미 과금됐기 때문이다. 카운터의 용도가 비용 추적이므로 "성공한 호출 수"가 아니라 "나간
호출 수"를 세는 것이 맞다.

버린 안: 성공했을 때만 세기(원래 동작). 그러면 이번처럼 해석이 전부 실패하는 상황에서
`llamaparse_calls=0` 이 되어 **돈은 나갔는데 장부는 0** 이 된다.

### 실패 사유를 둘로 가른다

`warnings` 에 "LlamaParse 호출 실패"와 "LlamaParse 응답을 해석하지 못해"를 따로 남긴다.
둘은 대응이 다르다 — 앞은 재시도·키·네트워크 문제이고, 뒤는 **SDK 가 바뀐 것**이라 코드를
고쳐야 한다. 한 문구로 뭉치면 다음 사람이 응답 모양 변화를 장애로 오해한다.

### 문자열 응답도 계속 받는다

`_markdown_of()` 가 `isinstance(container, str)` 을 먼저 본다. 지금 SDK 는 모델을 주지만
tier·버전에 따라 다르게 올 수 있고, 이 분기는 한 줄이라 유지 비용이 없다.

## 3. 지표

전문은 [verify_log/2026-08-18-llamaparse-응답모양.md](../verify_log/2026-08-18-llamaparse-응답모양.md) 에 있다.

- pytest 466 → **468 passed, 2 skipped** / ruff 통과
- 실파일 6개 무과금 청킹에서 잠긴 값 3건(47/70/45) 전부 일치
- E2E : 유료 패스 재실행은 이 기록 시점에 진행 중

## 4. 실패 분석

### 가짜가 코드와 **똑같이 틀려서** 테스트가 버그를 가렸다

`test_pdf_table_uses_llamaparse` 가 `SimpleNamespace(markdown="| A | B |...")` 로 모킹하고
있었다. 코드가 `result.markdown` 을 문자열로 알고 있었고 가짜도 문자열을 줬으니 통과한다.
**두 곳이 같은 오해를 공유하면 테스트는 그 오해를 검증한다.** 실제 API 를 한 번도 안
불러봤기 때문에(integration 테스트 미실행) 아무도 몰랐다.

막은 방법: 가짜를 실제 shape(`markdown.pages[*].markdown`, 실패 페이지는 `error`)으로
바꾸고, `_fake_parse_result()` · `_ok_page()` 헬퍼에 **"이 모양이 이 파일의 핵심"** 이라고
주석으로 못 박았다. 다음 사람이 편하다고 문자열로 되돌리면 같은 구멍이 다시 열린다.

### 유료 경로를 "설정만 켜면 되는 것"으로 봤다

계획서에 유료 패스를 "무과금과 같은 절차에 설정만 바꾼 것"으로 적었다. 실제로는 그 경로가
**한 번도 실행된 적이 없어** 첫 호출에서 죽었다. 무과금으로 통과하는 테스트 466개가
유료 경로에 대해 말해주는 것이 거의 없다는 뜻이다.

### 되돌린 것: 없음

수정 방향은 한 번에 잡혔다. 다만 `_markdown_of()` 를 만들기 전에 `try` 구조부터 고쳐
`NameError` 로 폴백이 도는 중간 상태를 한 번 거쳤고, PostToolUse 훅이 즉시 잡았다.

## 5. 회귀 테스트

`tests/ingestion/loader/content/test_router.py`

- `test_pdf_table_uses_llamaparse` — 실제 shape 에서 마크다운을 꺼내는가 (4절 첫 항목)
- `test_llamaparse_joins_pages_and_skips_failed` — 여러 페이지를 순서대로 잇고 실패 페이지는
  건너뛰는가
- `test_llamaparse_bad_response_shape_falls_back` — 모양이 깨져도 예외가 안 새고
  `table_native` 로 내려가며 **과금 카운터는 올라가는가** (2절 두 결정을 함께 잠근다)

못 막은 것: 실제 LlamaParse 응답과의 대조는 `-m integration` 몫이고 이번에 안 돌렸다.
SDK 가 또 바뀌면 위 테스트는 여전히 통과한다 — 가짜는 우리가 믿는 모양일 뿐이다.

## 6. 재현

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ingestion/loader/content -q
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
```

## 7. 남은 것

- **유료 패스 6문서 결과 확인** — LlamaParse 가 시행령 4쪽 표의 4조각 분절을 합치는지,
  대신 `rows` 를 잃어 헤더 반복이 깨지는지
- **PDF 표 리전 분절** — 한 표가 4개 리전으로 잡혀 열 이름이 1/4 청크에만 남는다.
  리전 병합 규칙을 둘지는 설계 결정이라 `plan-log` 로 넘긴다
- **PDF 에 `_is_layout_box` 상당이 없다** — 붙임 제목 배너가 1행짜리 표로 남는다
- **시행령 HWPX 45 vs PDF 104** — 어느 쪽이 맞는 청킹인지 못 정했다. 정답 질의 세트가
  없어 검색 품질로는 판정할 수 없다
