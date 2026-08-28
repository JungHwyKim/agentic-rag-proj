# 재시도 루프를 짓고 같은 핸들러를 그래프로 한 번 더 돌렸다 — 「승급」이 답이었다

- 작업일: 2026-08-28
- 검증 방법: 오프라인 단위 테스트(가짜 도구·판정자·재작성기·생성기) + `langgraph` 를
  meta_path 로 막은 대조 실행 + 전체 pytest·ruff. **그리고 실제 코퍼스·실제 모델로 질의 3건**
  (사용자 승인 — OpenAI 재작성 5·판정 4·생성 6회, Cohere 리랭킹 5회). `-m integration` 은
  안 돌렸고 `EVALUATOR_MODEL` 은 **환경변수로만** 켰다(저장소 스위치 안 건드림)

## 1. 무엇을 바꿨나

- `rag_pipeline/agent/loop.py` **(신규)**
  ⚑Checkpoint A 본체. Router 가 고른 행동을 핸들러 셋에 넘기는 while 루프.
- `rag_pipeline/agent/graph.py` **(신규)**
  Phase 11. **로직이 하나도 없다** — 같은 핸들러를 노드로, 같은 Router 를 분기로 배선만 한다.
- `rag_pipeline/schemas/agent.py`
  `StopReason` · `searched_query` · `stop_reason` 이 늘었다. 둘 다 **루프가 없으면 필요 없던 칸**이다.
- `rag_pipeline/agent/router.py`
  규칙 3 의 축이 «도구를 불렀나» → «이 질의로 찾아봤나» 로 바뀌고, 규칙 2.5(`stop_reason`)가 늘었다.
- `rag_pipeline/agent/state.py`
  `mark_searched()` · `with_stop_reason()` 이 늘고, `start_retry()` 가 낡은 판정을 지운다.
- `rag_pipeline/agent/__init__.py`
  *"루프를 돌리지 않는다"* 문단을 갈고 `run_agent_loop` 를 내보낸다. **`graph.py` 는 일부러 안 import 한다.**
- `requirements.txt`
  `langgraph` 를 **선택 갈래**로 추가.
- `tests/agent/_loop_stubs.py` **(신규)** · `test_retry_loop.py` **(신규)** ·
  `test_agent_graph.py` **(신규)** · `test_agent_router.py` · `test_agent_state.py`

## 2. 설계 결정

### 루프의 `rewrite` 는 재작성이 아니라 **승급**이다

26회차가 남긴 질문의 답이다. 이 저장소의 재작성기는 `(원질의, verdict) → 새 질의` 가 아니라
`retrieve()` **안**에서 도는 PRF 라, 직전 검색이 이미 쓴 질의가 `last_rewritten` 에 있다.
루프는 그것을 `current_query` 로 올리기만 한다.

**버린 안 하나 — 스펙 원안대로 루프가 재작성기를 직접 부르는 것.** 모양은 스펙의
`Retriever→Evaluator→Rewriter→Retriever` 와 글자까지 같다. 그런데 그러려면 검색 도구에
재작성기를 안 넘겨야 하고(안 그러면 한 회차에 재작성이 두 번 나간다), 그러면 **첫 검색이
PRF·fuse 를 안 탄다.** 23회차 실측에서 `fuse` 는 LLM 60건에서 RRF 를 여덟 지표 전승했다 —
루프 경로만 그것을 버리게 되고, 그때 루프가 재는 검색은 **실사용 경로가 아니다.**

**버린 안 둘 — 둘 다 하기**(첫 검색은 PRF, 재시도만 루프가 재작성). fuse 를 지키지만 재작성
호출이 두 경로에서 나가 캐시 키가 갈린다(1차 wide pool vs 최종 top_k). 그러면 *어느 재작성이
이 순위를 만들었나* 가 회차마다 달라진다.

고른 안의 이득 셋: **추가 LLM 호출 0**(재작성은 원래 `retrieve()` 당 한 번) · `retrieve()`
무수정 · 루프의 검색 경로가 `answer_question()`·`run_eval` 과 **글자 그대로 같다**.
진행도 보장된다 — 승급된 질의로 다시 찾으면 1차 pool 이 달라져 다음 PRF 의 입력
`(질의, pool)` 도 달라진다. 실측으로 재시도 3회에서 검색 질의 4개가 전부 서로 다르다.

### 종료 사유를 `retry_count` 로 유추하지 않는다

`AgentState.stop_reason` 을 필드로 뒀다. 카운터로는 셋이 안 갈린다 — 마지막 재시도에서
*충분* 이 나온 경우가 소진으로 읽히고, *승급할 질의가 없어 접은 것* 과 *소진* 이 같은 수를 낸다.

그래서 판정으로 가른다: ***더 찾고 싶은데(불충분이거나 0건) 재시도가 없다*** 일 때만
`retries_exhausted` 다. `Answer.verdict`·`Answer.rewrite` 를 필드로 실은 것과 같은 잣대이고,
**필드여야 그래프 경로에서도 같은 자리에서 읽힌다**(그래프 노드는 상태를 주고받는다).

**버린 안 — `LoopResult(state, reason)` 로 반환을 넓히기.** 스키마는 안 늘지만 그래프 노드가
그 값을 실을 자리가 없어 Phase 11 에서 필드를 다시 만들게 된다. 사유의 두 번째 진실이 생긴다.

### LangGraph 미결을 「쓴다」로 닫았고, 미룬 대가는 안 쳤다

`DESIGN.md` 가 *"`AgentState` 를 pydantic 으로 굳힌 뒤 옮기면 reducer·`Annotated` 때문에
다시 쓰게 되므로 **Phase 7 전에** 정해야 한다"* 로 미뤄 뒀는데, **그 시점은 이미 지났다**
(26회차가 Phase 7 을 커밋했다). 그래서 물음이 *언제 정하나* 에서 **지났으니 대가를 치르나**
로 바뀌었고, 답은 **거의 안 친다** 였다.

- reducer 가 필요한 조건은 **한 칸에 둘 이상이 동시에 쓰는 것**(병렬 분기)인데 이 그래프는
  한 번에 노드 하나다
- `state.py` 가 이미 *"결과는 덧붙이는 것이 아니라 갈아끼운다"* 로 **전량 교체**를 못 박아
  LangGraph 의 기본(마지막 쓰기가 이긴다)과 뜻이 같다

**읽은 것이라 먼저 쟀다.** 탐침으로 셋을 확인했다 — pydantic 모델이 상태 스키마로 그대로
되고, `dict(model)` 이 `RetrievedChunk` 신원을 지키며(`model_dump()` 는 뭉갠다),
상한 초과가 `GraphRecursionError` 다.

**버린 안 — 그래프 전용 TypedDict.** LangGraph 관례에 가깝고 `Annotated` 를 붙이기 쉽다.
그런데 **상태가 두 벌**이 되어 *동일 재현* 을 필드 대조로 못 잰다 — 26회차의
*"살아 있는 타입이 초안을 이긴다"* 가 여기서 그대로 문다.

### `langgraph` 를 필수가 아니라 선택 갈래로 뒀다

`requirements.txt` 에 넣되 `agent/__init__.py` 가 `graph.py` 를 **일부러 import 하지 않는다.**
기본 경로인 `run_agent_loop()` 은 langgraph 를 아예 안 건드린다.

**버린 안 — 필수 의존성으로 두고 그래프를 유일한 실행 경로로.** 코드에 분기가 안 생긴다.
대신 안 깔린 머신에서 `from rag_pipeline.agent import ...` 가 통째로 죽어 **`tests/agent` 가
전멸**한다. 이 저장소가 *"기본 pytest 는 키 없이 전부 통과해야 한다"* 로 여러 번 값을 한
자리이고, `cohere` 가 밟은 길과 같다. 실측으로 langgraph 를 막아도 **64 passed · 1 skipped** 다.

### Router 규칙 3 의 축을 바꿨다 — 26회차가 잠근 함정은 그대로 지켜진다

`if not state.tool_call_log` → `if state.searched_query != state.current_query`.

바꾼 이유는 하나다: **재작성이 질의를 갈아끼워도 로그는 이미 차 있어 재검색이 안 걸린다.**
그러면 루프가 `rewrite` 만 되풀이하며 검색을 영영 안 한다.

26회차가 잠근 함정 셋 중 *0건에서 `retrieve` 를 되고르지 않는다* 는 새 축에서도 성립한다 —
질의가 안 바뀌면 안 고른다. 오히려 *검색했는데 0건* 과 *아직 안 했다* 를 더 정확히 가른다.
**`ToolCallLog.input_summary` 로 판정하는 안은 기각했다** — 120자로 잘린 요약이라 앞부분이
같은 긴 질의 둘이 **에러 없이** 같아진다(`test_long_queries_that_share_a_prefix_are_not_confused`).

### `stop_reason` 이 정해지면 Router 는 생성으로만 보낸다 (규칙 2.5)

핸들러가 *더 갈 수 없다* 고 판정한 자리(`rewrite_unavailable`)의 상태는 **판정이 여전히
*불충분* 이고 재시도도 남아 있다.** 이 규칙이 없으면 Router 가 `rewrite` 를 되고르고 핸들러가
같은 판정을 내리며 제자리를 돈다.

경계는 지켰다 — **Router 는 이 칸을 읽기만 하고 쓰지 않는다.** 쓰는 것은 핸들러다.
*무엇을 할 수 있나* 는 핸들러가 알고 *다음에 무엇을 하나* 는 Router 가 안다.

### 안 멈추는 것을 상수가 아니라 구조로 막았다

바퀴마다 상태가 반드시 전진한다 — `retrieve` 는 `searched_query` 를(**도구가 실패해도**),
`rewrite` 는 `retry_count` 또는 `stop_reason` 을, `generate` 는 `final_answer` 를 바꾼다.

그 위의 `step_budget(max_retries) = 2 * max_retries + 3` 은 **여유가 아니라 정확한 상한**이다
(`retrieve` 1 + (`rewrite`+`retrieve`)×`max_retries` + `generate` 1 + `end` 1). 넘으면
`RuntimeError` 다 — 조용히 `stop_reason` 으로 접으면 *Router 와 핸들러가 어긋났다* 는 버그가
수치로만 드러난다. 그래프도 같은 수식을 `recursion_limit` 에 넘긴다.

## 3. 지표

전문은 [verify_log/2026-08-28-재시도루프-langgraph.md](../verify_log/2026-08-28-재시도루프-langgraph.md) 에 있다.

- pytest `1421 passed, 8 skipped` / `tests/agent` **39 → 77** / ruff `All checks passed!`
- 그래프 동일 재현 **9/9** (상태 대조 3 · 검색 질의 대조 3 · 정지점 대조 3)
- `langgraph` 를 막았을 때 `tests/agent` **64 passed, 1 skipped**
- E2E : **했다** — 질의 3건이 `answered` 2 · `retries_exhausted` 1 로 갈렸고 경고·폴백 0건.
  실물에서도 그래프가 제어 흐름 7필드를 3/3 재현했다(갈린 `final_answer` 는 생성 비결정성)

## 4. 실패 분석

### 계획이 표시를 다는 자리를 틀렸다 — 도구가 실패하면 루프가 영영 안 멈춘다

계획은 *"`record_tool_call()` 이 `searched_query` 도 함께 싣는다"* 였다. 그 함수는 **결과
모양**(청크가 왔나)으로 검색 도구를 판별하므로, **도구가 실패한 회차에는 표시가 안 남는다.**
그러면 Router 가 `searched_query != current_query` 를 보고 같은 검색을 되고르고,
`max_retries` 와 무관하게 상한까지 돌다 `RuntimeError` 로 끝난다 — 일시적 도구 실패가
예외가 되는 것은 폴백 규약과도 어긋난다.

축을 *찾았나* 에서 ***이 질의로 시도했나*** 로 바꾸고, 표시를 검색 단계(`_step_retrieve`)로
옮겨 `mark_searched()` 로 뺐다. **도구 종류로 판정하는 안은 기각했다** — `state.py` 가
`RetrieverTool` 을 알게 되고, 가짜 도구로 루프를 재는 테스트가 통째로 막힌다.

### `pytest.importorskip` 은 *미설치* 와 *깨진 설치* 를 가른다

「선택 갈래」를 확인하려고 `ImportError` 를 던지는 가짜 `langgraph` 패키지를 PYTHONPATH 에
놓았더니, skip 이 아니라 **collection error** 가 났다. 안 깔린 모듈이 내는 것은
`ModuleNotFoundError` 이고 pytest 가 그 둘을 일부러 가른다(설치가 깨진 것을 조용히 넘기면 안
되므로 옳은 동작이다). meta_path 파인더로 `ModuleNotFoundError` 를 던지게 바꿔서야 제대로
흉내가 났다. **틀린 것은 코드가 아니라 내 흉내였다.**

### 그래프 진입 테스트를 잘못 짰다

`decide_next_action` 을 늘 `"generate"` 로 고정했더니 생성 뒤에도 생성으로 가 상한에 걸렸다.
그것은 정상 동작(그리고 다른 테스트가 따로 재는 것)이고, 진입만 갈아끼우려면
*답이 생기면 끝낸다* 까지 흉내 내야 했다.

### 편집 스크립트가 CRLF 파일에서 빗나갔다

`plan_log/검색-생성.md` 는 CRLF 인데 `.py` 는 LF 다. 앵커에 `\n` 을 쓴 다중 행 치환이
`count=0` 으로 죽었다(한 줄짜리 앵커는 통과해서 더 헷갈렸다). 도우미가 파일의 줄끝을 감지해
앵커·치환문을 맞추도록 고쳤다. 저장소 파일은 전부 원래 줄끝 그대로다(확인함).

## 5. 회귀 테스트

| 4절 항목 | 막은 테스트 |
|---|---|
| 도구 실패 시 무한 재검색 | `tests/agent/test_retry_loop.py::test_a_failing_tool_does_not_spin` |
| 승급할 질의 없음에서 제자리 돌기 | `test_rewrite_without_a_new_query_folds` · `test_rewrite_returning_the_same_query_folds` · `test_without_a_rewriter_it_folds_instead_of_spinning` |
| 재작성 뒤 재검색이 안 걸림 | `tests/agent/test_agent_router.py::test_promoted_query_retrieves_again` |
| 요약 문자열로 질의를 판정 | `test_long_queries_that_share_a_prefix_are_not_confused` |
| 낡은 판정이 남음 | `tests/agent/test_agent_state.py::test_start_retry_clears_stale_verdict` |
| 판단·실행 어긋남 | `test_a_router_that_never_finishes_raises` · `test_unknown_action_raises` |
| 그래프가 while 과 갈림 | `tests/agent/test_agent_graph.py` 전체 (9/9 대조) |
| 「선택 갈래」가 깨짐 | `test_agent_graph.py` 의 `importorskip` — **다만 자동으로는 안 돈다** (아래) |

**못 막은 것 하나** — *`langgraph` 가 없을 때도 통과하는가* 는 이번에 손으로 쟀다.
CI 에 langgraph 없는 실행이 없으면 이 갈래는 **아무도 다시 안 잰다.** 7절에 남긴다.

## 6. 재현

```powershell
.\.venv\Scripts\python.exe -m pytest tests/agent -q
.\.venv\Scripts\python.exe -m pytest -q -rs
.\.venv\Scripts\python.exe -m ruff check .
```

## 7. 남은 것

- **`langgraph` 없는 실행이 자동으로 안 돈다.** 손으로 meta_path 를 막아야 확인된다.
  `tests/hooks` 처럼 하네스로 강제할지는 안 정했다
- **근거가 안 바뀌는 재시도가 실재한다** — 실물 실행에서 재시도 회차의 근거 5개가 **완전히
  같은 경우**가 나왔다(재작성이 어절 하나만 바꾼 문장을 냈다). **색인을 309 → 721청크로
  넓힌 뒤에도 재현됐고**, 두 실행 합쳐 재시도 회차 6개 중 2개다. 판정자를 `original_query` 로 부르므로 근거가 같으면
  verdict 도 같아 **그 회차의 판정은 중복**이다(캐시 히트라 이미 무료다). **다만 그 회차의
  검색까지 무의미한 것은 아니다** — 다음 회차의 재작성이 또 다른 질의를 낼 수 있고 그것이
  무엇을 찾을지는 안 쟀다. 그래서 접는 규칙은 증명이 아니라 추정이다. 접는 규칙을 넣을지는 `plan_log/검색-생성.md` 미결로 넘겼다
- **재시도가 답을 낫게 하는가는 여전히 못 잰다** — 골든 답변이 없고, 게다가 이번 실행에서
  재시도가 붙은 질의는 **답이 코퍼스에 없는** 경우 하나뿐이라 이득 표본이 0이다
- ~~색인에 문서가 셋뿐이다~~ → **사용자가 시행령·학교기록물을 PDF·HWPX 양판으로 색인했다**
  (문서 7종 · 721청크). 3절에서 실패했던 질의가 화면에서 *충분* 판정으로 답한다 —
  **그 실패의 원인이 루프가 아니라 색인이었던 것이 확정됐다**(`verify_log` 3-B절).
  AI가이드는 여전히 없다
- **`websockets` 16.1.1 에서 uvicorn 이 실제로 뜨는지 안 봤다.** import 만 확인했다
- **루프를 화면·평가에 안 이었다** — 네 번째 진입점으로만 있다.
  붙이려면 답변 캐시를 루프 어디에 둘지부터 정해야 한다(`plan_log/검색-생성.md` 27회차 미결)
