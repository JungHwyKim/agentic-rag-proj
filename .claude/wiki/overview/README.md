# agentic-rag-proj — 무엇이고 어디에 무엇이 있나

한국어 문서(PDF · DOCX · PPTX · XLSX · HWPX · 이미지 · 텍스트/CSV/마크다운)를 파싱해 통일
스키마로 만들고, 색인하고, 질문에 답하는 RAG 파이프라인.

**이 문서는 지도다 — 설명서가 아니다.** 세부는 옮겨 적지 않고 가리킨다.

## 진입점은 넷이고 전부 HTTP 를 모른다

스크립트나 노트북에서도 그대로 쓸 수 있다.

| 진입점 | 어디 | 무엇을 |
|---|---|---|
| `ingest()` | `rag_pipeline/ingestion/pipeline.py` | 파일 하나 → `ParsedDocument` |
| `index_document()` | 같은 파일 | 그 위에 청킹→임베딩→색인 |
| `answer_question()` | `rag_pipeline/query.py` | 질문 하나 → 검색→생성 → `Answer` |
| `run_agent_loop()` | `rag_pipeline/agent/loop.py` | 판정이 **흐름을 가르는** 재시도 루프 |

**넷째만 계약이 반대다** — 앞 셋은 *판정이 답변을 안 바꾼다* 이고 넷째는 *판정이 흐름을
가른다*. 그래서 합치지 않는다. backend 는 넷째를 직접 안 부르고 `query.py` 의 어댑터
(`answer_question_with_retries()`)를 부른다.

## 큰 흐름

```
파일 → detect_format() → get_loader() → BaseDocumentLoader.load()
                                          ├ detect_regions()   (포맷별)
                                          └ route_all()        (콘텐츠 핸들러)
     → ParsedDocument → clean() → split() → embed → index
                                                      ↓
질문 → [답변 캐시] → retrieve()                    (qdrant)
                      ├ dense · BM25 → RRF
                      ├ rewrite(PRF)  (기본 꺼짐)
                      └ rerank        (RRF 뒤 · 절단 앞)
     → [evaluate]  → generate_answer() → citations 검사 → Answer
```

모양의 근거와 «되돌리면 깨지는 것» 은 `architecture.md`, 계약은 `.claude/wiki/knowledge/` 다.

## 어디를 읽나

| 알고 싶은 것 | 어디 |
|---|---|
| 시스템의 모양·경계 | `architecture.md` |
| 이 저장소에만 있는 말 | `glossary.md` |
| 각 컴포넌트가 지금 어떻게 도나 | `.claude/wiki/knowledge/README.md` |
| 왜 그렇게 골랐나 (정하는 중) | `.claude/wiki/plan_log/INDEX.md` |
| 왜 그렇게 골랐나 (정하고 난 뒤) | `.claude/wiki/task_log/INDEX.md` |
| 얼마가 나왔나 | `.claude/wiki/verify_log/INDEX.md` |
| 실측값·함정·명령어·테스트 규약 | `CLAUDE.md` |

## 「어디까지 왔나」는 여기 안 적는다

진행 상태를 적는 순간 낡는다 — 이 저장소가 2회차에 명시적으로 기각한 것이라 *project-status.md*
같은 파일을 두지 않는다. 대신 **출처**를 본다.

- **구현된 단계** — `rag_pipeline/` 아래 디렉터리가 전부다
- **예약된 다음 단계** — `pipeline.py:ingest()` 안의 주석
- **품질을 무엇으로 재나** — 검색은 `evaluation/README.md`, 적재는 `regression/baseline.json` 과
  `tests/ingestion/**/test_*_real.py`, 생성은 `evaluation/answer_eval.py`. **자[尺]가 서로
  달라 섞지 않는다**
- **안 끝난 일** — `openwork` 스킬(재료는 `plan_log/*.md` 의 `## 미결` 과 `task_log/*.md` 의
  `## 7. 남은 것`)

그 목록의 자세한 판단 근거는 `CLAUDE.md` 의 「어디까지 왔나」 절에 있다 — **여기로 옮겨
적지 않는다**(옮기면 두 벌이 되고 한쪽이 조용히 낡는다).
