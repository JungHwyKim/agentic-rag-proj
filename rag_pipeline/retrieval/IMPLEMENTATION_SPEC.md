# Retrieval & Generation Pipeline — Implementation Spec (Phased Handoff)

> **이 문서는 외부에서 가져온 원안이다.** 확정된 설계는 `plan_log/검색-생성.md` 이고,
> 거기서 이 원안의 일부가 이미 기각됐다(평가 세트를 Phase 0 으로 올림 · tool 목록에서
> Vision·LlamaParse 제외). 어긋나면 그쪽이 맞다.

> 설계 근거는 `DESIGN.md`, 스키마 그림은 `SCHEMA.html` 참고. 이 문서는 코드 어시스턴트에게 phase 단위로 전달하기 위한 실행용 스펙이다.
>
> **2026-08-22 정정.** 저장소의 기존 계약과 대조해 고쳤다. 무엇을 왜 골랐는지는 `plan_log/검색-생성.md` 1회차.

## 사용법

- 매 세션마다 함께 전달: **Pre-Lock A (Agent State)**, **Pre-Lock B (Tool Interface)**, **Pre-Lock C (Tool 소속)**
- 그 다음 해당 세션에서 작업할 **Phase N 섹션 하나만** 추가
- "범위 밖" 항목은 그 phase에서 손대지 않음 (AGENTS.md 파일별 제한 규칙과 동일)
- 순서: **Phase 0** → 1 → 2 → 3 → 4 → 5 → **Checkpoint A** → 6 → 7 → 8 → 9 → 10 → 11
- **단계마다 사용자 승인을 받는다.** 한 phase 를 끝내면 멈추고 확인받은 뒤 다음으로 간다

---

## Pre-Lock A — Agent State Schema

```python
from pydantic import BaseModel
from typing import List, Optional, Literal, Any
from datetime import datetime

# rag_pipeline/schemas/retrieval.py 에 있다 (retrieval/schema.py 아님).
# 기존 SearchResult(schemas/vector.py)에 "어느 경로에서 왔나"를 얹은 것이다
class RetrievedChunk(BaseModel):
    chunk_id: str
    document_id: str
    text: str                      # `content` 아님 — 저장소 전체가 `text` 다
    metadata: dict = {}            # page·page_end·heading_path. 출처 인용의 유일한 근거
    dense_score: Optional[float] = None   # 그 경로의 후보가 아니었으면 None
    bm25_score: Optional[float] = None    # 0.0 으로 채우면 "점수 0"과 "후보 아님"이 안 갈린다
    fused_score: float = 0.0              # RRF 결과. 정렬 기준
    rerank_score: Optional[float] = None

class EvaluatorVerdict(BaseModel):
    is_sufficient: bool
    reasoning: str
    missing_aspects: Optional[List[str]] = None

class ToolCallLog(BaseModel):
    tool_name: str
    input_summary: str
    output_summary: str
    timestamp: datetime

class Message(BaseModel):
    role: Literal["user", "assistant", "tool"]
    content: str

class AgentState(BaseModel):
    conversation_history: List[Message] = []
    original_query: str
    current_query: str

    retrieved_chunks: List[RetrievedChunk] = []
    evaluator_verdict: Optional[EvaluatorVerdict] = None
    retry_count: int = 0
    max_retries: int = 3

    tool_call_log: List[ToolCallLog] = []

    final_context: Optional[str] = None
    final_answer: Optional[str] = None

    next_action: Optional[
        Literal["retrieve", "web_search", "vision", "llamaparse", "rewrite", "generate", "end"]
    ] = None
```

## Pre-Lock B — Tool Abstract Interface

```python
from abc import ABC, abstractmethod

class ToolInput(BaseModel):
    query: str
    metadata: dict = {}

class ToolOutput(BaseModel):
    success: bool
    data: Any
    error: Optional[str] = None

class BaseTool(ABC):
    name: str
    description: str

    @abstractmethod
    def run(self, input: ToolInput) -> ToolOutput:
        ...
```

## Pre-Lock C — Tool 소속

| Tool | 소속 | 개발 트랙 |
|---|---|---|
| Retriever (Hybrid) | Phase 2~5, Phase 6에서 wrapping | 메인 트랙 |
| Web Search | Phase 6 | 병행 가능 |
| ~~Vision~~ | **제외** | 질의 시점에 건넬 바이트가 없다 — 적재 시점 전처리다 |
| ~~LlamaParse~~ | **제외** | 같은 이유. 게다가 표를 HTML 로 돌려줘 기본값이 꺼져 있다 |

`blob` 은 splitter → records → chunk 경로에 한 번도 안 나온다(확인함). 근거와 그림은
`DESIGN.md` 2장과 `SCHEMA.html` 3절.

---

## Phase 0. 정답 질의 세트  ✅ 2026-08-22

- **의존성**: 없음 (splitter 까지 구현돼 있으면 된다)
- **범위**: `evaluation/` — 문서 그룹 정의, 채점 규칙, 세트(`qa_set.jsonl`), 러너, 형식 테스트
- **범위 밖**: retriever 구현, dense baseline 실측(과금이라 별도 승인)
- **핵심 결정**: 정답을 `chunk_id` 가 아니라 **`gold_spans`(정답 문자열)** 로 매긴다.
  chunk_id 로 매기면 (a) 청킹을 고치는 순간 세트가 무효인데 **청크 상한 재판정이 이 세트의
  목적**이라 자기 자신을 못 재고, (b) HWPX·PDF 판의 chunk_id 가 애초에 달라 포맷 비교가
  성립하지 않는다
- **DoD**: `python -m evaluation.run_eval --check` 통과 — 모든 span 이 그룹의 **모든 포맷에서,
  청크 하나 안에서** 발견된다. `tests/evaluation/` 이 이것을 지킨다

## Phase 1. Core Contract / Schema

- **의존성**: Phase 0
- **범위**: `RetrievedChunk` 를 `rag_pipeline/schemas/retrieval.py` 에 정의
  (`retrieval/schema.py` **아님** — 기존 `schemas/` 옆이 이 저장소의 자리다)
- **범위 밖**: 실제 retriever/tool 로직, LangGraph 노드,
  **`AgentState` 등 Pre-Lock A 의 나머지** — 소비자가 Phase 7 에 있고 LangGraph 여부가
  미결이라 지금 굳히면 두 번 쓰게 된다
- **DoD**: 모든 모델 import 가능, 이후 phase와 타입 100% 일치

## Phase 2. Retriever (Hybrid)

- **의존성**: Phase 1
- **계약**: `query: str, top_k: int -> List[RetrievedChunk]` (rerank_score는 None)
- **범위**: Dense 검색 + BM25 검색 + **RRF 병합**
  - Dense 는 **기존 qdrant** 다. **`ingestion/indexing/base.py` 의 `VectorStore` Protocol 만
    받는다** — `QdrantStore` 나 `qdrant_client` 를 직접 import 하면 안 된다
  - 질의 임베딩은 `Embedder.embed_query()` 이고 **색인과 같은 `EmbeddingConfig`** 여야 한다.
    다르면 컬렉션 이름이 갈려 검색이 0건이 아니라 예외로 죽는다
  - BM25 는 **저장소 밖 별도 인덱스**다. qdrant sparse 벡터로 얹으면 Protocol 을 고쳐야 하고
    저장소 중립이 깨진다
  - 병합은 **RRF**. Dot 점수와 BM25 점수는 단위가 달라 그대로 더하면 BM25 가 항상 이긴다
- **범위 밖**: Reranker 호출, tool 래핑
- **DoD**: `evaluation/run_eval.py` 로 `hit@10`·`recall@10`·`MRR` 측정. `use_bm25` 를 끄고 켜
  dense-only 와 hybrid 를 같은 세트에서 견준다
- **한국어 BM25 주의**: 토크나이저 없이 공백으로 자르면 조사 때문에 `시행령은` ≠ `시행령` 이다.
  형태소 토크나이저를 넣을지는 **비교 6번으로 판정한다** — 지금 고를 근거가 없다

## Phase 3. Reranker

- **의존성**: Phase 2
- **계약**: `query: str, chunks: List[RetrievedChunk] -> List[RetrievedChunk]` (rerank_score 채움)
- **범위**: `Reranker` Protocol + `get_reranker()` 팩토리 + **로컬 구현체**.
  `reranker_provider` 설정은 **상용 구현체가 실재할 때** 더한다(= 비교 3번을 돌릴 때).
  로컬을 먼저 두는 이유는 *기본 `pytest` 는 키 없이 전부 통과* 를 지키는 것이 그것뿐이라서다
- **범위 밖**: Evaluator 판단, retry 제어
- **DoD**: `nDCG@5`·`precision@3`·`recall@5` 로 유무를 견준다.
  **`recall@후보수` 로 재면 안 된다** — 리랭커는 집합을 안 바꾸고 순서만 바꿔 차이가 정의상 0 이다

## Phase 4. Relevance Evaluator

- **의존성**: Phase 3
- **계약**: `query: str, chunks: List[RetrievedChunk] -> EvaluatorVerdict`
- **범위 밖**: 재시도 루프 제어, rewrite
- **DoD**: `evaluation/qa_set.jsonl` 일부로 판정이 사람 판단과 합리적으로 일치
- **먼저 정할 것**: 실패 계약. Evaluator 가 죽었을 때 "충분"도 "불충분"도 조용히 나쁘다
  (`DESIGN.md` 6장)

## Phase 5. Query Rewriter

- **의존성**: Phase 4
- **계약**: `original_query: str, verdict: EvaluatorVerdict -> str`
- **범위 밖**: 재시도 횟수 카운팅
- **DoD**: 같은 verdict에도 이전과 다른 방향으로 변형된 query 산출

---

## ⚑ Checkpoint A — Retrieval Loop 통합 테스트

- **의존성**: Phase 2, 3, 4, 5
- **범위**: 그래프 없는 단순 스크립트(`checkpoint_a_retry_loop.py`)로 Retriever→Reranker→Evaluator→(불충분 시)Rewriter→Retriever 루프 실행, 반복마다 verdict/query 로그
- **범위 밖**: Tool 인터페이스, Agent State, LangGraph
- **DoD**:
  - sufficient 판정 시 즉시 종료
  - insufficient 반복 시 `retry_count == max_retries`에서 정확히 멈춤
  - 종료 사유(성공/재시도 소진)가 로그로 구분됨
- **핸드오프**: "Phase 2~5 함수들을 LangGraph 없이 while 루프로 감싼 통합 테스트 스크립트 작성해줘. retry_count가 max_retries(3)에서 정확히 멈추는지가 핵심 검증 포인트야."

---

## Phase 6. Tool Interface

- **의존성**: Checkpoint A, Pre-Lock B, C
- **범위**: `RetrieverTool`, `WebSearchTool` **둘** 구현 (Pre-Lock C — Vision·LlamaParse 는 제외됐다)
- **범위 밖**: tool 선택 로직 (Phase 8)
- **DoD**: 각 tool `ToolInput`으로 호출 → `ToolOutput` 반환, 실패 시 `success=False`+`error`

## Phase 7. Agent State

- **의존성**: Phase 6
- **범위**: Pre-Lock A 스키마의 초기화/업데이트 헬퍼, tool_call_log 기록
- **범위 밖**: 라우팅 판단, 그래프 노드
- **DoD**: 임의 tool 호출 시퀀스에 대해 state가 일관되게 누적

## Phase 8. Planner / Router

- **의존성**: Phase 6, 7
- **범위**: state 기반 `next_action` 결정 로직
- **범위 밖**: 실제 tool 실행, context 조립
- **DoD**: 다양한 state 시나리오(문서 있음/없음, retry 소진 등)에 합리적 next_action 산출

## Phase 9. Context Builder

- **의존성**: Phase 7
- **범위**: 선택된 청크 → `final_context` 조립 (정렬/포맷/출처 표기)
- **범위 밖**: 답변 생성
- **DoD**: 다양한 청크 조합에 대해 토큰 예산 내 일관된 포맷

## Phase 10. Generator  ✅ 2026-08-29

- **의존성**: Phase 9
- **범위**: `final_context` + ~~`current_query`~~ → **`original_query`** → `final_answer`, 출처 인용 형식
  - **2026-08-29 정정.** 재작성 질의는 PRF 로 부풀린 검색어 뭉치라 질문의 모양이 아니고, 그것에 답하면 **사용자가 묻지 않은 질문에 답한다.** 저장소는 이미 같은 규칙을 셋에서 쓴다 — 리랭커·Evaluator·`answer_question()` 의 생성. 여기만 스펙을 따르면 **네 번째 진입점만 나머지 셋과 다르게 답한다.** 근거는 `agent/loop.py` 모듈 docstring 의 「검색은 `current_query`, 판정·생성은 `original_query`」 절. 이 문서 머리말의 규약대로 **저쪽이 맞다.**
  - **Pre-Lock A 의 `AgentState.current_query` 필드는 이 정정과 무관하다** — 그 필드는 실재하고 **검색**이 그것을 쓴다(`step_retrieve()`). 문구를 고치면서 필드까지 지우면 검색이 원 질의로만 돌아 **승급이 죽는데 에러가 안 난다.**
- **범위 밖**: 평가 지표 계산
- **DoD**: `evaluation/qa_set.jsonl` 의 `gold_spans` 로 Faithfulness/Hallucination rate 측정 가능한 출력
- **DoD 는 문자 그대로는 못 채운다 — 대체 지표 셋으로 닫았다** (2026-08-29 · 53회차).
  Faithfulness·Hallucination rate 를 그 이름대로 재려면 **골든 답변**이 필요한데 없고,
  세트를 만드는 것은 `plan_log/평가-세트.md` 소관이라 이 phase 보다 크다. 대신
  `evaluation/answer_eval.py` 가 **골든 답변 없이** 셋을 낸다 — ① 인용 정합성(무료·결정적 ·
  답변이 인용한 `[n]` 청크가 `gold_span` 을 담나) ② 근거 지지(LLM · 2026-08-29 에 회귀
  게이트로 강등) ③ 음성 대조군. **이름을 `faithfulness` 로 안 짓는다** — 29회차가
  *"잰 것의 이름으로 부른다"* 로 닫았다.
- **그 수를 읽는 자[尺]도 함께 낸다** (53회차) — 무작위로 인용했을 때의 기대값과
  그 답변이 낼 수 있는 최대. 실측 7판에서 관측이 무작위의 **2.0~4.6배**이고
  **최대의 90~100%** 다(사람 세트 두 판은 최대와 같다). 근거는
  `verify_log/2026-08-29-인용정합성-대조군.md`.
- **닫아도 남는 구멍**: *"질문에 답했나"* 는 여전히 못 잰다. ①②는 *근거에 충실한가* 이지
  *질문에 답했나* 가 아니다 — `plan_log/검색-생성.md` 미결에 있다

## Phase 11. LangGraph Integration

- **의존성**: 전체 Phase
- **범위**: 노드 정의, 조건부 분기(next_action 기반), retry 루프를 그래프 사이클로 표현
- **범위 밖**: 개별 노드 내부 로직 재구현 (검증된 함수를 노드로 감싸기만)
- **DoD**: end-to-end 실행 시 Checkpoint A의 retry 동작이 그래프 상에서도 동일하게 재현
