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

## 4. 설정 이원화

| 파일 | 담당 |
|---|---|
| `backend/app/core/config.py` | 웹 (업로드 상한, CORS 등) |
| `rag_pipeline/config.py` | 파싱 (핸들러 선택, API 키, 비용 상한) |

둘 다 `PROJECT_ROOT/.env` 를 읽는다. 파싱 설정이 backend 쪽으로 넘어오면 `ingest()` 를
직접 부르는 경로에서 그 설정이 안 먹는다 — 지적한다.
