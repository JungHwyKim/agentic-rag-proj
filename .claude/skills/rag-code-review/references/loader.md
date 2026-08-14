# 1단계 loader — 영역별 리뷰 기준

`rag_pipeline/ingestion/loader/**` 를 건드린 변경에 적용한다. 공통 규약은 `contracts.md` 에 있다.

## 같이 읽어야 하는 곳

변경된 파일만 읽으면 "잘 짰네요"밖에 못 쓴다. 그 파일이 지키기로 한 계약이 적힌 곳을 같이 읽는다.

| 바뀐 곳 | 같이 읽어야 하는 곳 |
|---|---|
| `loader/document/*.py` | `loader/document/base.py`, `loader/registry.py`, `schemas/document.py` |
| `loader/content/*.py` | `loader/content/router.py`, `loader/content/base.py` |
| `loader/detect.py` | `schemas/document.py` 의 `FileFormat`, `loader/registry.py` |
| 아무 핸들러나 | 그 핸들러를 덮는 `tests/` 파일 |

## 1. 아키텍처 경계 (최우선)

라우팅은 2단계다.

```
detect_format()  →  get_loader()  →  BaseDocumentLoader.load()
                                       ├ detect_regions()   포맷별 구현
                                       └ route_all()        ContentType → 핸들러
```

**문서 로더는 "어디에 무엇이 있는가"만 답한다.** `ContentRegion` 목록을 돌려주는 게 전부다.
**콘텐츠 핸들러만 "어떻게 텍스트로 만드는가"를 안다.**

이 경계가 있어야 "표를 LlamaParse 로 볼지 로컬로 볼지"를 `router.py` 와 설정만 고쳐서 바꿀 수
있다. 로더 안에서 OCR 을 부르기 시작하면 포맷 수 × 핸들러 수만큼 분기가 퍼지고, 비용 정책을
한 곳에서 못 바꾸게 된다.

### 잡아야 할 위반

- `loader/document/` 안에서 `image_ocr` / `image_vision` / `table_llamaparse` / `pytesseract` /
  `openai` / `llama_cloud` 를 import 하거나 호출한다
- 로더가 `Element` 를 직접 만들어 반환한다 (핸들러의 일이다)
- `detect_regions()` 말고 다른 추상 메서드가 늘었다 — 포맷 로더가 져야 할 책임이 늘어난 것이므로,
  정말 공통 뼈대에 들어갈 일인지 따진다
- `route()` 의 `ContentType` 분기가 아니라 파일 확장자·포맷 이름으로 핸들러를 고른다
- `pipeline.py` 나 로더가 HTTP/FastAPI 를 안다 (`ingest()` 는 스크립트에서도 쓴다)
- `backend/app/api/routes/documents.py` 가 파싱 로직을 직접 한다 → `backend.md` 참고

경계 위반은 발견하면 **반드시 Critical 로 올린다.** 지금 안 잡으면 다음 포맷을 추가할 때 복사된다.

## 2. 폴백 사슬

규약(예외 금지·경고·재계산 금지)은 `contracts.md` 2절에 있다. loader 의 구체적인 사슬은 둘이다.

```
표    : table_llamaparse (blob 있고 table_pdf_handler="llamaparse") → 실패/키없음 → table_native
이미지: classify() → SKIP(로고·아이콘 크기 필터) / OCR / Vision → Vision 실패 → classify 가
        이미 돌린 OCR 결과(decision.ocr) 재사용
```

- Vision 실패 후 OCR 을 **다시** 돌리는 변경은 이미 낸 비용을 버리는 것이다
- hybrid 이미지 판정(`loader/content/classify.py`)이 무료·로컬 Tesseract 를 **먼저** 돌리는
  순서가 깨졌는지 본다. 이 순서가 유료 Vision 호출을 아낀다. 200페이지 스캔본을 전부 Vision 에
  보내면 감당이 안 된다

## 3. 정렬 계약 — `route_all()` 이 두 번 정렬하는 이유

`loader/content/router.py:82` 가 regions 를 `order` 로, `:84` 가 elements 를 `order` 로
정렬한다. 리전 하나가 여러 Element 로 늘어나기 때문이다.

- **앞 정렬**은 핸들러 호출 순서를 결정론적으로 만든다 → 비용 카운터·경고 누적 순서가 재현된다
- **뒤 정렬**은 늘어난 Element 를 문서 순서로 다시 합친다

둘 중 하나를 지우는 변경은 되돌리게 한다.

## 4. 알려진 함정

리뷰 중 이 근처를 건드렸다면 특별히 본다. 아래 수치는 전부 실측이다.

### OCR 한국어

- `ocr_psm=6` 을 쓴다. 기본 3 은 한글 한 줄을 세로로 오인식한다
  (실측: "대한민국 서울특별시" → `[ 완 ro 소 mo`, PSM 6 으로 바꾸니 신뢰도 93%)
- `preserve_interword_spaces=1` 이 없으면 "주식회사"가 "주 식 회 사"로 쪼개져 임베딩·검색이
  망가진다
- **`image_to_data` 는 이 옵션을 무시**하므로 용도가 분리돼 있다 — **신뢰도는 `image_to_data`,
  최종 텍스트는 `image_to_string`.** 상자 간격으로 재조립하는 방법은 실측으로 불가능이 확인됐다
  (한글 음절 내부 간격 0.26~0.56 과 영문 단어 경계 0.48~0.59 가 겹쳐 임계값 하나로 못 가른다).
  **이 둘을 합치는 변경은 되돌리게 한다**

### 이미지 전처리

600px 미만일 때만 확대한다. 이미 큰 이미지를 늘리면 보간 흐림 때문에 인식률이 떨어진다
(실측: 900→1000px 미세 업스케일에서 원본 7토큰 → 전처리 후 0토큰).

### HWPX

- **쪽 번호**: `hp:tbl` 의 `hp:sz height` 를 믿으면 안 된다. 쪽을 넘기는 표는 이 값이 **시작
  쪽에 남은 공간으로 잘려서** 저장돼 있다(실측: 43행 표가 선언 34206 / 실제 98326). 실제 높이는
  `hp:cellSz height` 를 `hp:cellSpan rowSpan` 으로 나눠 복원해야 나온다. vertpos 만 보고 쪽을
  세면 쪽 넘긴 표 뒤로 쪽 번호가 전부 밀린다
- **레이아웃 표**: 한글은 제목·인용을 그릴 때 표를 쓴다. 내용 든 칸이 2개 이하이고 9칸 이하인
  표는 `_is_layout_box` 가 본문 문단으로 강등한다(`metadata["layout_table"]` 로 표시).
  그림이 든 상자는 캡션-그림 관계가 끊기므로 표로 남긴다
- **가로 쪽**: 한컴은 가로 쪽에서도 `hp:pagePr@width/height` 에 물리 용지(세로) 치수를 그대로
  넣고 방향은 `landscape` 속성으로만 표시한다. **이름과 반대로 `NARROWLY` 가 가로, `WIDELY` 가
  세로다**(실측: 시행령 개정안 안내서 가로쪽 판 — 두 방향 모두 width=59528 height=84188 인데
  WIDELY 섹션의 vertpos 가 69898 까지 올라가 71435=height-여백 에 육박한다. 가로였다면 46775 를
  넘을 수 없다). 가로 섹션의 본문 높이는 `width` 에서 얻어야 한다 — 안 가르면 53% 과대평가해
  쪽 넘긴 표를 한 쪽으로 세고 이후 쪽 번호가 밀린다
- 실제 한컴 파일 회귀 테스트(`tests/ingestion/loader/document/test_hwpx_real.py`)는
  `tests/fixtures/` 에 파일이 있을 때만 돌고 없으면 조용히 skip 한다

### LlamaParse SDK

`llama-parse` 와 `llama-cloud-services` 는 **둘 다 deprecated** 다
(`llama-cloud-services` 는 2026-05-01 유지보수 종료, 이미 지났다).
`llama-cloud>=1.0` 의 `Client.parsing.parse()` 를 쓴다.

### 레거시 포맷

`.hwp`/`.doc`/`.ppt` 는 감지만 하고 `UnsupportedLegacyLoader` 가 변환 안내와 함께 415 로
거부한다. 정확한 안내를 주려면 업로드 자체는 받아야 하므로 **`SUPPORTED_EXTENSIONS` 에 남아
있어야 한다** — 여기서 빼는 변경은 안내를 망친다.

### zip bomb

`loader/detect.py:MAX_UNCOMPRESSED_BYTES`(500MB). 새 zip 계열 포맷을 추가하면서 이 검사를
우회하지 않았는지 본다.

### 포맷 판별

확장자를 믿지 않고 매직바이트로 판별한다(zip/ole 는 내부 구조까지 봐야 갈린다).
확장자로 분기하는 코드가 새로 생겼으면 지적한다.

### PDF 스캔 판정

10자 미만을 스캔으로 본다. 20자로 올리면 제목만 있는 표지·간지가 전부 OCR 로 넘어가 비용을
낭비한다.

## 5. 새 포맷을 추가했다면 등록 4곳을 센다

1. `schemas/document.py` 의 `FileFormat`
2. `loader/detect.py` 의 `_ZIP_MARKERS` / `_OLE_MARKERS` / `_IMAGE_MAGICS` 중 해당하는 곳
3. `loader/document/` 에 `BaseDocumentLoader` 상속 클래스 (`detect_regions()` 만 구현)
4. `loader/registry.py` 의 `_FACTORIES` + `SUPPORTED_EXTENSIONS`

## 6. loader 고유 정확성

`contracts.md` 7절의 범주에 더해 이 도메인에서 실제로 나오는 것들:

- `fitz.Document` / PIL 이미지 / 임시파일을 안 닫는다
- `max_excel_rows` 같은 상한이 새 경로에 안 걸렸다
- 빈 페이지 · 0바이트 blob · 손상된 zip 처리 누락
