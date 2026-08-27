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
- `rag_pipeline/generation/evaluate.py:OpenAIEvaluator` — 근거가 충분한지 판정 1회 (기본 꺼짐)
- `rag_pipeline/query.py:answer_question()` — 위 셋을 잇는 **세 번째 진입점**
- `backend/app/api/routes/query.py` — `POST /api/v1/query` · `GET /api/v1/documents`

**나머지는 여전히 없다** — 질의 재작성 루프 · agent 레이어.
**리랭커는 2026-08-25 에 생겼다**(`rag_pipeline/retrieval/reranker/`) — `RERANK_MODEL` 이
비면 `get_reranker()` 가 `None` 을 돌려 꺼져 있고, **그 빈 기본값도 미완성이 아니라
결정이다**(켜는 순간 질의당 LLM 호출이 1→2 다). **품질은 아직 안 쟀다** — 2×2 비교표가
`plan_log/검색-생성.md` 5회차에 비어 있다. **답변 캐시는 2026-08-24 에 생겼고 2026-08-26 에 갈래가 둘이 됐다**(`rag_pipeline/answer_cache/`) — `ANSWER_CACHE_MODE` 가 `semantic`(벡터 유사도)과 `exact`(정규화한 문자열)를 고른다. **꺼짐 조건이 갈래마다 다르다**: `semantic` 은 `ANSWER_CACHE_THRESHOLD` 가 비면 `get_answer_cache()` 가 `None` 을 돌려 꺼지고, **`exact` 는 그 값을 아예 안 봐 모드 한 줄로 켜진다**(완전 일치에는 정할 임계값이 없다). 저장소 커밋본은 `semantic` + 빈 임계값이라 여전히 꺼진 채이고, **그 빈 기본값이 미완성이 아니라 결정이다**(근거는 `references/indexing.md` 4절 · `plan_log/시맨틱-캐시.md` 4~6회차 — 양성·음성 분포가 겹쳐 임계값을 *"못 정한다"* 로 닫았다). **`exact` 를 "임계값 1.0" 으로 흉내 내자는 지적을 하지 않는다** — 그 안은 기각됐다(임계값은 *같은 벡터인가*를 재는 자[尺]인데 완전 일치가 묻는 것은 *같은 문자열인가*이고, 부동소수라 보장도 없다). 마찬가지로 **`put()`·무효화·`clear()` 가 갈래를 안 타는 것도 결정이다** — `exact` 에서도 진짜 벡터를 저장해야 나중에 `semantic` 으로 돌렸을 때 오염된 캐시를 검색하지 않는다.
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
- **질의 재작성은 한 줄도 없다.** 없는 것을 지적하지 않는다 — `plan_log/검색-생성.md`
  7회차가 표적이 80건 중 1건이라 물렸다
- **Evaluator 는 2026-08-26 에 생겼다**(`rag_pipeline/generation/evaluate.py`) —
  `EVALUATOR_MODEL` 이 비면 `get_evaluator()` 가 `None` 을 돌려 **꺼진 채 커밋된다**(켜는
  순간 질의당 LLM 호출이 1→2 다). 세 가지를 보되 **없는 것을 요구하지는 않는다.**
  ① **실패는 제3의 상태다** — `verdict="failed"` 를 *충분*·*불충분* 어느 쪽으로도 떨어뜨리는
  폴백이 보이면 지적한다. *불충분* 으로 떨어뜨리면 `qa_set_human.jsonl` 의 부정 **22건**(`56fb382` 로 25→23→22)에서
  **죽은 Evaluator 가 만점**을 받아 점수가 실력이 아니라 실패율을 잰다. `bool | None` 로
  바꾸자는 제안도 같은 지적 대상이다(`None` 이 falsy 라 호출부 한 줄이 실패를 삼킨다).
  ② **판정이 답변을 바꾸지 않는다** — 생성을 막거나 프롬프트를 바꾸는 코드가 보이면
  지적한다(재시도할 Rewriter 가 없고, 답변 품질을 재는 자[尺]가 아직 없어 그 변화가 판정
  수치와 섞인다). ③ **`Protocol` 도 `evaluator_provider` 도 없는 것이 결정이다** — 구현체가
  하나라 `answer_cache` 와 같은 자리다. 둘째가 실재하기 전에는 요구하지 않는다.
  **⑤ 호출 인자 셋은 2026-08-27 실측이라 "정리" 대상이 아니다**(`plan_log/검색-생성.md` 13회차).
  **`evaluator_temperature: float | None` 을 `float` 으로 좁히자는 제안이 지적 대상이다** —
  `None` 은 *그 인자를 아예 안 보낸다* 는 뜻이고, gpt-5 계열이 `temperature=0.0` 을 **400**
  (`Only the default (1) value is supported`)으로 거부해서 생긴 상태다. **`1.0` 을 대신 보내는
  것으로 대체하자는 제안도 같다** — 결과는 같지만 필드가 `float` 로 남아 *0.0 이 재현을 위해
  고른 값이었다* 는 사실이 코드에서 지워진다. **`max_completion_tokens` 에 모델별 분기를 넣자는
  제안도 지적 대상이다** — `gpt-4.1-mini` 도 그 이름을 받는 것을 탐침으로 확인했고, 갈라 두면
  새 모델마다 그 표를 손봐야 하는데 빠뜨리면 **에러가 아니라 전건 `failed`** 로 나타난다.
  그리고 **`EVAL_EVALUATORS` 의 기본이 `off` 가 아니라 `mini` 인 것도 결정이다** — 리랭커와
  모양이 같아 보여 맞추자는 제안이 나오는데, 판정이 안 돌면 **전건 `failed` 인데 혼동행렬은
  멀쩡해 보인다**(채점 도구는 판정이 반드시 돌아야 하는 자리다).
  ④ **화면 배선은 2026-08-27 에 생겼고, 인스턴스를 싱글턴으로 "고치는" 제안이 지적 대상이다** —
  `routes/query.py:ask()` 가 **질의마다** `get_evaluator()` 를 부른다. 이웃 셋(`embedder`·
  `bm25`·`reranker`)이 `app.state` 싱글턴이라 일관성 있게 보이지만, **저 셋의 근거는 가중치**
  (KURE 2.27GB · cross-encoder 적재 · 저장소 전량 훑기)이고 Evaluator 에는 그것이 없다.
  싱글턴으로 되돌리면 `warnings`·`usage` 가 인스턴스에 누적돼, `EVALUATOR_MODEL` 은 찼고
  키가 없는 조합에서 **질의마다 경고 한 줄**이 서버 수명 내내 쌓인다(읽는 곳도 없고 에러도
  안 난다). 화면은 **불충분·실패일 때만** 배지를 그리고 *충분* 은 줄 자체를 안 그리는데,
  *"충분도 보여주자"* 는 제안 역시 결정을 되돌리는 것이다(`cached_from` 배지와 같은 규칙이고,
  대가로 *충분* 과 *꺼짐* 이 화면에서 같아 보이는 것을 **알고 골랐다**).
  ⑤ **판정 프롬프트의 하위 규칙은 2026-08-27 에 하나만 생겼다**(v2 — *빈 서식은 답이 아니다*).
  **문구를 고치거나 규칙을 더하자는 제안에는 재측정이 딸린다** — `EVALUATOR_PROMPT_VERSION`
  을 함께 올려야 캐시가 갈리고(안 올리면 옛 응답이 새 프롬프트의 답인 척 돌아온다),
  값을 하는지는 43건 × K 둘 = **86호출**로만 알 수 있다. 특히 *"제목 머리만 있으면 불충분"*
  은 **이미 기각된 안**이다(정답 span 이 제목 머리에만 있는 청크가 실재한다 —
  `sih-06`·`sih-14`). 실측(`verify_log/2026-08-27-프롬프트-v2.md`)은 규칙 3이 **겨냥한 셋을
  하나도 안 고치고** 안 겨냥한 한 건을 고쳤다고 말한다 — 그러니 *"규칙을 더 좁게 쓰면
  그 셋이 잡힌다"* 는 제안은 표본 3에 대한 과적합이다.
  판정을 무엇으로 재는지는 `evaluation/verdicts.py`(혼동행렬 + 실패 **열**)이고, 리랭커의
  *"전부 아니면 전무"* 폴백 + `Reranker.warnings` 와 같은 모양이라 새 잣대가 아니다
- **"아직 없다"는 면제는 생긴 단계에 안 준다** — 하이브리드(2026-08-23) · 리랭커(2026-08-25) ·
  Evaluator(2026-08-26)는 전부 이 면제에서 빠진다. 아래 리랭커 계약과 위 Evaluator 셋을 적용한다
- **리랭커의 실패는 예외가 아니라 폴백이고, 반쯤 채우면 안 된다.** 점수를 하나라도 못 얻으면
  `rerank_score` 를 **아무것도 안 채우고** 받은 순서를 그대로 돌려준다 —
  `ranking_score` 가 `fused_score` 로 떨어져 RRF 순서가 된다. **부분 성공을 만드는 방향으로
  "개선"하지 말 것**(`표-경로.md` 13회차의 *채운 만큼 쓴다* 잣대를 여기 옮기면, 순서가 반쯤
  바뀌어 *"리랭커가 나쁜가 응답이 반쪽이었나"* 를 못 가른다). 경고는 `Reranker.warnings` 다
- **리랭커는 `retrieve()` 밖으로 빼면 안 된다.** 절단을 호출부로 옮기면 `top_k` 의 뜻이
  *"최종 개수"* 와 *"후보 수"* 로 갈리고, 세 호출부 중 하나만 빠뜨리면 **조용히** 리랭커 없이
  돈다. 같은 이유로 **dense-only 갈래의 후보 폭도 `reranker` 가 있으면 넓혀야 한다**
- **`rerank_provider` 는 죽은 설정이 아니다.** 구현체가 둘이라(OpenAI listwise · 로컬
  cross-encoder) `embedding_provider` 와 같은 자리다. `rerank_device` 기본값 `cpu` 는 성능
  선택이 아니라 **재현성**이다 — 지우면 GPU 머신이 다른 순위를 낸다
- **점수 임계값은 없다.** 근거 유무를 프롬프트로만 가르고 `dense_score` 는 실어 나르기만 한다
  — Dot 점수 분포를 아직 안 재서 임계값을 지어내야 하기 때문이다. 없는 것을 지적하지 않는다
- `frontend/` 는 비어 있다 — 화면은 `backend/app/templates/` 에 서버 렌더로 산다

## 승격 규칙

retriever 가 실제로 구현되면 이 파일을 `references/retriever.md` 로 승격하고,
`SKILL.md` 라우팅 표의 해당 줄을 바꾼다. cleaner·splitter·embedding·indexing 이 그렇게 갔다.

**다시 볼 조건이 두 번 충족됐고 두 번 다 승격하지 않았다** — hybrid(2026-08-23)와
리랭커(2026-08-25)다. 이번에도 안 올린 이유는 **같은 것이 아니다.**

저번에는 *"리랭커·재작성 루프가 없어 파일의 상당 부분이 아직 없는 것"* 이었다. 지금은
검색 쪽이 다 찼는데도 미룬 이유가 둘이다.

- **품질을 아직 안 쟀다.** 리랭커는 코드가 있고 계약이 잠겨 있지만 2×2 비교표가 한 칸도 안
  찼다(`plan_log/검색-생성.md` 5회차). *"켤 조합"* 이 정해지면 기본값·상한이 함께 정해지는데,
  그 전에 `retriever.md` 로 굳히면 **곧 낡을 값을 리뷰 기준으로 만든다**
- **가를지 말지가 아직 미결이다.** 검색과 생성을 한 파일로 둘지 두 파일로 가를지를 이 파일이
  계속 미뤄 왔다. 리랭커가 붙으며 검색 쪽 분량이 생성 쪽을 넘었으므로 **이제는 가르는 쪽이
  유력하다** — 다만 그 판단은 승격과 함께 한 번에 한다

**다시 볼 조건: 2×2 비교표가 채워져 리랭커를 켤지 정해질 때.** 그때 `retriever.md` ·
`generation.md` 로 가르는 것을 기본안으로 놓고 본다.
