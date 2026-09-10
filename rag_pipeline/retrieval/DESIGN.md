# Retrieval & Generation Pipeline — 설계서

> **이 문서는 외부에서 가져온 원안이다.** 확정된 설계는 `.claude/wiki/plan_log/검색-생성.md` 이고,
> 거기서 이 원안의 일부가 이미 기각됐다(평가 세트를 Phase 0 으로 올림 · tool 목록에서
> Vision·LlamaParse 제외). 어긋나면 그쪽이 맞다.

## 1. 목적 및 범위

Agentic RAG 시스템의 retrieval & generation 파트에 대한 설계 문서. 사용자가 질문하면 Agent가 판단해 Retriever/Web Search/Vision/LlamaParse 중 필요한 tool을 호출하고, 검색된 문서를 Reranker → Relevance Evaluator로 평가한 뒤, 불충분하면 Query Rewriter로 재시도하고, 충분하면 Context Builder → Generator를 거쳐 답변을 만든다.

이 문서는 "왜 이렇게 설계했는지"만 다룬다. "어떻게 구현하는지"(코드, 라이브러리, 클래스 구조)는 `IMPLEMENTATION_SPEC.md`에, 스키마 그림은 `SCHEMA.html`에 있다.

> **2026-08-22 정정.** 초안을 저장소의 기존 계약과 대조해 여섯 곳을 고쳤다 — 저장소(ChromaDB→qdrant), 평가 세트 순서(Phase 0 신설), tool 목록(Vision·LlamaParse 제외), `RetrievedChunk` 필드, 질의 시점 비용, 하네스 5곳. 무엇을 왜 골랐고 무엇을 기각했는지는 `.claude/wiki/plan_log/검색-생성.md` 1회차에 있다.

## 2. 전체 아키텍처

```
User
 └→ Agent (Router)
      ├→ Retriever ─┐→ Reranker ─┐
      └→ Web Search ─────────────┼→ Relevance Evaluator ──(불충분)──→ Query Rewriter ──┐
                                 │                                                    │
                              (충분)                                                  │
                                 ↓                                                    │
                        Context Builder → Generator → 답변                     (재시도, 최대 3회)
                                 ↑____________________________________________________┘
```

**Vision·LlamaParse 는 tool 이 아니다.** 질의 시점에는 그 둘에 건넬 입력이 없다 — 원본
바이트(`ContentRegion.blob`)가 로더 경계에서 죽고 splitter 이후로는 텍스트만 남는다(확인함).
둘 다 **적재 시점 전처리**이고, 그림은 `SCHEMA.html` 3절에 있다.

Retriever 안에서 dense(qdrant)와 BM25(저장소 밖 인덱스)가 나뉘고 RRF 로 합쳐진다.

## 3. Phase 구성과 순서 근거

11개 phase는 아래 5개 블록으로 묶이고, 블록 순서는 의존성을 따른다 (하위 컴포넌트 → 조립 → 통합).

| 블록 | Phase | 근거 |
|---|---|---|
| **A0. 측정 기반** | **0. 정답 질의 세트** | **Phase 2·4·10 의 DoD 셋이 전부 라벨링 QA셋을 요구하는데 없었다.** 세트 없이는 hybrid 가 dense 보다 나은지, 리랭커가 값을 하는지를 못 잰다 — 근거 없는 값이 그대로 굳는다. `.claude/wiki/plan_log/평가-세트.md` 2회차 |
| A. 파운데이션 | 1. Core Contract/Schema | 이후 모든 phase가 이 계약에 맞춰 입출력을 주고받음. 가장 먼저 고정 |
| B. Retrieval Quality Loop | 2. Retriever → 3. Reranker → 4. Evaluator → 5. Rewriter | 서로 강하게 얽힌 하나의 서브시스템. Agent/tool 레이어 없이도 독립적으로 Hit Rate@k, Recall@k 측정 가능 |
| C. Agent 레이어 | 6. Tool Interface → 7. Agent State → 8. Planner/Router | 검증된 retrieval loop를 tool로 감싸고, 상태를 정의하고, 그 상태를 보고 판단하는 라우터를 순서대로 구성. Router는 Tool Interface와 Agent State 둘 다에 의존 |
| D. 조립 및 생성 | 9. Context Builder → 10. Generator | 선택된 결과를 조립한 뒤 생성 |
| E. 통합 | 11. LangGraph Integration | 개별 검증된 노드 로직을 마지막에 그래프로 묶음. graph-first보다 노드 로직 디버깅이 안전 |

## 4. Phase 간 데이터 흐름 (개념 계약)

구체적 타입/코드는 `IMPLEMENTATION_SPEC.md`의 Pre-Lock 섹션 참고. 여기서는 각 phase가 "무엇을 받아 무엇을 내는지"만 정리.

| Phase | 받는 것 | 내는 것 |
|---|---|---|
| 2. Retriever | 질문 문자열, top_k | 청크 목록 (내용, 출처, dense/BM25 점수) |
| 3. Reranker | 질문, Phase 2 청크 목록 | rerank 점수가 채워져 재정렬된 청크 목록 |
| 4. Evaluator | 질문, Phase 3 청크 목록 | 충분/불충분 판정 + 근거 + (불충분 시) 부족한 부분 |
| 5. Rewriter | 원본 질문, Evaluator 판정 | 변형된 질문 |
| 6. Tool Interface | 표준화된 tool 입력 | 표준화된 tool 출력 (성공 여부 포함) |
| 7. Agent State | tool 호출/평가 결과들 | 누적된 대화·검색·재시도 상태 |
| 8. Router | 현재 상태 | 다음 행동 (어떤 tool 호출 / rewrite / generate / 종료) |
| 9. Context Builder | 최종 선택된 청크들 | 하나로 조립된 context 문자열 |
| 10. Generator | context, 질문 | 최종 답변 |

## 5. 핵심 설계 결정

**5.1 Agent State 필드를 Phase 1에서 미리 스케치**
Phase 7에서 구현할 State 스키마(특히 retry count, 대화 히스토리, tool 호출 로그)가 Phase 1~6에서 정의한 타입들과 어긋나면 리팩토링이 필요해짐. 그래서 필드 설계만 Phase 1 시점에 확정하고, 실제 구현은 Phase 7에서 한다.

**5.2 Retriever를 순수 함수로 먼저 만들고, Tool은 나중에 감싼다**
Retriever(Phase 2)를 tool 인터페이스 없이 순수 함수로 먼저 구현하면, retrieval 성능(Hit Rate@k 등)을 tool-call 오버헤드 없이 순수하게 측정할 수 있다. 다만 Tool의 추상 시그니처는 Phase 1에서 미리 정해서, Phase 6에서 어댑터를 다시 설계하는 일이 없게 한다.

**5.3 Retry loop을 그래프 통합 전에 별도로 검증한다 (Checkpoint A)**
Evaluator → Rewriter → Retriever 재호출 루프, 그리고 재시도 2~3회 캡은 Phase 2~5 단계에서는 개별 컴포넌트 단위로만 테스트된다. 이 반복 제어 로직이 실제로 처음 실행되는 게 Phase 11(LangGraph)이면, 그래프 wiring 버그인지 retry 로직 버그인지 구분하기 어렵다. 그래서 Phase 5 직후, LangGraph 없이 while 루프로 감싼 통합 테스트를 먼저 돌려 retry cap 동작만 별도로 검증한다.

**5.4 Web Search 의 위치는 Phase 6 이전에 확정한다**
Agent가 여러 tool 중 하나를 선택하는 구조이지만, 11-phase 목록엔 Retriever 계열만 명시돼 있었다. 이 tool이 Phase 6에 포함되는지 미리 정하지 않으면 Phase 8(Router) 설계 시 "라우터가 고를 수 있는 선택지"가 불명확해진다. **Vision·LlamaParse 는 이 질문에서 빠졌다** — 2장 참고.

**5.5 저장소를 갈아끼울 수 있게 하되 새 추상화는 만들지 않는다**
`ingestion/indexing/base.py` 의 `VectorStore` Protocol 에 `search()` 가 이미 있다. retriever 가 `QdrantStore` 를 직접 잡지 않고 이 Protocol 만 받으면 교체 가능성이 충족된다. `provider` 설정은 **두 번째 구현체가 실재할 때** 더한다 — 구현체가 하나면 합법값이 하나뿐인 죽은 설정이고, 이 저장소가 `embedding.provider` 를 안 만든 것과 같은 기준이다. Reranker 도 같은 모양이다(Protocol + 팩토리 먼저, 설정은 나중).

**5.6 질의 시점 비용은 적재 시점과 축이 다르다**
적재는 문서당 1회라 `max_vision_calls_per_doc` 같은 문서 단위 상한이 성립한다. 질의는 **반복된다** — Router + Evaluator×3 + Rewriter×3 + Generator 로 질문 하나에 최대 ~10회 LLM 호출이 일어난다. 상한과 카운터를 어디에 둘지는 Phase 4~5 에서 실제 호출 수를 보고 정한다(미결).

## 6. 미해결 / 결정 필요 항목

닫힌 것부터.

- ~~**LlamaParse의 소속**~~ — **적재 시점 전처리로 확정.** 질의 시점에는 건넬 바이트가 없다(2장).
- ~~**Vision tool의 트리거 조건**~~ — 같은 이유로 **tool 이 아니다.** 트리거는 적재 시점의 `image_handler` 설정이고 이미 있다.

남은 것.

- **LangGraph를 정말 쓸지**: 이 흐름은 while 루프로 표현된다. 체크포인팅·스트리밍·병렬 분기가 필요해지면 값을 한다. **Checkpoint A 가 답을 준다** — 다만 `AgentState` 를 pydantic 으로 굳힌 뒤 옮기면 reducer·`Annotated` 때문에 다시 쓰게 되므로 **Phase 7 전에** 정해야 한다.
- **Router의 tool 선택 정책**: 여러 tool이 동시에 후보일 때 우선순위 규칙이 아직 없음 (Phase 8에서 구체화 필요).
- **Evaluator·Rewriter 의 실패 계약**: 이 저장소는 "폴백 층이 있으면 빈 결과 + `warn()`, 없으면 예외"로 갈라 왔다. Evaluator 는 어느 쪽도 안전하지 않다 — 실패를 "충분"으로 떨어뜨리면 나쁜 context 로 답을 만들고, "불충분"으로 떨어뜨리면 재시도를 태운다. **둘 다 조용히 나쁘다.**
- **질의 엔드포인트가 `store` 를 언제 잡고 놓는지**: qdrant 로컬 파일 모드가 배타 잠금을 걸어, 요청마다 열고 안 닫으면 두 번째 질의가 죽는다.
- **agent 코드의 거처**: `retrieval/` · `generation/` · 새 `agent/` 중 어디.
- **질의당 LLM 호출 상한**: 5.6 참고.
