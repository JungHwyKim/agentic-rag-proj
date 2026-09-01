# 재시도 루프(⚑Checkpoint A)와 그 그래프(Phase 11) 검증

- 검증일: 2026-08-28
- 대상: `task_log/2026-08-28-재시도루프-langgraph.md` (미커밋)
- 비용: 단위 테스트는 **외부 API 호출 없음**. **3·4절의 실행은 유료다**(사용자 승인) —
  OpenAI 재작성 11회(36,660토큰) · 판정 9회(24,975토큰) · 생성 8회, Cohere 리랭킹 11회.
  `-m integration` 은 안 돌렸다

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

## 3. 실제 데이터 — 실제 코퍼스·실제 모델로 돌렸다 (유료)

`tests/fixtures/` 실파일 파싱이 아니라 **색인된 코퍼스에 실제 질의를 넣었다.** 이 변경은
파싱을 한 줄도 안 건드리므로 잴 것이 파싱이 아니라 **루프가 실물에서 도는가**다.

`EVALUATOR_MODEL` 은 `.env` 에 없어 **환경변수로만** 켰다(저장소 스위치는 안 건드렸다).
나머지는 `.env` 그대로다 — `EMBEDDING_DEVICE=cuda` · `RERANK_PROVIDER=cohere`(`rerank-v3.5`) ·
`REWRITE_MODEL=gpt-4.1-mini` · `REWRITE_MODE=fuse` · 생성 `gpt-4.1-mini` · `top_k=5`.
uvicorn 은 안 떠 있었다(포트 8000 리슨 0 · qdrant 배타 잠금 자유).

### 색인 내용 (이 실행의 전제)

| document_id | 청크 |
|---|---:|
| `260630_(EU TRQ) OJ_L_202601457_EN_TXT.pdf` | 181 |
| `2026+중등+기초학력+보장+주친+계획.pdf` | 103 |
| `2025년도전북과학교육우수학교표창계획.hwpx` | 25 |
| **합계** | **309** |

**시행령·학교기록물·AI가이드는 이 색인에 없다.** 그래서 첫 질의는 *답이 코퍼스에 없는*
경우가 됐고, 그것이 오히려 `retries_exhausted` 경로의 실물 표본이 됐다.

### 질의 셋 (`max_retries=2`)

| 질의 | `stop_reason` | `retry_count` | 검색 | verdict | 판정 |
|---|---|---:|---:|---|---|
| 데이터세트를 이관할 때 기록관이 확인해야 하는 사항은? | `retries_exhausted` | 2 | 3 | `insufficient` | **코퍼스에 답이 없다** — 근거를 못 찾고 *"문서에서 찾지 못했습니다"* 로 나갔다 |
| 2026 중등 기초학력 보장 추진 계획의 추진 과제는? | `answered` | 0 | 1 | `sufficient` | 두드림학교·선도학교·교과보충·학습지원튜터 넷을 출처와 함께 답했다 |
| 전북과학교육 우수학교 표창 대상과 신청 방법은? | `answered` | 0 | 1 | `sufficient` | 대상·제출 서류·기한을 `[1]`·`[2]` 인용과 함께 답했다 |

**경고 0건 · Cohere 429 폴백 0건**(`rerank_calls` 가 검색 횟수와 일치). 지연은 첫 질의
71.18초인데 **그 대부분이 KURE 가중치 첫 적재**이고, 적재 뒤 답변 가능한 질의는 10.62초다.

### 그래프 대조 — 제어 흐름은 같고 **생성 문장만 갈린다**

같은 질의를 while 과 그래프에 넣고 `model_dump()` 를 대조했다.

| 질의 | 갈린 필드 |
|---|---|
| 1 (코퍼스에 답 없음) | **없음** (완전 일치) |
| 2 | `final_answer` · `conversation_history` |
| 3 | `final_answer` · `conversation_history` |

`stop_reason`·`retry_count`·`current_query`·`searched_query`·`tool_call_log`·
`retrieved_chunks`·`evaluator_verdict` 는 **셋 다 전부 같다.** 갈린 둘은 같은 값의 다른
표현이라 **생성 모델의 비결정성**이다(`temperature` 를 안 고정한 생성 단계). 1번이 일치한
것은 근거가 없어 두 번 다 같은 거절 문장이 나왔기 때문이다.

**이것이 2절의 가짜 생성기가 약점이 아니라 전제인 이유다** — 결정적 생성기가 아니면
*동일 재현* 을 필드 대조로 못 잰다.

### 새로 잰 것 — **승급은 질의만 바꾸지 근거를 바꾼다고 보장하지 않는다**

첫 질의를 다시 돌리며 회차마다 청크 id 를 찍었다(캐시가 덥혀져 있어 **외부 호출 0**).

```
[0] 데이터세트를 이관할 때 기록관이 확인해야 하는 사항은?
    0027 · 0058 · 0026 · 0084 · 0100
[1] 데이터세트 이관 시 기록관이 확인해야 하는 사항에 관한 행정 지침 및 관리 기준은 무엇입니까?
    0027 · 0049 · 0025 · 0026 · 0048          <= 근거 바뀜
[2] 데이터세트 이관 시 기록관이 확인해야 하는 사항에 관한 행정 지침 및 관리 기준은 무엇인지 알려주시기 바랍니다.
    0027 · 0049 · 0025 · 0026 · 0048          <= 앞 회차와 **완전히 동일**
```

(전부 `2026+중등+기초학력+보장+주친+계획.pdf` 다. 앞 숫자는 청크 번호)

2회차 재작성이 낸 것이 **어투만 바꾼 문장**(`무엇입니까?` → `무엇인지 알려주시기 바랍니다.`)
이라 검색 결과가 그대로였다. **원리적으로 중복인 것은 그 회차의 판정 하나다** — 판정자는
`original_query` 로 부르므로 근거가 같으면 verdict 도 같다(실측으로 그 회차가 판정 캐시
히트였다). 오늘 루프가 접는 조건은 *다시 쓴 질의가 없거나 지금 질의와 같을 때* 뿐이라
이 경우를 못 잡는다. 미결로 넘긴다(7절).

## 3-B. 색인이 넓어진 뒤 다시 쟀다 (같은 날 2회차)

사용자가 **시행령 개정안 안내서**와 **학교기록물 관리 지침**을 PDF·HWPX 양판으로 색인했다.
`309 → 721청크 · 문서 3 → 7종`. 3절의 두 결론이 그대로인지 다시 봤다.

### 색인이 원인이었던 것이 확정됐다

3절에서 `retries_exhausted` 로 끝났던 질의를 **화면 경로**(`POST /api/v1/query`)로 다시
보냈다. 셸을 안 거치고 `urllib` 로 보내며 **응답이 되돌려준 질문 문구를 대조**했다(cp949 함정).

| 질의 | 왕복 문구 | 판정 배지 | 재작성 줄 | 답변 |
|---|---|---|---|---|
| 데이터세트를 이관할 때 기록관이 확인해야 하는 사항은? | 일치 | **없음**(= *충분*) | 렌더링 · `fuse` 문구 | 관리대상시스템·관리기준표·국가기록원 협의를 `[1][3][5]` 로 인용 |
| 이 지침에서 정한 직원 연차휴가 일수는? | 일치 | **`verdict-insufficient`** + 사유가 `title` 에 | 렌더링 | *"문서에서 찾지 못했습니다."* |

**즉 3절의 첫 질의가 실패한 것은 루프가 아니라 색인이었다.** 그리고 이것이
`EVALUATOR_MODEL` 을 `.env` 에 상시로 켠 뒤의 첫 화면 확인이기도 하다 — 배지가 실제로 뜬다.

### 근거가 안 바뀌는 재시도는 **넓어진 코퍼스에서도 난다**

생성은 안 부르고(제어 흐름만 본다) 회차별 청크 id 를 찍었다. `max_retries=2`.

| 질의 | `stop_reason` | 재시도 회차 | 근거 완전 동일 |
|---|---|---:|---:|
| 이 지침에서 정한 직원 연차휴가 일수는? | `retries_exhausted` | 2 | 0 (겹침 0/5 · 2/5) |
| 행정정보 데이터세트 관리기준표는 언제까지 작성해야 하나? | `retries_exhausted` | 2 | **1** |
| 학교기록물 폐기 심의는 누가 어떻게 하나? | `answered` | 0 | — |

동일하게 나온 회차의 재작성은 **조사 하나만 바뀌었다** —
`…작성 완료 기한은 언제인지 알려주시기 바랍니다.` → `…작성 완료 기한이 언제인지 알려주시기 바랍니다.`

**두 실행을 합치면 재시도 회차 6개 중 2개(33%)가 근거가 완전히 같다.** 표본이 작아 비율을
값으로 인용하면 안 되지만, *드물게 나는 사고* 가 아니라 **재작성이 어절 하나만 바꿀 때마다
나는 것**이라는 갈래는 확실하다.

**여기서 「그 회차는 원리적으로 무의미하다」로 넘어가면 안 된다** — 원리적으로 중복인 것은
**판정 하나**이고 그것은 캐시로 이미 무료다. **그 회차의 검색까지 무의미한 것은 아니다**:
다음 회차의 재작성은 `(그 질의, 그 pool)` 을 새 키로 부르므로 또 다른 질의가 나올 수 있고,
그것이 무엇을 찾을지는 안 쟀다. 그래서 접는 규칙은 **증명이 아니라 추정**이다
(`plan_log/검색-생성.md` 「다음 승인 단위 A」).

**그리고 검색 결과가 옳은 문서로 갔다** — 연차휴가·폐기 심의는 `학교기록물`, 관리기준표는
`시행령` 에서만 뽑혔다. 3절에서 엉뚱한 문서가 온 것도 색인 탓이었다.

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
| 실물 실행(3절) | **의도한 변화** | 세 질의가 `answered` 2 · `retries_exhausted` 1 로 갈렸고 경고·폴백 0건 |
| 실물에서의 그래프 동등성 | **의도한 변화** | 제어 흐름 7필드가 3/3 일치. 갈린 `final_answer` 는 생성 비결정성이다 |
| 근거가 안 바뀌는 재시도 | **확인 필요** | 실재하고 원리적으로 무의미하다. **색인을 2.3배로 넓힌 뒤에도 난다**(3-B). 접는 규칙을 넣을지는 설계 결정이라 미결로 남겼다 |
| 판정 배지 실사용 | **의도한 변화** | `EVALUATOR_MODEL` 을 `.env` 에 상시로 켠 뒤 화면에서 *불충분* 배지와 사유 `title` 이 실제로 렌더된다(3-B) |
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

실물 실행(유료). uvicorn 을 먼저 내린다 — qdrant 로컬 파일 모드가 배타 잠금을 건다.

```powershell
$env:EVALUATOR_MODEL = "gpt-4.1-mini"; $env:PYTHONPATH = "."
.\.venv\Scripts\python.exe <scratch>un_loop_real.py "<질의1>" "<질의2>"
```

## 7. 재지 못한 것

- **재시도가 답을 낫게 하는가** — 답변 품질을 재는 자[尺]가 없다(골든 답변이 없다).
  이 검증은 *제어 흐름이 맞나* 까지만 답한다
- **근거가 안 바뀌는 재시도를 접을지** — 실측으로 실재한다(3·3-B절). 판정자가 `original_query`
  로 불리므로 근거가 같으면 verdict 도 같아 **그 회차는 원리적으로 무의미**하다.
  그런데 접는 규칙을 넣는 것은 설계 결정이라(`router.py` 의 *0건에서 되고르지 않는다* 와
  같은 자리) `plan_log/검색-생성.md` 미결로 넘겼다
- **재시도가 답을 낫게 하는가는 여전히 못 쟀다** — 이번 실행에서 재시도가 붙은 질의는
  **답이 코퍼스에 없는** 경우 하나뿐이라, 재시도가 이득을 내는 표본이 0이다
- **`websockets` 16.1.1 에서 uvicorn 이 실제로 뜨는지** — import 만 확인했다.
  서버를 안 띄웠다(다른 세션이 같은 트리를 쓰고 있어 포트·잠금을 안 건드렸다)
- **`langgraph` 의 체크포인터·스트리밍** — 이번에 안 썼다
