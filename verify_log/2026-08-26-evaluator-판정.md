# Evaluator(Phase 4) 구현 검증 — 계약 셋과, 채점의 분모

- 검증일: 2026-08-26
- 대상: Phase 4 Evaluator 구현 (`task_log/2026-08-26-evaluator-판정.md`)
- 비용: **외부 API 호출 없음.** 실행 리포트의 `usage.calls` 0 · `usage.tokens` 0 이고,
  판정 채점(유료)은 **아직 안 돌렸다**(7절)

## 1. 확인하려 한 것

둘이고 성격이 다르다.

- **규칙이 도는가**(가상) — 실패 세 갈래가 전부 `failed` 로 나가고 *불충분* 으로 안
  미끄러지는가 · 안 넘기면 기존 경로가 한 줄도 안 바뀌는가 · 채점 도구의 기대 verdict 가
  세트가 아니라 **그 실행의 순위**에서 나오는가
- **분모가 얼마인가**(실파일) — 사람 세트를 **리랭커 켜고** 한 번 돌려 K 별 기대 분포를
  만든다. 이 값이 없으면 첫 혼동행렬을 읽을 수가 없다. 임계값을 정하는 실측이 아니라
  **채점표의 축을 정하는 실측**이다

## 2. 가상 데이터

무엇을 고립시켰나: **응답 하나만** 바꿨다. 클라이언트·후보·설정을 고정하고 모델이 돌려주는
본문만 갈아 끼워, `verdict` 가 어디서 갈리는지를 응답 한 축으로 좁혔다.

```python
# tests/generation/test_evaluate.py — 요지. 전문은 그 파일에 있다
class FakeResponse:
    def __init__(self, content, *, empty_choices=False, tokens=120):
        message = type("Message", (), {"content": content})()
        self.choices = [] if empty_choices else [type("Choice", (), {"message": message})()]
        self.usage = type("Usage", (), {"total_tokens": tokens})()

class FakeClient:            # client.chat.completions.create(...) 경로를 그대로 흉내낸다
    def __init__(self, *responses):
        self._responses = list(responses); self.calls = []
        self.chat = type("Chat", (), {"completions": self})()
    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses[min(len(self.calls) - 1, len(self._responses) - 1)]

def _body(verdict, reasoning="근거에 조항이 그대로 있습니다"):
    return json.dumps({"verdict": verdict, "reasoning": reasoning}, ensure_ascii=False)

mocker.patch.object(evaluate_module, "_make_client", return_value=FakeClient(...))
```

| 입력 | 기대 | 실측 | 판정 |
|---|---|---|---|
| `{"verdict":"sufficient",...}` | `sufficient` · 경고 0 | 같음 | 통과 |
| `{"verdict":"insufficient",...}` | `insufficient` · 경고 0 · `usage.evaluated` 1 | 같음 | 통과 |
| `_make_client` 가 `None`(키 없음) | `failed` · 경고 1 · `evaluated` 0 | 같음 | 통과 |
| `create()` 가 `RuntimeError` | `failed` · 경고에 `RuntimeError` | 같음 | 통과 |
| 본문이 `None` / JSON 아님 / `verdict` 칸 없음 / `"maybe"` / 리스트 | `failed` (5건) | 5건 전부 `failed` | 통과 |
| `choices=[]` | `failed` (예외 아님) | 같음 | 통과 |
| `{"verdict":"failed","reasoning":"제가 판정을 못 하겠습니다"}` | `failed` 이되 **모델 문장이 사유로 안 실림** | 사유가 `판정을 얻지 못했습니다` | 통과 |
| 후보 0건 | `insufficient` · **API 호출 0** | 같음 | 통과 |
| 질의가 공백뿐 | `failed` · API 호출 0 | 같음 | 통과 |
| 같은 (질의, 후보) 2회 | API 1회 · `cache_hits` 1 | 같음 | 통과 |
| 후보 **순서만** 뒤집어 2회 | API 2회 | 같음 | 통과 |
| 깨진 응답 뒤 정상 응답 | API 2회 (깨진 것은 캐시 안 됨) | 같음 | 통과 |
| `EVALUATOR_PROMPT_VERSION` 을 `v2` 로 | API 2회 (salt 가 갈린다) | 같음 | 통과 |
| `evaluator_model=None` | `get_evaluator()` 가 `None` | 같음 | 통과 |

배선(`tests/test_query.py`)과 채점 도구(`tests/evaluation/test_verdicts.py`)도 같은 방식이다.

| 입력 | 기대 | 실측 | 판정 |
|---|---|---|---|
| `evaluator` 안 넘김 | `Answer.verdict is None` · 생성 그대로 | 같음 | 통과 |
| `verdict="insufficient"` | 생성 1회 · `text` 그대로 | 같음 | 통과 |
| `verdict="failed"` | 그대로 실림(불충분으로 안 접힘) | 같음 | 통과 |
| 답변 캐시 히트 | 판정 호출 0 · `verdict is None` | 같음 | 통과 |
| 정답이 상위 K 안 | 기대 `sufficient` | 같음 | 통과 |
| **정답이 상위 K 밖(긍정 질의)** | 기대 `insufficient` · 맞은 것으로 셈 | 같음 | 통과 |
| 전건 `failed` | 혼동행렬 칸 0 · 실패 열 2 · 맞은 것 0 | 같음 | 통과 |
| `top_k=3` | 판정이 청크 3개를 본다 | 같음 | 통과 |
| 판정 도중 예외 | 저장소가 닫힌다 | 같음 | 통과 |
| `get_evaluator()` 가 `None` | `SystemExit` (조용히 안 돈다) | 같음 | 통과 |

## 3. 실제 데이터

색인에 쓴 PDF 넷(다섯째 `아동정책기본계획` 은 이 세트의 그룹이 아니라 안 들어간다).

| 파일 | bytes | sha256(12) |
|---|---:|---|
| `시행령 개정안 안내서(정보시스템의무사항알림).pdf` | 1156767 | `6eab0a32d09e` |
| `[DNA+_2019-5]_공공기관_신뢰가능_AI_구현_실용가이드.pdf` | 1097567 | `331465567f8e` |
| `2026+중등+기초학력+보장+주친+계획.pdf` | 1074313 | `a7654b7523e7` |
| `「2025년 학교기록물 관리 지침」 (1).pdf` | 1158573 | `c50409732078` |

실행: `h-ce-gpu-pdf` — `kure-v1` + hybrid + `local-gpu` 리랭커 · 세트 `qa_set_human.jsonl`
43건(부정 23은 검색 채점에서 제외) · 색인 414청크(118 / 71 / 103 / 122) ·
`usage.reranked` 1000 = 20질의 × 50 · `rerank_warnings` 비었음(**리랭커가 실제로 돌았다**).

**이 표가 첫 혼동행렬의 분모다.** 기대 verdict 는 긍정 20건에서 *정답이 상위 K 안에
왔는가* 로 갈리고, 부정 23건은 늘 *불충분* 이다.

| K | hit@K | 기대 `sufficient` | 기대 `insufficient` |
|---:|---:|---:|---:|
| 1 | 0.1000 | 2 | 18 + 23 = 41 |
| 3 | 0.3000 | 6 | 14 + 23 = 37 |
| **5** | **0.4500** | **9** | **11 + 23 = 34** |
| **10** | **0.8000** | **16** | **4 + 23 = 27** |
| 20 | 0.9000 | 18 | 2 + 23 = 25 |
| 50 | 1.0000 | 20 | 0 + 23 = 23 |

리랭커 off 로 잰 기존 값(`h-ceiling-kure`)과 대조.

| 항목 | 기준(리랭커 off) | 이번(local-gpu) | 판정 |
|---|---:|---:|---|
| `hit@5` | 8/20 (0.400) | **9/20 (0.450)** | 의도한 변화 — 계획서가 쓴 8/20 은 off 값이다 |
| `hit@10` | 14/20 (0.700) | **16/20 (0.800)** | 의도한 변화 — 같은 이유 |
| `hit@20` | 18/20 | 18/20 | 같음 |
| `hit@50` | 20/20 | 20/20 | 같음(리랭커는 천장을 안 올린다 — 5회차 결론 재확인) |

그룹별 `hit@10`: 시행령 0.5000(miss 3) · AI 가이드 1.0000 · 중등 0.8333(miss 1) ·
학교기록물 1.0000.

## 4. 테스트 스위트

- ruff : `All checks passed!`
- pytest : `1185 passed, 5 skipped, 7 deselected in 157.08s`
- 이번에 늘어난 것 34건 — `test_evaluate.py` 19 · `test_verdicts.py` 8 ·
  `test_query.py` +5(11→16) · `test_eval_config.py` +2(새 테스트 1 + `_MODELS` 파라미터 1)
- **직전 상태를 직접 안 쟀다.** 저장소에 기록된 마지막 전체 수치는 `c9e4ce1` 의
  `1141 passed, 5 skipped` 이고 그 뒤로 다른 세션 커밋 둘(`68e38ed`·`a99cec5`)이 들어왔다
- skip 사유 5건 : `test_eval_config.py` 3건(로컬 갈래가 아니면 장치를 안 쓴다 2 · 모델이
  없으면 provider 가 뜻이 없다 1) · `test_splitter_real.py` 2건(그 문서에 쪼개진 표가 없다).
  **픽스처가 없어서 건너뛴 것은 하나도 없다**

## 5. 판정

- **의도한 변화** — 판정 단계가 생겼고 기본은 꺼짐이다(`EVALUATOR_MODEL` 이 비면
  `get_evaluator()` 가 `None`). `answer_question()` 에 인자가 하나 늘었으나 **안 넘기면
  기존 경로와 동일**함을 테스트가 잠갔다
- **의도한 변화** — 사람 세트의 리랭커 켠 실행이 처음 생겼다(`h-ce-gpu-pdf`).
  `hit@5` 0.450 · `hit@10` 0.800 이고, 이것이 첫 혼동행렬의 분모다
- **회귀 없음** — 전체 pytest 통과, ruff 통과, `usage.calls` 0
- **확인 필요** — **판정이 실제로 맞는지는 아직 0건도 안 쟀다.** 그것이 유일한 유료
  단계이고 7절에 있다

## 6. 재현

```powershell
$env:PYTHONIOENCODING = "utf-8"
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest -q
# 분모를 만든 실행 (무료 · 약 8분 · 색인부터 다시 한다)
.\.venv\Scripts\python.exe -m evaluation.run_eval --dense --hybrid --rerank local-gpu `
    --embedding kure-v1 --qa-set evaluation/qa_set_human.jsonl --k 50 --report-k 10 --label h-ce-gpu
# 판정 채점 (**과금** — 아직 안 돌렸다. 모델은 EVAL_SETTINGS 가 못 박아 .env 가 필요 없다)
.\.venv\Scripts\python.exe -m evaluation.verdicts --label h-ce-gpu --format pdf `
    --qa-set evaluation/qa_set_human.jsonl --rerank local-gpu --top-k 5 10 --wrong --json
```

## 7. 재지 못한 것

- **판정 정확도(첫 혼동행렬)** — 유료라 안 돌렸다. 43건 × K 둘 = **86 호출**이고 입력이
  대략 65만 토큰이다(청크 1,100자대 × 5 또는 10). 사용자 승인이 필요하다
- **HWPX 판** — `--format pdf` 하나만 돌렸다. 포맷 축은 이 회차의 질문이 아니다
- **캐시 히트율·지연** — 판정을 한 번도 안 돌려서 `usage.cache_hits` 가 전부 0 이다.
  두 번째 실행부터 의미가 생긴다
- **실사용 경로(화면)** — backend 는 이번에 안 건드렸다. `.env` 에 `EVALUATOR_MODEL` 이
  없으므로 화면은 지금도 판정 없이 돈다
