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

### 원본 보관·서빙·삭제·재색인 — 되돌리면 깨지는 것 여덟

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
- **색인·재색인 라우트에서 `embedder=` 를 빼지 않는다.** `store=` 와 나란히 있어 같은
  이유처럼 보이지만 아니다 — 저쪽은 qdrant 배타 잠금이고 **이쪽은 모델 적재**다.
  빼면 `index_document()` 가 `get_embedder()` 로 새 인스턴스를 만드는데 그것이
  메모이즈를 안 해, **새 문서 하나마다 KURE-v1 2.27GB 를 다시 올린다**(실측: 업로드
  65초 중 파싱은 0.8초). **같은 문서를 재색인해 보는 것으로는 이 회귀를 못 잡는다** —
  전량 캐시 히트라 모델이 안 올라와 1.3초로 통과한다. 넘길 것은
  `core/singletons.py:embedder_for(app)` 이고, `routes` 끼리 import 하면 순환이다
- **재색인은 캐시를 비우는 쪽이다.** 셋의 대우가 다르고 셋 다 의도다 — 색인·재색인은
  `clear()` 하고 삭제는 안 한다. 재색인이 비우는 이유는 색인과 **같은 축**이다: 파서를 고쳐
  청크가 *늘어난* 경우 옛 답의 근거는 여전히 멀쩡해 근거 대조가 **원리적으로 못 잡는다**
- **`reindex_document()` 가 `index_document()` 에 넘기는 `original_name` 은 디스크 이름이
  아니라 문서 키다.** `_stored_name()` 이 만든 `안내서__a3f9c2.pdf` 를 넘기면 `document_id`
  가 새로 생겨 `delete_by_document_id()` 가 옛 청크를 못 찾고 **같은 문서가 두 벌** 색인된다
  — `indexing.md` 2절의 *"키가 바뀌면 문서가 두 벌"* 과 같은 사고이고, 여기서는 **에러 없이**
  난다. 그리고 `store=` 주입은 색인 라우트와 같은 이유로 필수다(안 넘기면 같은 uvicorn 안에서
  배타 잠금과 부딪히는데 **운영에서만** 드러난다). `_keep_original()` 은 부르면 안 된다 —
  파일이 이미 제자리라 `shutil.move` 가 자기 자신을 덮는다
- **그림자 색인은 색인·재색인·삭제를 전부 따라가야 한다.** 한쪽만 하면 두 컬렉션이 서로
  다른 청크를 들어 `overlap.py` 가 경고하는 상태가 되는데, 사이드바 배지는 *있다/없다* 만
  봐서 **그 어긋남이 화면에 안 보인다**

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

## 5. 게으른 getter와 기동 워밍업 — 되돌리면 조용히 깨지는 것 여섯

`routes/query.py` 의 `_embedder`·`_bm25`·`_reranker` 와 `warm_up()`, `main.py` 의 lifespan
스레드에 적용한다. 근거는 `verify_log/2026-08-26-{첫질의-대표값,기동-워밍업}.md` 다.

- **`warmup_on_startup` 의 기본값을 켜짐으로 바꾸지 않는다.** `tests/api` 의 일부가
  `with TestClient(...)` 로 **lifespan 을 진짜로 돌린다**(`test_query_route.py` ·
  `test_shadow_routes.py`). 켜진 채 커밋되면 **기본 pytest 가 KURE 가중치 2.27GB 를 적재한다** —
  임베딩 provider 핀과 같은 종류의 유출인데 여기서는 픽스처가 아니라 **설정 기본값이 유일한
  방어**다. `offline_config` 는 이 경로를 안 지난다
- **워밍업을 "객체만 만드는" 것으로 바꾸지 않는다.** `get_embedder()`·`get_reranker()` 자체는
  0.002초이고 가중치는 **첫 사용**에 올라온다. 생성만 하는 워밍업은 **아무것도 안 하면서 다 한
  것처럼 보이고**, 그 실패는 *"첫 질의가 여전히 21.8초"* 로만 드러난다
- **워밍업 입력을 고정 문자열로 바꾸지 않는다.** 임베딩 캐시도 리랭크 캐시도 텍스트로 키를
  만들어서, 고정하면 **워밍업이 자기 입력을 캐시에 넣고 다음 기동부터 그 캐시에 걸려 모델을
  안 올린다.** 예외가 없어 `warmup_errors` 는 `[]` 이고 로그도 깨끗해서 **VRAM 곡선 말고는
  신호가 없다.** 실제로 그렇게 냈다가 잡았다. 가짜를 쓰는 계약 테스트로는 원리적으로 못 잡으니
  (캐시가 실제 구현체 *안에* 있다) 대신 **입력이 호출마다 다른가**를 잠근다.
  유일값은 `uuid.uuid4()` 다 — `time.time_ns()` 는 Windows 에서 연달아 부르면 **같은 값이 나온다**
- **리랭커 워밍업 후보를 하나로 줄이지 않는다.** `local_ce.rerank()` 는 후보가 1개 이하면
  모델을 안 올리고 그대로 돌려준다. 실경로는 후보가 늘 50이라 이 경계가 **테스트로만 잡힌다**
- **getter 의 인자를 `Request` 로 되돌리지 않는다.** 워밍업에는 요청이 없어서 `FastAPI` 를
  받는 것이고, 그래야 배경 스레드와 라우트가 **같은 인스턴스**를 본다. 되돌릴 거면 `ask()` 의
  호출부 셋을 **같이** 되돌려야 한다 — 시그니처만 바꾸면 `app.state` 가 실제로는
  `request.state` 를 가리켜 `KeyError: 'ingest_config'` 로 `tests/api` 가 무더기로 깨진다.
  **타입 힌트가 안 잡아준다**(`from __future__ import annotations` + 둘 다 `.state` 를 갖는다)
- **`_BUILD_LOCK` 을 지우지 않는다.** 없으면 워밍업이 `state.embedder` 를 세팅하기 전에 들어온
  질의가 자기 것을 또 만들어 **2.27GB 가 두 벌** 뜬다. 에러가 안 나고 메모리로만 드러난다.
  `_rerank_ready` 는 표시지 락이 아니다
- **워밍업에서 저장소를 건드리지 않는다.** `embed_query()` 도 `rerank()` 도 qdrant 를 안 열어서
  lifespan 이 남겨 둔 게으름(*"색인이 아직 없으면 서버가 아예 안 뜬다"*)이 유지된다. BM25 를
  데우고 싶어도 넣지 않는다 — 414청크에서 **0.09초**라 얻을 것이 없고 그 게으름만 깨진다

## 6. 판정(verdict)이 화면·로그로 나가는 길 — 되돌리면 조용히 깨지는 것 셋

`routes/query.py:ask()` · `core/query_log.py` · `templates/_answer.html` 에 적용한다.
근거는 `task_log/2026-08-27-판정-화면배선.md` 다.

- **Evaluator 를 `app.state` 싱글턴으로 "정리"하지 않는다.** 위 5절의 셋과 달리 여기는
  **질의마다** 만드는 것이 결정이다 — 가중치가 없어 만드는 값이 사실상 0 이고, 싱글턴이면
  `warnings`·`usage` 가 누적된다(`EVALUATOR_MODEL` 은 찼는데 키가 없는 조합에서 질의마다
  한 줄, **에러 없음**). 일관성은 모양이 아니라 **이유**로 판정한다
- **판정 배지를 *충분* 에도 그리지 않는다.** `cached_from` 배지의 *"없으면 줄 자체를 안
  그린다 — 빈 배지가 남으면 사용자는 그 기능이 켜져 있다고 읽는다"* 와 같은 규칙이다.
  대가(*충분* 과 *판정 꺼짐* 이 화면에서 같아 보인다)를 알고 고른 것이라, 그것을 근거로
  뒤집자는 제안은 이미 기각된 안이다. **모델 사유(`reasoning`)를 배지 본문으로 옮기는 것도**
  같다 — 길이가 모델 마음대로라 `title` 에 둔다
- **`query_log` 의 `verdict` 키를 조건부로 만들지 않는다.** 판정을 안 돌렸으면 값이 `null`
  이지 키가 없는 것이 아니다. 빼면 *"판정이 꺼져 있었다"* 와 *"옛 형식이라 칸이 없다"* 가
  집계에서 안 갈린다. **`reasoning` 을 추가하자는 제안도 지적 대상**이다 — 상한도 자동
  정리도 없는 파일이고, 사유를 읽는 도구는 `evaluation/verdicts.py --wrong` 이 이미 있다

## 7. 재작성(rewrite) 흔적 — 위 6절과 같은 규칙, 한 곳만 반대다

`rag_pipeline/schemas/answer.py` · `rag_pipeline/query.py` · `core/query_log.py` ·
`templates/_answer.html` 에 적용한다. 근거는 `task_log/2026-08-27-재작성-흔적.md` 다.

- **`Answer.rewrite` 를 `rewritten: str` 로 "펴지" 않는다.** `query` 와 `mode` 가 한 덩어리인
  이유는 **화면 문구가 mode 에서 갈리기** 때문이다 — `fuse` 는 원 질의 갈래도 함께 돌았으므로
  *"…로 바꿔 검색했습니다"* 가 거짓이고 `replace` 는 *"…도 함께"* 가 거짓이다. 나란한 두
  필드로 쪼개면 *질의는 있는데 mode 가 없는* 조합을 템플릿이 매번 방어해야 한다.
  `cached_from` 이 셋을 묶은 것과 같은 잣대다
- **`retrieve()` 가 다시 쓴 질의를 돌려주게 넓히지 않는다.** 반환은 공개 계약이고
  `evaluation/run_eval.py` 가 그 모양에 맞춰져 있다. 읽는 자리는 `rewriter.last_rewritten`
  이고, `warnings`·`usage` 를 부르는 쪽이 읽는 것과 같은 관행이다
- **`rewrite()` 맨 앞의 `self._last_rewritten = None` 을 지우지 않는다.** 지우면 폴백으로
  내려간 질의가 **앞 질의의 재작성을 자기 것으로** 화면과 로그에 싣는다. 라우트는 질의마다
  재작성기를 새로 만들어 안전하지만 `run_eval` 은 **재사용**한다.
  `tests/retrieval/test_rewrite.py::test_fallback_clears_the_previous_rewrite` 가 막는다
- **캐시 히트에서 흔적을 `None` 으로 떨어뜨리지 않는다.** 재작성이 걸린 것은 같고 갈리는
  것은 과금뿐이다 — `None` 을 주면 캐시가 도는 순간 *켜 놓고도 안 보이는* 상태가 된다
- **`query_log` 의 `rewritten` 은 전문이고, 그것이 6절의 `reasoning` 규칙과 어긋나지
  않는다.** 축이 둘 다르다 — 판정 사유는 **읽는 도구가 이미 있고**(`verdicts.py --wrong`)
  재작성은 **여기가 유일한 기록**이다(`.cache/rewrite` 는 내용 해시가 키라 원 질의를 아예
  안 담아 *무엇이 무엇으로 바뀌었나* 를 못 되짚는다). 길이도 산문 대 한 문장이다
  (`rewrite_max_tokens` 256). **이 줄을 «`reasoning` 과 같은 위반» 으로 지적하지 않는다**
- **`.is-placeholder` CSS 규칙은 사용처가 0 이어도 지우지 않는다.** 배지 테스트 셋이
  *이 클래스가 안 붙었음* 을 단언해 「진짜 값이 흐려지는」 회귀를 막는데, 규칙을 지우면
  그 셋이 **영원히 참**이 되어 조용히 죽는다
