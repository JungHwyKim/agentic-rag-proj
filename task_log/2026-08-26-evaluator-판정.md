# Evaluator(Phase 4) — 판정을 내되 아무것도 안 바꾸고, 그것을 잴 자[尺]까지 낸다

- 작업일: 2026-08-26
- 검증 방법: ruff · 전체 pytest · 사람 세트를 리랭커 켜고 한 번 돌린 무료 실행
  (`h-ce-gpu-pdf`). **외부 API 과금 없음** — 리포트 `usage.calls` 0 · `usage.tokens` 0 이고
  판정 채점(유료 86호출)은 **사용자 승인 전이라 안 돌렸다**. `-m integration` 안 돌림

## 1. 무엇을 바꿨나

- `rag_pipeline/generation/evaluate.py` (신규)
  판정 구현체 하나(`OpenAIEvaluator`) + `get_evaluator()`. 리랭커의 `openai_llm.py` 를 본떴다
- `rag_pipeline/generation/prompts.py`
  판정 프롬프트와 버전 상수. 근거 블록을 생성과 **같은 함수**로 만든다
- `rag_pipeline/schemas/answer.py`
  `EvaluatorVerdict`(3-상태) + `Answer.verdict`
- `rag_pipeline/config.py`
  `EvaluatorConfig` + `IngestConfig.evaluator` lazy 프로퍼티
- `rag_pipeline/query.py`
  `answer_question(..., evaluator=None)`. 자리는 `retrieve()` 뒤 · `generate_answer()` 앞
- `tests/conftest.py`
  `offline_config` 에 `_evaluator` 핀. **설정을 만든 편집과 같은 자리에서 넣었다**(4절)
- `evaluation/verdicts.py` (신규) · `evaluation/config.py`
  혼동행렬 채점 도구와 그 도구가 쓰는 고정 설정
- `.env.example` · `CLAUDE.md` · `evaluation/README.md` ·
  `.claude/skills/rag-code-review/references/next-stages.md`
  켜는 법 · 진입점 인자 · 재는 절차 · 리뷰 기준

## 2. 설계 결정

여섯을 면담 두 라운드로 정했다(`plan_log/검색-생성.md` 11회차에 회차 기록이 있다).
여기에는 **코드가 그 결정을 어떻게 지키는지**만 적는다.

### verdict 를 `bool | None` 이 아니라 3-상태 `Literal` 로 둔다

버린 것은 `is_sufficient: bool | None` 이다. `RetrievedChunk.dense_score: float | None`
(*"후보가 아니었음"과 0.0 은 다르다*)의 선례를 그대로 따르는 안이라 그럴듯했는데, **`None`
이 falsy 라는 것 하나가 계약을 되돌린다** — 호출부의 `if not verdict.is_sufficient:` 한 줄이
실패를 *불충분* 으로 삼키고, 그 순간 10회차가 닫은 결론(*실패를 불충분으로 떨어뜨리면
부정 예시 23건에서 죽은 Evaluator 가 만점을 받는다*)이 되살아난다. 아무 에러도 안 난다.

두 칸(`sufficient: bool` + `failed: bool`)도 버렸다 — `(True, True)` 가 타입으로 표현
가능해지고, 채점 도구가 그 조합을 만나면 어느 버킷인지 정할 근거가 없다.

**계약을 지키는 것이 문장이 아니라 타입이어야 한다**가 이 회차의 결론이다.

### `"failed"` 는 모델이 말할 수 없는 값이다

파서가 받는 값은 `sufficient`·`insufficient` **둘뿐**이고 나머지는 전부 `failed` 로
떨어진다. 모델이 `{"verdict": "failed"}` 를 돌려줘도 그것은 판정이 아니라 손상된 응답으로
센다 — 자기 실패를 선언하게 두면 **실패 버킷이 모델의 말버릇을 재게 된다.**
테스트가 사유 문자열까지 잠갔다(모델 문장이 `reasoning` 으로 안 실린다).

### 판정이 답변을 바꾸지 않는다

*불충분* 이어도 생성은 그대로 돈다. 버린 것 둘 — **`NO_EVIDENCE_ANSWER` 로 떨어뜨리기**는
정확도를 재기 전에 오판의 대가를 사용자가 먼저 무는 안이고(이번 실측으로 `hit@10` 이
16/20 이라 네 건은 근거 없이 온다), **생성 프롬프트에 불충분을 알리기**는 답변 문장이
바뀌어 *판정이 맞았나* 와 *답이 좋아졌나* 가 한 실행에 섞이는 안이다 — **답변 품질을 재는
자[尺]가 아직 없어** 그 미끄러짐을 잡을 방법이 없다.

### 프롬프트에 하위 규칙을 안 넣는다

*"빈 양식 표는 불충분"* · *"제목 머리만 있으면 불충분"* 이 그럴듯해 보이지만, 이 코퍼스에는
**정답 span 이 제목 머리에만 있는 청크가 실재한다**(`sih-06`·`sih-14`). 미리 박으면 그
건들이 판정 오류로 세어지는데 그건 Evaluator 의 잘못이 아니라 세트의 축 문제다.
첫 혼동행렬이 *무엇 때문에 틀렸나* 를 보여준 뒤에 넣는다.

### Protocol 도 `evaluator_provider` 도 안 만든다

리랭커는 만들었고 답변 캐시는 안 만들었다. 가르는 것은 **두 번째 구현체가 실재하는가**
하나이고 여기는 하나다. 같은 이유로 `evaluator_top_k` 도 안 만들었다 — 청크 수는
`retrieve()` 의 `top_k` 가 이미 정하고, 여기 두면 **셋째 출처**가 생겨 어느 것이 이겼는지
호출부마다 갈린다(2회차가 `RetrievalConfig.top_k` 를 뺀 이유와 같다).

### 채점은 `run_eval` 이 아니라 새 도구다

verdict 는 순위를 안 바꾸므로 `run_eval` 의 지표는 판정이 맞든 틀리든 **같은 수**를 낸다.
그리고 그 리포트 JSON 은 `as_baseline()`·`compare_to_baseline()` 에 물려 있어 판정 지표를
얹으면 그 파일이 무엇을 재는지가 흐려진다. `negatives.py`·`rerank_diff.py` 가 이미
*실행 라벨의 컬렉션을 열어 다시 검색하는* 선례라 뼈대를 그대로 썼다.

**기대 verdict 를 세트에 고정하지 않는 것이 이 도구의 핵심이다.** 부정 23건은 늘 *불충분*
이 옳지만 긍정 20건은 아니다 — 검색이 근거를 안 물어왔으면 *불충분* 이 옳은 답이다.
고정하면 그 건들이 오답으로 세어지고, 그 순간 표가 Evaluator 가 아니라 **검색기를 잰다.**

## 3. 지표

전문은 [verify_log/2026-08-26-evaluator-판정.md](../verify_log/2026-08-26-evaluator-판정.md).

- ruff 통과 · pytest **1185 passed, 5 skipped, 7 deselected** (이번 몫 34건)
- 사람 세트 첫 리랭커 실행(`h-ce-gpu-pdf`, 무료) — `hit@5` **0.450** · `hit@10` **0.800** ·
  `hit@50` 1.000. 이것이 첫 혼동행렬의 **분모**다
- E2E : **안 함**(판정 채점이 유료라 승인 대기)

## 4. 실패 분석

### 계획서를 한 번 물렸다 — 세션 중에 트리가 다섯 커밋 움직였다

첫 계획서를 낸 뒤 사용자가 *"상태확인하고 설계서 새로 작성해줘"* 로 물렸고, 다시 읽으니
그 사이 다른 세션이 커밋 5개를 넣어 넷이 달라져 있었다. 그중 둘이 실제로 이 작업의 내용을
바꿨다 — `AnswerCacheConfig.answer_cache_mode` 가 생기며 `offline_config` 핀이 둘로 넓어져
***필드를 더한 회차가 핀을 함께 넓힐 의무*** 가 명시됐고(그래서 `EvaluatorConfig` 핀에
`evaluator_model=None` 을 함께 박았다), backend `routes/query.py` 가 워밍업·질의 로그로
`_BUILD_LOCK` 을 갖게 되어 **backend 를 안 건드리는 이유가 하나 늘었다.**

곁가지로 회차 번호도 틀렸다 — 초안이 12회차로 적었는데 **회차는 파일별**이고
`검색-생성.md` 의 마지막은 10회차였다(11은 `평가-세트.md` 것이다).

### 계획서가 쓴 분모 8/20 · 14/20 이 **리랭커 off 값**이었다

계획서는 `h-ceiling-kure`(리랭커 off)의 `hit@5` 8/20 · `hit@10` 14/20 을 기대 분포의 근거로
썼는데, 이번에 리랭커를 켜고 처음 돌리니 **9/20 · 16/20** 이었다. 방향은 안 바뀌었지만
K=10 의 *긍정 불충분* 칸이 6 → **4건**으로 줄어, 그 칸으로 판정력을 논하기엔 더 얇아졌다.
**계획서 안의 수치도 다른 설정에서 온 값일 수 있다**는 것이 요지다 — 실행 라벨을 안 보고
숫자만 옮기면 이렇게 된다.

### heredoc 이 백슬래시를 먹어 소스를 깨뜨렸다

Bash 도구의 heredoc(`<<'PY'`)으로 파이썬을 써 내려보냈는데 `\\n` 이 **실제 개행**으로 들어가
`prompts.py` 가 `SyntaxError: unterminated string literal` 로 죽었다(PostToolUse 훅이 즉시
잡았다). 백슬래시가 필요한 파일은 `Write`/`Edit` 도구로 쓰고, 스크립트로 써야 하면
`chr(92)` 로 조립했다. 회귀 테스트로 못 막는 종류라 여기 적어둔다.

곁가지 하나 더 — `evaluation/verdicts.py` 의 표 머리 `기대\실제` 를 f-string 안에 두었다가
`f-string expression part cannot include a backslash` 로 죽었다(py3.11). 상수로 빼냈다.

## 5. 회귀 테스트

| 4절·2절의 항목 | 막은 곳 |
|---|---|
| 실패 셋이 전부 `failed` (불충분으로 안 미끄러짐) | `tests/generation/test_evaluate.py::test_missing_key_is_failed_not_insufficient` · `test_api_error_is_failed` · `test_broken_response_is_failed`(5종) |
| 모델이 `"failed"` 를 말해도 안 받는다 | `test_model_cannot_claim_failed` |
| 후보 0건은 실패가 아니라 `insufficient` · API 0회 | `test_no_chunks_skips_the_model` |
| 깨진 응답이 캐시에 안 들어간다 / 순서가 키를 가른다 / 프롬프트 버전이 salt 에 있다 | `test_broken_response_is_not_cached` · `test_candidate_order_changes_the_key` · `test_prompt_version_is_in_the_salt` |
| 모델 이름이 비면 통째로 꺼진다 | `test_factory_is_off_without_a_model` |
| 안 넘기면 기존 경로가 안 바뀐다 / 불충분이어도 생성이 돈다 / 실패가 그대로 실린다 | `tests/test_query.py::test_without_evaluator_the_path_is_unchanged` · `test_insufficient_does_not_block_generation` · `test_failed_verdict_is_carried_as_is` |
| 기대 verdict 가 **그 실행의 순위**에서 나온다 | `tests/evaluation/test_verdicts.py::test_gold_missing_flips_the_expectation` |
| 실패가 칸이 아니라 열이다 | `test_failure_is_a_column_not_a_cell` |
| 판정 설정이 `.env` 를 이긴다 | `tests/evaluation/test_eval_config.py::test_evaluator_model_is_pinned` |

**못 막은 것** — heredoc 백슬래시 사고(도구 사용 습관이라 테스트로 못 막는다)와 **판정
품질 자체**(유료 실행 전이라 아직 아무 테스트도 없다).

## 6. 재현

```powershell
$env:PYTHONIOENCODING = "utf-8"
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest -q

# 분모를 만든 무료 실행 (약 8분 · 색인부터 다시 한다)
.\.venv\Scripts\python.exe -m evaluation.run_eval --dense --hybrid --rerank local-gpu `
    --embedding kure-v1 --qa-set evaluation/qa_set_human.jsonl --k 50 --report-k 10 --label h-ce-gpu

# 판정 채점 — **과금** 86호출. 모델은 EVAL_SETTINGS 가 못 박아 .env 가 필요 없다
.\.venv\Scripts\python.exe -m evaluation.verdicts --label h-ce-gpu --format pdf `
    --qa-set evaluation/qa_set_human.jsonl --rerank local-gpu --top-k 5 10 --wrong --json
```

## 7. 남은 것

- **첫 혼동행렬** — 43건 × K 둘 = 86 호출(대략 65만 토큰). **사용자 승인이 필요하다.**
  **채점은 `.env` 없이 돈다** — `EVAL_SETTINGS["evaluator"]` 가 모델을 못 박기 때문이고,
  `.env` 의 `EVALUATOR_MODEL` 은 화면 경로를 켤 때 필요한 별개 축이다. 표를 읽기 전에
  **실패 버킷이 0인지** 먼저 본다
- **화면·`query_log` 에 verdict 를 실을지** — 지금 `Answer.verdict` 의 소비자는 테스트뿐이다.
  실사용을 켤 때 함께 정한다
- **질의당 LLM 호출 상한** — Evaluator 가 도는 순간 표적이 생긴다. 정상 상태 질의
  3.0~4.3초 위에 판정 1회가 얹힌다
- **세 번째 종류** — *근거는 왔는데 빈 양식이라 못 쓴다*(10회차 5번). 첫 혼동행렬 뒤에 본다
- **커밋 안 했다** — 사용자가 시킬 때만 한다
