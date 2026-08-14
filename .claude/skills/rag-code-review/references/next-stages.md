# 아직 구현되지 않은 단계 — 최소 계약

`cleaner` / `splitter` / `embedding` / `indexing` / `retriever` 를 건드린 변경에 적용한다.

**이 단계들은 아직 코드가 없다.** 삽입 지점만 `pipeline.py:31-35` 에 주석으로 예약돼 있다.

```python
#   document = clean(document, config.cleaner)
#   chunks   = split(document, config.splitter)
#   vectors  = embed(chunks, config.embedding)
#   index(vectors, config.indexing)
```

그래서 이 파일에는 **기존 코드에서 확실히 도출되는 계약만** 적는다. 추측으로 만든 체크리스트로
리뷰하면 없는 규칙을 근거로 멀쩡한 설계를 지적하게 된다. 여기 없는 사항은 `contracts.md` 와
실제 코드로 판단하고, 판단이 안 서면 "확인 필요"에 넣는다.

## 확실한 계약

### 입출력 타입

모든 단계는 `ParsedDocument`(`schemas/document.py`) 에서 출발한다. cleaner 는
`ParsedDocument → ParsedDocument` 를 유지한다. 여기서 다른 타입을 만들어내면
`ingest()` 의 순차 합성이 깨지고 단계를 떼었다 붙였다 할 수 없게 된다.

### 순서 계약

`elements` 의 `order` 정렬 계약을 깨지 않는다. loader 가 이걸 어떻게 지키는지가
`loader/content/router.py:82,84` 에 있다 — 리전 하나가 여러 Element 로 늘어나므로
**앞 정렬로 처리 순서를 결정론적으로 만들고, 뒤 정렬로 늘어난 Element 를 문서 순서로 다시
합친다.** 뒤에 붙는 단계가 Element 를 합치거나 쪼개면 같은 문제를 만나므로, 결과가 문서 순서로
정렬돼 나오는지 본다. 순서가 흐트러지면 청크 경계가 문서와 어긋나 검색 품질이 조용히 떨어진다.

### HTTP 격리

각 단계는 HTTP 를 모른다. `ingest()` 는 스크립트에서도 쓴다. (`contracts.md` 1절)

### 설정 3곳

새 설정은 `rag_pipeline/config.py` · `.env.example` · `tests/conftest.py:44` 의
`offline_config` 가 맞아야 한다. `offline_config` 는 "키 없음 + 외부 호출 없음"을 보장하는
픽스처이므로, 새 단계가 기본값으로 외부 호출을 하면 여기에 끄는 설정이 추가돼야 한다.

### 외부 API 를 쓰는 단계 (embedding / indexing)

**`contracts.md` 2·3절이 그대로 적용된다.** 이건 새로운 추측이 아니라 이미 선 규약이다.

- 예외를 밖으로 던지지 않는다 → 빈 결과 + `ctx.warn()`
- 카운터는 성공한 호출 뒤에 올린다. 호출당 비용이 보여야 한다
- 호출 상한 (`max_vision_calls_per_doc` 에 해당하는 것)
- 캐시 salt 에 **모델명·차원·버전** 을 넣는다. 임베딩 모델을 바꿨는데 옛 벡터가 나오면
  검색이 조용히 망가진다
- **`LoadContext`/`content_hash`/`ResultCache` 를 재사용·일반화했는지 vs 복제했는지 확인한다.**
  지금 이 셋은 `loader/content/base.py` 안에 있다 (`contracts.md` 3절)

### 새 예외

`rag_pipeline/exceptions.py` 에 추가하고 `backend/app/main.py:21` 에 등록한다.
(`contracts.md` 4절)

## 아직 설계되지 않은 것 — 규칙을 만들지 않는다

- **qdrant 는 아직 설치 전이다.** 연결 실패 시 적재를 멈출지 폴백할지, 부분 색인을 허용할지는
  정해진 바가 없다. 리뷰에서 마주치면 지적이 아니라 **"확인 필요"** 로 올린다
- 청크 크기·오버랩·분할 기준
- 임베딩 배치 크기와 재시도 정책
- `frontend/` 는 비어 있다

## 승격 규칙

해당 단계가 실제로 구현되면 이 섹션을 `references/cleaner.md` / `splitter.md` /
`embedding.md` / `indexing.md` 로 승격하고, `SKILL.md` 라우팅 표에 해당 줄을 추가한다.
그때 이 파일에서는 승격된 내용을 지운다.
