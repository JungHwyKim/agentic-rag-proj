# 아직 구현되지 않은 단계 — 최소 계약

`retriever` 를 건드린 변경에 적용한다.
**구현된 단계는 각자 승격됐다** — cleaner 는 `references/cleaner.md`, splitter 는
`references/splitter.md`, embedding 은 `references/embedding.md`, indexing 은
`references/indexing.md` 다.

**retriever 는 아직 코드가 없다.** `plan_log/embedding.md` 의 선행 조건 표에 따르면 이 단계에서
**질의 엔드포인트가 처음 생긴다** — 지금 `backend` 에는 업로드 하나뿐이라 "질문"이라는 것이
존재하지 않는다. 그래서 이 단계는 `rag_pipeline/` 만이 아니라 `backend/` 변경을 동반한다.

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
  임시값이고, 근거는 정답 질의 세트가 생겨야 나온다(`plan_log/평가-세트.md`).
  수치를 지적하지 않는다
- **metadata 필터 검색은 일부러 안 만들었다.** `search()` 에 `filters` 인자가 없는 것은
  누락이 아니다 — 쓸 만한 필터 축이 `document_id` 뿐이고 **`page` 는 못 쓴다**(같은 문서를
  PDF 28쪽 / HWPX 97쪽으로 세는 미해결 함정). metadata 저장·반환은 이미 된다
- **하이브리드 검색·리랭커·질의 재작성**은 한 줄도 없다. 없는 것을 지적하지 않는다
- `frontend/` 는 비어 있다

## 승격 규칙

retriever 가 실제로 구현되면 이 파일을 `references/retriever.md` 로 승격하고,
`SKILL.md` 라우팅 표의 해당 줄을 바꾼다. cleaner·splitter·embedding·indexing 이 그렇게 갔다.
