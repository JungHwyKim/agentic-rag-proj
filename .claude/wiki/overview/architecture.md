# 시스템의 모양

**여기는 지도다 — 계약을 옮겨 적지 않는다.** 각 항목의 «되돌리면 조용히 깨지는 것» 은
`.claude/wiki/knowledge/` 의 해당 문서가 갖고, 실측 근거는 각 단계 `__init__.py` docstring 과
`CLAUDE.md` 가 갖는다.

## 로더 안에서 라우팅이 두 번 일어난다

파이프라인 단계 수와는 무관한 이야기다.

```
detect_format()            매직바이트로 실제 포맷 판별 (확장자 불신)
  ↓                        loader/detect.py — zip/ole 는 내부 구조까지 봐야 갈린다
get_loader()               1차 라우팅: FileFormat → 문서 로더
  ↓                        loader/registry.py 의 _FACTORIES 분기표
BaseDocumentLoader.load()  템플릿 메서드 (loader/document/base.py)
  ├ detect_regions()       ← 포맷별 로더가 구현하는 유일한 추상 메서드
  ├ route_all()            2차 라우팅: ContentType → 콘텐츠 핸들러
  └ DocumentMetadata 조립  LoadContext 의 비용 카운터·경고를 옮겨 담는다
```

자세히 → `.claude/wiki/knowledge/ingestion/loader.md`

## 지켜야 할 경계 셋

| 누가 | 무엇만 안다 | 어기면 |
|---|---|---|
| **문서 로더** (`loader/document/*`) | «어디에 무엇이 있는가» — `ContentRegion` 목록 | 로더가 OCR/Vision/LlamaParse 를 직접 부르면 설계가 무너진다 |
| **콘텐츠 핸들러** (`loader/content/*`) | «어떻게 텍스트로 만드는가» | 표를 LlamaParse 로 볼지·이미지를 Vision 으로 볼지는 `content/router.py` 와 설정으로만 바뀐다 |
| **`rag_pipeline/`** | HTTP 를 모른다 | `backend` 가 부르는 쪽이다 |

모든 출력은 `ParsedDocument(metadata, elements)` 로 수렴한다(`schemas/document.py`).

자세히 → `.claude/wiki/knowledge/contracts.md`

## 폴백 사슬 — 외부 API 실패가 적재를 멈추면 안 된다

외부 API 핸들러는 **예외를 던지지 않고 빈 리스트를 반환**하는 것이 규약이다. 이 규약을 깨면
라우터의 폴백이 동작하지 않는다.

- **표** — `table_llamaparse` → 실패·키없음이면 `table_native`
- **표 승급** — `hybrid` 면 빈 칸에 벡터 드로잉이 있는 표만 `table_vision` 으로, 실패하면 `table_native`
- **이미지** — `classify()` → SKIP / OCR / Vision → Vision 실패 시 classify 가 이미 돌린 OCR 재사용

**기본값은 무료 경로다**(`table_pdf_handler="pymupdf"`). 왜 유료 경로를 껐는지는
`.claude/wiki/plan_log/표-경로.md`, 계약은 `.claude/wiki/knowledge/ingestion/loader.md` 2절.

## 파이프라인 단계

| 단계 | 진입점 | 타입 | 지식 문서 |
|---|---|---|---|
| cleaner | `clean()` | `ParsedDocument` → `ParsedDocument` | `.claude/wiki/knowledge/ingestion/cleaner.md` |
| splitter | `split()` | `ParsedDocument` → `list[Chunk]` | `.claude/wiki/knowledge/ingestion/splitter.md` |
| embedding | `get_embedder()` | `list[str]` → `list[list[float]]` | `.claude/wiki/knowledge/ingestion/embedding.md` |
| indexing | `get_store()` | 부수 효과 | `.claude/wiki/knowledge/ingestion/indexing.md` |

**splitter·embedding·indexing 셋 다 `ingest()` 밖에 있다.** 누락이 아니라 결정이다 —
`ingest()` 의 반환이 곧 backend 의 `response_model` 이라 청크·벡터를 내보내는 것은 각 단계가
아니라 **API 결정**이고, 더 중요하게는 **기본 `pytest` 가 임베딩을 안 부르는 상태**를 그 배치가
지탱한다. 전 구간은 `pipeline.py:index_document()` 다.

## 질의 경로

`answer_question()` 이 인자로 받는 갈래가 여섯이고 **붙는 자리가 다 다르다.**

| 갈래 | 붙는 자리 | 스위치 |
|---|---|---|
| `cache` | `retrieve()` **앞** | `ANSWER_CACHE_MODE` |
| `bm25` | `retrieve()` 안의 한 갈래 | 설정 없음 — 안 넘기면 꺼짐 |
| `rewriter` | `retrieve()` **안**(1차 검색 뒤·합류 앞) | `REWRITE_MODEL` |
| `reranker` | `retrieve()` 안, RRF 뒤·절단 앞 | `RERANK_PROVIDER`·`RERANK_MODEL` |
| `evaluator` | `retrieve()` 와 `generate_answer()` **사이** | `EVALUATOR_MODEL` |
| 인용 검사 | 생성 뒤 — **인자가 아니다** | 없음(고를 축이 없다) |

**«고를 축이 실재할 때만 설정이 생긴다»** 가 이 표를 읽는 법이고, 인용 검사가 그 **반대쪽
사례**다. 자세히 → `.claude/wiki/knowledge/query/README.md`

## backend ↔ rag_pipeline

- 화면은 **서버 렌더**다(Jinja2 + 벤더링한 htmx, Node 툴체인 없음). `routes/pages.py` 가 화면을,
  `routes/query.py` 가 HTMX 가 부를 **HTML 조각**을 돌려준다 — JSON 을 주고 브라우저에서 다시
  조립하면 템플릿이 두 벌이 된다. **그래서 프런트엔드 디렉터리가 없다**
- 로더는 동기·CPU 바운드라 `run_in_threadpool` 로 넘긴다
- `main.py` 의 `_STATUS_BY_EXCEPTION` 이 `RagPipelineError` 를 HTTP 상태코드로 매핑한다.
  새 예외를 만들면 여기 추가해야 500 이 안 난다
- 설정도 둘로 나뉜다 — `backend/app/core/config.py`(웹) vs `rag_pipeline/config.py`(파싱).
  둘 다 `PROJECT_ROOT/.env` 를 읽는다

자세히 → `.claude/wiki/knowledge/backend.md`
