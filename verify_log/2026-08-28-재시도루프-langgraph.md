# 재시도 루프(⚑Checkpoint A)와 그 그래프(Phase 11) 검증

- 검증일: 2026-08-28
- 대상: `task_log/2026-08-28-재시도루프-langgraph.md` (미커밋)
- 비용: **외부 API 호출 없음.** `-m integration` 안 돌림 · `EVALUATOR_MODEL`·`REWRITE_MODEL`
  을 안 켬 · qdrant 를 안 염 · 모델 가중치를 안 올림(가짜 도구·판정자·재작성기·생성기)

## 1. 확인하려 한 것

넷이고 전부 **"규칙이 도는가"** 다. 임계값을 정하는 검증이 아니다 — 이 변경은 제어 흐름
계층이라 정할 임계값이 없고, 유일한 수 `max_retries=3` 은 스펙이 이미 정했다.

1. 스펙 DoD 셋 — 충분이면 즉시 종료 / `retry_count == max_retries` 에서 **정확히** 멈춤 /
   종료 사유가 갈림
2. 안 멈추는 경우가 없는가 — 도구 실패 · 승급 불가 · 재작성기 없음 세 갈래
3. 그래프가 while 과 **같은 것**을 도는가 (Phase 11 DoD)
4. `langgraph` 가 없어도 기본 pytest 가 통과하는가 (「선택 갈래」가 실제로 성립하는가)

## 2. 가상 데이터

무엇을 고립시켰나: **가짜 넷으로 외부를 전부 끊고 제어 흐름만 남겼다.** 검색 도구는 호출된
질의를 기록하고, 재작성기는 검색이 돌 때마다 `last_rewritten` 을 갱신하며(실제로도 재작성은
`retrieve()` 안에서 돈다), 판정자는 정해둔 verdict 를 순서대로 낸다.

가짜는 `tests/agent/_loop_stubs.py` 에 **저장소로 들어갔다** — 두 테스트가 나눠 써야
동등성이 성립하기 때문이다(각자 지으면 서로 다른 가짜를 보면서도 「같다」가 나온다).
아래 둘은 스크래치에만 있어 여기 그대로 남긴다.

### 2-1. `langgraph` 가 없는 머신 흉내 (`sitecustomize.py`)

```python
import sys


class _BlockLanggraph:
    def find_spec(self, name, path=None, target=None):
        if name == "langgraph" or name.startswith("langgraph."):
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return None


sys.meta_path.insert(0, _BlockLanggraph())
```

### 2-2. LangGraph 전제 탐침 (pydantic 상태 · `dict()` 반환 · 상한 예외)

```python
from langgraph.graph import END, START, StateGraph
from rag_pipeline.schemas.agent import AgentState
from rag_pipeline.schemas.retrieval import RetrievedChunk
from rag_pipeline.agent.state import new_state, mark_searched

s = new_state("질문")
chunked = s.model_copy(update={"retrieved_chunks": [
    RetrievedChunk(chunk_id="c", document_id="d", text="t", metadata={})]})
print(type(dict(chunked)["retrieved_chunks"][0]))          # 신원이 유지되나

def node_a(state: AgentState) -> dict:
    return dict(mark_searched(state, state.current_query))

def node_b(state: AgentState) -> dict:
    return dict(state.model_copy(update={"final_answer": "답"}))

g = StateGraph(AgentState)
g.add_node("a", node_a); g.add_node("b", node_b)
g.add_edge(START, "a")
g.add_conditional_edges("a", lambda st: "b" if st.searched_query else END, {"b": "b", END: END})
g.add_edge("b", END)
out = g.compile().invoke(s, config={"recursion_limit": 9})
print(type(out))                                            # 반환 타입

def spin(state: AgentState) -> dict:
    return dict(state.model_copy(update={"retry_count": state.retry_count + 1}))
g2 = StateGraph(AgentState)
g2.add_node("s", spin); g2.add_edge(START, "s")
g2.add_conditional_edges("s", lambda st: "s", {"s": "s"})
try:
    g2.compile().invoke(s, config={"recursion_limit": 4})
except Exception as e:
    print(type(e).__module__ + "." + type(e).__name__)      # 상한 초과 예외
```

### 실측표

| 입력 | 기대 | 실측 | 판정 |
|---|---|---|---|
| 판정 `sufficient` 1회 | 검색 1회 · `retry_count=0` · `answered` | 같음 | 통과 |
| 판정 `insufficient` 반복 · `max_retries=0` | 검색 1회 · `retry_count=0` · `retries_exhausted` | 같음 | 통과 |
| 〃 `max_retries=1` | 검색 2회 · `retry_count=1` · `retries_exhausted` | 같음 | 통과 |
| 〃 `max_retries=3` | 검색 4회 · `retry_count=3` · `retries_exhausted` | 같음 | 통과 |
| 판정 `failed` | `retry_count=0` · `answered` (재시도 안 탐) | 같음 | 통과 |
| `last_rewritten is None` | 검색 1회에서 멈춤 · `rewrite_unavailable` | 같음 | 통과 |
| `last_rewritten == current_query` | 〃 | 같음 | 통과 |
| 재작성기 미주입 | 〃 | 같음 | 통과 |
| 도구가 예외를 던짐 | 검색 1회 · `searched_query` 는 **남음** · `rewrite_unavailable` | 같음 | 통과 |
| 근거 0건 · 재작성 불가 | `NO_EVIDENCE_ANSWER` 가 나감 | 같음 | 통과 |
| 재시도 3회 뒤 질의 | 검색만 재작성 질의 · 판정·생성은 **원 질의** | 같음 | 통과 |
| `step_budget(0/1/3)` | `3 / 5 / 9` | `3 / 5 / 9` | 통과 |
| Router 를 늘 `rewrite` 로 고정 | `RuntimeError` (상한 초과) | 같음 | 통과 |
| `step_for("web_search")` | `RuntimeError` | 같음 | 통과 |
| **그래프 상태 대조** 3시나리오 | `model_dump()` 가 같음(`next_action`·timestamp 제외) | 같음 | 통과 |
| **그래프 검색 질의 대조** 3시나리오 | 질의 목록이 같음 | 같음 | 통과 |
| **그래프 정지점 대조** `max_retries=0/1/3` | `retry_count`·`stop_reason`·검색 횟수가 같음 | 같음 | 통과 |
| 그래프 진입을 `generate` 로 갈아끼움 | 검색을 안 거치고 생성 | 같음 | 통과 |
| 그래프가 안 끝남 | `GraphRecursionError` 가 아니라 `RuntimeError` | 같음 | 통과 |
| `run_agent_graph()` 반환 | `AgentState` (dict 아님) | 같음 | 통과 |

탐침(2-2) 결과 셋. **전부 그래프 설계의 전제였다.**

| 물음 | 실측 |
|---|---|
| `dict(model)` 이 중첩 객체 신원을 지키나 | `rag_pipeline.schemas.retrieval.RetrievedChunk` — 지킨다 |
| `StateGraph.invoke()` 반환 타입 | `dict` — 그래서 `model_validate()` 로 되돌린다 |
| 상한 초과 예외 | `langgraph.errors.GraphRecursionError` |

### 의존성 해결 (`uv pip install --dry-run langgraph`)

| 항목 | 실측 |
|---|---|
| 설치되는 패키지 | 17개 (`langgraph==1.2.11` · `langchain-core==1.6.0` · `langgraph-checkpoint` · `langgraph-sdk` · `langgraph-prebuilt` · `langsmith` · `orjson` · `ormsgpack` · `tenacity` · `xxhash` · `zstandard` 등) |
| **내려가는 패키지** | `websockets` 17.0.1 → **16.1.1** (`langgraph-sdk` 가 `websockets<17,>=14`) |
| `torch` · `pydantic` · `openai` | **안 움직인다** |
| 저장소의 WebSocket 사용처 | `grep -rn "websockets\|WebSocket" backend/ rag_pipeline/` → **0건** |

## 3. 실제 데이터

**없음.** 이 변경은 파싱·검색·생성을 한 줄도 안 건드리는 **제어 흐름 계층**이라
`tests/fixtures/` 실파일이 답할 질문이 없다. 실데이터로 재야 하는 것(*재시도가 답을 낫게
하나*)은 7절에 있고 이번 범위 밖이다.

## 4. 테스트 스위트

- ruff   : `All checks passed!`
- pytest : `1421 passed, 8 skipped, 7 deselected in 87.57s`
- `tests/agent` : **39 → 77 passed** (세션 시작 시 39)
- `tests/agent` (`langgraph` 를 meta_path 로 막음) : **64 passed, 1 skipped**
- skip 사유 (전체 8건)

```
[4] tests/evaluation/test_eval_config.py:253  로컬 갈래가 아니면 장치를 안 쓴다
[1] tests/evaluation/test_eval_config.py:270  모델이 없으면 구현체를 안 고른다
[1] tests/evaluation/test_eval_config.py:342  모델이 없으면 재작성기를 안 만든다
[2] tests/ingestion/splitter/test_splitter_real.py:151  해당 문서에 쪼개진 표가 없음
```

`langgraph` 를 막았을 때의 skip 1건은
`tests/agent/test_agent_graph.py:31 langgraph 는 선택 갈래다 (requirements.txt 참고)` 다.

## 5. 판정

| 항목 | 판정 | 근거 |
|---|---|---|
| DoD 1~3 (즉시 종료 · 정확한 정지 · 사유 구분) | **의도한 변화** | 위 실측표 앞 8행 |
| Phase 11 DoD (동일 재현) | **의도한 변화** | 상태 대조 3 + 질의 대조 3 + 정지점 대조 3 = **9/9** |
| 「선택 갈래」 성립 | **의도한 변화** | langgraph 없이 64 passed · 1 skipped |
| 전체 스위트 | **회귀 없음** | 1421 passed · 0 failed. 26회차가 적어 둔 기존 실패(`test_one_query_writes_one_line`)도 **그 사이 커밋에서 고쳐져** 지금은 통과한다 |
| `websockets` 다운그레이드 | **감수** | 저장소 사용처 0건 · `uvicorn[standard]` 하한이 그보다 낮다. **회귀로 안 봤지만 재본 것은 import 뿐이다** |

## 6. 재현

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest -q -rs
.\.venv\Scripts\python.exe -m pytest tests/agent -q
```

`langgraph` 없는 머신 흉내(2-1 을 `<scratch>/nolg/sitecustomize.py` 로 저장한 뒤):

```powershell
$env:PYTHONPATH = "<scratch>\nolg"; .\.venv\Scripts\python.exe -m pytest tests/agent -q -rs
```

의존성 확인:

```powershell
uv pip install --dry-run langgraph
```

## 7. 재지 못한 것

- **재시도가 답을 낫게 하는가** — 답변 품질을 재는 자[尺]가 없다(골든 답변이 없다).
  이 검증은 *제어 흐름이 맞나* 까지만 답한다
- **실제 코퍼스에서 승급된 질의가 정말 다른가** — `EVALUATOR_MODEL`(유료)을 켜야 하고
  `.qdrant` 배타 잠금 때문에 서버를 내려야 한다. 안 돌렸다
- **`websockets` 16.1.1 에서 uvicorn 이 실제로 뜨는지** — import 만 확인했다.
  서버를 안 띄웠다(다른 세션이 같은 트리를 쓰고 있어 포트·잠금을 안 건드렸다)
- **`langgraph` 의 체크포인터·스트리밍** — 이번에 안 썼다
