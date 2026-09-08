"""적재 파이프라인 1단계 — 파일 하나를 `ParsedDocument` 로 만든다.

바이트를 받아 `ParsedDocument(metadata, elements)` 를 돌려준다(`schemas/document.py`).
뒤 단계들과 달리 입력이 파이프라인 밖에서 오므로, **확장자도 파일이 자기에 대해 말하는 것도
믿지 않는 것**이 이 단계의 출발점이다.

라우팅이 이 안에서 두 번 일어난다. 파이프라인 단계 수와는 무관한 이야기다.

    detect_format()            매직바이트로 실제 포맷 판별 (detect.py)
      ↓
    get_loader()               1차 라우팅: FileFormat -> 문서 로더 (registry.py `_FACTORIES`)
      ↓
    BaseDocumentLoader.load()  템플릿 메서드 (document/base.py)
      ├ detect_regions()       ← 포맷별 로더가 구현하는 유일한 추상 메서드
      ├ route_all()            2차 라우팅: ContentType -> 콘텐츠 핸들러 (content/router.py)
      └ DocumentMetadata 조립  LoadContext 의 비용 카운터·경고를 옮겨 담는다

## 두 라우팅을 가른 것이 이 단계의 유일한 구조 결정이다

**문서 로더**(`document/*`)는 *"어디에 무엇이 있는가"* 만 `ContentRegion` 목록으로 답한다.
**콘텐츠 핸들러**(`content/*`)만 *"어떻게 텍스트로 만드는가"* 를 안다. 그래서 표를
LlamaParse 로 볼지 이미지를 Vision 으로 볼지가 `content/router.py` 와 설정 **한 곳**에서만
바뀐다.

되돌리면 — 로더 안에서 OCR·Vision·LlamaParse 를 직접 부르기 시작하면 — 포맷별 로더가
여덟이라 같은 결정이 여덟 벌이 되고, 그때부터 *"이미지를 어떻게 읽나"* 에 답이 여덟 개다.
설정으로 끄는 자리도 여덟이 되는데 **그중 하나를 빠뜨려도 에러가 안 난다**(유료 호출이
조용히 나갈 뿐이다).

## 외부 API 핸들러는 예외를 안 던지고 빈 결과를 준다 — 그런데 OCR 은 이유가 다르다

contracts 2절의 규약이고, 그것이 폴백 사슬의 **전제**다. 핸들러가 예외를 던지면 라우터가
내려갈 자리를 못 잡아 사슬이 통째로 죽는다.

**`content/image_ocr.py` 만 같은 모양인데 이유가 다르다.** 그 규약은 *"내려갈 층이 있어서"*
성립하는데(LlamaParse -> PyMuPDF · Vision -> OCR) OCR 은 **사슬의 맨 아래**라 아래에 층이
없다. 거기서 삼키는 것은 폴백을 살리려는 것이 아니라 **이미지 하나가 문서 전체 적재를
멈추는 것을 막으려는 것**이다. 규약 위반이 아니라 계약 밖이고, 그 파일이 근거를 적어 뒀다 —
**«같은 코드 모양은 같은 이유»로 읽으면 틀린다.**

## 비용 카운터가 여기 있다 — `indexing` 이 «없는 이유» 를 적은 것의 반대쪽이다

contracts 3절의 카운터·호출 상한·캐시는 **유료 외부 API** 규약이라, 로컬 파일 모드뿐인
`indexing` 은 *"형식만 베끼면 아무것도 안 지키는 카운터가 남는다"* 며 일부러 안 뒀다.
적재 경로에서 유료 호출이 **실제로 일어나는 곳은 이 단계 하나**이므로, 그 규약이 온전히
사는 자리도 여기다 — `LoadContext` 가 `vision_calls`·`llamaparse_calls`·`ocr_applied`·
`warnings` 를 모아 `DocumentMetadata` 로 내보내고, `max_vision_calls_per_doc` 상한과
`ResultCache`(내용 해시 키)가 재업로드 재과금을 막는다.

## 무료 OCR 만 병렬로 돈다

전면 스캔 PDF 에서 Tesseract 가 적재 시간의 약 90%다. `pytesseract` 는 subprocess 라 대기 중
GIL 을 놓아 스레드가 듣는다. **가르는 축은 유료/무료**이고, 유료 Vision·LlamaParse 를 직렬로
남긴 이유(상한 검사와 증가가 갈라져 있어 병렬로 올리면 **키 없는 기본 pytest 가 못 잡는
과금**이 난다)와 그 전제 넷은 `content/router.py` 가 실측과 함께 갖는다.

## 이 단계의 규약이 어디 있나

  detect.py            확장자를 못 믿는 이유(docx/pptx/xlsx/hwpx 가 전부 ZIP · hwp/doc/ppt/xls
                       가 전부 OLE 라 겉모습이 같다) · zip bomb 상한 `MAX_UNCOMPRESSED_BYTES`
  registry.py          1차 라우팅 분기표 `_FACTORIES` · `SUPPORTED_EXTENSIONS`
                       (.hwp/.doc/.ppt 가 거기 있는 이유 — 정확한 안내를 주려면 업로드 자체는
                       받아야 한다)
  document/base.py     템플릿 메서드 — 포맷 로더가 `detect_regions()` 하나만 구현하면 되는 이유
  document/headings.py 제목 승급을 로더에서 하는 이유(경계를 내놓는 로더가 둘뿐이었다) ·
                       스타일이 아니라 글자 크기를 쓰는 이유 · 판정 규칙 셋(문서별 최빈값 /
                       배율 아닌 부등호 / `max` 가 아니라 `min`) · 기각한 신호(마커·볼드) ·
                       `detect_regions()` 안이 아니라 그 뒤에서 도는 이유(기준선이 흔들린다)
  document/pdf.py      *"PDF 에는 표 구조가 없다"* 에서 나온 것 전부 — 병합 셀
                       `_fill_merged_cells()` · 격자 구멍 `_grid_holes()` · 조각 표 되붙이기
                       `_merge_split_tables()` · 쪽 경계 `_merge_across_pages()` · 갈린 셀 잇기
                       `_join_split_cells()` · 표시와 배경 음영을 가르는 `_MARK_AREA_RATIO`
  document/hwpx.py     OWPML 네임스페이스를 와일드카드로 찾는 이유 · 표를 만나면 재귀를 멈추는
                       이유 · 그림이 BinData 참조인 구조 · vertpos 로 쪽을 세는 근거와 쪽 넘긴
                       표 보정 · 레이아웃 상자 강등 `_is_layout_box` · 병합 셀 격자 복원
                       `_table_rows()`(세로는 되풀이, 가로는 빈 칸) · 가로 쪽에서 본문 높이를
                       `width` 에서 얻는 이유(`_LANDSCAPE` — 이름과 반대로 `NARROWLY` 가 가로)
  document/text.py     cp949 자동 감지가 필수인 이유
  document/hwp.py      구형 바이너리를 파싱 대신 거부하는 이유와 변환 자리
  document/{docx,pptx,excel,image}.py  각 라이브러리가 원본 순서를 안 지켜 직접 순회하는 이유 등
  content/base.py      2차 라우팅의 어휘(`ContentType`·`ContentRegion`)와 `LoadContext` —
                       비용 카운터·경고 · `child()`/`absorb()` 와 그 안의 **유료 카운터 가드**
                       (어기면 조용히 과금되는 대신 `RuntimeError` 로 터진다) · `mark_symbols`
  content/router.py    2차 라우팅의 유일한 결정 지점 · **무료만 병렬**의 근거와 전제 넷
                       (판정이 병렬 구간 안에 있어야 하는 이유 · 디코드한 이미지를 밖으로 안
                       내보내는 이유 · `e.order` 재정렬 · 경고를 자식 컨텍스트로 모으는 이유)
  content/classify.py  무료 Tesseract 를 먼저 돌려 유료 Vision 을 아끼는 판정
  content/image_ocr.py OCR 이 예외를 삼키는 이유가 contracts 2절이 **아니라는** 것
  content/image_vision.py  보낼 이미지 포맷을 `blob_kind` 가 아니라 바이트에서 읽는 이유
                       (`_normalize_for_vision()`) · 지원 포맷을 재인코딩하지 않는 이유
  content/table_vision.py  승급이 한 번 닫혔다 다시 열린 경위 · 표를 다시 읽는 것이 아니라
                       빈 칸만 메우는 이유 · 문서당이 아니라 **서명당 1회**인 것
  content/table_llamaparse.py  응답의 `markdown` 이 문자열이 아니라 `pages` 를 담은 모델인 것
  content/{table_native,text_native}.py  외부 호출이 없는 바닥 경로

## 새 포맷을 더할 때 등록이 넷이다

`FileFormat`(`schemas/document.py`) · `detect.py` 의 시그니처 표 · `document/` 의 로더 ·
`registry.py` 의 `_FACTORIES` 와 `SUPPORTED_EXTENSIONS`. **하나만 빠뜨려도 조용히 안 걸린다** —
리뷰가 그 넷을 세는 근거는 `.claude/wiki/knowledge/ingestion/loader.md` 5절이다.

포맷별 실측 함정(HWPX 쪽 수 부풀림 · PDF 낱말 공백 유실 · OCR 한국어 옵션 · Vision 이미지
포맷 등)은 CLAUDE.md 「알고 있어야 할 함정」과 위 knowledge 문서 4절이 갖는다. **여기 옮겨
적지 않는다** — 같은 사실이 세 곳에 있으면 세 곳에서 각자 낡는다. 이 docstring 이 답하는
것은 *"어느 규약이 어느 파일에 사는가"* 하나다.
"""

from __future__ import annotations

from rag_pipeline.ingestion.loader.detect import detect_format
from rag_pipeline.ingestion.loader.registry import get_loader

__all__ = ["detect_format", "get_loader"]
