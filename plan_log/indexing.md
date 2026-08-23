# indexing (VectorDB) 설계

상태: 구현됨 (1회차, 2026-08-18 · 커밋 전)
관련 기록: [task_log/2026-08-18-indexing-단계.md](../task_log/2026-08-18-indexing-단계.md)
· [verify_log/2026-08-18-indexing.md](../verify_log/2026-08-18-indexing.md)

## 계획과 실제가 어긋난 곳

셋 있었다.

**(1) `ensure_collection()` 이 차원을 인자로 받게 됐다.** 계획서에는 인자가 없었는데,
`EmbeddingConfig.embedding_dimensions` 가 `None`("모델 기본값")일 수 있어 **설정만 봐서는
벡터 크기를 모른다.** 모델→차원 표를 코드에 박으면 모델이 늘 때마다 거짓이 되므로 실제
벡터에서 받는다. 컬렉션 이름의 차원 부분은 여전히 `dimensions or "default"` 라 salt 규칙과
같고, 실제 크기 검사만 따로 논다.

**(2) `close()` 가 인터페이스에 들어갔다.** 계획 단계에서는 몰랐던 제약이다 — qdrant 로컬
파일 모드가 **저장 디렉터리에 배타 잠금**을 걸어 두 번째 클라이언트가 `RuntimeError` 로
죽는다(실측). 안 닫으면 다음 색인이 통째로 실패하므로 닫는 것이 선택이 아니다.

**(3) `count()` 를 더했다.** 계획서에 없었지만 "청크 수 = 색인 수", "재색인 후 안 늘어남"을
검증할 방법이 이것뿐이라 넣었다. 소비자가 실재하므로 죽은 메서드가 아니다.

나머지는 계획대로다. 특히 **`document_id` 를 파일명에서 뽑은 것이 실제로 9절을 살렸다** —
대조군(`upsert` 만)에서 5가 남고 `delete`→`upsert` 에서 3이 남는 것을 실측으로 찍었다.

이 파일이 "VectorDB" 가 아니라 `indexing` 인 이유: `pipeline.py:ingest()` 의 예약 주석이
`index(...)` 로 자리를 잡아뒀고, 다른 단계 기록도 전부 단계 디렉터리 이름을 쓴다
(`splitter.md`·`embedding.md`). qdrant 는 이 단계의 **구현체**지 단계 이름이 아니다.

## 확정된 것

### document_id — 앞 두 단계가 열어두고 기다린 미결

- **`document_id` 는 원본 파일명(`source_name`)에서 뽑는다.** 같은 이름 = 같은 문서 = 갱신
  — 근거: `document_key` 의 현재 기본값인 **파싱 결과 해시로는 재색인이 성립하지 않는다.**
  문서를 수정하면 해시가 바뀌어 옛 청크를 지울 키를 잃고, C·E 만 남는 게 아니라 **문서
  전체가 두 벌 색인된다**
- **`unicodedata.normalize("NFC", ...)` 를 반드시 건다** — 근거: 한글 파일명이 macOS 는 NFD,
  Windows 는 NFC 다. 정규화 없이는 눈으로 같은 파일명이 다른 키가 되어 같은 사고가 난다.
  이 코퍼스가 한국어라 이론적 위험이 아니다
- `split(document, document_key=document_id)` 로 주입한다. `Chunk` 스키마는 **안 바뀐다** —
  splitter 가 인자로 열어둔 자리가 이 용도였다

### 실패 계약 — embedding 과 같은 이유로 예외를 던진다

- **`len(chunks) != len(vectors)` 는 즉시 `IndexingError`.** 부분 성공을 반환하지 않는다
  — 근거: contracts 2절("빈 결과 + `warn()`")은 **내려갈 층이 있어서** 성립하는 규약이고
  (LlamaParse→PyMuPDF, Vision→OCR), indexing 에는 embedding 과 똑같이 없다. 조용한 부분
  실패는 "영원히 검색되지 않는 청크"를 만든다
- `IndexingError` 하나로 받고 하위 타입으로 안 가른다 — 근거: `_status_for()` 의
  `isinstance` 순서 문제만 는다. embedding 이 같은 판단을 했다
- `_STATUS_BY_EXCEPTION` 에 **503** 으로 등록한다. HTTP 경로가 아직 없어도 넣는다 —
  `EmbeddingError` 가 같은 이유로 미리 들어가 있다(`main.py`)

### 모델 혼입 — 검사가 아니라 구조로 막는다

- **컬렉션 이름을 `f"{collection_name}_{model}_{dim}"` 로 파생한다** — 근거: 차원만 검사하면
  **차원이 같고 모델이 다른 경우**를 못 잡는다(`-small` 기본 1536 vs `-large` 를
  `dimensions=1536` 으로 축소한 것). 벡터 공간이 다른데 조용히 섞여 검색이 서서히 나빠진다.
  embedding 이 캐시 salt 를 `embed:{model}:{dim}` 으로 **둘 다** 넣은 것과 같은 판단이다
- 이름에 넣는 쪽을 고른 이유: 사이드카 파일이 필요 없고 **로컬 파일 모드와 서버 모드에서
  똑같이 동작한다.** 그리고 혼입이 검사 통과 여부에 의존하지 않고 구조적으로 불가능해진다

### 저장 형태

- **`VectorRecord.id` 는 `chunk_id` 가 아니라 `uuid5(NS, chunk_id)`** — 근거: qdrant 는
  point ID 로 **unsigned int 또는 UUID 만** 받는다. `"AI-가이드.pdf:0007"` 같은 문자열을
  그대로 못 쓴다. uuid5 는 결정론적이라 같은 `chunk_id` → 같은 point → upsert 가 갱신이 된다.
  사람이 읽는 `chunk_id` 는 payload 로 보존한다
- **거리 척도는 Dot 고정** — 근거: 벡터가 단위 길이라는 것이 유료 호출로 확인됐다
  (0.9998~1.0005, `dimensions=256` 축소본도 0.9999 — `plan_log/embedding.md`).
  Cosine 대신 더 싼 Dot 을 쓸 수 있고 재정규화도 불필요하다
- `SearchResult` 에 **score·text·metadata 를 전부 싣는다.** score 는 이후 threshold·rerank
  가 붙을 자리라 반드시 보존한다

### 합성 지점 — `ingest()` 는 안 바꾼다

- **`index_document()` 를 별도 진입점으로 만든다.** `ingest()` 의 반환은 그대로다
  — 근거: `ingest()` 반환 = `backend` 의 `response_model` 이라 바꾸는 순간 API 결정이 된다.
  그리고 **기본 pytest 가 embedding 을 안 부르는 상태가 유지된다** — "키 없으면 예외"가
  기본 테스트를 안 깨는 것은 그 부작용이었고(`embedding.md`), 여기서 `ingest()` 에 끼우면
  그 지탱이 무너진다
- 재색인 순서는 `ensure_collection()` → `delete_by_document_id()` → `upsert()`.
  단순성과 일관성을 upsert 만으로 얻는 최적화보다 위에 둔다

## 미결

- **backend 색인 엔드포인트** — 정하려면: 이번 단계가 끝나고 `index_document()` 가 실제로
  도는 것을 본 뒤. Retriever 를 뺀 것과 같은 이유로 API 결정이다
- **`top_k` 기본값** — 지금 5 는 임시값이다. **2026-08-23 에 근거의 절반이 생겼다** —
  첫 dense baseline 의 `hit@k` 가 1→0.150 · 3→0.300 · 5→0.400 · 10→0.483 · 20→0.617 이다
  (`evaluation/baseline.json`). 5에서 10으로 늘리면 5건, 10에서 20으로 늘리면 8건이 더 잡힌다.
  **그래도 값을 못 정한다** — 후보를 늘리는 **대가**를 재는 쪽이 아직 없다(생성 단계의
  컨텍스트·비용·지연). 이득만 보고 정하면 `top_k` 가 한없이 커진다.
  정하려면: 생성 단계가 붙어 후보 수의 대가가 생길 때
- **`search(filters=...)`** — 정하려면: 쓸 만한 필터 축이 생겨야 한다. 지금은 `document_id`
  뿐이고 **`page` 는 못 쓴다**(같은 문서를 PDF 28쪽 / HWPX 97쪽으로 세는 미해결 함정).
  metadata 저장·반환은 이번에 하되 필터는 안 만든다
- **모델을 바꿨을 때 옛 컬렉션 정리** — 컬렉션 이름에 모델이 들어가므로 옛 컬렉션이 남는다.
  롤백이 되는 이점과 맞바꾼 것이다. 정하려면: 실제로 모델을 바꿔 디스크가 걸릴 때
- **`document_id` 가 파일명이라 다른 내용의 동명 파일이 서로를 덮어쓴다** — 이번 선택의
  감수 사항이다. 정하려면: backend 에 문서 레지스트리가 생길 때(그때 명시적 id 로 간다)

## 안 하기로 한 것

- **모든 메서드가 `collection: str` 을 받는 것** — 이유: 컬렉션을 하나로 정해놓고 인자로
  받으면 합법값이 하나뿐인 인자다. `embedding.provider` 를 "구현체가 하나면 죽은 설정"이라며
  안 만든 잣대가 그대로 적용된다 / 다시 볼 조건: 컬렉션을 실제로 여럿 쓸 요구가 생길 때
- **`create_collection()` · `delete_collection()` · `collection_exists()` 3종** —
  이유: 실제로 필요한 것은 `ensure_collection()` 하나다. 나머지는 소비자가 없고, 테스트
  정리용이면 픽스처 몫이다
- **`distance` · `qdrant_url` · `provider` 설정** — 이유: 전부 합법값이 하나뿐인 죽은 설정이
  된다. 거리는 Dot 고정(근거가 실측에 있다), 서버 모드는 이번에 안 쓴다 / 다시 볼 조건:
  정규화 안 되는 로컬 임베딩 모델 / 관리형 qdrant
- **`ResultCache` 재사용** — 이유: qdrant 자체가 저장소라 캐시를 얹으면 이중 진실이 된다.
  `content_hash`·`ResultCache` 를 재사용한 splitter·embedding 과 여기가 갈리는 지점이다
- **contracts 3절의 비용 카운터·호출 상한** — 이유: 로컬 파일 모드라 과금이 없다. 그 규약은
  **유료** 외부 API 규약이고, 없는 비용에 카운터를 다는 것은 형식만 베끼는 것이다 /
  다시 볼 조건: 관리형 qdrant 로 갈 때
- **Retriever** — 이유: **질의 엔드포인트가 처음 생기는 단계**라 backend 변경을 동반한다
  (`embedding.md` 선행 조건 표 3번). `search()` 까지가 이번 몫이다 / 다시 볼 조건: 다음 단계
- **초안 16절의 완료 기준 8·10번**("Retriever 가 구현체에 의존하지 않는다", "Semantic cache 가
  침투하지 않는다") — 이유: 그 코드가 저장소에 한 줄도 없어 **체크박스가 항상 통과한다.**
  검사로서 값어치가 0이다. `embedding.md` 1회차가 같은 패턴을 이미 기각했고, 금지 선언 대신
  **다시 볼 조건**으로 적는 것이 이 저장소 형식이다

---

## 1회차 — 2026-08-18 · 설계서 검토와 indexing 설계 확정

**물은 것**

사용자가 VectorDB 구현 계획서(17절)를 가져와 "고쳐야 할 부분"을 요청했다. 초안의 뼈대는
*"저장·검색까지만 하고 semantic cache·history·reranker·hybrid search 를 밀어낸다"* 였다.

**드러난 것 — 뼈대는 옳고, 어긋난 곳이 셋이다**

*(1) 9절 재색인 전략이 지금 코드로는 동작하지 않는다.* 초안은 `Chunk.document_id` 가 있다고
전제했는데 실제 필드는 `document_key` 이고 **기본값이 파싱 결과 해시**다. 그래서 초안이 든
바로 그 예시(`A B C D E` → `A B D`)에서 **수정본이 새 키를 갖는다** — 옛 청크를 못 찾고 문서
전체가 중복된다. 초안이 막으려던 상황이 초안대로 해서 일어난다. `document_id` 가 없는 것은
누락이 아니라 splitter·embedding 이 **일부러 열어두고 indexing 을 기다린 미결**인데
(`splitter.md`·`next-stages.md`), 초안이 이미 있는 것으로 전제해 **이번 단계 최대의 결정을
통째로 건너뛰었다.**

*(2) 11절의 실패 계약이 계약이 아니다.* *"전체 batch 의 성공 여부를 명확하게 반환하거나
예외 처리한다"* — "또는"이 들어간 순간 선택지지 계약이 아니다. embedding 이 같은 자리를
`len(out)==len(in)` **아니면 예외**로 못 박았고 그 이유가 `exceptions.py` 주석에 있다.
같은 논거가 한 칸 밀려 그대로 적용되는 자리다.

*(3) 이 저장소가 강제하는 것 다섯이 빠졌다.* `_STATUS_BY_EXCEPTION` 등록(빠지면 500) ·
`.env.example` · `offline_config` · `auto_test_check.py` 매핑(빠지면 **그 단계만 자동
테스트가 안 도는데 아무 에러도 안 난다**) · 테스트 파일 basename 유일성(초안 14절의
`test_search.py` 류는 `tests/` 에 `__init__.py` 가 없어 collection error 를 낸다).

곁가지로 사실 오류 넷: `Chunk.document_id`(실제 `document_key`) · `Chunk.metadata` 에 page·
section 이 있다는 것(실제로는 **최상위 필드**다) · `preprocessor`(실제 `cleaner`) ·
`ingestion/*.py` 평면 모듈(실제 전부 패키지). 그리고 **`vectorstore/` 최상위**는 `ingest()` 가
`index(...)` 로 예약해둔 자리와 어긋난다.

그리고 초안이 몰랐던 제약 하나를 확인 과정에서 찾았다 — **qdrant 는 point ID 로 임의 문자열을
받지 않는다**(uint 또는 UUID). 4.2절의 *"id 는 기본적으로 chunk_id 를 사용한다"* 가 그대로는
실행되지 않는다.

**선택지 — `document_id` 를 무엇으로 삼나**

| 안 | 장점 | 단점 | 이 프로젝트에서의 근거 |
|---|---|---|---|
| 원본 파일명 기반 안정 키 | 재색인이 실제로 성립. backend 에 저장소를 안 만들어도 된다 | 내용이 다른 동명 파일이 서로를 덮어쓴다 | `source_name` 이 `DocumentMetadata` 에 이미 있다. 한글 파일명이라 **NFC 정규화가 필수** |
| 업로드 시 부여하는 명시적 id | 가장 정확 | "이 업로드가 그 문서의 새 판인가"를 아는 **문서 레지스트리**가 backend 에 새로 필요 | backend 는 지금 `uuid4()` 임시 파일명만 만들고 버린다 — 이번 단계 범위가 크게 는다 |
| 파일 내용 해시 | 지금 동작에 가장 가깝다. 재업로드 중복이 공짜로 막힌다 | **수정본이 새 문서가 되어 옛 판이 영원히 남는다** | 초안 9절 재색인 시나리오를 포기하는 선택 |

**고른 것과 이유**

네 갈래 전부 사용자가 골랐다.

1. **원본 파일명 기반 안정 키.** 세 번째 안은 초안이 스스로 세운 목표(9절)를 포기하는 것이고,
   두 번째는 이번 단계를 backend 설계까지 끌고 간다. 동명 파일 충돌은 감수 사항으로 미결에
   적었다.
2. **`ingest()` 는 그대로 두고 `index_document()` 를 별도 진입점으로.** `ingest()` 반환이 곧
   공개 응답 스키마라는 것이 첫째 이유이고, **둘째가 더 중요하다** — 기본 pytest 가 embedding
   을 안 부르는 상태가 유지되어 "키 없으면 예외"가 테스트를 안 깬다. embedding 의 결정 2·3이
   서로를 지탱하던 구조가 여기서도 그대로 산다.
3. **범위는 `search()` 까지.** Retriever 제외, `search(filters=...)` 제외 — 다만 사용자가
   **metadata 자체는 저장하고 반환해야 한다**고 못 박았다. 필터를 안 만드는 것과 metadata 를
   안 싣는 것은 다른 얘기이고, 검색 결과에서 원본 청크를 되짚는 것이 metadata 의 본래 용도다.
4. **qdrant 로컬 파일 모드.** 도커 서버를 전제하면 *"기본 테스트는 키 없이 전부 통과"* 가
   깨진다. `requirements.txt` 에 `# qdrant-client # 로컬 파일 모드` 로 이미 예약돼 있었다.

**초안에서 덜어낸 것**

`collection` 인자 6개 → 설정 / 컬렉션 관리 3종 → `ensure_collection()` 하나 /
`vectorstore/`·`retrieval/` 최상위 구조 / Phase 5(Retriever) / 완료 기준 8·10번 /
`search(filters=...)`.

**초안에 더한 것**

`document_id` 정의와 NFC 정규화 · uuid5 파생 · 컬렉션 이름의 모델·차원 · Dot 거리 척도 ·
`to_records()` 합성 지점 · `IndexingError` 와 HTTP 매핑 · 하네스 4곳(`.env.example` ·
`offline_config` · `auto_test_check.py` · `requirements.txt`) · 실패 경로 테스트
(초안 14절은 사실상 전부 성공 경로다 — contracts 6절이 *"이 저장소에서 제일 자주 빠지는
테스트"* 라고 적어둔 곳이다) · 재색인 시나리오 테스트(`test_indexing_reindex.py` 가 초안
9절이 실제로 되는지를 본다).

**남은 질문**

위 "미결" 전부. `top_k` 는 `평가-세트.md` 가 닫혀야 근거가 생기고, 색인 엔드포인트는
이번 구현이 도는 것을 본 뒤에 정한다.
