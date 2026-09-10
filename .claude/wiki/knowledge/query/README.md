# 질의 경로 — 지도와 현재 상태

`rag_pipeline/retrieval/**` · `rag_pipeline/generation/**` · `rag_pipeline/agent/**` ·
`rag_pipeline/answer_cache/**` 를 건드린 변경에 적용한다. 전 단계 공통 규약은 `contracts.md`.

| 파일 | 무엇을 |
|---|---|
| `retriever.md` | 검색 — 주입 인자 · dense/BM25/RRF · 리랭커 · 질의 재작성 · `top_k`·필터 |
| `generation.md` | 생성과 판정 — 에이전트 트랙 · 생성 실패 · Evaluator |
| (여기) | 무엇이 언제 생겼나 · 「도출되는 계약만」 원칙 |

**이 폴더는 `rag-code-review` 스킬이 갖고 있던 *next-stages.md* 의 승격이다.** 그 파일이 자기 「승격 규칙」에
*"`retriever.md` · `generation.md` 로 가르는 것을 기본안으로 놓고 본다 — 검색 쪽 분량이 생성
쪽을 이미 크게 넘었다"* 라고 적었고, 「다시 볼 조건」(*재작성 첫 유료 측정이 끝나
`fuse`/`replace` 가 정해질 때*)이 **2026-08-27 에 충족됐다**(`fuse` 로 닫혔다).
**두 번 미뤘던 이유 둘도 함께 없어졌다** — 리랭커를 켤 조합은 `RERANK_PROVIDER=cohere` 로,
재작성 모드는 `fuse` 로 정해졌다.

**2026-08-23 에 dense·생성에 이어 hybrid(BM25+RRF)까지 생겼다.** 이 파일의 옛 전제
(*"retriever 는 아직 코드가 없다"* · *"질의 엔드포인트가 아직 없다"*)는 더 이상 맞지 않는다.
지금 있는 것은 이렇다.

- `rag_pipeline/retrieval/hybrid.py:retrieve()` — **공개 진입 함수.** dense 한 갈래로도,
  BM25 를 얹어서도 돈다
- `rag_pipeline/retrieval/dense.py:search_dense()` — 질의 임베딩 → `store.search()`.
  `SearchResult` 를 그대로 낸다(승격은 합류하는 쪽이 한다)
- `rag_pipeline/retrieval/bm25.py` · `tokenize.py` · `fusion.py` — BM25 인덱스 · 한국어
  토크나이저 · RRF. **`fusion.py` 는 갈래 N 개다**(`Branch` · `fuse_branches`) — 재작성의
  `fuse` 모드가 넷을 쓴다
- `rag_pipeline/retrieval/rewrite.py:QueryRewriter` — 1차 검색 결과를 보고 질의를 다시
  쓴다(PRF). 기본 꺼짐
- `rag_pipeline/generation/answer.py:generate_answer()` — 근거 청크로 답변 문장 1회
- `rag_pipeline/generation/evaluate.py:OpenAIEvaluator` — 근거가 충분한지 판정 1회 (기본 꺼짐)
- `rag_pipeline/query.py:answer_question()` — 위 셋을 잇는 **세 번째 진입점**.
  `bm25`·`reranker`·`rewriter`·`cache`·`evaluator` 를 인자로 받아 넘긴다.
  **판정이 답변을 안 바꾼다** — verdict 는 `Answer.verdict` 에 실리기만 한다
- `backend/app/api/routes/query.py` — `POST /api/v1/query` · `GET /api/v1/documents`
- `rag_pipeline/agent/` — 도구(`tools/`) · 상태(`state.py`) · 라우터(`router.py`) ·
  근거 조립(`context.py`)은 2026-08-27 에, **재시도 루프(`loop.py`)와 그 그래프(`graph.py`)는
  2026-08-28 에** 생겼다. 스키마는 `schemas/agent.py` 다
- `rag_pipeline/agent/loop.py:run_agent_loop()` — **네 번째 진입점**이고 `answer_question()`
  과 **계약이 반대다**: 저쪽은 판정이 답변을 안 바꾸는데 여기서는 판정이 **흐름을 가른다**.
  그래서 둘을 합치자는 지적을 하지 않는다 — 한 함수가 반대되는 두 계약을 가지면
  *판정이 맞았나* 와 *답이 좋아졌나* 가 한 실행에 섞이는데, **골든 답변이 없어 「답이
  맞나」는 아직 못 잰다**(`evaluation/answer_eval.py` 가 2026-08-28 에 생겼지만 그것이
  재는 것은 **근거 충실도**이지 정답 여부가 아니다 — 무관한 근거를 충실히 옮긴 답변은
  실제로 근거에 충실하다).
  **backend 는 안 부른다**(배선은 미결)
- `rag_pipeline/agent/graph.py` — 같은 핸들러·같은 `decide_next_action()` 을 LangGraph 로
  배선만 한다. **여기에 로직을 더하지 않는 것이 「동일 재현」의 근거다**

**agent 레이어의 뼈대와 그것을 잇는 루프가 다 생겼다.** 남은 것은 배선(화면·평가)과
Phase 10 의 **나머지 반**이고, **배선은 미완성이 아니라 미결이다**(`.claude/wiki/plan_log/검색-생성.md` 27회차).
**Phase 10 의 앞 반은 2026-08-28 에 생겼다** — 인용 검증(`rag_pipeline/generation/citations.py`)이
답변의 `[n]` 이 근거 범위 안인지 보고 `Answer.citation` 에 싣는다. **검사가 답변을 안 바꾸므로**
*"틀린 번호를 지우자"* 는 지적을 하지 않는다(`verdict` 와 같은 계약이다). **캐시 히트에도 도는 것이
`verdict` 와 반대인 것도 결정이다** — 저쪽은 *이번 실행에서 판정을 돌렸나* 라 실행의 속성이고 이쪽은
*이 답변의 인용이 맞나* 라 답변의 속성이라, 캐시에 실린 `(text, sources)` 로 공짜로 다시 계산된다.
**«통일하자» 가 둘 중 하나를 반드시 틀리게 한다.** 그리고 **건너뛰는 자리 둘**(근거 0개 · 답변이
`NO_EVIDENCE_ANSWER`)을 «중복이니 하나로» 로 줄이자는 지적도 하지 않는다 — 뒤엣것이 없으면
«근거를 못 찾았다» 가 전부 인용 오류로 뜬다. **남은 반도 2026-08-28 에 들어왔다** — `step_generate()` 가
`select_within_budget()` 으로 **한 번 자르고** 그 목록을 `final_context` 와 생성 입력 **양쪽**에
준다. 그래서 *"예산을 build_context 에 도로 넣자"* 는 지적을 하지 않는다 — 두 벌로 자르면 한쪽
인자만 고쳐도 에러가 안 나고, **그 모양이 정확히 고쳐진 버그였다.** **`retrieved_chunks` 를 자른
목록으로 덮자는 지적도 하지 않는다** — 자르는 것은 *보여줄 것* 이고 저쪽은 *물어온 것* 이라,
덮으면 `evidence_unchanged_at` 의 앞뒤 대조와 `_reason_for()` 의 0건 판정이 조용히 다른 것을 본다.
**생성 질의가 `original_query` 인 것도 결정이다** — 스펙은 `current_query` 라고 적었지만 재작성
질의는 검색어 뭉치이고, 리랭커·Evaluator·`answer_question()` 이 이미 원 질의를 쓴다.
**Phase 10 은 2026-08-29 에 닫혔다 — DoD 를 문자 그대로가 아니라 대체 지표 셋으로 채웠다.**
Faithfulness·Hallucination rate 는 골든 답변이 없어 그 이름대로 못 재고, 대신
`evaluation/answer_eval.py` 가 인용 정합성 · 근거 지지 · 음성 대조군을 낸다. **«이름을
`faithfulness` 로 통일하자» 는 지적을 하지 않는다**(29회차 — *잰 것의 이름으로 부른다*).
**같은 날 그 수를 읽는 대조군 둘이 붙었다** — `citation_groundedness_chance`(무작위로
인용했을 때의 기대값)와 `_ceiling`(그 답변이 낼 수 있는 최대). 셋을 한 칸으로 접자는 제안이
지적 대상이다 — 접으면 *0.47 이 좋은 수인가* 를 다시 못 묻는다(실측 7회에서 관측이
무작위의 2.0~4.6배 · 최대의 90~100% 이고, **최대가 0.44~0.53 이라 1.0 은 도달 가능한 값이
아니다**). **`chance <= 관측` 을 불변식으로 잠그자는 제안도 지적 대상이다** — 관측이 무작위
아래로 갈 수 있고 그것도 답이라, 잠그면 지표가 결론을 미리 정한다(잠기는 것은
`ceiling >= 관측` 하나다). **대조군을 회계 게이트나 통과선에 넣자는 제안도 같다** —
40회차의 «무효화 vs 분모 주석» 어느 쪽도 아닌 **읽는 재료**다. **«인용 정합성을 올리자» 는 제안은
더 강하게 지적한다** — 최대가 `c`(인용 개수)에 지배되어(손실의 74.8% · `G` 는 사실상 0/1) 답변을
한 글자도 안 바꾸고 **인용만 하나로 줄이면 0.474 → 0.876** 이다. 그것은 품질이 오른 것이 아니라 지표를 유리하게 만든 것이다. 그리고 **백필이 인용 칸을
다시 계산해 저장된 값과 대조하는 것을 «중복이니 빼자» 로 줄이지 않는다** — 그 검사가
실제로 버그를 잡았다(기권 행은 `score_answers()` 가 인용 칸을 아예 안 계산해 `None` 인데,
백필이 다시 돌리면 `0` 이 되어 «인용을 다 틀렸다» 가 없던 데서 생긴다).
**질의 재작성은 2026-08-27 에 생겼다**(`rag_pipeline/retrieval/rewrite.py`) — 아래 계약 절 참고.
**리랭커는 2026-08-25 에 생겼다**(`rag_pipeline/retrieval/reranker/`) — `RERANK_MODEL` 이
비면 `get_reranker()` 가 `None` 을 돌려 꺼져 있고, **그 빈 기본값도 미완성이 아니라
결정이다**(켜는 순간 질의당 LLM 호출이 1→2 다). **품질은 2026-08-25 부터 쟀다** — 2×2 비교표는
`.claude/wiki/verify_log/2026-08-25-리랭커-2x2-무료칸.md` 와 `2026-08-26-리랭커-유료칸.md` 에 있고,
사람 세트·cohere·GPU 축까지 이어졌다. **답변 캐시는 2026-08-24 에 생겼고 2026-08-26 에 갈래가 둘이 됐다**(`rag_pipeline/answer_cache/`) — `ANSWER_CACHE_MODE` 가 `semantic`(벡터 유사도)과 `exact`(정규화한 문자열)를 고른다. **꺼짐 조건이 갈래마다 다르다**: `semantic` 은 `ANSWER_CACHE_THRESHOLD` 가 비면 `get_answer_cache()` 가 `None` 을 돌려 꺼지고, **`exact` 는 그 값을 아예 안 봐 모드 한 줄로 켜진다**(완전 일치에는 정할 임계값이 없다). 저장소 커밋본은 `semantic` + 빈 임계값이라 여전히 꺼진 채이고, **그 빈 기본값이 미완성이 아니라 결정이다**(근거는 `.claude/wiki/knowledge/ingestion/indexing.md` 4절 · `.claude/wiki/plan_log/시맨틱-캐시.md` 4~6회차 — 양성·음성 분포가 겹쳐 임계값을 *"못 정한다"* 로 닫았다). **`exact` 를 "임계값 1.0" 으로 흉내 내자는 지적을 하지 않는다** — 그 안은 기각됐다(임계값은 *같은 벡터인가*를 재는 값인데 완전 일치가 묻는 것은 *같은 문자열인가*이고, 부동소수라 보장도 없다). 마찬가지로 **`put()`·무효화·`clear()` 가 갈래를 안 타는 것도 결정이다** — `exact` 에서도 진짜 벡터를 저장해야 나중에 `semantic` 으로 돌렸을 때 오염된 캐시를 검색하지 않는다.

`ingest()` 에 아무 호출도 없다는 이유로 "그 단계가 아직 없다"고 판단하지 않는다.
splitter·embedding·indexing 은 구현됐지만 **일부러 그 자리에 안 끼웠다** — 전 구간 흐름은
`pipeline.py:index_document()` 에 있다 (`.claude/wiki/knowledge/ingestion/indexing.md` 3절).

그래서 이 파일에는 **기존 코드에서 확실히 도출되는 계약만** 적는다. 추측으로 만든 체크리스트로
리뷰하면 없는 규칙을 근거로 멀쩡한 설계를 지적하게 된다. 여기 없는 사항은 `contracts.md` 와
실제 코드로 판단하고, 판단이 안 서면 "확인 필요"에 넣는다.

- **"아직 없다"는 면제는 생긴 단계에 안 준다** — 하이브리드(2026-08-23) · 리랭커(2026-08-25) ·
  Evaluator(2026-08-26) · **질의 재작성(2026-08-27)** 은 전부 이 면제에서 빠진다.
  아래 리랭커 계약과 위 Evaluator 셋·재작성 다섯을 적용한다

- **프런트엔드 디렉터리가 없다** — 화면은 `backend/app/templates/` 에 서버 렌더로 산다.
  빈 `frontend/` 가 안 쓰인 채 남아 있다가 2026-08-31 에 지워졌다. 되살리자는 제안은
  «템플릿을 두 벌로 만들지 않는다» 를 뒤집는 것이므로 지적 대상이다
