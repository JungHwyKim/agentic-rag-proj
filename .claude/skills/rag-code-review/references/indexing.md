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
- **`index_document()` 의 `embedder=` 주입.** 안 넘기면 `get_embedder()` 로 만드는 것이
  계약이고 그때 `config.embedding` 을 존중해야 한다(`store` 와 같다). 다만 **이유가
  다르다** — `store` 는 배타 잠금, 이쪽은 **모델 적재**다. `get_embedder()` 는
  메모이즈하지 않고 로컬 임베더는 임베딩 캐시가 빗나갈 때 가중치를 올리므로, 긴 수명
  프로세스(backend·평가 루프)가 안 넘기면 **문서마다 2.27GB 를 다시 적재한다.**
  그리고 주입받았을 때 `IndexResult.usage` 는 **앞뒤 차**여야 한다 — `usage.reset()`
  으로 바꾸자는 제안은 기각이다. 같은 인스턴스를 질의 쪽도 쓰고 `run_eval` 은 루프를
  다 돈 뒤 `total_tokens` 를 읽어, 여기서 0으로 만들면 그 회계가 조용히 사라진다.
  **임베더는 닫지 않는다** — `close()` 가 없어 `store` 의 소유권 규칙이 안 딸려온다
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

### 답변 캐시가 이 파일에 얹은 것 (2026-08-24)

`rag_pipeline/answer_cache/` 가 **같은 저장 경로의 두 번째 컬렉션**(`qa_cache_*`)을 연다.
`qdrant_store.py` 의 접근자 셋이 그것을 받치고, **셋 다 되돌리면 조용히 깨진다.**

- **`QdrantStore.client` 는 `_qdrant` 를 그대로 통과시킨다.** 여기서 새로 열거나 `__init__`
  으로 앞당기면 **`lifespan` 의 게으름이 깨진다** — 색인 전에도 저장소가 열린다.
  `tests/answer_cache/test_answer_cache.py:test_building_cache_does_not_touch_the_client` 가
  tripwire 로 잠가 뒀다
- **두 번째 컬렉션에 `get_store()` 를 다시 부르자는 제안은 기각이다.** 로컬 파일 모드가
  디렉터리에 배타 잠금을 걸어 **같은 프로세스 안에서 자기 자신과 충돌한다.** `.qdrant_cache`
  로 경로를 가르는 안도 같은 이유로 밀렸다(잠금은 피하지만 클라이언트·`close()` 가 둘이 된다).
  한 클라이언트가 여러 컬렉션을 보는 것이 qdrant 의 정상 사용이다
- **`collection_name_for(..., prefix=)` 를 우회하는 컬렉션 이름은 지적한다.** `prefix` 는
  네임스페이스일 뿐이고 모델·차원 파생은 공유해야 한다 — 고정 이름을 쓰면 모델을 갈아탄 뒤
  **옛 모델 벡터가 든 컬렉션에 새 모델 질의 벡터로 유사도를 재게 된다**(검색 품질만 조용히
  나빠져 늦게 발각된다). 파생에 `provider` 는 안 들어간다 — 임베딩 캐시
  `embedding/base.py:cache_salt_for()` 와 같은 필드 집합(`model`, `dimensions`)이고,
  provider 축이 열리면 **두 파생을 한 자리에서** 고친다.
  `tests/ingestion/embedding/test_cache_salt.py` 가 그 일치를 강제하므로, 한쪽에만 축을
  더하면 그 필드에서 실패한다
- **`fetch_by_ids()` 는 찾은 것만 돌려준다.** 없는 것을 빈 자리로 채우자는 제안은 기각이다 —
  그 차이가 곧 *"그 사이 지워졌다"* 는 신호이고 캐시 무효화가 그것으로 판정한다.
  `list_documents()` 와 같은 이유로 **Protocol 에는 안 올렸다**(소비자가 retriever 가 아니다)

캐시 쪽 규약 둘은 `answer_cache/` 안에 있지만 리뷰에서 자주 걸린다.

- **캐시 실패는 예외가 아니라 미스다.** 내려갈 층(정상 검색·생성)이 있어 로더의 *빈 결과 +
  경고* 규약이 성립한다 — embedding·indexing 이 예외를 던지는 것과 갈리는 지점이 그것이다.
  그래서 새 예외도 `_STATUS_BY_EXCEPTION` 항목도 **없는 것이 맞다**
- **`clear()` 와 근거 대조를 같은 것으로 보지 않는다.** 둘은 서로 다른 실패를 막는다 —
  조회 시점 대조는 *"딛고 선 청크가 사라지거나 바뀌었다"* 를, `clear()` 는 *"새 문서가 들어와
  옛 답이 더는 최선이 아니다"* 를 맡는다. 뒤엣것은 대조로 **원리적으로 못 잡는다**(사라진 것이
  없다). 그래서 *"대조가 있으니 `clear()` 는 중복"* 이라는 지적은 틀렸고, 반대로
  *"`clear()` 가 있으니 대조는 불필요"* 도 틀렸다
- **색인 라우트가 캐시를 갈아끼우면 지적한다.** `app.state.answer_cache = None` 은 BM25 의
  대우이고, 캐시는 **객체를 두고 내용만 비운다** — 스냅샷이 아니라 새로 만들 이유가 없고
  `warnings` 만 잃는다. `clear()` 는 실패해도 안 던지므로 호출부에 try/except 가 필요 없다
- **`warnings` 에 직접 `append` 하면 지적한다.** `_warn()` 만 쓴다 — 이 객체는 앱 수명 내내
  살아 상한이 없으면 무한히 는다. `Bm25Index.warnings` 가 안 물리는 것은 **수명이 달라서**다
- **payload 의 `pipeline` 칸을 지우거나 지문 대조를 빼자는 제안은 기각이다** (2026-08-28).
  캐시가 낡는 축이 넷인데 **검색·생성 설정만 다른 무엇도 안 막는다** — 히트하면 `retrieve()` 를
  아예 안 부르므로 리랭커·재작성기를 켜도 히트한 질의는 옛 순서로 서빙되고 **에러가 안 난다**.
  기동 시 `clear()` 로 대신하자는 안도 기각이다: 지문을 둘 자리가 없어 sentinel point 라는 새 상태
  축이 생기는데, 그게 `plan_log/시맨틱-캐시.md` 1회차가 «색인 세대 번호» 를 기각한 그 이유다
- **지문이 안 맞을 때 `_drop()` 을 부르자는 제안은 기각이다.** `_sources_still_valid()` 와 모양이
  같아 «비대칭» 으로 보이지만, 근거 소멸은 *확정 사망* 이고 설정 변경은 **되돌리면 다시 유효해지는
  조건부 사망**이다. 지우면 손잡이를 왔다 갔다 할 때마다 캐시를 통째로 버린다
  (`test_pipeline_mismatch_does_not_drop_the_entry`). 옛 형식에 마이그레이션을 만들자는 제안도
  마찬가지다 — 키가 없으면 `None` 이라 **공짜로 미스**다
- **`_ensure_collection()` 이 `bool` 을 돌려주고 예외를 안 던지는 것이 결정이다.**
  `QdrantStore.ensure_collection()` 은 같은 자리에서 `IndexingError` 로 크게 우는데, 여기서
  따라 하면 **캐시 때문에 질의가 죽는다.** 검사 자체를 빼자는 제안도 기각이다 — 안 보면 upsert
  실패를 `put()` 의 `except` 가 삼켜 *"저장하지 못했습니다"* 라는 **원인 없는 경고 하나**로 끝나고
  캐시가 조용히 아무것도 안 쌓는다. 그래서 경고 문구에 *차원* 이 들어가는 것까지가 계약이다
- **`answer_cache_threshold` 에 기본값을 넣자는 제안은 기각이다.** 없으면 캐시가 통째로
  꺼지는 것이 설계다 — *같은 뜻 다른 말* 쌍이 아직 0쌍이라(`qa_set.jsonl` 60 ·
  `qa_set_human.jsonl` 20 의 `gold_spans` 교집합 0) 분포의 틈을 못 봤고, **임계값은 임베딩
  모델에 딸린 값**이라 모델이 바뀌면 다시 재야 한다. 근거는
  `task_log/2026-08-24-답변캐시.md`

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
