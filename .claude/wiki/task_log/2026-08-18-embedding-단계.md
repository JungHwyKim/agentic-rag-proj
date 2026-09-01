# embedding 단계 신설 — 지시서를 이 저장소의 계약에 맞춰 깎고 붙였다

- 작업일: 2026-08-18 (세션은 2026-08-17 밤에 시작)
- 검증 방법: ruff + pytest 전체(383 passed, 2 skipped, 유료 5건 deselect), 가상 응답 16축,
  `tests/fixtures/` 5건의 토큰 실측(로컬). **여기까지 과금 없음.**
  더해서 **사용자 승인을 받아 유료 호출을 2회 돌렸다**(약 400토큰 · $0.000008) —
  가짜 클라이언트가 흉내내던 응답 모양을 실제와 대조하기 위해서다. 전문은 3절 링크

## 1. 무엇을 바꿨나

- `rag_pipeline/ingestion/embedding/{__init__,base,openai_api}.py`
  신설. 진입점은 `get_embedder(config) -> Embedder` 하나이고 HTTP 를 모른다
- `rag_pipeline/config.py`
  `EmbeddingConfig` 와 `IngestConfig.embedding` lazy 프로퍼티. 기존 셋과 같은 형태라
  테스트가 `object.__setattr__(config, "_embedding", ...)` 로 덮어쓴다
- `rag_pipeline/exceptions.py` · `backend/app/main.py`
  `EmbeddingError` 와 503 매핑. **지금은 이 줄에 닿는 HTTP 경로가 없다**(embedding 이
  `ingest()` 밖) — 끼우는 순간 살아나는데 그때 빠져 있으면 500 이 나가므로 미리 넣었다
- `tests/conftest.py`
  `offline_config` 에 `_embedding` 주입. **`EmbeddingConfig()` 가 `.env` 를 읽어 개발자
  머신의 실키가 픽스처로 샐 수 있다**
- `tests/ingestion/embedding/test_embedding_integration.py`
  **유료.** 가짜가 흉내내던 응답 스키마를 실제 API 와 대조한다. `@pytest.mark.integration`
  이라 기본 실행에서 빠진다
- `.claude/hooks/auto_test_check.py` · `requirements.txt` · `.env.example`
  매핑 한 줄, `tiktoken`, 설정 노출

## 2. 설계 결정

### 실패하면 예외를 던진다 — 다른 외부 API 핸들러와 반대로 갔다

`contracts.md` 2절은 "외부 API 는 예외를 안 던지고 빈 결과 + `ctx.warn()`" 이다. 여기서는
**일부러 안 따랐다.**

버린 것은 그 규약을 문자대로 지키는 안이다. 이유: **그 규약은 폴백 층이 있어서 성립한다.**
LlamaParse 가 죽으면 PyMuPDF 로, Vision 이 죽으면 OCR 로 내려간다. 임베딩에는 내려갈 층이
없다. 빈 벡터를 돌려주면 그 청크는 영원히 검색되지 않는데 아무도 모른다 — `contracts.md`
2절이 막으려던 문장("사용자는 결과가 왜 엉성한지 알 길이 없다")이 규약을 지켜서 일어난다.

같이 버린 것: **결정론적 fake embedder 로 키 없는 환경을 때우는 안.** 테스트는 통과하지만
검색이 조용히 쓰레기가 된다. "돌긴 도는데 틀린" 상태가 제일 늦게 발각된다.

### 반환은 `list[list[float]]`, 대신 길이를 계약으로 못 박았다

버린 것은 `EmbeddedChunk`(chunk_id ↔ vector) 같은 결과 타입이다. 부분 성공을 표현할 수
있지만 **Embedder 가 `Chunk` 를 알게 되고 `embed_query` 와 비대칭이 된다.** 상위가
`[c.text for c in chunks]` 를 만들어 넘기는 쪽이 대칭이 유지된다.

대신 `len(반환) == len(입력)` 이거나 예외를 강제했다. 호출부가 `zip(chunks, vectors)` 로
자리를 맞추므로 한 칸만 밀려도 검색이 엉뚱한 문서를 가리키는데 아무 에러도 안 난다.
그래서 **응답 개수·index 중복·index 범위·차원 일관성을 전부 응답 파싱 자리에서 검사**한다.

### `ingest()` 에 안 끼운다 — splitter 와 같은 이유가 한 칸 밀려 반복됐다

벡터를 소비할 곳(indexing)이 없다. `ingest()` 의 반환은 곧 `backend` 의 `response_model`
이라 벡터를 내보내는 것은 embedding 결정이 아니라 API 결정이다. 청크 45~102개 × 1536차원
float 를 업로드 응답에 실으면 수 MB 이기도 하다.

**예상하지 못했던 것은 이 결정이 앞 결정의 부작용을 없앤다는 점이다.** "키 없으면 예외"는
기본 테스트를 깰 위험이 있는데, `ingest()` 에 안 붙어 있으니 기본 pytest 가 embedding 을
아예 안 부른다. 두 결정이 서로를 지탱한다.

### 상한을 호출 수가 아니라 토큰으로 잡았다

버린 것은 `max_vision_calls_per_doc` 를 그대로 베끼는 안이다. **임베딩은 배치라 청크
500개가 5회 호출로 끝나 호출 수 상한이 아무것도 못 막는다.** 돈은 토큰이다. 그래서
`tiktoken` 을 더해 **보내기 전에** 세도록 했다 — Vision 이 상한을 검사하는 자리와 같은 모양.

값은 실측이 정했다. 문서당 최대가 51,341토큰(학교기록물, 청크 102개)이라 그 약 4배인
200,000 으로 잡았다. **초안의 500,000 은 실측 최대의 10배로 너무 헐거웠다.**

### `provider` 설정을 만들지 않았다 — 추상화는 두고 설정만 뺐다

지시서 8절은 `embedding.provider` / `embedding.model` 설정 쌍을 제안했다. 버렸다.
구현체가 하나면 **합법값이 하나뿐인 죽은 설정**이다. `table_pdf_handler` 에 두 값이 있는
것은 두 경로가 실재해서다. `Embedder` Protocol 과 `get_embedder()` 팩토리는 뒀다 —
그건 상위가 구현체에 직접 붙는 것을 막는 실제 역할이 있다. 로컬 구현체가 생기는 날
분기와 설정을 함께 더한다.

### 지시서에서 덜어낸 것 — 같은 금지를 네 번 적는 대신 선행 조건을 한 번

지시서 3·15·16·18절에 "Vector DB / Retriever / Semantic Cache / Conversation History 를
안 한다"가 네 벌 있었다. 이 패턴은 `plan_log/splitter.md` 1회차에서 이미 기각한 것이다 —
*"그 코드가 한 줄도 없어서 '변경하지 않았다' 체크박스가 항상 통과한다. 검사로서 값어치가
0이다."* 네 영역 전부 이 저장소에 코드가 한 줄도 없다.

사용자가 여기서 **"언제 해야 되는지 명시해줘"** 를 짚었고, 그게 이 절을 지우는 대신
**선행 조건 표로 바꾸는** 결론이 됐다(`plan_log/embedding.md` 의 "언제 하나"). 정리하면서
지시서가 둘을 한 절에 묶어 만든 오해가 드러났다 — **Conversation History 는 embedding 을
기다릴 이유가 없다.** 일반 대화 히스토리는 벡터를 안 쓴다. embedding 을 쓰는 것은 장기
기억뿐이고 그건 별개 기능이다. 반대로 Semantic Cache 는 제일 뒤인데, 순서가 아니라 위험
때문이다 — **임계값이 틀리면 다른 질문에 옛 답을 준다.**

같이 덜어낸 것: 4절(embedding 정의)·중복 다이어그램 3개·10절 예시 = 교과서.
13절의 `timeout`/`max_retries` = `openai` SDK 가 이미 갖고 있어 이중이 된다.
14절 "인터페이스 계약 확인" 테스트 = `Protocol` 은 정적 타입이라 런타임에 검사할 것이 없고
기존 `ContentHandler` Protocol 에도 그런 테스트가 없다.

### 지시서의 사실 오류 넷

`Chunk.content`(실제 `text`), `document_id`(실제 `document_key`, 그리고 그게 없는 것이
splitter 의 명시적 미결), `Preprocessor`(실제 `cleaner`), `ingestion/splitter.py`(실제
패키지). 그리고 *"차원은 모델이 정하니 코드에 고정하지 않는다"* 는 반만 맞다 —
`text-embedding-3-*` 는 `dimensions` 로 줄일 수 있어 **차원은 설정값**이고, 그래서 캐시
salt 에 모델과 **함께** 들어간다.

## 3. 지표

전문은 [verify_log/2026-08-18-embedding.md](../verify_log/2026-08-18-embedding.md) 에 있다.

- pytest 337 → **383 passed, 2 skipped** / ruff 통과
- 한국어 청크 자/토 비율 **중앙값 1.03~1.09 · 최소 0.90** — `splitter` 의 "문자 vs 토큰"
  미결을 문자 쪽으로 닫은 값
- 유료 호출 : **tiktoken 로컬 추정이 API 청구 토큰과 정확히 일치**(46 vs 46).
  토큰 상한 사전 검사가 어림이 아니라는 뜻이다
- E2E : 안 함. 유료 호출은 짧은 문장 3개뿐이고 **실파일 청크는 실제 API 에 안 태웠다**

## 4. 실패 분석

### 계약을 세워놓고 한 경로에서 안 지켰다 (리뷰에서 잡음)

"실패하면 `EmbeddingError` 를 던진다"를 이 단계의 계약으로 정하고, 개수 불일치·index
범위·차원 불일치·키 없음·빈 문자열·상한 초과를 전부 그렇게 막았다. **그런데 응답 항목에서
벡터를 꺼내는 한 줄이 남아 있었다.**

```python
slots[index] = [float(value) for value in item.embedding]
```

`item.embedding` 이 `None` 이면 `TypeError`, 필드가 아예 없으면 `AttributeError` 가
**날것으로** 밖으로 나간다. `RagPipelineError` 가 아니라서 `backend` 의 `_status_for()` 가
매핑표에서 못 찾고 500 을 돌려준다 — 이 작업에서 `EmbeddingError` 를 503 에 등록한 이유가
통째로 무의미해지는 자리다. 게다가 이 줄은 `_call_api()` 의 `try/except` **밖**에 있어
API 예외 처리에도 안 걸린다.

`_as_vector()` 로 감싸 `EmbeddingError` 로 바꿨다. **계약을 정하는 것과 모든 경로에서
지키는 것은 다른 일**이고, 스스로 정한 계약일수록 지켰다고 착각하기 쉽다.

### 테스트 헬퍼를 두 번 잘못 짰다

둘 다 제품 코드가 아니라 가짜 클라이언트 쪽이었지만, 그 탓에 실패 원인이 엉뚱하게 보였다.

- 해시로 벡터를 만들 때 `digest * (dim // 32 + 1)` 로 늘렸다. 4바이트씩 읽으므로
  `dim * 4` 바이트가 필요한데 `dim` 바이트만 계산했다. 차원 9에서 `struct.error` 가 났고,
  그게 `except Exception` 에 삼켜져 **"차원이 다릅니다" 대신 "API 호출 실패"** 로 보였다.
  가짜의 결함이 진짜 코드의 다른 분기처럼 보이는 전형이다
- 토큰 상한 테스트에서 상한을 30으로 잡았다. 상한 검사는 **tiktoken 추정치**로 하는데
  누적은 **가짜가 보고한 토큰**(텍스트당 10)이라 단위가 달랐고 경계를 안 넘었다.
  21로 바꾸고 왜 21인지를 주석에 남겼다

### 훅이 두 번 먼저 잡았다

`auto_test_check.py` 에 매핑을 넣자 `test_mapped_targets_exist` 가 "가리키는 테스트
디렉터리가 없다"로 즉시 막았다. 그리고 `tiktoken` 미설치도 첫 테스트 작성 시점에 잡혔다.
둘 다 커밋 직전이 아니라 그 자리에서 드러났다 — 매핑을 **산문이 아니라 테스트로** 강제한
설계가 값을 한 자리다.

### 유료 호출에서 내 단언이 틀렸다 — 존재하지 않는 성질을 요구했다

통합 테스트 5건 중 2건이 첫 실행에서 실패했다. 둘 다 **서로 다른 두 호출의 벡터를
`abs(a - b) < 1e-6` 로 비교**하는 형태였다. 제품이 아니라 그 단언이 틀렸다 — OpenAI 는
호출 간 비트 재현성을 보장하지 않는다.

**더 나쁜 것은 두 번째 테스트의 설계였다.** 그 테스트의 목적은 *순서 보존*인데, 원소별
비교로는 **자리가 밀린 것과 부동소수점 잡음을 구분조차 못 한다** — 둘 다 `assert False`
로만 보인다. 즉 실패했을 때 무엇이 잘못됐는지 알 수 없는 테스트였다. 코사인 유사도로
바꿔 "자기 짝이 가장 비슷한가"를 보게 했고, 이제 밀리면 `best != index` 로 **어디가
어디로 갔는지**까지 나온다.

2차에서 5건 전부 통과하면서 재현성에 대한 내 결론도 정정됐다. 1차 실패를 보고 "임베딩은
호출마다 다르다"로 단정했는데 2차의 최대 원소차가 **정확히 0** 이었다. 정확한 사실은
**보장되지 않는다**이고 두 동작을 다 관측했다. 어느 쪽이든 원소별 비교를 쓰면 안 된다는
결론은 같아서, 그 근거를 모듈 docstring 과 `references/embedding.md` 7절에 박아뒀다.

곁가지로 **걱정이 하나 빗나갔다.** `dimensions=256` 으로 줄이면 정규화가 풀려 indexing 에서
재정규화가 필요할까 싶어 값을 찍게 해뒀는데, 0.999942 로 유지됐다. 단언을 안 걸고 측정만
한 것이 맞았다 — 모르는 것을 아는 척했으면 틀린 단언을 박을 뻔했다.

## 5. 회귀 테스트

`tests/ingestion/embedding/` 신설. 4절 각 항목의 대응은 이렇다.

| 막은 것 | 테스트 |
|---|---|
| 손상된 `embedding` 필드가 날것으로 새는 것 (4절 첫째) | `test_embedder.py::test_broken_vector_raises_embedding_error` |
| 응답 개수 부족 / index 범위·중복 | `test_short_response_raises` |
| 응답 순서 뒤섞임 | `test_embedding_batch.py::test_shuffled_response_is_reordered` |
| 배치 분할에서 자리 밀림 | `test_order_survives_batching` · `test_batch_size_of_one` |
| `batch_size=0` 으로 `range(0,n,0)` 이 죽는 것 | `test_zero_batch_size_does_not_hang` |
| 키 없을 때 빈 리스트로 조용히 넘어가는 것 | `test_missing_key_raises` |
| 빈 문자열을 0벡터로 때우는 것 | `test_blank_text_raises` |
| 실패한 호출을 비용으로 세는 것 | `test_failed_call_does_not_count` |
| 상한을 호출 뒤에 검사하는 것 | `test_token_cap_blocks_before_calling` |
| **모델·차원을 바꿔도 옛 벡터가 나오는 것** | `test_embedding_cache.py::test_changing_{model,dimensions}_misses_cache` |
| 부분 캐시에서 자리 밀림 | `test_partial_cache_keeps_order` |
| 문자 상한이 토큰 상한을 못 지키게 되는 것 | `test_embedding_real.py::test_chars_per_token_stays_near_one` |
| 청크가 모델 입력 상한을 넘는 것 | `test_no_chunk_exceeds_model_limit` |
| **가짜가 실제와 다른 모양을 흉내내는 것** (유료) | `test_embedding_integration.py::test_live_response_schema` |
| 원소별 비교로 되돌리는 것 (유료) | `test_live_query_matches_document` · `test_live_batching_preserves_order` |

**못 막은 것**: 실파일 청크를 실제 API 에 태우는 것. 유료 호출은 짧은 문장 3개뿐이라
1,201토큰짜리 큰 청크나 마크다운 표 청크가 실제로 어떻게 나오는지는 모른다. 그리고 유료
테스트는 **기본 실행에서 빠지므로** SDK 가 바뀌어도 아무도 안 알려준다 — 사람이 주기적으로
돌려야 하는 종류다.

## 6. 재현

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest tests/ingestion/embedding -q
```

의존성이 하나 늘었다.

```powershell
uv pip install tiktoken
```

## 7. 남은 것

- ~~실제 키로 한 번 돌려봐야 한다~~ — **했다.** 응답 스키마 가정 셋이 전부 사실이었고,
  덤으로 **벡터가 단위 길이**(0.9998~1.0005, 축소본도 0.9999)라는 것을 얻었다. 이건
  **indexing 의 입력**이다 — qdrant 에서 Cosine 대신 Dot 을 쓸 수 있고 재정규화도 불필요하다.
  남은 것은 실파일 청크를 태우는 것인데, 소비할 곳이 없어 지금 해도 쓸 데가 없다
- **모델·차원이 임시값이다.** 근거는 `plan_log/평가-세트.md` 가 닫혀야 나온다.
  비용이 싸다는 것은 품질 근거가 아니다
- **다음은 indexing** 이고, 거기서 `document_id` 정책("재업로드 = 중복인가")이 정해진다.
  splitter 와 embedding 이 둘 다 이 미결을 열어두고 기다린다
- **캐시가 청크마다 파일 하나**라 102청크 문서가 3MB 다. 지금은 문제가 아니지만 문서 수가
  늘면 한 문서를 파일 하나로 묶는 안을 봐야 한다
- `next-stages.md` 의 embedding 절은 **승격 대상**이 됐다(그 파일 끝의 승격 규칙).
  이번 5단계에서 `references/embedding.md` 로 옮겼다
