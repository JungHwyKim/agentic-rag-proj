---
name: rag-code-review
description: agentic-rag-proj(한국어 문서 RAG 적재 파이프라인)의 코드를 리뷰한다. 아키텍처 경계 위반(로더가 OCR/Vision/LlamaParse 직접 호출), 폴백 사슬 규약 위반, 외부 API 비용 누수, 예외→HTTP 매핑 누락, 한국어 주석 컨벤션, 테스트 모킹 이음새를 중점 점검하고 심각도별 리포트를 낸다. 사용자가 "코드 리뷰", "리뷰해줘", "검토해줘", "PR 봐줘", "변경사항 점검", "커밋 전에 확인", "이거 괜찮아?" 같은 말을 하거나, rag_pipeline/ 또는 backend/ 의 코드를 다 짜고 나서 확인받고 싶어할 때 반드시 이 스킬을 쓴다. 새 로더·새 콘텐츠 핸들러·새 예외를 추가한 직후에도 등록 누락을 잡기 위해 쓴다.
---

# rag-code-review

이 저장소는 한국어 문서를 파싱해 통일 스키마(`ParsedDocument`)로 만드는 RAG 적재
파이프라인이다. 일반적인 파이썬 리뷰 상식만으로는 여기서 제일 비싼 실수를 못 잡는다.
이 프로젝트에서 실제로 아픈 실수는 세 종류다.

1. **설계 경계가 무너지는 변경** — 한 번 무너지면 되돌리는 비용이 크다
2. **외부 API 비용이 새는 변경** — 200페이지 스캔본 한 건으로 청구서가 튄다
3. **폴백이 조용히 죽는 변경** — 키 하나 없다고 적재 전체가 멈춘다

이 세 가지를 우선 보고, 그다음에 일반적인 정확성·간결성을 본다.

## 1단계 — 리뷰 범위를 먼저 확정한다

사용자가 범위를 안 줬으면 이 순서로 정한다.

1. git 저장소면 `git diff HEAD` / `git diff --staged` 로 변경분을 잡는다
2. git 저장소가 아니거나 diff가 비었으면, 대화에서 방금 만지던 파일을 범위로 잡는다
3. 그것도 없으면 사용자에게 "무엇을 리뷰할까요"라고 한 번 묻는다

범위를 정했으면 **변경된 파일만 읽지 말고, 그 파일이 지키기로 한 계약이 적힌 곳을
같이 읽는다.** 이 프로젝트에서는 보통 다음 짝이다.

| 바뀐 곳 | 같이 읽어야 하는 곳 |
|---|---|
| `loader/document/*.py` | `loader/document/base.py`, `loader/registry.py`, `schemas/document.py` |
| `loader/content/*.py` | `loader/content/router.py`, `loader/content/base.py` |
| `rag_pipeline/exceptions.py` | `backend/app/main.py` 의 `_STATUS_BY_EXCEPTION` |
| `rag_pipeline/config.py` | `.env.example`, `tests/conftest.py` 의 `offline_config` |
| 아무 핸들러나 | 그 핸들러를 덮는 `tests/` 파일 |

계약을 안 읽고 리뷰하면 "잘 짰네요"밖에 못 쓴다.

## 2단계 — 아키텍처 경계 (최우선)

진입점은 `rag_pipeline/ingestion/pipeline.py:ingest()` 하나이고, 라우팅은 2단계다.

```
detect_format()  →  get_loader()  →  BaseDocumentLoader.load()
                                       ├ detect_regions()   포맷별 구현
                                       └ route_all()        ContentType → 핸들러
```

**문서 로더는 "어디에 무엇이 있는가"만 답한다.** `ContentRegion` 목록을 돌려주는 게
전부다. **콘텐츠 핸들러만 "어떻게 텍스트로 만드는가"를 안다.**

이 경계가 있어야 "표를 LlamaParse로 볼지 로컬로 볼지"를 `router.py` 와 설정만
고쳐서 바꿀 수 있다. 로더 안에서 OCR을 부르기 시작하면 포맷 수 × 핸들러 수만큼
분기가 퍼지고, 비용 정책을 한 곳에서 못 바꾸게 된다.

### 잡아야 할 위반

- `loader/document/` 안에서 `image_ocr` / `image_vision` / `table_llamaparse` /
  `pytesseract` / `openai` / `llama_cloud` 를 import하거나 호출한다
- 로더가 `Element` 를 직접 만들어 반환한다 (핸들러의 일이다)
- `detect_regions()` 말고 다른 추상 메서드가 늘었다 — 포맷 로더가 져야 할 책임이
  늘어난 것이므로, 정말 공통 뼈대에 들어갈 일인지 따진다
- `route()` 의 `ContentType` 분기가 아니라 파일 확장자·포맷 이름으로 핸들러를 고른다
- `pipeline.py` 나 로더가 HTTP/FastAPI 를 안다 (`ingest()` 는 스크립트에서도 쓴다)
- `backend/app/api/routes/documents.py` 가 파싱 로직을 직접 한다 (업로드 수신 /
  확장자 1차 검증 / 임시저장 / 정리까지만이 이 파일의 몫이다)

경계 위반은 발견하면 **반드시 Critical 로 올린다.** 지금 안 잡으면 다음 포맷을
추가할 때 복사된다.

## 3단계 — 폴백 사슬과 비용

### 외부 API 핸들러의 규약: 예외를 던지지 않는다

```
표    : table_llamaparse (blob 있고 table_pdf_handler="llamaparse") → 실패/키없음 → table_native
이미지: classify() → SKIP(로고·아이콘) / OCR / Vision → Vision 실패 → classify가 이미 돌린 OCR 재사용
```

핸들러가 실패하면 **예외 대신 빈 리스트를 반환**해야 라우터의 폴백이 돈다.
`table_llamaparse.handle()` 이 참고 구현이다 — `except Exception:  # noqa: BLE001`
로 삼키고, `ctx.warn()` 으로 사용자에게 알린 뒤 `[]` 를 돌려준다.

체크할 것:

- 새 외부 API 호출부가 예외를 밖으로 던진다 → **Critical**. 키 없는 환경에서
  적재 전체가 죽고, 기본 테스트도 깨진다
- `except Exception` 을 썼는데 `# noqa: BLE001` 과 이유 주석이 없다
- 실패했는데 `ctx.warn()` 을 안 한다 → 사용자는 왜 표가 엉성한지 알 길이 없다
- 폴백으로 내려갔는데 이미 계산한 결과를 버리고 다시 계산한다
  (Vision 실패 시 `decision.ocr` 재사용이 그 예다)

### 비용 카운터와 캐시

`LoadContext` 가 `vision_calls` / `llamaparse_calls` / `ocr_applied` / `warnings` 를
누적하고 `DocumentMetadata` 로 노출한다. 이게 유일한 비용 가시성이다.

- 외부 API를 실제로 호출했는데 해당 카운터를 안 올린다 → **Critical**
- 호출이 **실패**했는데 카운터를 올린다 (과금 안 된 걸 셌다)
- `max_vision_calls_per_doc` 상한 검사를 우회한다
- `ResultCache` 를 안 거치고 외부 API를 부른다 → 재업로드마다 재과금
- 캐시 키 salt에 결과를 바꾸는 설정(티어·버전·모델명)이 빠졌다 → 설정을 바꿔도
  옛 결과가 나온다. `content_hash(blob, salt=f"llamaparse:{tier}:{version}")` 형태를 지킨다
- hybrid 이미지 판정에서 무료·로컬 Tesseract를 먼저 돌리는 순서가 깨졌다

## 4단계 — 이 저장소의 알려진 함정

리뷰 중 이 근처를 건드렸다면 특별히 본다.

- **OCR 한국어**: `ocr_psm=6` 이어야 한다(기본 3은 한글 한 줄을 세로로 오인식).
  `preserve_interword_spaces=1` 이 없으면 "주식회사"가 "주 식 회 사"로 쪼개져
  임베딩·검색이 망가진다. **`image_to_data` 는 이 옵션을 무시하므로 신뢰도는
  `image_to_data`, 최종 텍스트는 `image_to_string`** 으로 용도가 분리돼 있다.
  이 둘을 합치는 변경은 되돌리게 한다
- **이미지 전처리**: 600px 미만일 때만 확대한다. 큰 이미지를 늘리면 보간 흐림으로
  인식률이 떨어진다(실측: 원본 7토큰 → 미세 업스케일 후 0토큰)
- **LlamaParse SDK**: `llama-parse` / `llama-cloud-services` 는 둘 다 deprecated다.
  `llama-cloud>=1.0` 의 `Client.parsing.parse()` 를 쓴다
- **레거시 포맷**: `.hwp`/`.doc`/`.ppt` 는 감지만 하고 `UnsupportedLegacyLoader` 가
  415로 거부한다. 정확한 변환 안내를 주려면 업로드는 받아야 하므로
  `SUPPORTED_EXTENSIONS` 에 남아 있어야 한다 — 여기서 빼는 변경은 안내를 망친다
- **zip bomb**: `loader/detect.py:MAX_UNCOMPRESSED_BYTES`(500MB). 새 zip 계열
  포맷을 추가하면서 이 검사를 우회하지 않았는지 본다
- **포맷 판별**: 확장자를 믿지 않고 매직바이트로 판별한다. 확장자로 분기하는
  코드가 새로 생겼으면 지적한다

### 새 포맷/새 예외를 추가했다면 등록 누락을 센다

새 포맷 4곳: `schemas/document.py:FileFormat` → `loader/detect.py` 의 시그니처 표
→ `loader/document/` 의 `BaseDocumentLoader` 상속 클래스 → `loader/registry.py` 의
`_FACTORIES` + `SUPPORTED_EXTENSIONS`.

새 예외 2곳: `rag_pipeline/exceptions.py` → `backend/app/main.py` 의
`_STATUS_BY_EXCEPTION`. **매핑을 빼먹으면 사용자가 500을 본다.** 매핑 리스트는
`isinstance` 로 위에서부터 훑으므로, 하위 클래스를 상위 클래스보다 **먼저** 넣어야
한다 — 순서가 틀리면 조용히 엉뚱한 상태코드가 나간다.

## 5단계 — 컨벤션과 테스트

컨벤션 위반은 대부분 Minor지만, 이 코드베이스는 전체가 한 형태라 어긋나면 눈에 띈다.

- **모든 class/def 위에 한 줄 한국어 설명 주석**. 예외 없다
- 모듈 docstring에는 *왜* 그렇게 했는지를 적는다. 실측 수치는 근거와 함께
- 모든 모듈 상단에 `from __future__ import annotations`
- ruff: line-length 100, `E,F,I,UP,B,SIM`, E501 무시

테스트는 다음을 본다.

- **기본 실행은 API 키 없이 전부 통과해야 한다.** 새 테스트가 키를 요구하면
  `@pytest.mark.integration` 을 달았는지 확인한다. 안 달면 CI가 깨지고,
  달았는데 실제 과금 호출이면 그것도 리포트에 적는다
- **모킹 이음새는 각 API 핸들러의 모듈 레벨 `_make_client()`** 다.
  `mocker.patch.object(image_vision, "_make_client", return_value=fake)` 형태로 쓴다.
  **이 함수를 인라인화하거나 클래스 메서드로 옮기면 외부 API 테스트가 전부
  깨진다** → 그런 변경은 Critical
- `IngestConfig.content` 는 lazy 프로퍼티라 테스트에서
  `object.__setattr__(config, "_content", ContentRoutingConfig(...))` 로 덮어쓴다
- 새 핸들러·새 로더에 **폴백 경로 테스트**가 있는지 본다. 성공 경로만 있으면
  폴백이 죽어도 아무도 모른다. 이 저장소에서 제일 자주 빠지는 테스트다
- 샘플 파일은 `tests/conftest.py` 가 코드로 생성한다. 바이너리를 커밋하지 않는 방침이므로
  새 픽스처도 코드 생성이어야 한다

## 6단계 — 일반 정확성

여기까지 왔으면 평범한 버그도 본다. 다만 **추측으로 쓰지 않는다.** 각 지적마다
"이런 입력이면 이렇게 깨진다"를 한 줄로 댈 수 있어야 리포트에 올린다.

이 도메인에서 실제로 나오는 것들:

- `order` 로 정렬하는 계약을 깬 변경 (`route_all()` 이 두 번 정렬하는 이유가 있다)
- 손상된 파일·빈 페이지·0바이트 blob 처리 누락
- 인코딩: 한국어 파일명, `errors=` 지정 없는 `decode()`
- 리소스 누수: `fitz.Document` / 이미지 / 임시파일을 안 닫는다
- 거대 입력: `max_excel_rows` 같은 상한이 새 경로에 안 걸렸다
- 경로 처리: `Path` 대신 문자열 결합, Windows 경로 가정

## 검증 — 지적하기 전에 확인한다

리포트에 올리기 전에 **각 지적을 한 번씩 반증해 본다.** "이 코드가 실제로 그렇게
동작하나? 내가 못 본 방어 코드가 위에 있지 않나?" 관련 파일을 다시 읽어 확인한다.

가능하면 실제로 돌려본다. 셋 다 몇 초 안 걸린다.

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest -q
```

`-m integration` 은 **절대 자동으로 돌리지 않는다.** LlamaParse/OpenAI에 실제
과금이 발생한다. 필요하면 사용자에게 허락을 구한다.

확인 못 한 지적은 버리거나, 확실한 것과 섞이지 않게 "확인 필요"로 따로 묶는다.
틀린 지적 하나가 맞는 지적 다섯 개의 신뢰를 깎는다.

## 리포트 형식

한국어로 쓴다. 심각도 순으로, 없는 섹션은 통째로 생략한다.

```markdown
## 리뷰 요약
[2~3문장. 무엇을 봤고, 제일 중요한 결론이 뭔지.
 문제가 없으면 "문제 없음"이라고 분명히 쓴다]

리뷰 범위: [파일 목록 또는 diff 범위]
검증: ruff [통과/실패] · pytest [125 passed 등] · 빌드 [통과/실패]

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
```

심각도 기준:

- **Critical** — 아키텍처 경계 위반, 폴백을 죽이는 변경, 비용 누수, 기본 테스트를
  깨뜨리는 변경, 데이터를 잃는 버그
- **Major** — 특정 입력에서 잘못된 결과가 나오는 버그, 등록 누락, 폴백 테스트 부재
- **Minor** — 컨벤션, 네이밍, 중복, 읽기 어려운 코드

## 리뷰 태도

**모든 지적에 근거를 댄다.** `파일:줄` 을 달고, 왜 문제인지를 이 프로젝트의 맥락으로
설명한다. "베스트 프랙티스"라는 말로 때우지 않는다.

**취향을 버그로 포장하지 않는다.** 다르게 짤 수 있다는 것과 틀렸다는 것은 다르다.
취향 차이는 Minor에 넣거나 아예 쓰지 않는다.

**깨끗하면 깨끗하다고 쓴다.** 억지로 지적을 만들어내면 다음 리뷰부터 아무도 안 읽는다.
Critical이 없으면 없다고 쓰는 게 정직한 리뷰다.

**고칠 수 있는 형태로 쓴다.** "에러 처리가 부족합니다"가 아니라 어떤 예외를 어디서
어떻게 잡으라고 쓴다. 짧은 diff를 붙일 수 있으면 붙인다.
