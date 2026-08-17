# rag-code-review Skill 리팩터링 작업 지시서

## 0. 작업 목적

현재 `.claude/skills/rag-code-review/SKILL.md`가 사실상 Loader 전용으로 비대해져 있다.

목표는 **Loader-specific knowledge는 유지하면서 분리하고, 여러 RAG 파이프라인 단계에서 공통으로 적용되는 규약은 `contracts.md`로 승격하는 것**이다.

향후 `cleaner → splitter → embedding → indexing → retriever` 등의 단계가 추가되어도 `SKILL.md` 자체는 커지지 않고, 변경 영역에 맞는 reference만 선택적으로 읽는 **progressive disclosure 구조**로 만든다.

중요한 원칙:

* 기존 Loader 관련 내용은 삭제하지 않는다.
* 공통 규약과 Loader 전용 내용을 분리한다.
* 아직 구현되지 않은 단계는 추측하여 상세 규칙을 만들지 않는다.
* 실제 코드/테스트에 근거한 규약을 우선한다.
* 문서 리팩터링 과정에서 기존 review rule이 조용히 유실되지 않도록 반드시 대조한다.

---

# 1. 목표 구조

최종 구조를 다음과 같이 만든다.

```text
.claude/skills/rag-code-review/
├── SKILL.md
└── references/
    ├── contracts.md
    ├── loader.md
    ├── backend.md
    └── next-stages.md
```

예상 분량:

```text
SKILL.md                  ~100줄
references/contracts.md   ~90줄
references/loader.md      ~130줄
references/backend.md    ~40줄
references/next-stages.md ~50줄
```

정확한 줄 수보다 역할 분리가 중요하다.

---

# 2. Progressive Disclosure 규칙

`SKILL.md`는 항상 로드되는 엔트리 문서이고, `references/`는 변경 영역에 해당하는 경우에만 Read 한다.

## Reference 적용 규칙

다음 규칙을 반드시 SKILL.md에 명시한다.

1. `references/contracts.md`는 항상 읽는다.
2. 변경 영역에 해당하는 reference가 존재하면 반드시 읽는다.
3. 변경 범위가 여러 영역에 걸치면 해당 reference를 모두 읽는다.
4. 해당 영역의 reference가 아직 없으면 `contracts.md`만 적용한다.
5. reference가 없는 영역에 대해서는 추측하여 프로젝트 고유 규칙을 만들지 않는다.
6. 리뷰 시 실제 코드와 테스트를 확인하여 reference와 실제 구현의 차이를 검증한다.

## Source of Truth 우선순위

reference 간 내용이 충돌하거나 reference와 실제 구현이 다른 경우 다음 우선순위를 따른다.

```text
실제 코드/테스트
    >
영역별 reference
    >
contracts.md
    >
추측
```

즉, 실제 코드와 테스트가 가장 우선이며, 문서만 보고 구현을 가정하지 않는다.

---

# 3. SKILL.md 재작성

`.claude/skills/rag-code-review/SKILL.md`를 전면 재작성한다.

## Frontmatter

현재 description에는 Loader 고유명사가 전면에 드러나 있다.

기존의 Loader 편향 표현을 제거하고 다음과 같은 관심사 중심으로 바꾼다.

* 아키텍처 경계
* 폴백 규약
* 비용 누수
* 예외 매핑
* 파이프라인 단계 간 계약
* code review / PR review

다음 트리거 문구는 그대로 유지한다.

* 리뷰해줘
* 검토해줘
* PR 봐줘
* 커밋 전에 확인

---

## SKILL.md 본문 구성

### 1. 아픈 실수 3종

현행 내용을 유지한다.

* 경계 붕괴
* 비용 누수
* 조용한 폴백 죽음

의미와 중요도를 유지한다.

---

### 2. 1단계: 범위 확정

현행 동작을 그대로 유지한다.

순서:

```text
git diff HEAD
→ git diff --staged
→ 대화 중 제공된 파일
→ 필요한 경우 사용자에게 범위 질문
```

---

### 3. 2단계: 영역 판별 → Reference 로드

이번 리팩터링의 핵심이다.

다음 라우팅 표를 넣는다.

| 변경된 경로                                                            | 읽을 reference                     |
| ----------------------------------------------------------------- | -------------------------------- |
| 항상                                                                | `references/contracts.md`        |
| `rag_pipeline/ingestion/loader/**`                                | `references/loader.md`           |
| `backend/**`, `rag_pipeline/exceptions.py`                        | `references/backend.md`          |
| `rag_pipeline/ingestion/{cleaner,splitter,embedding,indexing}/**` | `references/next-stages.md`      |
| `pipeline.py`의 새 단계                                               | `references/next-stages.md`      |
| `rag_pipeline/config.py`                                          | `contracts.md` + 해당 영역 reference |
| `tests/**`                                                        | 해당 테스트가 덮는 영역의 reference         |

여러 영역에 걸치면 관련 reference를 전부 읽는다.

해당 영역 reference가 없다면 `contracts.md`만 적용하고, 리포트 마지막에:

> 이 영역에 대한 별도 reference가 없습니다.

를 명시한다.

---

### 4. 3단계: 검증

현행 방식을 유지한다.

```text
ruff check .
pytest -q
```

`pytest -m integration`은 절대 실행하지 않는다.

---

### 5. 4단계: 반증

현행 방식을 유지한다.

* 지적 하나하나를 다시 읽고 사실인지 확인한다.
* 확인하지 못한 사항은 확정적으로 쓰지 않는다.
* 확인하지 못한 경우 `확인 필요`로 분리한다.

---

### 6. 리포트 형식 / 심각도 / 리뷰 태도

현행 SKILL.md의 리포트 형식과 심각도 기준 및 리뷰 태도를 그대로 이관한다.

현재 잘 작동하고 있으므로 의미를 변경하지 않는다.

---

### 7. 리뷰 후 학습 제안

신규로 다음 규칙을 추가한다.

이번 리뷰 과정에서 reference에 없지만 프로젝트 고유의 반복적으로 유효한 함정이나 계약을 발견했다면 리포트 맨 끝에 다음 형식으로 제안한다.

```text
Reference 제안:
이 내용은 references/<파일명>.md 에 남길 만한 프로젝트 고유 규약입니다.
```

중요:

* Skill이 reference 파일을 직접 수정하지 않는다.
* 이번 작업에서는 읽기 전용으로 동작한다.
* 실제 reference 반영은 별도 작업으로 한다.

---

# 4. references/contracts.md

신규 작성한다.

목적:

**Loader에 갇혀 있던 범용 규약을 모든 RAG pipeline 단계가 공유하는 계약으로 승격한다.**

다음 내용을 실제 코드 근거와 함께 명시한다.

---

## 4.1 Pipeline contract

* `ingest()`가 유일한 진입점
* HTTP를 모른다.
* 단계는 순차적으로 합성된다.
* 각 단계가 독립적으로 호출되더라도 HTTP 계층에 의존하지 않는다.

---

## 4.2 External API contract

외부 API 호출 단계는 다음 규약을 따른다.

* 예외를 외부로 그대로 던지지 않는다.
* 실패 시 적절한 빈 결과를 반환한다.
* `ctx.warn()`으로 경고를 남긴다.
* `except Exception` 사용 시 반드시 `# noqa: BLE001`와 이유 주석을 둔다.
* 규약 위반은 Critical로 본다.

실제 근거:

```text
rag_pipeline/ingestion/loader/content/table_llamaparse.py
```

---

## 4.3 Cost control 3종 세트

### 성공 호출 이후에만 카운터 증가

근거:

```text
image_vision.py:108
table_llamaparse.py:62
```

성공 여부가 확인되기 전에 카운터를 증가시키는 구현을 지적한다.

### 호출 상한 우회 금지

근거:

```text
image_vision.py:71
config.py:39
```

예:

```text
max_vision_calls_per_doc
```

새 외부 API 단계도 동일한 보호 장치를 적용해야 한다.

### Cache key salt

결과를 변경할 수 있는 설정은 cache key/salt에 반드시 반영한다.

근거:

```text
image_vision.py:65
content_hash(blob, salt=f"vision:{cfg.vision_model}")
```

```text
table_llamaparse.py:35
salt=f"llamaparse:{tier}:{version}"
```

주의:

비용 제어 장치인 `LoadContext`, `content_hash`, `ResultCache`가 현재 Loader 패키지 안에 구현되어 있다.

```text
loader/content/base.py:44-119
```

새 단계에서 외부 API 비용이 발생할 경우:

1. 기존 기계장치를 재사용/일반화했는지 확인한다.
2. 별도 구현을 복제했다면 비용 가시성이 분리되는 문제를 지적한다.

---

## 4.4 Exception → HTTP contract

새 예외 클래스를 추가하면:

```text
rag_pipeline/exceptions.py
```

뿐 아니라:

```text
backend/app/main.py:21
_STATUS_BY_EXCEPTION
```

에도 반드시 등록되어야 한다.

주의:

`isinstance`로 위에서부터 순차적으로 검사하므로 하위 예외 타입을 상위 예외 타입보다 먼저 등록한다.

누락 시 사용자가 500을 받는 문제를 Critical로 검토한다.

---

## 4.5 Coding conventions

다음 규칙을 공통 계약으로 명시한다.

* class/def 위 한 줄 한국어 주석
* 모듈 docstring에는 왜 존재하는지 설명
* 실측 수치는 반드시 근거와 함께 작성
* `from __future__ import annotations`
* ruff line length 100
* Ruff 적용 규칙:
  `E,F,I,UP,B,SIM`
* `E501`은 무시

---

## 4.6 Test contracts

* 기본 `pytest -q`는 외부 API key 없이 통과할 수 있어야 한다.
* 실제 외부 API가 필요한 테스트는 `@pytest.mark.integration` 사용
* 리뷰 중 기본 검증에서는 integration을 실행하지 않는다.
* 모킹 이음새는 모듈 레벨 `_make_client()`를 사용한다.
* `_make_client()`를 인라인화하거나 메서드로 이동하는 것은 Critical
* lazy property를 테스트에서 덮어쓸 때:
  `object.__setattr__(config, "_content", ...)`
* 폴백 경로 테스트는 필수
* 바이너리 픽스처를 repository에 commit하지 않는다.

---

## 4.7 General correctness

공통적으로 다음을 확인한다.

* 정렬 계약
* 손상 입력
* 0바이트 입력
* 인코딩
* `errors=` 없는 `decode()`
* 한국어 파일명
* 리소스 누수
* 상한 미적용
* Path 대신 문자열 결합

Loader-specific 예시는 `loader.md`로 이동한다.

---

# 5. references/loader.md

기존 SKILL.md의 Loader 전용 내용을 이관한다.

내용을 임의로 줄이지 않는다.

실측 수치와 실제 코드 근거도 유지한다.

다음 항목을 포함한다.

---

## 5.1 Architecture boundary

현재 SKILL.md 2단계의 Loader 구조를 그대로 이관한다.

```text
detect_format
→ get_loader
→ detect_regions
→ route_all
```

---

## 5.2 Loader / Handler 책임 경계

다음을 명확히 유지한다.

* Loader가 어떤 역할을 담당하는가
* content handler가 어떤 역할을 담당하는가
* 어떤 책임이 서로 침범하면 안 되는가

기존의 "잡아야 할 위반" 6개도 그대로 이관한다.

경계 위반은 Critical.

---

## 5.3 Loader fallback chain

기존의 구체적인 폴백 사슬을 유지한다.

* 표 fallback
* 이미지 fallback

구체적인 Loader 사슬은 여기 유지하고, "외부 API 예외/비용/상한/cache" 같은 일반 규약은 `contracts.md`에 둔다.

---

## 5.4 Loader 함정

다음 실제 함정을 모두 유지한다.

### OCR

* `psm=6`
* `preserve_interword_spaces`
* `image_to_data()`의 신뢰도 활용
* `image_to_string()`과 텍스트 추출 역할을 분리

### Image preprocessing

* 600px 미만만 확대

### LlamaParse

* SDK 이중 deprecated 문제

### Legacy format

* 레거시 포맷을 `SUPPORTED_EXTENSIONS`에서 제거하지 않는다.

### ZIP bomb

* `MAX_UNCOMPRESSED_BYTES`

### File type detection

* 매직바이트 판별

### HWPX

* 쪽번호 계산:
  `hp:cellSz ÷ rowSpan`
* 레이아웃 표 강등

HWPX 관련 함정은 기존 스킬뿐 아니라 `CLAUDE.md`에서 누락된 내용도 이번에 추가한다.

---

## 5.5 새 포맷 등록

기존의 "새 포맷 등록 4곳" 규칙을 그대로 유지한다.

---

## 5.6 Loader별 같이 읽을 짝

기존 SKILL.md의 "같이 읽을 곳" 매핑표 5줄을 그대로 이관한다.

---

# 6. references/backend.md

현행 SKILL.md에 흩어져 있는 Backend 규칙을 이동한다.

포함 내용:

### documents route

`backend/app/api/routes/documents.py`의 책임:

* 업로드 수신
* 확장자 whitelist 1차 검증
* UUID 임시 저장
* 정리

파싱 로직이 이 영역에 들어오면 경계 위반.

### CPU-bound Loader

Loader는 동기/CPU-bound이므로 `run_in_threadpool`을 사용한다.

이벤트 루프에서 직접 CPU-bound Loader를 호출하면 지적한다.

### Exception mapping

`_STATUS_BY_EXCEPTION` 순서 규칙은 `contracts.md`와 교차 참조한다.

### Configuration

설정이 다음 두 영역으로 이원화되어 있다.

```text
backend/app/core/config.py
    → Web

rag_pipeline/config.py
    → Parsing
```

둘 다 `.env`를 사용한다.

---

# 7. references/next-stages.md

현재 아직 구현되지 않은 영역을 위한 최소 계약만 작성한다.

중요:

**추측으로 상세한 체크리스트를 만들지 않는다.**

현재 구현된 코드에서 확실히 도출되는 계약만 작성한다.

---

## 7.1 현재 상태

다음 단계가 아직 구현되지 않았음을 명시한다.

```text
pipeline.py:31-35
```

에:

```text
clean → split → embed → index
```

단계 삽입 지점이 주석으로 예약되어 있다.

---

## 7.2 확실한 계약

### Document contract

모든 단계는 `ParsedDocument`에서 출발한다.

Cleaner는:

```text
ParsedDocument → ParsedDocument
```

형태를 유지한다.

### Ordering contract

`elements`의 order 정렬 계약을 깨지 않는다.

`route_all()`이 두 번 정렬하는 이유와 연결하여 설명한다.

### HTTP isolation

각 pipeline 단계는 HTTP를 모른다.

`ingest()`는 스크립트에서도 사용할 수 있어야 한다.

### Configuration

새 설정이 추가되면 다음 3곳을 고려한다.

```text
rag_pipeline/config.py
.env.example
tests/conftest.py:offline_config
```

### External API

Embedding / Indexing 등 외부 API를 사용하는 단계는 `contracts.md`의 다음 규약을 그대로 따른다.

* 예외를 외부로 던지지 않음
* 비용 카운터
* 호출 상한
* cache salt
* 공통 메커니즘 재사용 여부

이 내용은 새로운 추측이 아니라 기존 선행 규약이다.

### Exception

새 예외는 `_STATUS_BY_EXCEPTION`에 등록한다.

### Qdrant

Qdrant는 현재 설치 전이다.

따라서:

* 연결 실패 시 어떻게 할지
* 폴백할지
* 적재를 중단할지

등은 현재 설계되지 않은 사항으로 표시한다.

추측하여 규칙을 만들지 않는다.

---

## 7.3 승격 규칙

파일 마지막에 다음 문구를 넣는다.

```text
해당 단계가 실제 구현되면 이 섹션을
references/cleaner.md
references/splitter.md
references/embedding.md
references/indexing.md
등의 단계별 reference로 승격하고,
SKILL.md 라우팅 표에 해당 영역을 추가한다.
```

---

# 8. .claude/agents/code-reviewer.md

기존 위임 구조는 유지한다.

본문에 있는:

> 먼저 rag-code-review 스킬을 호출한다

라는 구조는 옳으므로 변경하지 않는다.

다만 description의 Loader 편향을 제거한다.

기존:

```text
새 로더·새 콘텐츠 핸들러를 추가한 직후
```

를 다음으로 변경한다.

```text
새 로더·핸들러·파이프라인 단계·예외를 추가한 직후
```

그 외:

* tools
* model: opus

는 그대로 유지한다.

---

# 9. 중요한 구분

이번 리팩터링에서 `rag-code-review`는 **Validation/Evaluation Skill이 아니다.**

구분은 다음과 같다.

```text
Code Review
→ 코드가 프로젝트의 설계/계약/규칙을 지키는가?

Validation / Evaluation
→ 실제 실행 결과가 Expected Result와 일치하는가?
```

이번 작업에서는 Validation Skill을 만들거나 합치지 않는다.

---

# 10. 작업 순서

다음 순서를 그대로 따른다.

1. `references/contracts.md` 작성
2. `references/loader.md` 작성
3. `references/backend.md` 작성
4. `references/next-stages.md` 작성
5. `SKILL.md` 전면 재작성
6. `.claude/agents/code-reviewer.md` description 수정
7. 기존 254줄과 새 구조를 대조하여 이관 누락 검사

---

# 11. 이관 누락 검사

이번 작업에서 가장 중요한 검증 중 하나다.

기존 `.claude/skills/rag-code-review/SKILL.md`의 254줄에 있는 모든 의미 있는 규칙/함정/근거/매핑이 새 파일 중 최소 하나에 존재하는지 확인한다.

특히 다음은 반드시 대조한다.

* Loader architecture boundary
* fallback chain
* OCR 규칙
* image preprocessing
* LlamaParse 규칙
* HWPX 규칙
* ZIP bomb
* magic bytes
* 새 포맷 등록 위치
* Loader "같이 읽을 곳" 매핑
* external API exception contract
* cost counter
* call limit
* cache salt
* exception → HTTP mapping
* coding convention
* test convention
* general correctness
* report format
* severity
* review attitude

**조용한 유실은 실패로 간주한다.**

---

# 12. 실제 검증

문서 변경이므로 pytest만으로 검증하지 말고 실제 Claude Code 환경에서 Skill routing이 예상대로 동작하는지 확인한다.

## 12.1 Loader routing test

현재 워킹트리에 다음 미커밋 변경이 있다.

```text
rag_pipeline/ingestion/loader/document/hwpx.py
tests/conftest.py
tests/ingestion/loader/document/test_hwpx*.py
```

이 상태에서 code-reviewer를 실행한다.

정상 기준:

* `references/contracts.md`를 읽는다.
* `references/loader.md`를 읽는다.
* `references/next-stages.md`는 읽지 않는다.
* HWPX 함정(쪽번호 / 레이아웃 표)이 리뷰에 반영된다.
* 기존 리포트 형식(심각도 / 파일:줄 / 검증 결과)을 유지한다.

---

## 12.2 Future-stage routing test

다음과 같이 질문한다.

```text
cleaner를 이렇게 짜려고 하는데 리뷰해줘.
```

실제 cleaner 코드를 만들지 않고 routing 동작만 확인한다.

정상 기준:

* `references/contracts.md`
* `references/next-stages.md`

가 적용된다.

Loader reference를 불필요하게 로드하지 않는다.

---

## 12.3 Regression check

리팩터링 이후 기존 SKILL.md의 규칙 중 하나라도 조용히 사라졌는지 확인한다.

7번 단계의 이관 누락 대조 결과를 근거로 판단한다.

---

## 12.4 코드 검증

다음 명령을 실행한다.

```powershell
.\.venv\Scripts\python.exe -m ruff check .
```

그리고:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

단,

```text
pytest -m integration
```

은 실행하지 않는다.

---

# 13. 최종 완료 기준

다음 조건을 모두 만족해야 작업 완료로 본다.

* `SKILL.md`가 Loader 전용 지식 없이 단계 중립적인 routing/procedure 중심으로 정리됨
* 공통 계약이 `contracts.md`로 승격됨
* Loader-specific 내용이 `loader.md`로 이동됨
* Backend 내용이 `backend.md`로 이동됨
* 미구현 단계는 `next-stages.md`의 최소 계약만 가짐
* 기존 Loader 규칙이 삭제되지 않음
* Reference 선택 규칙과 source-of-truth 우선순위가 명시됨
* code-reviewer agent description이 단계 중립적으로 변경됨
* Loader routing이 올바르게 동작함
* future-stage routing이 올바르게 동작함
* 기존 리포트 형식이 유지됨
* ruff 통과
* 기본 pytest 통과
* integration은 실행하지 않음
* 기존 254줄의 의미 있는 규칙이 새 구조 어딘가에 모두 존재함

작업 과정에서 기존 규칙의 의미를 변경하거나 삭제하기 전에 실제 코드 근거를 확인한다.
