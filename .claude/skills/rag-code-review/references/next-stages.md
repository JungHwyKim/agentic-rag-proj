# 아직 구현되지 않은 단계 — 최소 계약

`retriever` 를 건드린 변경에 적용한다.
**구현된 단계는 각자 승격됐다** — cleaner 는 `references/cleaner.md`, splitter 는
`references/splitter.md`, embedding 은 `references/embedding.md`, indexing 은
`references/indexing.md` 다.

**2026-08-23 에 dense·생성에 이어 hybrid(BM25+RRF)까지 생겼다.** 이 파일의 옛 전제
(*"retriever 는 아직 코드가 없다"* · *"질의 엔드포인트가 아직 없다"*)는 더 이상 맞지 않는다.
지금 있는 것은 이렇다.

- `rag_pipeline/retrieval/hybrid.py:retrieve()` — **공개 진입 함수.** dense 한 갈래로도,
  BM25 를 얹어서도 돈다
- `rag_pipeline/retrieval/dense.py:search_dense()` — 질의 임베딩 → `store.search()`.
  `SearchResult` 를 그대로 낸다(승격은 합류하는 쪽이 한다)
- `rag_pipeline/retrieval/bm25.py` · `tokenize.py` · `fusion.py` — BM25 인덱스 · 한국어
  토크나이저 · RRF
- `rag_pipeline/generation/answer.py:generate_answer()` — 근거 청크로 답변 문장 1회
- `rag_pipeline/query.py:answer_question()` — 위 둘을 잇는 **세 번째 진입점**
- `backend/app/api/routes/query.py` — `POST /api/v1/query` · `GET /api/v1/documents`

**나머지는 여전히 없다** — 리랭커 · 질의 재작성 루프 · semantic cache.
그래서 이 파일은 아직 `retriever.md` 로 승격하지 않았다(승격 규칙은 맨 아래). 있는 것에는
아래 계약을 적용하고, 없는 것은 여전히 지적하지 않는다.

새로 생긴 확실한 계약 넷:

- **`retrieve()`·`answer_question()` 은 `store`·`embedder`·`bm25` 를 인자로 받는다.** 스스로
  열면 backend 의 startup 싱글턴과 qdrant 배타 잠금이 충돌하고, BM25 는 질의마다 저장소를
  다시 훑게 된다. 편의 기본값(없으면 열어준다/지어준다)이 보이면 지적한다.
  **`bm25=None` 은 예외다** — 그것이 dense-only 스위치이고, `use_bm25` 설정을 안 만든 이유다
- **`bm25=None` 일 때의 결과는 dense 와 글자 그대로 같아야 한다.** RRF 를 태우면 안 된다 —
  경로가 하나면 순위 변환이 점수 간격을 잃기만 하고, 무엇보다 **hybrid 비교의 기준선이 함께
  움직여** 무엇이 달라졌는지 못 가른다. `test_retrieve_hybrid.py:test_no_bm25_is_exactly_dense`
  가 지킨다
- **BM25 후보 판정을 점수 부호(`score > 0`)로 하면 안 된다.** `BM25Okapi` 는 흔한 낱말에
  IDF 0 을 주므로 **낱말이 겹치는 청크가 통째로 버려진다**(실측: 문서 2개 코퍼스에서 재현).
  판정은 낱말 겹침이고 점수는 순위용이다
- **생성 실패는 예외(`GenerationError`)다.** 로더 쪽 *"빈 결과 + warn"* 규약은 폴백 층이
  있어서 성립하는 것이고 생성에는 없다. `_STATUS_BY_EXCEPTION` 에 503 으로 등록돼 있다.
  **검색 0건은 실패가 아니다** — 모델을 안 부르고 `NO_EVIDENCE_ANSWER` 를 돌려주며
  `Answer.sources` 가 빈 리스트로 나간다
- **`QdrantStore.list_documents()` 는 `VectorStore` Protocol 에 없다.** 누락이 아니라
  저장소 중립을 지킨 결정이다(`plan_log/backend.md`). Protocol 에 올리자는 지적을 하지 않는다

`ingest()` 에 아무 호출도 없다는 이유로 "그 단계가 아직 없다"고 판단하지 않는다.
splitter·embedding·indexing 은 구현됐지만 **일부러 그 자리에 안 끼웠다** — 전 구간 흐름은
`pipeline.py:index_document()` 에 있다 (`references/indexing.md` 3절).

그래서 이 파일에는 **기존 코드에서 확실히 도출되는 계약만** 적는다. 추측으로 만든 체크리스트로
리뷰하면 없는 규칙을 근거로 멀쩡한 설계를 지적하게 된다. 여기 없는 사항은 `contracts.md` 와
실제 코드로 판단하고, 판단이 안 서면 "확인 필요"에 넣는다.

## 확실한 계약

### 입출력 타입

retriever 는 `ParsedDocument` 에서 출발하지 않는다. 사슬은 splitter 에서 이미 끊겼다 —
`indexing` 이 `SearchResult`(`rag_pipeline/schemas/vector.py`)를 내놓으므로 **retriever 는
거기서 출발한다.** 임의의 타입을 새로 만들어내면 `SearchResult` 가 두 벌이 되므로,
새 결과 타입이 보이면 왜 필요한지를 묻는다.

질의 임베딩은 `Embedder.embed_query()` 다. `embed_documents()` 와 **같은 모델·같은 salt** 를
쓰는 것이 계약이고, 다른 모델을 쓰면 컬렉션이 갈려 검색이 아예 0건이 된다
(`references/indexing.md` 2절 — 컬렉션 이름에 모델이 들어간다).

### 저장소를 직접 알지 않는다

retriever 는 `VectorStore` Protocol(`ingestion/indexing/base.py`)만 알아야 하고
`QdrantStore` 나 `qdrant_client` 를 직접 import 하면 지적한다.

**`store.close()` 를 누가 부르는지를 본다.** qdrant 로컬 파일 모드는 저장 디렉터리에 배타
잠금을 걸어, 질의마다 store 를 열고 안 닫으면 두 번째 질의가 `RuntimeError` 로 죽는다.
질의 엔드포인트가 store 를 어떻게 잡고 있는지(요청마다? 앱 수명 동안?)는 **설계 결정이고
정해진 바가 없다** — 마주치면 지적이 아니라 "확인 필요"로 올린다.

### HTTP 격리

`rag_pipeline/` 의 각 단계는 HTTP 를 모른다. `backend` 가 부르는 쪽이다 (`contracts.md` 1절).

### 설정 3곳

새 설정은 `rag_pipeline/config.py` · `.env.example` · `tests/conftest.py` 의
`offline_config` 픽스처가 맞아야 한다. `offline_config` 는 "키 없음 + 외부 호출 없음"을
보장하는 픽스처이므로, 새 단계가 기본값으로 외부 호출을 하면 여기에 끄는 설정이 추가돼야 한다.

### 새 예외

`rag_pipeline/exceptions.py` 에 추가하고 `backend/app/main.py` 의 `_STATUS_BY_EXCEPTION` 에
등록한다. (`contracts.md` 4절)

## 아직 설계되지 않은 것 — 규칙을 만들지 않는다

- **`top_k` 기본값과 relevance threshold 는 근거가 없다.** 지금 `search(top_k=5)` 의 5는
  임시값이다. **2026-08-22 에 정답 질의 세트가 생겼고**(`evaluation/qa_set.jsonl`, 60건,
  사람 검수 완료) **dense baseline 도 쟀다**(`hit@10` 0.483 · `MRR` 0.253 —
  `verify_log/2026-08-22-검색평가-세트.md`). 근거를 잴 수 있으므로 `top_k` 수치를 지적하는
  대신 *"무엇으로 정했나"* 를 묻는다. 다만 **비교 1~6번은 아직 하나도 안 돌았다** —
  hybrid·리랭커·포맷이 전부 미구현이거나 미측정이라 지금 값들은 여전히 임시값이다.
  수치를 지적하지 않는다
- **metadata 필터 검색은 일부러 안 만들었다.** `search()` 에 `filters` 인자가 없는 것은
  누락이 아니다 — 쓸 만한 필터 축이 `document_id` 뿐이고 **`page` 는 못 쓴다**(같은 문서를
  PDF 28쪽 / HWPX 97쪽으로 세는 미해결 함정). metadata 저장·반환은 이미 된다
- **하이브리드 검색·리랭커·질의 재작성**은 한 줄도 없다. 없는 것을 지적하지 않는다
- **점수 임계값은 없다.** 근거 유무를 프롬프트로만 가르고 `dense_score` 는 실어 나르기만 한다
  — Dot 점수 분포를 아직 안 재서 임계값을 지어내야 하기 때문이다. 없는 것을 지적하지 않는다
- `frontend/` 는 비어 있다 — 화면은 `backend/app/templates/` 에 서버 렌더로 산다

## 승격 규칙

retriever 가 실제로 구현되면 이 파일을 `references/retriever.md` 로 승격하고,
`SKILL.md` 라우팅 표의 해당 줄을 바꾼다. cleaner·splitter·embedding·indexing 이 그렇게 갔다.

**다시 볼 조건이 2026-08-23 에 충족됐다** — hybrid 가 붙었다. 그런데도 **승격하지 않았다.**
리랭커·재작성 루프가 없어 파일의 상당 부분이 여전히 "아직 없는 것"이고, 제목만 바꾸면 그
부분이 거짓이 된다. 조건을 다시 좁힌다 — **다시 볼 조건: 리랭커(Phase 3)가 붙을 때.**
그때 검색과 생성을 두 파일로 가를지도 함께 정한다.
