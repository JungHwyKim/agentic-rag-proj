---
name: rag-code-review
description: agentic-rag-proj(한국어 문서 RAG 적재 파이프라인)의 코드를 리뷰한다. 아키텍처 경계 위반, 파이프라인 단계 간 계약 위반, 폴백 사슬 규약 위반, 외부 API 비용 누수, 예외→HTTP 매핑 누락, 한국어 주석 컨벤션, 테스트 모킹 이음새를 중점 점검하고 심각도별 리포트를 낸다. 사용자가 "코드 리뷰", "리뷰해줘", "검토해줘", "PR 봐줘", "변경사항 점검", "커밋 전에 확인", "이거 괜찮아?" 같은 말을 하거나, rag_pipeline/ 또는 backend/ 의 코드를 다 짜고 나서 확인받고 싶어할 때 반드시 이 스킬을 쓴다. 새 로더·새 핸들러·새 파이프라인 단계·새 예외를 추가한 직후에도 등록 누락을 잡기 위해 쓴다.
---

# rag-code-review

이 저장소는 한국어 문서를 파싱해 통일 스키마(`ParsedDocument`)로 만드는 RAG 적재 파이프라인이다.
일반적인 파이썬 리뷰 상식만으로는 여기서 제일 비싼 실수를 못 잡는다. 실제로 아픈 실수는 셋이다.

1. **설계 경계가 무너지는 변경** — 한 번 무너지면 되돌리는 비용이 크다
2. **외부 API 비용이 새는 변경** — 200페이지 스캔본 한 건으로 청구서가 튄다
3. **폴백이 조용히 죽는 변경** — 키 하나 없다고 적재 전체가 멈춘다

이 셋을 우선 보고, 그다음에 일반적인 정확성·간결성을 본다.

## 1단계 — 리뷰 범위를 확정한다

사용자가 범위를 안 줬으면 이 순서로 정한다.

1. git 저장소면 `git diff HEAD` / `git diff --staged` 로 변경분을 잡는다
2. git 저장소가 아니거나 diff 가 비었으면, 대화에서 방금 만지던 파일을 범위로 잡는다
3. 그것도 없으면 사용자에게 "무엇을 리뷰할까요"라고 한 번 묻는다

## 2단계 — 영역을 판별하고 reference 를 읽는다

변경된 경로에 해당하는 reference 를 읽는다. **여러 영역에 걸치면 전부 읽는다.**

| 변경된 경로 | 읽을 reference |
|---|---|
| (항상) | `.claude/wiki/knowledge/contracts.md` |
| `rag_pipeline/ingestion/loader/**` | `.claude/wiki/knowledge/ingestion/loader.md` |
| `backend/**`, `rag_pipeline/exceptions.py` | `.claude/wiki/knowledge/backend.md` |
| `rag_pipeline/ingestion/cleaner/**` | `.claude/wiki/knowledge/ingestion/cleaner.md` |
| `rag_pipeline/ingestion/splitter/**`, `rag_pipeline/schemas/chunk.py` | `.claude/wiki/knowledge/ingestion/splitter.md` |
| `rag_pipeline/ingestion/embedding/**` | `.claude/wiki/knowledge/ingestion/embedding.md` |
| `rag_pipeline/ingestion/indexing/**`, `rag_pipeline/schemas/vector.py` | `.claude/wiki/knowledge/ingestion/indexing.md` |
| `rag_pipeline/answer_cache/**` | `.claude/wiki/knowledge/ingestion/indexing.md` (qdrant 컬렉션·잠금이 같은 축이다. 전용 reference 는 임계값이 정해질 때) + `.claude/wiki/knowledge/query/README.md` |
| `rag_pipeline/retrieval/**` | `.claude/wiki/knowledge/query/retriever.md` |
| `rag_pipeline/generation/**`, `rag_pipeline/agent/**` | `.claude/wiki/knowledge/query/generation.md` |
| `rag_pipeline/query.py` | `.claude/wiki/knowledge/query/README.md` + 위 둘 |
| `rag_pipeline/schemas/document.py` | `.claude/wiki/knowledge/contracts.md` (전 단계 공통 계약의 원본) |
| `rag_pipeline/ingestion/pipeline.py` | `contracts.md` + 추가·변경된 단계의 reference |
| `rag_pipeline/config.py` | `contracts.md` + 그 설정을 쓰는 영역의 reference |
| `tests/**` | 그 테스트가 덮는 영역의 reference |

해당 영역 reference 가 아직 없으면 `contracts.md` 만 적용하고, 리포트 끝에 "이 영역에 대한
별도 reference 가 없습니다"라고 적는다. **없는 영역에 대해 추측으로 프로젝트 고유 규칙을
만들지 않는다.**

변경된 파일만 읽지 말고, 그 파일이 지키기로 한 **계약이 적힌 곳을 같이 읽는다.** 영역별
reference 에 "같이 읽어야 하는 곳" 표가 있다.

### Source of truth 우선순위

```
실제 코드·테스트  >  영역별 reference  >  contracts.md  >  추측
```

문서만 보고 구현을 가정하지 않는다. reference 와 실제 구현이 어긋나면 **실제 구현이 맞다** —
코드를 지적하는 대신 "reference 가 낡았다"를 리포트에 따로 적는다. 낡은 문서를 근거로 멀쩡한
코드를 지적하는 것이 최악이다.

## 3단계 — 검증

가능하면 실제로 돌려본다. 둘 다 몇 초 안 걸린다.

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest -q
```

`-m integration` 은 **절대 자동으로 돌리지 않는다.** LlamaParse/OpenAI 에 실제 과금이 발생한다.
필요하면 사용자에게 허락을 구한다.

## 4단계 — 반증

리포트에 올리기 전에 **각 지적을 한 번씩 반증해 본다.** "이 코드가 실제로 그렇게 동작하나?
내가 못 본 방어 코드가 위에 있지 않나?" 관련 파일을 다시 읽어 확인한다.

확인 못 한 지적은 버리거나, 확실한 것과 섞이지 않게 "확인 필요"로 따로 묶는다.
틀린 지적 하나가 맞는 지적 다섯 개의 신뢰를 깎는다.

## 리포트 형식

한국어로 쓴다. 심각도 순으로, 없는 섹션은 통째로 생략한다.

````markdown
## 리뷰 요약
[2~3문장. 무엇을 봤고, 제일 중요한 결론이 뭔지.
 문제가 없으면 "문제 없음"이라고 분명히 쓴다]

리뷰 범위: [파일 목록 또는 diff 범위]
적용한 reference: [contracts.md, loader.md 등]
검증: ruff [통과/실패] · pytest [125 passed 등]

## Critical — 고치고 커밋해야 한다
### 1. [한 줄 제목]
`파일:줄`

[무엇이 문제인지 1~2문장]

**깨지는 경우**: [구체적 입력/상황 → 결과]

**수정**:
```python
# 지금
...
# 이렇게
...
```

## Major — 곧 문제가 된다

## Minor — 여유 있을 때

## 확인 필요
[정적으로 판단 못 한 것. 사용자가 확인해야 할 질문 형태로]

## 좋았던 점
[진짜로 잘한 게 있을 때만. 없으면 이 섹션을 쓰지 않는다]
````

심각도 기준:

- **Critical** — 아키텍처 경계 위반, 폴백을 죽이는 변경, 비용 누수, 기본 테스트를 깨뜨리는 변경,
  데이터를 잃는 버그
- **Major** — 특정 입력에서 잘못된 결과가 나오는 버그, 등록 누락, 폴백 테스트 부재
- **Minor** — 컨벤션, 네이밍, 중복, 읽기 어려운 코드

## 리뷰 후 — reference 제안

reference 에 없던 **프로젝트 고유의 반복 유효한 함정·계약**을 발견했으면 리포트 맨 끝에
`Reference 제안: 이 내용은 .claude/wiki/knowledge/ 아래 <파일명>.md 에 남길 만합니다`
한 줄을 적는다.
**reference 파일을 직접 고치지는 않는다.** 이 스킬은 읽기 전용이고, 반영은 별도 작업이다.

## 이 스킬의 범위

코드가 프로젝트의 설계·계약·규칙을 지키는가를 본다. **실행 결과가 기대값과 맞는가는 이 스킬의
일이 아니다.** 파싱 품질이나 검색 정확도 문제는 리뷰가 아니라 별도 검증 작업이므로, 눈에 띄면
"확인 필요"에 한 줄 적고 넘어간다.

## 리뷰 태도

**모든 지적에 근거를 댄다.** `파일:줄` 을 달고, 왜 문제인지를 이 프로젝트의 맥락으로 설명한다.
"베스트 프랙티스"라는 말로 때우지 않는다.

**취향을 버그로 포장하지 않는다.** 다르게 짤 수 있다는 것과 틀렸다는 것은 다르다.
취향 차이는 Minor 에 넣거나 아예 쓰지 않는다.

**깨끗하면 깨끗하다고 쓴다.** 억지로 지적을 만들어내면 다음 리뷰부터 아무도 안 읽는다.
Critical 이 없으면 없다고 쓰는 게 정직한 리뷰다.

**고칠 수 있는 형태로 쓴다.** "에러 처리가 부족합니다"가 아니라 어떤 예외를 어디서 어떻게
잡으라고 쓴다. 짧은 diff 를 붙일 수 있으면 붙인다.
