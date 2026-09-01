# Knowledge — 지금 어떻게 동작하나

**여기는 현재의 Source of Truth 다.** 폐기된 방식·기각한 대안 때문에 현재 동작 설명이
흐려지지 않게 한다 — *"예전에는 A 였는데 B 로 바꿨다"* 는 `plan_log/`·`task_log/` 몫이고,
여기에는 *"지금 B 를 쓴다"* 와 **되돌리면 조용히 깨지는 것**을 적는다.

## 어디를 읽나

| 만지는 곳 | 읽을 것 |
|---|---|
| (항상) | `contracts.md` — 전 단계 공통 계약 |
| `rag_pipeline/ingestion/loader/**` | `ingestion/loader.md` |
| `rag_pipeline/ingestion/cleaner/**` | `ingestion/cleaner.md` |
| `rag_pipeline/ingestion/splitter/**` | `ingestion/splitter.md` |
| `rag_pipeline/ingestion/embedding/**` | `ingestion/embedding.md` |
| `rag_pipeline/ingestion/indexing/**`, `rag_pipeline/answer_cache/**` | `ingestion/indexing.md` |
| `rag_pipeline/retrieval/**` | `query/retriever.md` |
| `rag_pipeline/generation/**`, `rag_pipeline/agent/**` | `query/generation.md` |
| `rag_pipeline/query.py` | `query/README.md` + 위 둘 |
| `backend/**`, `rag_pipeline/exceptions.py` | `backend.md` |

같은 표가 `rag-code-review/SKILL.md` 의 라우팅과 `plan-log/SKILL.md` 의 「읽을 곳」에도 있다 —
**리뷰어가 읽는 기준과 사람이 읽는 지식이 같은 문서**다.

## 여기 없는 것

- **포맷별 함정과 실측값** — `CLAUDE.md` 의 「알고 있어야 할 함정」이 그 자리다. 이 문서들의
  함정 절은 그 **확장판**(같은 사실 + 실측 문자열 + 기각 근거)이라 **한쪽을 지울지가 아직
  미결**이다(`plan_log/문서-최신성.md` 16회차 2판)
- **단계별 상세 계약과 실측 근거** — 그 단계 `__init__.py` 모듈 docstring 이 갖는다. 그 끝의
  「이 단계의 규약이 어디 있나」 색인이 하위 모듈까지 가리킨다
- **검색 품질을 재는 절차** — `evaluation/README.md`
- **적재 회귀 기준선** — `regression/baseline.json`

## 규칙

- **한 문서는 20,000자를 넘기지 않는다.** 넘기면 폴더로 승격한다(`.claude/wiki/README.md`)
- **docstring 을 복사하지 않는다** — 가리킨다. 복사하면 한쪽만 고쳐지고 아무 에러가 안 난다
- **줄번호를 인용하지 않는다** — 파일과 심볼 이름으로 가리킨다
