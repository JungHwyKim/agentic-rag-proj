# tests — 픽스처와 모킹 이음새

`tests/` 를 읽거나 고칠 때만 컨텍스트에 들어온다. 뿌리 `CLAUDE.md` 의 `## 테스트 패턴` 절이
2026-08-31 에 통째로 내려왔다.

**이 절을 뿌리에서 뺀 것이 제일 망설여진 결정이다.** 여기 적힌 것 중 셋은 이 저장소가
**실제로 밟은** 유출이다 — 픽스처가 provider 를 안 박아 OpenAI 갈래 테스트 27개가 로컬로
새어 2.2GB 를 적재했고, 색인 라우트 테스트가 개발자의 실제 보관함에 파일을 썼고,
`monkeypatch.delenv` 로 막은 줄 알았던 Tavily 를 기본 pytest 가 2회 실제로 쳤다.

**그래도 내린 근거는 「새 테스트를 쓸 때는 `tests/` 를 읽는다」** 하나다. 이 파일이 안
들어오는 상황은 `tests/` 를 한 줄도 안 보고 테스트를 만드는 경우인데, 그런 일은 없다.
**틀렸다고 판명되면 되돌릴 것은 이 파일 하나다.**

**원문을 한 글자도 안 고치고 옮겼다.** 실측 수치와 «버린 안»이 이 문장들의 값어치라,
다듬으면 그것이 사라진다.

**여기 있는 항목을 뿌리 `CLAUDE.md` 에 다시 적으면 `tests/test_docs.py` 가 막는다**(같은
항목 제목이 두 `CLAUDE.md` 에 있으면 실패). 한 사실은 한 파일에만 산다.
## 테스트 패턴

- `tests/conftest.py` 가 샘플 파일을 **코드로 생성**한다(docx/pptx/xlsx/pdf/png/hwpx). 바이너리를 저장소에 커밋하지 않는 방침
- `offline_config` 픽스처 = 키 없음 + `table_pdf_handler="pymupdf"` + `image_handler="ocr"` + 캐시 끔. 단위 테스트의 기본값. **임베딩 설정도 여기서 키를 지운다** — `ingest()` 가 임베딩을 안 부르지만 `EmbeddingConfig()` 는 `.env` 를 읽으므로, 안 지우면 개발자 머신의 실키가 픽스처에 실린다. **거기에 더해 `embedding_provider="openai"` 를 못 박는다 — 이건 키와 다른 축이고, 2026-08-25 부터 유일한 방어다.** 로컬 갈래는 **키가 없어도 돌아서** 기본 pytest 가 모델 가중치 2.27GB 를 내려받는 경로가 **아무 에러 없이** 열린다. 전에는 `requirements-local.txt` 로 의존성을 가른 것이 두 번째 겹이었는데, **기본 임베더가 로컬 KURE-v1 이 되면서 `torch` 가 본체로 와 그 겹이 사라졌다.** 게다가 이제는 `.env` 와 무관하게 **코드 기본값 자체가 `local`** 이라, 핀이 없으면 기본 상태가 곧 가중치 적재다. **그 한 겹은 실제로 뚫린 적이 있다** — 이관 당일 `tests/ingestion/embedding/conftest.py` 가 provider 를 안 박고 기본값에 기대고 있어 OpenAI 갈래 테스트 27개가 로컬로 새어 2.2GB 를 적재했다(그 폴더가 241초). **새 픽스처를 만들 때 provider 를 빠뜨리면 같은 일이 난다.** **색인 설정은 키가 아니라 저장 위치를 덮는다** — `IndexingConfig()` 의 기본 `qdrant_path` 가 `PROJECT_ROOT/.qdrant` 라 안 덮으면 테스트가 개발자의 실제 색인을 건드리고 테스트끼리 상태를 물려받는다. 그래서 이 픽스처가 `tmp_path` 를 받는다. **생성 설정도 임베딩과 같은 이유로 키를 지운다** — `IngestConfig.generation` 이 lazy 프로퍼티인 것이 그 덮어쓰기를 가능하게 하는 전제라, lazy 를 없애면 이 차단이 조용히 사라진다. **저장 위치를 덮어야 하는 곳이 하나 더 있다** — `Settings.originals_dir` 기본값이 `PROJECT_ROOT/original_files` 라 색인 라우트를 부르는 테스트가 개발자의 실제 보관함에 파일을 쓴다(실제로 `test_index_route.py` 가 22바이트짜리를 남겼고 **아무 에러도 안 났다**). `tests/api/conftest.py` 의 **autouse** 픽스처가 `ORIGINALS_DIR` 환경변수로 덮는다 — 각 테스트가 손으로 덮는 규약이면 새 테스트가 잊는 순간 다시 새고, 환경변수인 것은 `get_settings()` 에 `lru_cache` 가 없어 한 줄이 모든 호출부에 먹기 때문이다(모듈별 patch 는 새 호출부가 생길 때마다 빠뜨린다). **같은 픽스처가 `QUERY_LOG_PATH` 도 덮는다** — `Settings.query_log_enabled` 기본값이 켜짐이라 질의 라우트를 부르는 모든 테스트가 `data/queries.jsonl` 에 줄을 쌓는데, 남는 것이 파일이 아니라 **사용자 질문 문자열**이라 개발자의 실제 반복률 통계가 오염된다(테스트 질의는 늘 같은 문자열이라 반복률을 위로 민다). **그리고 이 픽스처는 그것을 만드는 편집보다 *먼저* 들어가야 한다** — 2026-08-26 에 라우트를 먼저 배선했더니 `auto_test_check.py` 훅이 몇 초 만에 `tests/api` 를 돌려 **15줄이 실제로 샜다**(전부 `q`·`질문` 같은 픽스처 질의, 아무 에러 없음). 함정을 알고 계획에 적어둔 상태에서 밟았고, 가른 것은 지식이 아니라 **순서**였다. 켜진 채 태어나는 부수 효과는 "이따 픽스처 넣지"가 성립할 시간이 없다 **그런데 순서를 지켜도 방식을 틀리면 똑같이 샌다 — 2026-08-29 에 그렇게 샜다.** `tests/agent/conftest.py` 가 `TAVILY_API_KEY` 를 **구현보다 먼저** 막았는데 `monkeypatch.delenv` 로 짜서 **아무것도 안 막았고**, 기본 pytest 가 Tavily 를 실제로 2회 쳤다. pydantic-settings 는 **환경변수와 `.env` 파일을 둘 다** 읽으므로 환경변수를 *지우는* 것은 파일 쪽을 안 건드린다 — 지운 자리를 `.env` 가 곧바로 채운다. **바로 위 두 선례가 통하는 이유가 여기서 갈린다**: `ORIGINALS_DIR`·`QUERY_LOG_PATH` 는 **덮어쓰기**라 환경변수가 파일을 이기고, 이건 **지우기**라 파일이 남는다. 자리도 모양도 같아 보여 **그대로 베끼면 조용히 안 먹는다.** 처방은 `monkeypatch.setenv("…", "")` 이고, **가짜 키를 넣는 안은 더 나쁘다**(클라이언트가 실제로 만들어져 네트워크까지 간 뒤 401 로 죽는다 — 막으려던 것이 호출인데 호출이 나간다). **시간 차가 왕복의 증거로 남는다**(2 failed·4.89초 → 87 passed·0.85초). **그리고 `paid_test_check.py` 는 이 자리를 원리적으로 못 막는다** — 그 훅이 막는 것은 `-m integration` 인데 이건 기본 마커라, **유료 호출이 기본 pytest 로 새는 두 번째 경로**다. 가른 것은 지식도 순서도 아니고 `auto_test_check.py` 가 구현 직후 실제로 테스트를 돌린 것 하나였다. 근거는 `.claude/wiki/verify_log/2026-08-29-도구계층-tavily-기본도구.md` 5-1절
- `IngestConfig.content` · `cleaner` · `splitter` · `embedding` 은 lazy 프로퍼티라 테스트에서 `object.__setattr__(config, "_content", ContentRoutingConfig(...))` 형태로 덮어쓴다. **설정 클래스가 전부 `extra="ignore"` 라 필드 이름을 틀리면 예외 없이 그 항목만 조용히 안 먹는다** — 덮어썼다고 믿는 값이 실은 `.env` 를 탄다. 이름을 코드에 못 박는 곳(`evaluation/config.py` 의 `EVAL_SETTINGS` 와 `EVAL_EMBEDDINGS`)은 `model_fields` 와 대조해 막는다 — **프로필을 더할 때 기본 프로필만 대조하면 새 프로필의 오타가 그대로 산다**
- **모킹 이음새는 각 API 핸들러의 모듈 레벨 `_make_client()`** 다. `mocker.patch.object(image_vision, "_make_client", return_value=fake)` 형태로 쓴다. 이 함수를 인라인화하면 외부 API 테스트가 전부 깨진다
- 실파일 회귀 테스트는 `tests/fixtures/` 에 파일이 있을 때만 돌고 없으면 조용히 skip한다. 지금 다섯이다 — `loader/document/test_hwpx_real.py`(한컴 파싱 실측), `cleaner/test_real.py`(PDF 제어문자·반복 머리말 실측), `splitter/test_splitter_real.py`(요소→청크 병합률·상한), `embedding/test_embedding_real.py`(청크 토큰 분포·문자/토큰 비율), `indexing/test_indexing_real.py`(청크 수백 개에서의 자리 밀림·실제 한글 파일명). 합성 문서로는 "이 규칙이 실제로 필요한가"를 못 보여주기 때문에 따로 둔다
- **테스트 파일 이름은 저장소 전체에서 유일해야 한다.** `tests/` 에 `__init__.py` 가 없어 basename 이 곧 모듈명이라, 같은 이름이 두 폴더에 있으면 pytest 가 collection error 로 죽는다. 영역 테스트만 돌릴 때는 안 나고 전체에서만 난다. 그래서 실파일 테스트가 `test_hwpx_real.py` · `test_splitter_real.py` 처럼 영역 이름을 달고 있다. **`conftest.py` 도 같은 규칙 아래 있다** — 루트 `tests/conftest.py` 가 `sys.modules["conftest"]` 를 먼저 차지해, 하위 폴더 테스트에서 `import conftest` 하면 엉뚱한 모듈을 집는다. 그래서 conftest 끼리 나눠 쓸 값은 이름이 겹치지 않는 모듈에 둔다(`tests/hooks/_escape_hatches.py`)

