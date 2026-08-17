# 3단계 splitter — 영역별 리뷰 기준

정리된 요소를 검색 단위(`Chunk`)로 만든다. `rag_pipeline/ingestion/splitter/` 아래가 전부이고
진입점은 `split()` 하나다.

## 같이 읽어야 하는 곳

| 무엇을 확인하나 | 어디 |
|---|---|
| 전 단계 공통 계약 | `contracts.md` |
| 들어오는 것 | `rag_pipeline/schemas/document.py` (`ParsedDocument`·`Element`) |
| 나가는 것 | `rag_pipeline/schemas/chunk.py` (`Chunk`) |
| 앞 단계가 실제로 내놓는 것 | `cleaner.md`, `loader.md` 의 제목 승격 절 |
| 왜 이 설계인가 | `plan_log/splitter.md`, `task_log/2026-08-17-splitter-청킹단계.md` |

## 1. 제일 자주 나올 오해 — "자르는 단계"로 읽는 것

**이 단계의 주 작업은 병합이다.** 실측으로 요소 자수 중앙값이 PDF 23 / HWPX 15 / 시행령 40 이고
1200자를 넘는 텍스트 요소가 세 문서 전부 0개다. 상한 분할은 **큰 표에서만** 실제로 발화한다.

그래서 다음 제안은 근거가 없다. 리포트에 올리기 전에 이 절을 다시 읽는다.

- "고정 길이로 먼저 자르고 넘치면 쪼개자"
- "작은 청크를 인접 섹션과 합쳐 크기를 맞추자" — **섹션 경계를 넘는 병합은 금지다.**
  서로 다른 절의 내용이 한 청크에 담기면 검색 결과가 어느 절 얘기인지 알 수 없다
- "`oversized.py` 가 거의 안 쓰이니 지우자" — 표에서 `rows` 를 못 얻었을 때의 폴백이다

## 2. 되돌리면 깨지는 것

- **상한은 제목 머리를 붙인 뒤에도 지켜져야 한다.** 예산에서 머리 길이를 미리 빼는
  `_body_budget()` 이 그 장치다. 자른 뒤에 붙이는 순서로 되돌리면 상한이 조용히 거짓이 된다
  (실측: 1200 상한에 최대 1258자). `test_cap_holds_after_heading_prefix` 가 막는다
- **겹침은 `oversized` 구간에만.** 섹션 경계에 주면 구조로 지킨 의미 단위를 다시 흐리고,
  표에 주면 같은 행이 두 청크에 들어가 중복 색인된다
- **쪼갠 표 조각마다 헤더 행을 다시 넣는다.** 빼면 행 하나가 검색됐을 때 열 이름이 없다
- **헤더 + 행 하나가 상한을 넘어도 행을 쪼개지 않는다.** 쪼개면 열 대응이 깨져 표의 뜻이 사라진다
- **`order` 연속성을 가정하지 않는다.** cleaner 가 지운 자리와 핸들러가 0개를 만든 자리에
  빈틈이 있다. `chunk_index` 는 별개 수다
- **본문 없는 제목은 청크가 되지 않는다.** 알맹이 없는 검색 결과가 된다

## 3. `ingest()` 에 안 붙어 있는 것은 누락이 아니다

`pipeline.py` 의 예약 주석은 그대로 두는 것이 맞다. **"단계를 만들었으니 `ingest()` 에
끼워야 한다"고 지적하지 않는다.**

근거: `backend/app/api/routes/documents.py` 가 `response_model=ParsedDocument` 로 `ingest()`
결과를 그대로 반환하므로 반환 타입이 곧 공개 HTTP 응답 스키마다. 청크를 소비할 곳(embedding)이
생길 때 반환 형태를 한 번에 정한다. 자세한 것은 `plan_log/splitter.md` "합성 지점" 절.

`ingest(split=True)` 같은 **플래그로 반환 타입을 가르는 제안도 기각된 안**이다 —
`response_model` 을 선언할 수 없게 된다.

## 4. `chunk_id` 와 `document_key`

`document_key` 는 `split()` 의 인자이고 기본값만 파싱 결과 해시다. **내용 해시를 못박자는
제안을 승인하지 않는다** — "재업로드 = 중복"인지는 indexing 정책이고 아직 미결이다.

`content_hash` 는 `loader/content/base.py` 것을 재사용해야 한다. 복제하면 해시 규칙이 두 곳으로
갈라진다 (`contracts.md` 3절).

같은 문서를 두 번 쪼개면 같은 `chunk_id` 가 나와야 한다. 안 그러면 재적재마다 색인이 중복된다.

## 5. 확인 필요로 올릴 것 — 정해진 바가 없다

지적이 아니라 **"확인 필요"** 로 올린다.

- **청크 크기 상한(1200)과 겹침(150)은 임시값이다.** 근거가 생기는 시점은 정답 질의 세트가
  생길 때다(`plan_log/평가-세트.md`). "512가 낫다" 같은 남의 실측을 근거로 쓰지 않는다
- **크기 단위가 문자다.** 임베딩 모델을 고르면 토큰으로 재판정한다
- **표지·간지 장식이 제목으로 승격돼 `heading_path` 에 남는다.** 알려진 미결이고 규칙을
  일부러 안 만들었다 — "첫 쪽 제외"류는 표지가 여러 쪽인 문서에서 깨진다
- **excel/csv 를 못 쟀다.** CSV 는 파일 전체가 표 하나, Excel 은 시트당 표 하나에
  `max_excel_rows=5000` 이라 행 그룹 분할이 제일 크게 걸리는데 픽스처가 없다

## 6. 등록 3곳

새 설정을 넣었으면 센다. **splitter 는 외부 API 를 안 쓰므로 `offline_config` 에는 끌 것이 없다** —
3곳 중 2곳만 해당한다.

1. `rag_pipeline/config.py` 의 `SplitterConfig`
2. `.env.example`
3. (해당 없음) `tests/conftest.py` 의 `offline_config`

`.claude/hooks/auto_test_check.py` 의 `_MAPPINGS` 에 splitter 줄이 살아 있는지도 본다.
지우면 이 영역만 자동 테스트가 조용히 안 돈다.
