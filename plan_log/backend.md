# backend 설계

상태: 논의 중 (1회차)
관련 기록: task_log/... (구현 후 채운다)

`frontend.md` 와 짝이다 — 저쪽 1회차의 확정 10줄 중 **프론트 고유는 2줄뿐**(HTMX 스택 ·
히스토리 자리만 두기)이고 나머지는 backend 또는 생성 결정이다. 그것을 이 파일로 옮기는 것이
2회차이고, **1회차인 지금은 가리키기만 한다**(미결 첫 줄에 조건이 있다).

## 표면 지도

**이 파일의 유일한 원본 콘텐츠다.** 나머지 절은 전부 다른 곳을 가리킨다 — 같은 문장이 두
곳에 살면 한쪽만 고쳐진다(`문서-최신성.md` 6회차). 2026-08-23 워킹 트리 기준.

| 메서드 · 경로 | 하는 일 | 이 결정이 사는 곳 |
|---|---|---|
| `POST /api/v1/documents/upload` | 업로드 수신 · 확장자 1차 검증 · UUID 임시저장 · 정리. 파싱은 `ingest()`. **색인은 안 한다** | `CLAUDE.md` "backend ↔ rag_pipeline 분리" |
| `GET /api/v1/health` | 상태 확인 | — |
| `POST /api/v1/query` | 질문 → `answer_question()` → **HTML 조각**(`_answer.html`) | `routes/query.py` docstring · `frontend.md` 1회차 |
| `GET /api/v1/documents` | 사이드바 문서 목록 조각. `QdrantStore.list_documents()` 를 `getattr` 로 집어 없으면 목록만 빈다 | `frontend.md` 1회차 (Protocol 안 넓힘) |
| `GET /` | 채팅 화면 전체(`chat.html`). **`api_prefix` 를 안 붙인다** — 사람이 여는 주소다 | `routes/pages.py` docstring |
| `/static` (mount) | 벤더링한 htmx 등 정적 파일 | `CLAUDE.md` "backend ↔ rag_pipeline 분리" |

앱 수준으로 함께 봐야 하는 넷.

- **`store` 싱글턴은 `lifespan` 이 든다. 다만 열기를 startup 에서 시도하지 않는다** —
  `QdrantStore` 가 처음 쓸 때 클라이언트를 만들도록 늦춰져 있고, 그 게으름을 깨면 색인이
  없는 상태에서 서버가 아예 안 뜬다. 근거는 `main.py:lifespan` 주석
- **`embedder` 는 첫 질의에 만든다** — `get_embedder()` 가 키 없으면 그 자리에서 예외라
  startup 에서 만들면 키 없는 개발 환경에서 화면조차 못 본다. `query.py:_embedder()`
- **`_STATUS_BY_EXCEPTION` 은 7종을 매핑한다.** `EmbeddingError`·`IndexingError`·
  `GenerationError` 셋이 `/query` 가 생기며 **처음 살아났다**(그전에는 닿는 HTTP 경로가
  없었다). `main.py`
- **설정은 `backend/app/core/config.py:Settings`** — 파싱 설정과 갈라져 있다

## 확정된 것

- **backend 설계의 거처는 이 파일이다** — 근거: 그전까지 backend 결정이 `CLAUDE.md` ·
  `frontend.md` · `indexing.md` · `splitter.md` · `검색-생성.md` 다섯 곳에 흩어져 있어,
  *"색인 엔드포인트를 어디서 정했나"* 를 알려면 세 파일을 뒤져야 했다
- **1회차는 가리키기만 하고 옮기기는 2회차다** — 근거: 아래 미결 첫 줄의 조건 둘.
  이동 자체는 손실이 없지만(복사가 아니라 이동이라 이중 진실이 안 생기고, 대상이 회차 본문이
  아니라 갱신되는 요약 절이라 기록이 거짓이 되지도 않는다) **지금은 시점이 나쁘다**
- **생성 관련 결정의 최종 거처도 이 파일이다** — gpt-4.1-mini 실호출 · 환각은 프롬프트로만 ·
  점수 임계값 기각 · 인용 코드검증 기각 · `GenerationError` 재시도. 근거: 지금 그 결정들이
  `/query` 한 엔드포인트 안에서만 산다. 단계 파일로 떼어내기에는 다섯 줄뿐이다 /
  다시 볼 조건: 생성 고유 결정이 늘면 `plan_log/생성.md` 로 분가한다
- **인증·레이트리밋은 안 한다** — 아래 "안 하기로 한 것"

## 미결

- **[2회차 예약] `frontend.md` 의 backend·생성 항목을 옮겨온다** — 정하려면 조건이 아니라
  **때를 기다린다**. 조건 둘: (a) `frontend.md` 가 커밋될 것 — 지금 git 이 이 파일을 모른다
  (`git ls-files` 가 `did not match any file`)라 잘라내면 이전 상태가 어디에도 없다.
  (b) 다른 세션의 구현이 끝나 `generation/` · `routes/query.py` · `templates/chat.html` ·
  `tests/api/test_pages.py` 가 안정될 것.
  **옮길 때 포인터를 같은 커밋에서 함께 고친다** — 실측으로 `plan_log/frontend.md` 를
  가리키는 곳이 **12곳**이다(코드·테스트·템플릿 9 · 문서 3). 이 세션 안에서 9→12 로 늘었다.
  목록은 아래 1회차 본문에 있다
- **색인 엔드포인트** — 업로드가 지금 파싱만 하고 색인을 안 한다. **`indexing.md` 가 이 미결에
  걸어둔 조건은 이미 충족됐다** — 저쪽은 *"`index_document()` 가 실제로 도는 것을 본 뒤"* 라고
  적었는데 그것이 돌아 baseline 까지 나왔다. 그런데도 못 여는 이유(싱글턴 충돌)와 첫 후보는
  `CLAUDE.md` 의 qdrant 잠금 항목과 `main.py:lifespan` 주석이 이미 적었으므로 여기서 다시
  쓰지 않는다. 정하려면: 업로드를 실연결해 실제로 막히는지 본다
- **문서 삭제·재색인 API 와 `document_id` 명시화** — 지금 `document_id` 는 원본 파일명 NFC 라
  **다른 내용의 동명 파일이 서로를 덮는다**(`indexing.md` 감수 사항). 삭제 API 를 먼저 만들면
  *"무엇을 지우는가"* 가 파일명인 채로 굳는다. 정하려면: 문서 레지스트리(rDBMS)가 실제로
  들어올 때 — `frontend.md` 의 *"문서 목록의 진짜 출처"* 와 한 줄이다
- **CORS 오리진** — `Settings.cors_origins` 가 `http://localhost:3000`/`:5173` 인데 이건
  React·Vite 개발 서버 전제다. `CLAUDE.md` 가 *"빈 `frontend/` 는 이 결정 이후로 쓰이지
  않는다"* 라고 못 박고 같은 uvicorn 이 HTML 을 서빙하는 지금 **전제가 어긋나 있다.**
  정하려면: 이 API 의 외부 소비자가 실재하는지
- **`RetrievalError` 신설** — `검색-생성.md` 의 같은 미결과 한 줄. 만들면
  `_STATUS_BY_EXCEPTION` 등록이 따라온다. 정하려면: Evaluator·Rewriter 의 실패 계약이 먼저
- **`top_k` 기본값** — `indexing.md` · `frontend.md` · `검색-생성.md` 셋이 같은 미결을 이고
  있다. **여기서 다시 정하지 않고 그 셋을 가리킨다** — 네 번째 자리를 만들면 어느 쪽이
  진실인지 갈린다
- **업로드 확장자 화이트리스트가 backend 에 있는 것** — `routes/documents.py` 가
  `SUPPORTED_EXTENSIONS` 를 직접 읽어 1차 검증하고, 매직바이트 검증은 로더가 또 한다.
  이중 검증은 의도된 것이지만 두 곳이 어긋날 수 있다. 정하려면: 실제로 어긋나는 사례가
  나오는지

## 안 하기로 한 것

- **인증·레이트리밋** — 이유: 밖에 노출될 전제가 아직 없다. qdrant 가 로컬 파일 모드라
  디렉터리 배타 잠금으로 한 프로세스만 쓰고, 프론트를 같은 uvicorn 이 서빙하며,
  `.env` 의 키를 로컬에서 읽는다. **감수 사항**: 노출되는 순간 유료 LLM 을 부르는 `/query` 가
  통째로 열린다 / **다시 볼 조건**: 이 앱이 `127.0.0.1` 밖으로 나가거나 사용자가 둘 이상이
  될 때
- **`frontend.md` 항목을 이 파일로 *복사*** — 이유: 같은 문장이 두 곳에 살면 한쪽만
  고쳐진다. `문서-최신성.md` 6회차가 "삼중으로 적힌 계약"을 걷어낸 것과 같은 형태다.
  2회차에 하는 것은 복사가 아니라 **이동**이다
- **`frontend.md` 를 `backend.md` 로 개명** — 이유: 확정된 1회차의 제목이 바뀐다. 그리고
  프론트 고유 결정 2줄(HTMX 스택 · 히스토리 자리만 두기)은 그쪽에 남아야 한다
- **`frontend.md` 1회차 본문(라운드 표 · 대조에서 갈린 셋)을 옮기기** — 이유: 그 세션에서
  실제로 일어난 일의 기록이라 옮기면 거짓이 된다. **2회차에도 안 옮긴다.** 옮기는 것은
  맨 위 세 절뿐이다
- **문서 삭제 API 를 문서 레지스트리보다 먼저 만들기** — 이유: 위 미결. 지우는 대상이
  파일명인 채로 굳는다
- **`DECISIONS.md` 같은 넷째 기록 폴더** — 이유: `frontend.md` 1회차가 이미 기각했다.
  이 저장소의 ADR 자리는 `plan_log/<기능>.md` 다

---

## 1회차 — 2026-08-23 · backend 에 제 이름의 자리를 만든다

**물은 것**

사용자가 *"backend 아직 하나도 설계 안 되어 있어?"* 로 물었다. `plan_log/` 15개 파일 중
backend 이름을 단 것이 없는 것을 본 데서 나온 질문이다.

**답은 "설계가 없다"가 아니라 "제 이름의 자리가 없다" 였다**

코드는 있었고(그 시점 엔드포인트 둘 — 업로드와 health) 결정도 있었는데, 전부 *다른 기능
파일의 부산물*이었다. 특히 `frontend.md` 1회차는 제목이 프론트인데 확정 10줄 중 **프론트
고유가 2줄**이었다.

**탐색 중에 워킹 트리가 두 번 바뀌었다**

세션 시작 `git status` 는 `?? plan_log/frontend.md` 하나뿐이었다. 탐색 중에
`routes/query.py` · `routes/pages.py` · `core/templates.py` · `templates/` · `static/` ·
`rag_pipeline/generation/` · `query.py` · `retrieval/dense.py` · `schemas/answer.py` 가
나타났고, 그 뒤 `CLAUDE.md` 가 갱신돼 세 번째 진입점 `answer_question()` · `pages.py`/
`query.py` 경계 · qdrant 잠금 함정의 싱글턴 충돌이 들어갔다. **다른 세션이 `frontend.md` 를
지금 구현 중이었다.** 그래서 위 표면 지도는 계획 시점이 아니라 **작성 직전에 트리를 다시
읽어** 채웠다.

**라운드별 선택지와 결과**

| 라운드 | 물은 것 | 고른 것 | 떨어진 것 |
|---|---|---|---|
| 1 | 논의 정체 | `plan_log/backend.md` 신설 | 새 파일 안 만듦 · `frontend.md` 개명 |
| 2 | 흩어진 결정 처리 | 가리키기만 | 옮겨온다 · 새로 정하는 것만 |
| 2 | 이번 회차 범위 | 표면 지도 + 지금 정할 수 있는 것 | 색인 엔드포인트까지 · 경계·정책만 |
| 3 | 인증·레이트리밋 | 안 한다 + 다시 볼 조건 | 지금 최소한 · 미결로만 |
| 3 | 삭제·재색인 API | 미결(레지스트리와 한 줄) | 삭제만 이번에 · 안 하기로 |
| 4 | 생성 항목의 거처 | `backend.md` 로 함께 | `plan_log/생성.md` 신설 · `frontend.md` 에 존치 |
| 4 | 옮기는 시점 | **다음 회차로 예약** | 지금 옮김 · 포인터 그대로 두고 지금 옮김 |

**모델이 2라운드에서 갈래를 잘못 설명했고 사용자가 되물어 잡혔다**

2라운드 "옮겨온다" 선택지의 대가로 *"이미 확정된 회차를 사후 편집하게 된다"* 를 달았는데,
**옮길 대상은 회차 본문이 아니라 맨 위 요약 절**이다. `문서-최신성.md` 가 그 절을 두고
*"지금까지 확정된 결정만 모은다. 여기만 읽으면 현재 설계를 안다"* 로 정한 대로 거기는 원래
누적·갱신되는 자리다. 그래서 사용자가 *"옮겨 적을 수는 없어?"* 로 되물었고 그것이 맞았다.

**그 되물음이 진짜 블로커를 찾게 했다**

이어서 사용자가 *"옮기면 나중에 매핑 못 한다거나 데이터 손실이 일어난다든가 없어?"* 라고
물어 실제로 재봤더니 **둘 다 실재했다.**

1. **`plan_log/frontend.md` 가 아직 git 에 없다** — `git ls-files --error-unmatch` 가
   `did not match any file` 이다. 커밋 전 파일에서 절을 잘라내면 되돌릴 곳이 없다
2. **12곳이 `frontend.md` 를 가리킨다** — 그중 다섯이 하필 옮겨질 항목을 정확히 가리킨다

| 가리키는 곳 | 가리키는 대상 | 옮겨지나 |
|---|---|---|
| `rag_pipeline/exceptions.py` (`GenerationError`) | 1회차 | 예 |
| `rag_pipeline/generation/__init__.py` | "답변을 무엇으로 재나" 미결 | 예 |
| `rag_pipeline/generation/prompts.py` | 점수 임계값 미결 | 예 |
| `rag_pipeline/config.py` | "60건 세트는 검색을 잰다" | 예 |
| `backend/app/main.py` | "store 분리" 미결 | 예 |
| `backend/app/api/routes/query.py` | Protocol 안 넓힘 판단 | 예 |
| `backend/app/templates/chat.html` | 업로드 버튼 목업 근거 | 예 |
| `tests/api/test_pages.py` | 업로드 비활성 계약 | 예 |
| `tests/ingestion/indexing/test_qdrant_documents.py` | `list_documents()` 계약 | 예 |
| `.claude/skills/rag-code-review/references/next-stages.md` | Protocol 확장 금지 | 예 |
| `task_log/2026-08-23-프론트엔드-1회차.md` (2곳) | 회차 전반 | — (회고는 그대로) |
| `verify_log/2026-08-23-프론트엔드-1회차.md` | 미결 | — |

**옮기는 것 자체는 손실이 없다. 막은 것은 시점이다** — 포인터를 같은 커밋에서 고쳐야 하는데
그 파일 중 여럿을 다른 세션이 지금 쓰고 있어, 손대면 같은 줄에서 충돌한다. 그래서 4라운드
질문이 "옮길까 말까"가 아니라 **"언제 옮길까"** 가 됐고, 세 선택지의 최종 모양이 전부 같고
순서만 달랐다.

**남은 질문**

위 "미결" 일곱. 머리는 **2회차 옮기기**다. 그다음이 **색인 엔드포인트**인데, 이 파일의 미결
중 유일하게 **제품 코드의 서명을 바꾸는 쪽**이라 나머지와 무게가 다르다.
