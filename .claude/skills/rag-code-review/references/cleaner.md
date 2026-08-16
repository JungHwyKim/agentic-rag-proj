# 2단계 cleaner — 영역별 리뷰 기준

`rag_pipeline/ingestion/cleaner/**` 를 건드린 변경에 적용한다. 공통 규약은 `contracts.md` 에 있다.

이 단계는 코드가 짧아서 리뷰가 쉬워 보이지만, **깨져도 예외가 안 나고 테스트도 안 터지는**
종류의 회귀가 모여 있다. 텍스트가 조용히 나빠지고 임베딩·검색에서 나중에 드러난다.

## 같이 읽어야 하는 곳

| 바뀐 곳 | 같이 읽어야 하는 곳 |
|---|---|
| `cleaner/characters.py` | `cleaner/__init__.py` (유일한 호출부), `tests/ingestion/cleaner/test_characters.py` |
| `cleaner/__init__.py` | `schemas/document.py` 의 `Element`·`DocumentMetadata`, `pipeline.py` 의 `ingest()` |
| 정리 규칙을 추가·변경 | `tests/ingestion/cleaner/test_real.py` — 실측 기대값이 상수로 박혀 있다 |
| `config.py` 의 `CleanerConfig` | `.env.example`, `tests/conftest.py` 의 `offline_config` |

## 1. 단계 계약 (최우선)

`clean()` 은 `ParsedDocument` 를 받아 `ParsedDocument` 를 돌려준다. 이 타입을 바꾸면 `ingest()`
의 순차 합성이 깨지고, 뒤에 splitter/embedding 을 붙일 때 단계를 떼었다 붙였다 할 수 없게 된다.

### 잡아야 할 위반

- `clean()` 이 다른 타입(`list[Element]`, 튜플, dict)을 돌려준다
- cleaner 가 HTTP·FastAPI·외부 API 를 안다. 이 단계는 순수 로컬이고 비용이 0 이다.
  외부 호출이 새로 들어오면 `contracts.md` 2·3절(예외 금지·카운터·상한·캐시)이 전부 적용되므로
  **Critical 로 올리고** 그 규약을 다 지켰는지 따로 센다
- 로더가 해야 할 일(파싱·리전 판별)이 cleaner 로 넘어왔다
- `config` 인자 없이도 동작해야 한다 (`clean(document)` → `CleanerConfig()` 기본값).
  이 기본값 경로가 사라지면 스크립트·노트북에서 쓰기 어려워진다
- 규칙을 끄는 설정(`remove_control_chars=False`)이 무시된다. 끄면 **입력을 그대로** 돌려줘야 한다

## 2. 원본 객체 반환 규약 — 이 영역에서 제일 잡기 어려운 회귀

세 층 모두가 **안 바뀌었으면 원본 객체를 그대로 돌려준다.**

```
strip_control_chars()  바뀐 게 없으면 인자로 받은 str 그대로
_clean_element()       text·rows 둘 다 그대로면 인자로 받은 Element 그대로
clean()                바뀐 요소가 0개면 인자로 받은 ParsedDocument 그대로
```

상위가 `is` 비교로 변경 여부를 판단하기 때문이다(`_clean_element` 의 `text is element.text`,
`clean()` 의 `before is not after`). 값 비교가 아니라 **동일성 비교**라는 게 핵심이다.

### 깨지는 방식

- `strip_control_chars` 가 항상 `"".join(...)` 결과를 돌려주도록 "단순화"한다 → 내용이 같아도
  새 `str` 객체라 `is` 가 False → 모든 요소가 바뀐 것으로 집계된다. **`cleaned_elements` 가
  요소 수만큼 부풀고**, 깨끗한 문서(HWPX 는 제어문자가 0개다)에서도 요소 746개가 통째로 복사된다
- `_clean_element` 가 무조건 `model_copy()` 한다 → 같은 결과
- `clean()` 이 무조건 `document.model_copy()` 한다 → 아무것도 안 고쳤는데 새 문서가 나온다

**예외가 안 나고 텍스트도 똑같아서 일반 테스트로는 안 잡힌다.** 이걸 잠그는 건
`test_real.py` 의 HWPX 0개 기대값뿐이다. 그 테스트는 `tests/fixtures/` 가 없으면 skip 되므로,
이 규약을 건드리는 변경은 **fixtures 없이도 통과한다.** 그래서 리뷰에서 봐야 한다.

`strip_control_chars` 의 마지막 줄(`return text if cleaned == text else cleaned`)에 달린
주석을 지우는 변경도 같이 지적한다 — 그 한 줄이 이 규약의 유일한 설명이다.

## 3. 표의 두 표현을 함께 고치는가

표 요소는 같은 내용을 `text`(마크다운)와 `metadata["rows"]`(2차원 셀 배열) 두 곳에 갖는다.
**한쪽만 고치면 같은 표의 두 표현이 어긋난다**(실측: AI 가이드 PDF 에서 `text` 202개 요소 ·
`rows` 셀 26개가 함께 고쳐졌다).

- 새 규칙을 `element.text` 에만 적용하고 `_clean_rows` 에 안 넣었는지 본다
- `_clean_rows` 가 `list[list[str]]` 이 아닌 `rows` 를 만나면 `None` 을 돌려주고 조용히 넘어간다.
  이 방어를 지우면 로더가 다른 모양으로 넣은 `rows` 에서 터진다
- `rows` 를 제자리에서 수정(mutate)하지 않는지 본다. 입력 문서를 변형하면 안 된다

## 4. 무엇을 지우고 무엇을 남기나

- 기준은 코드포인트 목록이 아니라 **유니코드 범주** — `Cc`(제어)와 `Cf`(서식).
  개별 코드포인트를 하드코딩한 목록으로 바꾸는 변경은 후퇴다. `Cf` 에는 U+200B(zero-width
  space)·U+FEFF(BOM)·U+00AD(soft hyphen) 처럼 눈에 안 보이면서 단어를 갈라놓는 문자가 들어간다
- **`_KEEP` 에서 `\n` 을 빼는 변경은 Critical 이다.** 실측 560개가 전부 표 요소에 있는 마크다운
  표의 행 구분자다. 지우면 표가 한 줄로 뭉개진다
- **`\r` 은 지우지 않고 `\n` 으로 바꾼다.** `\r` 을 `_STRIP_CATEGORIES` 에 맡겨 지우게 하는
  변경(치환 줄 삭제)은 앞뒤 줄을 서로 붙여버린다. 치환이 필터보다 **먼저** 와야 한다는 순서도 본다
- ZWJ(U+200D)가 `Cf` 라 이모지 결합이 쪼개지는 것은 **알고 감수한 것**이다. 지적하지 말고,
  되살리는 변경이 오면 `_KEEP` 추가가 맞는 방법인지만 본다

## 5. 요소를 지우는 규칙을 새로 넣었다면

지금 규칙은 요소 **안의 글자**만 고쳐서 `order` 에 새 빈틈을 만들지 않는다. 반복 머리말·꼬리말
제거처럼 **요소 자체를 지우는** 규칙이 들어오면 그 전제가 깨진다. 그때 볼 것:

- **`order` 를 재부여하지 않는지.** 남은 요소의 원본 `order` 를 유지해야 한다. `order` 의 계약상
  용도는 정렬 하나뿐이라 연속성이 필요 없고, `elements` 에는 이미 빈틈이 있다(핸들러가 요소를
  0개 만든 자리). 번호를 다시 매기면 앞뒤 단계가 참조하던 값이 조용히 달라진다
- 지운 개수가 `DocumentMetadata` 에 드러나는지. `cleaned_elements` 는 "고친 개수"라 의미가
  다르다 — 지운 개수를 같은 칸에 합산하면 두 값을 못 가른다
- 문서 전체가 비어버리는 입력(전부 머리말로 판정)에 대한 방어

## 6. 실측 근거가 있는가

이 단계의 규칙은 **전부 재서 넣었다.** 제어문자 제거 하나도 픽스처 5개(HWPX 4 · PDF 1)를 세어
"PDF 는 243개, HWPX 는 0개" 를 확인하고 넣은 것이다.

새 정리 규칙에 근거 수치가 없으면 **지적이 아니라 "확인 필요"로 올린다** — "어느 문서에서 몇 건
나왔습니까, `tests/fixtures/` 로 재봤습니까"라고 묻는다. 근거 없는 규칙은 멀쩡한 텍스트를 조용히
깎아먹고, 깎였다는 사실을 아무도 모른다.

`test_real.py` 의 기대 상수(`AI_GUIDE_PDF_CLEANED_ELEMENTS` 등)를 **테스트를 통과시키려고**
고친 흔적이 있으면 반드시 짚는다. 그 값은 사람이 세어 확인한 실측이라, 코드가 아니라 값이
바뀌었다면 파싱 결과가 달라졌다는 뜻이다.

## 7. 설정 3곳

새 설정은 세 곳이 맞아야 한다.

1. `rag_pipeline/config.py` 의 `CleanerConfig`
2. `.env.example`
3. `tests/conftest.py` — 외부 호출을 부르는 설정이면 `offline_config` 에서 꺼야 한다

`IngestConfig.cleaner` 는 `content` 와 같은 lazy 프로퍼티다. 테스트에서
`object.__setattr__(config, "_cleaner", CleanerConfig(...))` 로 덮어쓰므로, 이 프로퍼티를
`__init__` 에서 만드는 형태로 바꾸면 기존 테스트 패턴이 깨진다.
