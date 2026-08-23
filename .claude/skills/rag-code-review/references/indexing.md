# 5단계 indexing — 영역별 리뷰 기준

청크와 벡터를 저장소에 넣고 찾는다. `rag_pipeline/ingestion/indexing/` 아래가 전부이고
진입점은 `get_store()` 하나, 전 구간 흐름은 `pipeline.py:index_document()` 다.

## 같이 읽어야 하는 곳

| 무엇을 확인하나 | 어디 |
|---|---|
| 전 단계 공통 계약 | `contracts.md` |
| 들어오는 것 | `rag_pipeline/schemas/chunk.py` (`Chunk`) + 벡터 |
| 나가는 것 | `rag_pipeline/schemas/vector.py` (`VectorRecord`·`SearchResult`) |
| 앞 단계가 실제로 내놓는 것 | `splitter.md` · `embedding.md` |
| 예외 매핑 | `backend.md` |
| 왜 이 설계인가 | `plan_log/indexing.md` |

## 1. 제일 자주 나올 오해 — `contracts.md` 2·3절 위반으로 읽는 것

**이 단계도 실패하면 예외를 던진다. embedding 과 같은 이유이고 의도된 것이다.**
`except Exception` 으로 삼키고 빈 결과를 돌려주라고 지적하지 않는다. 그 규약은 내려갈 층이
있어서 성립하는데(LlamaParse→PyMuPDF, Vision→OCR) 저장에는 없다 — 저장 못 한 청크는
영원히 검색되지 않는데 아무도 모른다.

**비용 카운터·호출 상한·캐시 salt(3절)도 여기엔 없고, 그게 맞다.** 로컬 파일 모드라
호출당 과금이 없다. `ResultCache` 를 안 쓰는 것도 같은 이유이고 추가로 qdrant 자체가
저장소라 캐시를 얹으면 진실이 두 곳이 된다. **관리형 qdrant 로 바뀌면 그때 셋 다 필요해진다** —
그 변경을 보면 카운터를 요구한다.

그래서 반대 방향이 지적 대상이다. **예외가 아니라 조용히 빠져나가는 경로가 있으면 올린다.**

## 2. 되돌리면 깨지는 것

- **`to_records()` 의 길이 검사.** embedding 이 `len(out)==len(in)` 을 계약으로 세웠어도
  소비하는 쪽에서 확인하지 않으면 계약이 아니다. 여기가 `zip` 이 일어나는 유일한 자리다
- **`document_id` 는 파일명에서 뽑는다** (`document_id_for()`). `Chunk.document_key` 의
  기본값인 파싱 결과 해시로 바꾸자는 제안은 기각이다 — 문서를 고치면 키가 바뀌어
  `delete_by_document_id()` 가 옛 청크를 못 찾고 **문서가 통째로 두 벌 남는다**
- **`unicodedata.normalize("NFC", ...)`.** 지우면 macOS(NFD)와 Windows(NFC)의 같은 한글
  파일명이 다른 키가 되어 같은 사고가 난다
- **`VectorRecord.id` 는 `uuid5(chunk_id)` 다.** "`chunk_id` 를 그대로 쓰자"는 제안은
  실행되지 않는다 — qdrant 는 point ID 로 uint 또는 UUID 만 받고 문자열은 `ValueError` 다
  (실측). `_NAMESPACE` 상수를 바꾸는 변경도 막는다. 바꾸면 기존 색인 전체가 새 point 가 되어
  재색인이 갱신이 아니라 중복이 된다
- **컬렉션 이름에 모델·차원이 들어간다** (`collection_name_for()`). 빼고 차원만 검사하자는
  제안은 기각이다 — `-small` 기본 1536 과 `dimensions=1536` 으로 줄인 `-large` 는 크기가
  같고 벡터 공간이 다르다. 에러 없이 검색 품질만 서서히 나빠진다
- **거리는 Dot 고정이고 설정이 아니다.** 근거는 벡터가 단위 길이라는 실측(0.9998~1.0005)이다.
  정규화하지 않는 임베딩이 붙으면 그때 함께 바꾼다
- **`store.close()`.** qdrant 로컬 모드는 저장 디렉터리에 배타 잠금을 건다. `finally` 에서
  빼면 두 번째 색인이 `RuntimeError` 로 죽는다 — `index_document()` 의 `try/finally` 를 본다
- **`index_document()` 에서 레코드가 0개여도 `delete_by_document_id()` 는 부른다.**
  건너뛰면 문서가 빈 문서로 바뀌었을 때 옛 청크가 통째로 남는다

## 3. `ingest()` 와의 관계

**`ingest()` 는 split·embed·index 를 부르지 않는다. 누락이 아니라 결정이다.**
"파이프라인이 반쪽이다", "`ingest()` 에 합쳐야 한다"는 지적을 올리지 않는다.

이유 둘이고 뒤가 더 중요하다.

1. `ingest()` 의 반환이 곧 `backend` 의 `response_model` 이라 바꾸는 순간 API 결정이 된다
2. **기본 `pytest` 가 `ingest()` 만 부르므로 임베딩이 호출되지 않는다.** 그래서 "키가 없으면
   예외"라는 embedding 설계가 기본 테스트를 안 깬다. 합치면 그 지탱이 무너진다

전 구간이 필요하면 `index_document()` 를 쓴다.

**`IndexResult.metadata` 를 덜어내지 말 것.** `pipeline.py` 안에서는 안 쓰여 죽은 필드로
보이지만 소비자가 밖에 있다 — 평가 러너(`evaluation/run_eval.py`)가 `vision_calls`·
`ocr_applied`·`warnings` 를 리포트에 실어, 검색 수치가 움직였을 때 **검색이 변한 것인지
적재가 변한 것인지**를 가른다. 호출부가 `ingest()` 를 한 번 더 불러 얻으면 안 된다:
Vision 은 LLM 이라 응답이 실행마다 달라(`plan_log/표-경로.md` 12회차) 그 두 번째 파싱이
실제로 색인된 것과 다를 수 있고, **다른 파싱의 카운터는 애초에 쓸모가 없다.**

## 4. 설정 3곳 + 저장 경로

새 설정은 `rag_pipeline/config.py` · `.env.example` · `tests/conftest.py` 의 `offline_config`
가 맞아야 한다(`contracts.md` 6절). **indexing 은 여기에 하나가 더 붙는다** —
`offline_config` 가 막는 것은 키가 아니라 **저장 위치**다. `IndexingConfig()` 의 기본
`qdrant_path` 가 `PROJECT_ROOT/.qdrant` 라, 안 덮으면 테스트가 개발자의 실제 색인을 건드리고
테스트끼리 상태를 물려받는다. 그리고 `.qdrant/` 는 `.gitignore` 에 있어야 한다.

`distance` · `qdrant_url` · `provider` 설정을 만들자는 제안은 기각이다 — 지금 전부 합법값이
하나뿐인 죽은 설정이고, `embedding.provider` 를 같은 이유로 안 만들었다.

## 5. 테스트에서 보는 것

- **qdrant 를 모킹하지 않는다.** 로컬 파일 모드라 서버도 키도 과금도 없어서, 진짜 저장소를
  쓰는 편이 "upsert 가 정말 갱신인가"를 실제로 검증한다. 모킹은 저장소가 **고장났을 때**만이고
  이음새는 모듈 레벨 `_make_client()` 다 (`contracts.md` 6절)
- **store 를 열었으면 픽스처가 닫는다.** 안 닫으면 같은 경로를 쓰는 다음 테스트가
  `RuntimeError` 로 죽는다
- **재색인 시나리오 테스트가 있어야 한다** (`test_indexing_reindex.py`). `A B C D E → A B D`
  에서 C·E 가 없어지는지. 대조군(`upsert` 만 하면 5개가 남는다)을 같이 두는 것이 규칙의
  존재 이유를 지킨다
- **자기 청크가 자기 질의에 1등인가**로 자리 밀림을 본다. 값을 원소별로 비교하지 않는다 —
  임베딩은 호출 간 비트 재현성이 없다
