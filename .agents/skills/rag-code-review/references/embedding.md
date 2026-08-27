# 4단계 embedding — 영역별 리뷰 기준

청크 텍스트를 벡터로 만든다. `rag_pipeline/ingestion/embedding/` 아래가 전부이고 진입점은
`get_embedder()` 하나다.

## 같이 읽어야 하는 곳

| 무엇을 확인하나 | 어디 |
|---|---|
| 전 단계 공통 계약 | `contracts.md` |
| 들어오는 것 | `rag_pipeline/schemas/chunk.py` (`Chunk`) — 다만 **`Embedder` 는 `Chunk` 를 모른다** |
| 앞 단계가 실제로 내놓는 것 | `splitter.md` |
| 예외 매핑 | `backend.md` |
| 왜 이 설계인가 | `plan_log/embedding.md`, `task_log/2026-08-18-embedding-단계.md` |

## 1. 제일 자주 나올 오해 — `contracts.md` 2절 위반으로 읽는 것

**이 단계는 실패하면 예외를 던진다. 의도된 것이고 저장소에서 유일한 예외 지점이다.**
`except Exception` 으로 삼키고 `[]` 를 돌려주라고 지적하지 않는다.

근거: `contracts.md` 2절("빈 결과 + `ctx.warn()`")은 **내려갈 층이 있어서** 성립하는 규약이다
(LlamaParse→PyMuPDF, Vision→OCR). 임베딩에는 없다. 빈 벡터를 돌려주면 그 청크는 영원히
검색되지 않는데 아무도 모른다 — 2절이 막으려던 상황("사용자는 결과가 왜 엉성한지 알 길이
없다")이 2절을 문자대로 지켜서 일어난다.

그래서 반대 방향이 지적 대상이다. **예외가 아니라 조용히 빠져나가는 경로가 있으면 올린다.**

- 짧은 목록·부분 결과·0벡터·`None` 을 돌려주는 경로
- `RagPipelineError` 를 상속하지 않은 예외가 밖으로 나가는 경로 → `backend` 가 매핑표에서
  못 찾아 500 이 나간다. **실제로 한 번 뚫렸다** — 응답 항목의 `embedding` 이 `None` 일 때
  `TypeError` 가 날것으로 샜고 `_as_vector()` 로 감싸 고쳤다

## 2. 되돌리면 깨지는 것

- **`len(반환) == len(입력)` 이 이 단계의 계약이다.** 호출부가 `zip(chunks, vectors)` 로
  자리를 맞추므로 한 칸만 밀려도 검색이 엉뚱한 문서를 가리키는데 **아무 에러도 안 난다**
- **응답을 자리로 믿지 않고 `data[i].index` 로 재정렬한다.** "OpenAI 가 순서를 보장하니
  불필요하다"는 제안을 승인하지 않는다. 실제 응답으로는 한 번도 안 돌려봤다(7절)
- **상한은 호출 수가 아니라 토큰이다.** `max_vision_calls_per_doc` 를 베낀 형태로 바꾸자는
  제안은 기각이다 — 배치라 청크 500개가 5회 호출로 끝나 호출 수 상한이 아무것도 못 막는다.
  `tiktoken` 으로 **보내기 전에** 세는 순서도 유지한다(Vision 이 상한을 보는 자리와 같다)
- **캐시 salt 는 `embed:{model}:{dim}` 이고, 규칙은 `base.py:cache_salt_for()` 에 있다.**
  차원이 들어 있는 이유는 `text-embedding-3-*` 가 `dimensions` 로 차원을 줄일 수 있어
  **차원이 설정값**이기 때문이다. 하나라도 빼면 모델을 바꿔도 옛 벡터가 나오는데 에러가 안 난다.
  **구현체 안으로 되돌리자는 제안은 기각이다** — 원래 `OpenAIEmbedder._salt` 였는데, 그러면
  두 번째 구현체가 salt 를 **자기가 다시 짜야 하고 빠뜨려도 아무 에러가 안 난다**
- **그 함수는 `indexing.collection_name_for()` 의 거울이라 둘이 같은 필드에 반응해야 한다.**
  저쪽은 벡터가 *저장되는* 자리를, 이쪽은 *캐시되는* 자리를 가른다 — 갈리면 한쪽만 새 모델을
  알아보는데 예외가 아니라 나쁜 검색 결과로만 드러난다.
  `tests/ingestion/embedding/test_cache_salt.py` 가 `EmbeddingConfig` 필드를 하나씩 흔들어
  강제한다(값이 아니라 **무엇에 반응하는가**를 본다 — 두 파생의 문자열 모양은 원래 다르다).
  **한쪽에만 축을 더하는 변경은 지적한다.** `provider` 와 질의 접두사는 아직 **일부러 없다** —
  구현체가 하나면 합법값이 하나뿐인 죽은 축이고, 로컬 구현체가 생기면 **이 함수 한 곳**에 더한다
- **`embedding_device` 를 salt 에 더하자는 제안은 기각이다 — 실측으로 닫혔다.** 그럴듯한
  제안이고 `rerank_cache_salt_for()` 가 `rerank_device` 를 싣고 있어 **비대칭으로 보인다.**
  2026-08-26 에 쟀다: 실제 청크 414개에서 CPU/GPU 벡터가 `1−cos` 최대 **1.354e-09** ·
  성분 최대 절대차 5.728e-06 이다(`verify_log/2026-08-26-스레드-발열-GPU.md`).
  더하면 `.cache/embedding` 이 전량 미스가 되고 `.qdrant` 컬렉션이 고아가 되는 대가만 치른다.
  리랭커 쪽이 싣는 이유는 저쪽이 **순위만** 대조했고 점수는 안 봤기 때문이다 —
  *같다는 것을 확인한 것*과 *다를 수 없다는 것*은 다르다
- **그 결정 때문에 GPU 실행이 CPU 벡터를 조용히 읽는다 — 측정 코드를 리뷰할 때 본다.**
  device 가 salt 에 없으므로 장치를 바꿔 돌려도 캐시가 전량 적중하면 **모델이 한 번도 안 돈다.**
  실제로 E2E 검증 첫 실행이 `usage.calls == 0` 이었고 *"GPU 가 같은 결과를 낸다"* 와
  *"GPU 가 안 돌았다"* 가 수치로 구분되지 않았다. 장치·모델을 비교하는 실행은
  `EMBEDDING_CACHE_DIR` 를 갈라야 하고, **`usage.calls` 가 0인 비교는 근거가 아니다**
- **캐시 적중은 `total_tokens` 에 안 더한다.** 과금 안 된 것을 세면 비용이 부풀어 보인다
- **빈 문자열은 예외다.** 0벡터로 때우면 검색 구멍이 조용히 생긴다
- **`_make_client()` 는 모듈 레벨 함수다.** `__init__` 으로 옮기거나 인라인화하면 이 영역
  테스트가 전부 깨진다 (`contracts.md` 6절, Critical)

## 3. `ingest()` 에 안 붙어 있는 것은 누락이 아니다

splitter 와 **같은 이유이고 한 칸 밀린 같은 상황**이다(`splitter.md` 3절). 벡터를 소비할
곳(indexing)이 없고, `ingest()` 의 반환 타입이 곧 공개 HTTP 응답 스키마다.

**부수 효과를 함께 본다.** 이 단계가 `ingest()` 밖에 있어서 기본 `pytest` 가 임베딩을 아예
안 부르고, 그래서 "키 없으면 예외"가 기본 테스트를 안 깬다. **두 결정이 서로를 지탱하므로
한쪽만 되돌리는 변경은 지적한다** — `ingest()` 에 끼우는 변경은 키 없는 환경에서 적재
전체를 죽인다.

## 4. `Embedder` 가 `Chunk` 를 모르는 것도 결정이다

`embed_documents(texts: list[str])` 이고 상위가 `[c.text for c in chunks]` 를 만든다.
**`Chunk` 를 받게 하거나 `EmbeddedChunk` 를 돌려주자는 제안은 기각된 안이다** —
`embed_query()` 와 비대칭이 되고, 질의 임베딩이 같은 구현체를 못 쓰게 된다.

`embed_documents`/`embed_query` 분리는 **지금 아무 동작 차이가 없다**(같은 값이 나온다).
"중복이니 합치자"는 제안을 승인하지 않는다 — 로컬 모델(BGE·E5 는 질의에 접두사를 요구)
대비이고, 합치면 그때 호출부를 전부 고쳐야 한다.

`content_hash` 와 `ResultCache` 는 `loader/content/base.py` 것을 재사용한다. 반면
**`LoadContext` 는 일부러 재사용하지 않았다** — loader 전용 카운터를 들고 있어 끌어오면
loader 관심사가 샌다. 대신 `EmbeddingUsage` 가 인스턴스에 붙어 있다.

## 5. 확인 필요로 올릴 것 — 정해진 바가 없다

지적이 아니라 **"확인 필요"** 로 올린다.

- **모델(`text-embedding-3-small`)과 차원은 임시값이다.** 모델 간 한국어 검색 품질은 정답
  질의 세트가 없어 못 잰다(`plan_log/평가-세트.md`). **비용이 싸다는 것은 품질 근거가 아니다**
- **`provider` 설정이 없는 것은 의도다.** 구현체가 하나면 합법값이 하나뿐인 죽은 설정이다.
  추상화(`Embedder` Protocol + `get_embedder()`)만 두고 설정은 로컬 구현체와 함께 더한다
- **캐시가 청크마다 파일 하나다.** 102청크 문서가 3MB 쯤 된다. 지금은 문제가 아니고,
  한 문서를 파일 하나로 묶는 안이 후보로 열려 있다
- **`document_id`** — indexing 결정이라 여기서 앞질러 정하지 않는다

## 6. 등록 4곳

새 설정을 넣었으면 센다. **splitter 와 달리 4곳 전부 해당한다** — 이 단계는 외부 API 를 쓴다.

1. `rag_pipeline/config.py` 의 `EmbeddingConfig`
2. `.env.example`
3. `tests/conftest.py` 의 `offline_config` — **`EmbeddingConfig()` 가 `.env` 를 읽으므로
   키를 지워야 개발자 머신의 실키가 픽스처로 안 샌다.** `ingest()` 가 임베딩을 안 부른다는
   이유로 빼면 안 된다
4. `rag_pipeline/exceptions.py` + `backend/app/main.py` 의 `_STATUS_BY_EXCEPTION`(503).
   **지금 이 줄에 닿는 HTTP 경로가 없지만 지우지 않는다** — 3절이 풀리는 순간 살아난다

`.claude/hooks/auto_test_check.py` 의 `_MAPPINGS` 에 embedding 줄이 살아 있는지도 본다.

## 7. 실제 API 로 확인된 것 / 아직 아닌 것

기본 테스트는 `_make_client()` 를 가짜로 갈아끼운다. 그 가짜가 실제와 같은 모양인지는
`test_embedding_integration.py`(유료, `@pytest.mark.integration`)가 2026-08-18 에 확인했다.

**확인됨** — `data[i].index`·`data[i].embedding`(1536차원 float)·`usage.total_tokens` 가
전부 가정대로다. `dimensions=256` 이 실제로 먹고, **벡터가 단위 길이다**(0.9998~1.0005,
축소 차원도 0.9999). 배치를 나눠도 순서가 보존된다.

**여기서 얻은 규칙 둘 — 되돌리면 다시 깨진다.**

- **두 벡터를 원소별로 비교하지 않는다.** 호출 간 비트 재현성은 **보장되지 않는다**(실측:
  1차 실행에서 같은 텍스트 두 호출이 1e-6 넘게 달랐고, 2차에서는 정확히 같았다). 배치
  구성이 다르면 ~1e-4 수준으로 흔들린다. 판정은 코사인 유사도로 한다
- **순서 검증을 "값이 같은가"로 쓰지 않는다.** 그러면 **자리 밀림과 부동소수점 잡음을
  구분하지 못한다**(둘 다 `assert False` 로만 보인다). 자기 짝과의 유사도가 남과의
  유사도보다 높은가로 본다

**아직 아님** — 유료 호출은 짧은 문장 3개만 썼다. 실파일 청크(최대 1,201토큰, 마크다운 표)를
실제 API 에 태운 적은 없다. 그 부분의 변경은 확인 필요로 올린다.

`-m integration` 은 리뷰에서 절대 돌리지 않는다. `paid_test_check.py` 가 막고, 탈출구는
**사용자 환경의** `ALLOW_PAID_TESTS=1` 이다 — 명령줄에 붙여도 훅에는 닿지 않는다.
