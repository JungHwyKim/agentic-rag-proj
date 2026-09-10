# 질의 경로 — 검색

`knowledge/query/README.md` 가 이 폴더의 지도다. 공통 규약은 `contracts.md`.

## 주입과 갈래

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
- **`QdrantStore.list_documents()` 는 `VectorStore` Protocol 에 없다.** 누락이 아니라
  저장소 중립을 지킨 결정이다(`.claude/wiki/plan_log/backend.md`). Protocol 에 올리자는 지적을 하지 않는다

## 확실한 계약

### 입출력 타입

retriever 는 `ParsedDocument` 에서 출발하지 않는다. 사슬은 splitter 에서 이미 끊겼다 —
`indexing` 이 `SearchResult`(`rag_pipeline/schemas/vector.py`)를 내놓으므로 **retriever 는
거기서 출발한다.** 임의의 타입을 새로 만들어내면 `SearchResult` 가 두 벌이 되므로,
새 결과 타입이 보이면 왜 필요한지를 묻는다.

질의 임베딩은 `Embedder.embed_query()` 다. `embed_documents()` 와 **같은 모델·같은 salt** 를
쓰는 것이 계약이고, 다른 모델을 쓰면 컬렉션이 갈려 검색이 아예 0건이 된다
(`.claude/wiki/knowledge/ingestion/indexing.md` 2절 — 컬렉션 이름에 모델이 들어간다).

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
  `.claude/wiki/verify_log/2026-08-22-검색평가-세트.md`). 근거를 잴 수 있으므로 `top_k` 수치를 지적하는
  대신 *"무엇으로 정했나"* 를 묻는다. 다만 **비교가 다 돈 지금도 `top_k` 자체는 임시값이다** —
  hybrid(2026-08-23)·리랭커(2026-08-25~)·포맷(`2026-08-23-포맷비교.md`)은 전부 돌았지만
  그 어느 실행도 `top_k` 를 축으로 삼지 않았다. 수치를 지적하지 않는다
- **metadata 필터 검색은 일부러 안 만들었다.** `search()` 에 `filters` 인자가 없는 것은
  누락이 아니다 — 쓸 만한 필터 축이 `document_id` 뿐이고 **`page` 는 못 쓴다**(같은 문서를
  PDF 28쪽 / HWPX 97쪽으로 세는 미해결 함정). metadata 저장·반환은 이미 된다
- **질의 재작성은 2026-08-27 에 생겼다**(`rag_pipeline/retrieval/rewrite.py`) —
  1차 검색 결과를 보고 질의를 다시 쓴다(PRF). `REWRITE_MODEL` 이 비면 `get_rewriter()` 가
  `None` 을 돌려 **꺼진 채 커밋된다**. 다섯을 보되 **없는 것을 요구하지는 않는다.**
  ① **실패는 `None` 이고 그러면 1차 pool 이 그대로 쓰인다** — 예외를 던지거나, 응답에서 앞
  몇 글자를 건져 쓰는 "부분 성공"이 보이면 지적한다(리랭커의 *전부 아니면 전무* 와 같은 자리).
  ② **`rewrite_cache_salt` 에 `rewrite_mode` 를 넣자는 제안이 지적 대상이다** — `rerank` 쪽이
  `device` 를 **싣는** 것과 비교해 "빠뜨렸다"로 보이지만, 규칙은 같고(*출력을 가르는 것만
  salt 에 넣는다*) mode 는 재작성 호출 **뒤**에서 갈린다. 넣으면 `fuse`·`replace` 두 프로필이
  캐시를 못 나눠 써 **두 번째 프로필이 통째로 다시 과금된다**(`test_mode_does_not_split_the_cache`).
  **반대로 답변 캐시의 `pipeline_fingerprint()` 가 `rewrite_mode` 를 싣는 것은 지적 대상이 아니다**
  (2026-08-28) — 같은 기준이 낸 반대 답이다. 저쪽이 가리는 것은 *재작성 호출의 출력* 이고 이쪽은
  *검색 결과와 답 본문* 이라, `fuse`/`replace` 가 그것을 실제로 가른다. **«비대칭이니 통일하자» 가
  이 자리의 전형적인 오지적이고, 통일하면 둘 중 하나가 반드시 틀린다.**
  **세 번째 얼굴이 2026-08-29 에 생겼다 — `GENERATION_PROMPT_VERSION` 을 `pipeline_fingerprint()` 에
  넣자는 제안이 지적 대상이다.** `EVALUATOR_PROMPT_VERSION` 이 판정 캐시 salt 에 들어가는 것과
  비교해 «빠뜨렸다» 로 보이는데, 이 상수가 사는 이유가 애초에 다르다 — 생성에는 **캐시가 없어**
  (`generate_answer()` 가 `ResultCache` 를 안 거친다) *옛 응답이 새 프롬프트의 답인 척* 이 원리적으로
  불가능하고, 이 버전 번호가 막는 것은 **리포트가 자기 생성 조건을 못 말하는 것**이라 `answer_eval` 의 meta 가
  싣는다. 지문에 넣으면 문자열이 바뀌어 **`qa_cache_*` 가 전량 미스**가 되는데, 지금 `exact` 캐시는
  *"`source=="user"` 50줄에서 적중률 5% 미만이면 끈다"* 의 표본을 모으는 중이라 그 판정을 아래로 민다
  (`fingerprint.py` 가 재시도 인자를 **조건부로** 붙인 것과 같은 이유다).
  ③ **`rewrite_mode` 는 죽은 설정이 아니다** — 합법값 둘이 실재하고, 그 축이 존재하는 이유는
  **21회차 상한 프로브가 `replace` 를 쟀는데 7회차 로드맵은 `fuse` 였기 때문**이다
  (`sih-07` 102위 → 2위 는 `replace` 의 수치다). 하나로 줄이자는 제안은 *프로브가 재현되나* 와
  *로드맵이 옳았나* 중 하나를 영원히 못 묻게 만든다.
  ④ **리랭커에 넘어가는 질의가 원 질의인 것이 결정이다** — 재작성 질의로 "일관되게" 바꾸자는
  제안이 지적 대상이다. `rerank_cache_key()` 가 query 를 실어, 재작성 프롬프트를 한 줄 고칠
  때마다 **리랭킹 캐시가 전량 미스**가 되고 운영이 cohere 라 그것은 곧 유료 호출이다.
  ⑤ **`Protocol` 도 `rewrite_provider` 도 없는 것이 결정이다** — 구현체가 하나라 `answer_cache`
  · Evaluator 와 같은 자리다. 이웃한 `reranker/` 가 Protocol 을 가진 것은 구현체가 **셋**이라서다.
  **품질은 2026-08-27 에 쟀고 운영 모드는 `fuse` 로 닫혔다** — LLM 60건에서 RRF 를 여덟 칸에서
  이기고 어디서도 안 진다. **`replace` 를 「프로브가 잰 쪽이니 그게 낫다」로 되돌리자는 제안이
  지적 대상이다** — 그쪽은 `hit@1`·`hit@20` 에서 RRF 에 지고, RRF 1위였던 26건 중 1위 유지가
  19/26 이다(`fuse` 25/26). **다만 「모델이 사람만큼 다시 쓴다」는 기각됐다** — 프로브 표적 4건
  중 둘만 부분 상승했고 `sih-07` 102→2 는 재현되지 않았다. **그 상한 수치를 기대값으로 쓰는
  지적을 하지 않는다.**
  ⑥ **화면 배선은 2026-08-27 에 생겼고, `answer_question()` 안에서 질의를 미리 다시 쓰자는
  제안이 지적 대상이다** — `routes/query.py` 가 질의마다 `get_rewriter()` 를 부르고
  `answer_question()` 은 **넘겨주기만 한다.** 미리 쓰면 `run_eval` 이 `retrieve()` 를 직접
  불러 **평가가 그 경로를 안 밟는다.** 그리고 **인스턴스를 싱글턴으로 "고치는" 제안도 같다** —
  Evaluator 와 같은 자리이고 근거도 같다(가중치 없음 · `warnings`·`usage` 누적).
  딸린 `ResultCache` 가 축처럼 보이지만 **경로 + 불리언뿐이라 상태를 안 든다**

- **리랭커의 실패는 예외가 아니라 폴백이고, 반쯤 채우면 안 된다.** 점수를 하나라도 못 얻으면
  `rerank_score` 를 **아무것도 안 채우고** 받은 순서를 그대로 돌려준다 —
  `ranking_score` 가 `fused_score` 로 떨어져 RRF 순서가 된다. **부분 성공을 만드는 방향으로
  "개선"하지 말 것**(`표-경로.md` 13회차의 *채운 만큼 쓴다* 기준을 여기 옮기면, 순서가 반쯤
  바뀌어 *"리랭커가 나쁜가 응답이 반쪽이었나"* 를 못 가른다). 경고는 `Reranker.warnings` 다
- **리랭커는 `retrieve()` 밖으로 빼면 안 된다.** 절단을 호출부로 옮기면 `top_k` 의 뜻이
  *"최종 개수"* 와 *"후보 수"* 로 갈리고, 세 호출부 중 하나만 빠뜨리면 **조용히** 리랭커 없이
  돈다. 같은 이유로 **dense-only 갈래의 후보 폭도 `reranker` 가 있으면 넓혀야 한다**
- **`fusion.py` 의 갈래는 N 개이고 `fuse(dense, sparse)` 로 되돌리면 안 된다.** 재작성의
  `fuse` 모드가 갈래 넷을 쓴다. **두 갈래짜리 지름길을 "편의로" 되살리자는 제안도 지적 대상이다** —
  한 번 남겼다가 `hybrid.py` 가 `_branches_for()` 로 옮겨 가며 production 소비자가 0 이 되어
  지웠다(계약 테스트는 `test_rrf.py` 가 자기 helper 로 `fuse_branches` 를 부른다).
  그리고 **1차 pool 을 다시 RRF 에 넣으면 안 된다** — 순위 압축이 두 번 걸린다. 합칠 것은
  **원 검색 결과**다
- **`RetrievedChunk` 의 재작성 갈래 점수 두 칸을 한 칸으로 합치면 안 된다.**
  `rewrite_dense_score`·`rewrite_bm25_score` 를 `dense_score`·`bm25_score` 로 돌려쓰면
  *원 질의가 찾은 것* 과 *재작성이 찾은 것* 이 섞여, 재작성이 값을 했는지를 payload 로 못
  되짚는다. `replace` 모드에서 `dense_score` 가 전량 `None` 인 것은 **사실 그대로**다
  (원 질의로는 검색을 안 했다) — "채워 주자"는 제안이 지적 대상이고, 이 모듈이 `0.0` 을
  안 채워 지키려던 구분이 한 겹 위에서 다시 무너지는 자리다
- **`rerank_provider` 는 죽은 설정이 아니다.** 구현체가 **셋**이라(`openai` listwise LLM 채점기 ·
  `local` cross-encoder · `cohere` 전용 리랭킹 API) `embedding_provider` 와 같은 자리다.
  **뒤 둘을 같은 '유료' 로 묶어 읽으면 안 된다.** `rerank_device` 기본값 `cpu` 는 성능
  선택이 아니라 **재현성**이다 — 지우면 GPU 머신이 다른 순위를 낸다
- **점수 임계값은 없다.** 근거 유무를 프롬프트로만 가르고 `dense_score` 는 실어 나르기만 한다
  — Dot 점수 분포를 아직 안 재서 임계값을 지어내야 하기 때문이다. 없는 것을 지적하지 않는다
