# backend — 영역별 리뷰 기준

`backend/**` 과 `rag_pipeline/exceptions.py` 를 건드린 변경에 적용한다.
공통 규약은 `contracts.md` 에 있다.

## 1. 라우트의 몫은 여기까지다

`backend/app/api/routes/documents.py` 가 하는 일은 넷뿐이다.

1. 업로드 수신
2. 확장자 화이트리스트 1차 검증 (`upload_document()` 가 `SUPPORTED_EXTENSIONS` 로 거른다)
3. UUID 임시저장 (`_save_upload()`)
4. 정리 (`upload_document()` 의 `finally` 에서 `temp_path.unlink(missing_ok=True)`)

**파싱은 전부 `ingest()` 가 한다.** 라우트에 포맷 분기·리전 판별·핸들러 선택이 들어오면
경계 위반이다. `ingest()` 가 HTTP 를 모르는 덕에 스크립트·노트북에서 그대로 쓸 수 있는데,
파싱이 라우트로 새면 그 성질이 깨진다.

임시파일 정리가 `finally` 밖으로 나가거나 예외 경로에서 빠지면 지적한다 — 업로드 실패마다
임시파일이 쌓인다.

### 원본 보관·서빙·삭제 — 되돌리면 깨지는 것 넷

원본이 `Settings.originals_dir` 에 남는 것도 이 라우트의 몫이다. 아래 넷은 **좋은 뜻으로
되돌리기 쉬운데 되돌리면 조용히 깨진다.**

- **저장 이름의 해시 접미사를 "부딪힐 때만" 으로 바꾸지 않는다.** 항상 붙어야 이름이
  `document_id` 만으로 계산되고, 그래서 매핑 파일이 없다. 조건부로 바꾸면 *"이 문서의 원본이
  어느 파일인가"* 를 디스크를 뒤져야 알게 되어 **두 번째 진실**이 생긴다. 그리고 예약 문자
  치환만 하면 `보고서:1.pdf` 와 `보고서_1.pdf` 가 같은 파일이 되어 **한쪽의 원문 보기가 남의
  파일을 에러 없이 보여준다**
- **저장을 `try` 앞이나 `finally` 로 옮기지 않는다.** 색인이 **성공한 뒤**여야 목록에 없는
  파일이 안 쌓인다. 같은 이유로 파싱 전용 `upload_document()` 는 저장하지 않는다
- **`_original_path()` 의 `resolve()` 봉쇄를 지우지 않는다.** `document_id_for()` 의
  `PurePath(...).name` 이 잡는 것과 봉쇄가 잡는 것이 다르다 — 경로 파라미터는 사용자
  입력이다. `FileResponse` 에 `filename` 을 주는 것도 계약이다(안 주면 Starlette 이
  `Content-Disposition` 을 **아예 안 단다**)
- **삭제에서 답변 캐시를 비우지 않는다.** 색인 라우트가 `clear()` 를 부르는 것과 **반대**이고
  둘 다 의도다 — 새 문서 유입은 조회 시점 근거 대조가 원리적으로 못 잡지만(사라진 것이
  없다), 삭제는 근거 청크가 실제로 사라져 캐시가 스스로 걸러낸다. "일관성" 을 이유로 맞추면
  잡히는 것을 또 잡느라 `warnings` 만 잃는다. BM25 는 둘 다 되돌린다

**테스트가 `originals_dir` 을 덮는지 본다.** `tests/api/conftest.py` 의 autouse 픽스처가
`ORIGINALS_DIR` 로 덮는데, 이걸 지우거나 API 테스트를 그 폴더 밖에 만들면 **개발자의 실제
보관함에 파일을 쓴다.** 실제로 한 번 밟았다.

## 2. 로더는 동기·CPU 바운드다

`upload_document()` 가 `await run_in_threadpool(ingest, ...)` 로 넘긴다.
`ingest()` 를 이벤트 루프에서 직접 부르면 파싱이 도는 동안 서버 전체가 멈춘다.
200페이지 PDF 하나가 다른 모든 요청을 막는다.

## 3. 예외 → HTTP 매핑

`backend/app/main.py` 의 `_STATUS_BY_EXCEPTION` 규칙은 `contracts.md` 4절에 있다.
요약하면 **새 예외는 반드시 등록**하고(안 하면 500), **하위 클래스를 상위보다 먼저** 넣는다
(`_status_for()` 의 `isinstance` 순차 검사).

`user_message`(한국어)가 그대로 응답에 나가므로, 새 예외의 `user_message` 가 사용자에게
보여도 되는 문장인지 본다 — 내부 경로·스택·라이브러리 예외 문자열이 새면 지적한다.

**화면 요청과 공개 API 는 같은 핸들러에서 갈린다.** `HX-Request` 헤더가 있으면 `_error.html`
조각, 없으면 JSON 이다. 넷을 본다.

- **새 `HTTPException` 경로를 냈는가** — 415·413 은 `_STATUS_BY_EXCEPTION` 을 **안 탄다**.
  `StarletteHTTPException` 핸들러가 따로 받는다. 라우트가 `HTTPException` 을 새로 던지면 그
  갈래를 함께 봐야 하고, 안 보면 **나머지 테스트가 전부 통과하는 채로 그 오류만 화면에서
  조용하다**
- **상태코드를 200 으로 낮춘 곳이 있는가** — htmx 가 4xx/5xx 를 swap 안 하는 것을 피하려고
  200 을 돌려주면 매핑 계약이 화면 경로만 우회한다(SSE 기각과 같은 자리). 뒤집는 자리는
  `chat.html` 의 `htmx:beforeSwap` 한 줄이고, **거기서 `isError` 까지 끄면** 그리기는 하는데
  `htmx:responseError` 가 안 떠 콘솔 진단이 사라진다
- **오류 조각이 문구를 새로 짓는가** — `_error.html` 은 `user_message` 를 그대로 싣고
  `variant` 같은 인자를 안 받는다. 인자를 받으면 핸들러가 어느 라우트인지 알아야 해서 헤더
  하나로 가른 이유가 사라진다
- **'일관성' 을 이유로 reset 을 맞추지 않는다** — 업로드 폼은 조건 없이 `this.reset()`,
  composer 는 `event.detail.successful` 일 때만이다. **의도한 비대칭이다**: file input 을
  안 비우면 같은 파일을 다시 골라도 `change` 가 안 떠서 재시도도 재색인도 막히고, 질문 글자는
  실패 때 남아야 한다. 한쪽에 맞추자는 지적은 하지 않는다

## 4. 설정 이원화

| 파일 | 담당 |
|---|---|
| `backend/app/core/config.py` | 웹 (앱 이름, `api_prefix`, 업로드 상한) |
| `rag_pipeline/config.py` | 파싱 (핸들러 선택, API 키, 비용 상한) |

둘 다 `PROJECT_ROOT/.env` 를 읽는다. 파싱 설정이 backend 쪽으로 넘어오면 `ingest()` 를
직접 부르는 경로에서 그 설정이 안 먹는다 — 지적한다.
