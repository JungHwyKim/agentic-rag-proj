"""RAGAS 지표 **하나**를 **격리 환경에서** 한 번 돌린다. 이 파일만 저장소 코드를 안 쓴다.

## 부르는 법 — `.venv` 로 돌리지 않는다

```
uv run --no-project --python 3.11 --with "ragas==0.4.3" --with "langchain-community<0.4" \
    evaluation/ragas_probe.py --material <재료.json> --out <점수.json> \
    --metric factual_correctness [--query-id hbas-03 ...] [--variant self]
```

`.venv` 에도 `requirements.txt` 에도 `ragas` 를 안 넣는다 — dry-run 실측(2026-09-08)으로 최신
`ragas` 가 `openai` 를 **3.0.0 → 2.54.0** 으로 내리는데, 그 3.0.0 이 2026-09-03 타임아웃
실측의 관측 대상이다. 격리 환경에서는 그 다운그레이드가 **아무것도 안 망가뜨린다** — 지킬
`.venv` 가 거기 없다. 그래서 여기서는 `openai` 를 안 박는다.

**대신 박아야 하는 핀이 따로 있었고, 그것은 예상 밖이었다.** `langchain-community` 0.4.x 가
`chat_models.vertexai` 를 지웠는데 **ragas 0.3.1·0.4.3 이 둘 다 모듈 로드 시점에 그것을
import 한다** — 핀 없이 부르면 `ModuleNotFoundError` 로 **한 줄도 못 돌린다**(실측). 즉
「최신 조합이면 된다」가 여기서는 거짓이다.

## 왜 저장소 코드를 안 쓰나

쓸 수 있으면 `evaluation/` 의 다른 도구처럼 짰을 것이다. 못 쓰는 이유는 위 의존성 하나이고,
그 대가는 **기본 pytest 가 이 파일을 import 조차 못 한다**는 것이다. 그래서 잠그는 자리를
재료 쪽(`ragas_export.py` · `tests/evaluation/test_ragas_export.py`)에 뒀다. 여기서 지키는
규약은 둘뿐이다 — **입력 스키마를 안 바꾸는 것**과 **버전을 출력에 싣는 것**.

## 지표 넷 — 한 번에 하나만 돈다

`Faithfulness` 는 이 저장소의 «근거 지지»(`answer_eval` ②)와 **같은 축**인데 그건 다섯 실행
240/240 으로 포화해 2026-08-29 에 강등됐다. 같은 축을 다른 라이브러리로 한 번 더 재는 것은
포화를 두 번 관측하는 것이다. 그래서 안 넣었다.

`reference`(산문 골든)를 요구하는 셋은 **2026-09-09 에 세트에 그 칸이 생겨 열렸다.**
`ContextRecall`·`NoiseSensitivity` 는 아직 안 넣는다 — 재는 축이 검색 쪽이라 이번 실행의 물음
(«답이 맞나»)과 다르다.

| 이름 | 필수 칸 (2026-09-09 dry-run 실측) | 성질 |
|---|---|---|
| `answer_relevancy` | `user_input`·`response` | 기존. 2026-09-08 에 기각됐다 |
| `semantic_similarity` | `response`·`reference` | **임베딩만 · LLM 0** |
| `factual_correctness` | `response`·`reference` | `ClaimDecompositionPrompt` + `nli_prompt` |
| `answer_correctness` | `user_input`·`response`·`reference` | 아래 참고 |

**`--metric` 이 단수인 것이 회계 때문이다.** `evaluate()` 에 지표를 여럿 주면
`result.total_tokens()` 가 **합계 하나**로 돌아와 지표별 비용이 사라진다. 여럿 받게 만들면
그 경로가 «회계를 지우는 경로» 로 상시 열려 있게 되므로, 부르는 쪽이 여러 번 돌린다.

## `AnswerCorrectness` 는 성분 둘의 가중합이 **아니다** — 실측으로 닫혔다

2026-09-09 dry-run 에서 소스를 읽었다. `_ascore` 가
`np.average([f1_score, similarity_score], weights=[0.75, 0.25])` 인데,

- `similarity_score` 는 `AnswerSimilarity(embeddings=...)` — `SemanticSimilarity` 와 같은
  모듈(`ragas.metrics._answer_similarity`)의 형제다
- `f1_score` 는 **자기 `StatementGeneratorPrompt` + `CorrectnessClassifier`(TP/FP/FN)** 로
  만든다. `FactualCorrectness` 는 `ClaimDecompositionPrompt` + `nli_prompt` 를 쓴다 —
  **분해기가 다른 물건이다**

그래서 «성분 둘을 돌리고 합은 계산한다» 가 **성립하지 않는다.** 셋을 다 내려면 주장 분해
LLM 이 **두 벌** 나가고, 그 값이 얼마인지가 3행 탐침이 답할 것 중 하나다.

## `retrieved_contexts` 는 지표 넷 중 아무도 안 요구한다

실측이다(위 표). 그래도 재료에 싣는 이유는 재료 파일이 *이번 한 번* 이 아니라 **다시 먹일
수 있는 것**이어야 해서다(`AnswerRow` 가 원문을 싣는 것과 같은 기준이다).

## 임베딩 토큰은 회계에 **안 잡힌다** — 0 이 아니라 «못 쟀다» 다

`get_token_usage_for_openai` 는 `LLMResult` 의 `llm_output.token_usage` 만 읽는다(실측:
2026-09-09 에 소스를 그대로 읽었다). 임베딩 호출은 그 자료구조를 안 거치므로
`result.total_tokens()` 에 **한 톨도 안 들어간다.** `semantic_similarity` 는 LLM 을 아예 안
써서 그 실행의 회계가 `0 / 0` 으로 나오는데, 그것은 «안 썼다»(참)이지 «공짜다»(거짓)가 아니다.
그래서 출력 `usage` 에 `embedding_tokens: None` 을 **명시로** 싣는다 — 칸을 빼면 다음 사람이
LLM 토큰만 보고 총액을 낸다.

## 재현되지 않는다 — 이것을 수치 옆에 적어야 한다

`answer_relevancy` 는 답변에서 질문을 **생성해** 원 질의와의 임베딩 유사도를 보고, 생성
횟수가 `strictness`(기본 3)다. 즉 **재는 것 자체에 샘플링이 들어 있다.**
`EvaluatorConfig.evaluator_temperature=0` 이 *"판정은 재현이 값어치라 0"* 으로 박힌 것과
정반대 성질이라, 같은 재료로 다시 돌리면 다른 수가 나올 수 있다. 출력 `meta` 에 `strictness`
와 모델을 실어 그 사실이 표에 남게 한다. **나머지 셋의 재현성은 아직 안 쟀다** —
`semantic_similarity` 는 임베딩만이라 결정적일 것으로 보이지만, 그것을 확인하는 것이
17회차 판정 규칙 2다(같은 재료로 두 번 돌려 같은 수가 나오나).
"""

from __future__ import annotations

import os
import sys

# **자기 디렉터리를 `sys.path` 에서 뺀다.** 스크립트로 부르면 `sys.path[0]` 이 이 파일이 든
# `evaluation/` 이 되는데, 거기 `inspect.py` 가 있어 **표준 라이브러리 `inspect` 를 가린다** —
# pydantic 이 `typing_extensions` 를 거쳐 그것을 끌어다 `ModuleNotFoundError: evaluation` 로
# 죽는다(실측 2026-09-08). 원인이 ragas 쪽으로 보여서 한참 헤맬 자리다.
#
# **`python -P` 로 피하는 안을 기각했다** — 부르는 쪽이 붙이는 규약으로 두면 **안 붙인
# 호출에서만** 깨진다. 한국어 출력에서 `PYTHONIOENCODING` 대신 `main()` 이 자기 출력을
# 스스로 못 박기로 한 것과 같은 기준이다. 아래 import 들이 이 줄 뒤에 와야 해서 E402 다
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != _HERE]

import argparse  # noqa: E402
import json  # noqa: E402
import warnings  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

# 돌릴 수 있는 지표. **값이 «RAGAS 클래스 이름» 인 것은 import 를 `run()` 안에 두기 위해서다**
# — 모듈 레벨에서 ragas 를 끌면 `--help` 조차 격리 환경 밖에서 안 돈다.
# 키가 곧 점수 컬럼명이고(`metric.name`), 그것이 이 표의 두 번째 몫이다
METRIC_CLASSES = {
    "answer_relevancy": "ResponseRelevancy",
    "semantic_similarity": "SemanticSimilarity",
    "factual_correctness": "FactualCorrectness",
    "answer_correctness": "AnswerCorrectness",
}

# 기존 동작을 글자 그대로 보존한다 — 이 파일의 첫 실행(2026-09-08)이 이 지표였다
DEFAULT_METRIC = "answer_relevancy"

# **LLM 을 한 번도 안 부르는 지표.** 이 표가 필요한 이유는 RAGAS 의 회계가 빈 경우를 구분
# 못 하기 때문이다 — 실측(2026-09-09 첫 유료 실행): LLM 호출이 0이면 `result.total_tokens()`
# 가 `0/0` 을 돌려주는 것이 아니라 **`IndexError: list index out of range` 로 죽는다**
# (`ragas/cost.py:223` 의 `self.usage_data[0]`).
#
# 그래서 «비었으니 0» 으로 접으면 안 된다. 빈 `usage_data` 는 두 가지 뜻이다 —
# **①** LLM 을 안 쓰는 지표라 0 이 **참**이거나, **②** 회계가 응답을 못 읽어 **못 잰** 것이다.
# 예외만 보고는 못 가르므로 지표 쪽에 사실을 적어 둔다. ②를 0 으로 적으면 «공짜로 돌았다» 가
# 되는데 그것이 이 저장소가 「죽은 판정자가 만점을 받는다」로 부르는 바로 그 사고다
LLM_FREE_METRICS = frozenset({"semantic_similarity"})


# 재료 JSON 을 읽어 RAGAS 가 받는 모양으로. **키 이름을 여기서 안 바꾼다** — 재료 쪽이
# 이미 RAGAS 이름(`user_input`·`response`·`retrieved_contexts`·`reference`)으로 쓰고 있어
# 옮겨 적는 자리가 없다. 옮겨 적으면 두 파일이 서로 다른 이름을 믿는 상태가 조용히 생긴다
#
# **거르기가 셋이고 서로 곱해진다** — `--query-id`(어느 질의) · `--variant`(self/control) ·
# `--limit`(앞에서 N). 뒤엣것과 앞엣것을 함께 주면 «세 질의를 골랐는데 둘만 돌았다» 가
# 조용히 생겨서 부르는 쪽에서 막는다
def load_material(
    path: Path,
    *,
    limit: int | None = None,
    query_ids: list[str] | None = None,
    variant: str = "both",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("rows") or []
    if not rows:
        raise SystemExit(f"재료가 비었습니다: {path}")

    if variant != "both":
        rows = [r for r in rows if r.get("variant") == variant]
    if query_ids:
        wanted = list(dict.fromkeys(query_ids))
        rows = [r for r in rows if r.get("query_id") in set(wanted)]
        # **고른 것이 다 안 왔으면 죽는다** — 오타 하나가 «3행 견적» 을 2행짜리로 만드는데
        # 출력만 보면 멀쩡하다. 견적의 분모가 조용히 틀리는 것이 이번 실행에서 제일 큰 사고다
        got = {r.get("query_id") for r in rows}
        missing = [q for q in wanted if q not in got]
        if missing:
            raise SystemExit(
                f"재료에 없는 query_id {len(missing)}건: {', '.join(missing)} "
                f"(variant={variant})"
            )
    if limit is not None:
        rows = rows[:limit]
    if not rows:
        raise SystemExit("거르고 나니 행이 하나도 안 남았습니다 — 조건을 확인하세요")
    return rows, payload.get("meta") or {}


# 한 번 돌린다. 점수는 행 순서 그대로 돌아오므로 재료 행에 그대로 얹는다
def run(
    rows: list[dict[str, Any]], *, metric_name: str, model: str, embedding_model: str
) -> dict[str, Any]:
    warnings.filterwarnings("ignore", category=DeprecationWarning)

    import ragas
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings
    from ragas import EvaluationDataset, evaluate
    from ragas import metrics as ragas_metrics
    from ragas.cost import get_token_usage_for_openai
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper

    # **`reference` 를 조건부로 싣는다** — 재료에 그 칸이 있어도 빈 문자열이면 안 싣는다.
    # 빈 골든을 넘기면 지표가 «답이 다 틀렸다» 로 0점을 주는데 그건 라벨이 없다는 뜻이다
    dataset = EvaluationDataset.from_list(
        [
            {
                "user_input": row["user_input"],
                "response": row["response"],
                "retrieved_contexts": list(row.get("retrieved_contexts") or []),
                **(
                    {"reference": row["reference"]}
                    if (row.get("reference") or "").strip()
                    else {}
                ),
            }
            for row in rows
        ]
    )
    metric = getattr(ragas_metrics, METRIC_CLASSES[metric_name])()
    result = evaluate(
        dataset=dataset,
        metrics=[metric],
        llm=LangchainLLMWrapper(ChatOpenAI(model=model)),
        embeddings=LangchainEmbeddingsWrapper(OpenAIEmbeddings(model=embedding_model)),
        token_usage_parser=get_token_usage_for_openai,
        show_progress=True,
    )

    frame = result.to_pandas()
    # **컬럼명은 `metric.name` 이 아닐 수 있다** — 실측(2026-09-09 첫 유료 실행):
    # `FactualCorrectness` 는 `metric.name` 이 `factual_correctness` 인데 `to_pandas()` 가
    # **`factual_correctness(mode=f1)`** 으로 낸다(설정을 이름에 박는다). 이름을 믿고 집으면
    # `KeyError` 이고, `.get()` 으로 물러서면 **전부 `None`** 인 점수가 조용히 나간다.
    #
    # 그래서 이름을 **가정하지 않고 뺄셈으로 찾는다** — 입력으로 넣은 칸을 빼고 남는 것이
    # 점수 칸이다. 하나가 아니면 사유를 내고 죽는다(유료 실행을 헛되이 버리는 쪽이,
    # 무엇을 잰 수인지 모르는 채 표를 내는 것보다 낫다)
    inputs = {"user_input", "retrieved_contexts", "response", "reference"}
    extra = [str(c) for c in frame.columns if str(c) not in inputs]
    name = getattr(metric, "name", metric_name)
    if name in [str(c) for c in frame.columns]:
        column = name
    elif len(extra) == 1:
        column = extra[0]
    else:
        raise SystemExit(
            f"점수 컬럼을 못 고릅니다. 이름 {name!r} 이 없고 입력 밖 칸이 {extra} 입니다 "
            f"(결과 컬럼 {list(frame.columns)})"
        )
    scores: list[float | None] = []
    for value in frame[column].tolist():
        # NaN 은 «못 쟀다» 이고 0.0 과 다른 뜻이다. 0 으로 접으면 실패가 «관련 없음» 으로 읽힌다
        scores.append(None if value != value else float(value))

    # **회계가 비면 죽는다** (`LLM_FREE_METRICS` 주석 참고). 예외를 삼키되 **0 으로 안 접고**
    # 지표 쪽 사실로 «참인 0» 과 «못 쟀다» 를 가른다
    llm_free = metric_name in LLM_FREE_METRICS
    try:
        usage = result.total_tokens()
        input_tokens = getattr(usage, "input_tokens", None)
        output_tokens = getattr(usage, "output_tokens", None)
        usage_note = "관측"
    except IndexError:
        # ragas 가 «LLM 호출 0» 을 빈 목록으로 두고 첫 항목을 집는다
        if llm_free:
            input_tokens = output_tokens = 0
            usage_note = "LLM 을 안 쓰는 지표라 0 이 참이다"
        else:
            input_tokens = output_tokens = None
            usage_note = "**못 쟀다** — LLM 을 쓰는 지표인데 회계가 비었다"

    return {
        "scores": scores,
        "column": column,
        "columns": [str(c) for c in frame.columns],
        "usage_note": usage_note,
        # **호출 수·토큰만 남기고 금액은 안 담는다** — 이 저장소의 비용 로그와 같은 기준이다
        # (단가가 바뀐 날 이미 쌓인 줄이 조용히 거짓이 된다). 금액은 읽는 쪽이 곱한다
        "usage": {
            "model": model,
            "embedding_model": embedding_model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            # 위 두 칸이 «참인 0» 인지 «못 쟀다» 인지. 수만 보면 안 갈린다
            "note": usage_note,
            "llm_free_metric": llm_free,
            # **RAGAS 가 안 준다.** 0 으로 적으면 «임베딩은 공짜» 로 읽히는데
            # `semantic_similarity` 는 임베딩이 비용의 전부다 (모듈 docstring)
            "embedding_tokens": None,
            "rows": len(rows),
        },
        "ragas_version": ragas.__version__,
        "strictness": getattr(metric, "strictness", None),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="RAGAS 지표 하나를 격리 환경에서 한 번 돌린다 (유료)"
    )
    parser.add_argument("--material", required=True, help="ragas_export.py 가 만든 재료 JSON")
    parser.add_argument("--out", required=True, help="점수 JSON 을 쓸 경로")
    # **단수다** — 여럿 주면 `total_tokens()` 가 합계 하나라 지표별 회계가 사라진다
    parser.add_argument(
        "--metric", default=DEFAULT_METRIC, choices=sorted(METRIC_CLASSES), help="돌릴 지표 하나"
    )
    parser.add_argument("--model", default="gpt-4.1-mini", help="LLM 단계에 쓸 모델")
    parser.add_argument("--embedding-model", default="text-embedding-3-small")
    parser.add_argument("--limit", type=int, default=None, help="앞에서 N 행만 (파이프 확인용)")
    parser.add_argument(
        "--query-id",
        action="append",
        dest="query_ids",
        default=None,
        help="이 질의만 (여러 번 줄 수 있다). 비용 탐침처럼 몇 행만 돌릴 때",
    )
    parser.add_argument(
        "--variant",
        default="both",
        choices=("self", "control", "both"),
        help="self 는 제 골든, control 은 남의 골든을 물린 짝",
    )
    args = parser.parse_args()

    # **함께 주면 막는다** — 「세 질의를 골랐는데 둘만 돌았다」가 조용히 생기고,
    # 견적의 분모가 틀리는 것이 이번 실행에서 제일 큰 사고다
    if args.limit is not None and args.query_ids:
        raise SystemExit("--limit 과 --query-id 를 함께 줄 수 없습니다 — 고른 수가 조용히 줄어듭니다")

    # **자기 출력을 스스로 못 박는다.** 격리 환경에는 `PYTHONIOENCODING` 이 없어 한국어가
    # cp949 로 나가 깨진다(실측 2026-09-08, 이 스크립트의 첫 실행이 그랬다). 부르는 쪽이
    # 환경변수를 붙이는 규약으로 두면 **안 붙인 호출에서만** 깨진다 — 위 `sys.path` 와 같은 기준이다
    sys.stdout.reconfigure(encoding="utf-8")

    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY 가 없습니다 — 이 스크립트는 유료 호출을 합니다")

    rows, source_meta = load_material(
        Path(args.material),
        limit=args.limit,
        query_ids=args.query_ids,
        variant=args.variant,
    )
    outcome = run(
        rows,
        metric_name=args.metric,
        model=args.model,
        embedding_model=args.embedding_model,
    )

    scored = []
    for row, score in zip(rows, outcome["scores"], strict=True):
        scored.append({**row, outcome["column"]: score})

    payload = {
        "meta": {
            "tool": "ragas_probe",
            "metric": args.metric,
            "score_column": outcome["column"],
            "material": Path(args.material).name,
            # **재료가 밝힌 대조군 축을 그대로 나른다** — 읽는 쪽이 «이 지표가 그 축을 읽나» 를
            # 판정하는 재료다(`ragas_report.check_readable()`)
            "control_axis": source_meta.get("control_axis"),
            "selection": {
                "variant": args.variant,
                "query_ids": args.query_ids,
                "limit": args.limit,
            },
            "ragas_version": outcome["ragas_version"],
            # **재는 것 자체에 샘플링이 들어 있다** (모듈 docstring 참고) — 재현되지 않는다는 사실이
            # 표에 남아야 해서 실행마다 싣는다. `answer_relevancy` 밖에서는 None 이다
            "strictness": outcome["strictness"],
            "usage": outcome["usage"],
            "source": source_meta.get("sources"),
        },
        "rows": scored,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    usage = outcome["usage"]
    failed = sum(1 for s in outcome["scores"] if s is None)
    print(f"{len(rows)}행 채점 ({args.metric}) → {out}")
    print(f"  ragas {outcome['ragas_version']} · strictness {outcome['strictness']}")
    print(f"  점수 컬럼 {outcome['column']} · 결과 컬럼 {outcome['columns']}")
    print(f"  LLM 토큰 입력 {usage['input_tokens']} · 출력 {usage['output_tokens']} — {usage['note']}")
    # **0 이 아니라 «못 쟀다» 다** — RAGAS 의 회계가 LLMResult 만 읽어 임베딩이 안 들어온다
    print("  임베딩 토큰: 못 쟀다 (RAGAS 회계가 LLM 호출만 센다 — 모듈 docstring)")
    if failed:
        # 실패를 0 으로 접지 않는다 — 죽은 채점자가 만점을 받는 것의 거울상이다
        print(f"  **못 잰 행 {failed}건** — 0 이 아니라 None 으로 나갔다")


if __name__ == "__main__":
    main()
