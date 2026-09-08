"""RAGAS `ResponseRelevancy` 를 **격리 환경에서** 한 판 돌린다. 이 파일만 저장소 코드를 안 쓴다.

## 부르는 법 — `.venv` 로 돌리지 않는다

```
uv run --no-project --python 3.11 --with "ragas==0.4.3" --with "langchain-community<0.4" \
    evaluation/ragas_probe.py --material <재료.json> --out <점수.json>
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

## 왜 이 지표 하나인가

`Faithfulness` 는 이 저장소의 «근거 지지»(`answer_eval` ②)와 **같은 축**인데 그건 다섯 판
240/240 으로 포화해 2026-08-29 에 강등됐다. 같은 축을 다른 라이브러리로 한 번 더 재는 것은
포화를 두 번 관측하는 것이다. `reference`(골든 답변)를 요구하는 다섯(`ContextRecall`·
`AnswerCorrectness`·`FactualCorrectness`·`SemanticSimilarity`·`NoiseSensitivity`)은 세트에
그 칸이 없어 **원천 불가**다. 남는 것이 `ResponseRelevancy` 하나이고, 그것이 CLAUDE.md 가
*"«답이 맞나» 는 여전히 못 잰다"* 로 적어 둔 구멍과 같은 자리다.

**`retrieved_contexts` 는 이 지표가 요구하지 않는다**(실측 — 필수 칸이 `user_input`·`response`
둘뿐이다). 그래도 재료에 싣는 이유는 재료 파일이 *이 판 한 번* 이 아니라 **다시 먹일 수 있는
것**이어야 해서다(`AnswerRow` 가 원문을 싣는 것과 같은 잣대).

## 재현되지 않는다 — 이것을 수치 옆에 적어야 한다

이 지표는 답변에서 질문을 **생성해** 원 질의와의 임베딩 유사도를 보고, 생성 횟수가
`strictness`(기본 3)다. 즉 **샘플링이 자[尺] 안에 있다.** `EvaluatorConfig.evaluator_temperature=0`
이 *"판정은 재현이 값어치라 0"* 으로 박힌 것과 정반대 성질이라, 같은 재료로 다시 돌리면 다른
수가 나올 수 있다. 출력 `meta` 에 `strictness` 와 모델을 실어 그 사실이 표에 남게 한다.
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
# 스스로 못 박기로 한 것과 같은 잣대다. 아래 import 들이 이 줄 뒤에 와야 해서 E402 다
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != _HERE]

import argparse  # noqa: E402
import json  # noqa: E402
import warnings  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

# 지시문·필드 이름이 바뀌면 수가 갈리므로 무엇으로 쟀는지를 반드시 남긴다
METRIC_NAME = "answer_relevancy"


# 재료 JSON 을 읽어 RAGAS 가 받는 모양으로. **키 이름을 여기서 안 바꾼다** — 재료 쪽이
# 이미 RAGAS 이름(`user_input`·`response`·`retrieved_contexts`)으로 쓰고 있어 옮겨 적는
# 자리가 없다. 옮겨 적으면 두 파일이 서로 다른 이름을 믿는 상태가 조용히 생긴다
def load_material(path: Path, limit: int | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("rows") or []
    if not rows:
        raise SystemExit(f"재료가 비었습니다: {path}")
    if limit is not None:
        rows = rows[:limit]
    return rows, payload.get("meta") or {}


# 한 판 돌린다. 점수는 행 순서 그대로 돌아오므로 재료 행에 그대로 얹는다
def run(rows: list[dict[str, Any]], *, model: str, embedding_model: str) -> dict[str, Any]:
    warnings.filterwarnings("ignore", category=DeprecationWarning)

    import ragas
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings
    from ragas import EvaluationDataset, evaluate
    from ragas.cost import get_token_usage_for_openai
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import ResponseRelevancy

    dataset = EvaluationDataset.from_list(
        [
            {
                "user_input": row["user_input"],
                "response": row["response"],
                "retrieved_contexts": list(row.get("retrieved_contexts") or []),
            }
            for row in rows
        ]
    )
    metric = ResponseRelevancy()
    result = evaluate(
        dataset=dataset,
        metrics=[metric],
        llm=LangchainLLMWrapper(ChatOpenAI(model=model)),
        embeddings=LangchainEmbeddingsWrapper(OpenAIEmbeddings(model=embedding_model)),
        token_usage_parser=get_token_usage_for_openai,
        show_progress=True,
    )

    frame = result.to_pandas()
    scores: list[float | None] = []
    for value in frame[METRIC_NAME].tolist():
        # NaN 은 «못 쟀다» 이고 0.0 과 다른 뜻이다. 0 으로 접으면 실패가 «관련 없음» 으로 읽힌다
        scores.append(None if value != value else float(value))

    usage = result.total_tokens()
    return {
        "scores": scores,
        # **호출 수·토큰만 남기고 금액은 안 담는다** — 이 저장소의 비용 로그와 같은 잣대다
        # (단가가 바뀐 날 이미 쌓인 줄이 조용히 거짓이 된다). 금액은 읽는 쪽이 곱한다
        "usage": {
            "model": model,
            "embedding_model": embedding_model,
            "input_tokens": getattr(usage, "input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None),
            "rows": len(rows),
        },
        "ragas_version": ragas.__version__,
        "strictness": getattr(metric, "strictness", None),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="RAGAS ResponseRelevancy 를 격리 환경에서 한 판 돌린다 (유료)"
    )
    parser.add_argument("--material", required=True, help="ragas_export.py 가 만든 재료 JSON")
    parser.add_argument("--out", required=True, help="점수 JSON 을 쓸 경로")
    parser.add_argument("--model", default="gpt-4.1-mini", help="질문 생성에 쓸 모델")
    parser.add_argument("--embedding-model", default="text-embedding-3-small")
    parser.add_argument("--limit", type=int, default=None, help="앞에서 N 행만 (파이프 확인용)")
    args = parser.parse_args()

    # **자기 출력을 스스로 못 박는다.** 격리 환경에는 `PYTHONIOENCODING` 이 없어 한국어가
    # cp949 로 나가 깨진다(실측 2026-09-08, 이 스크립트의 첫 실행이 그랬다). 부르는 쪽이
    # 환경변수를 붙이는 규약으로 두면 **안 붙인 호출에서만** 깨진다 — 위 `sys.path` 와 같은 잣대다
    sys.stdout.reconfigure(encoding="utf-8")

    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY 가 없습니다 — 이 스크립트는 유료 호출을 합니다")

    rows, source_meta = load_material(Path(args.material), args.limit)
    outcome = run(rows, model=args.model, embedding_model=args.embedding_model)

    scored = []
    for row, score in zip(rows, outcome["scores"], strict=True):
        scored.append({**row, METRIC_NAME: score})

    payload = {
        "meta": {
            "tool": "ragas_probe",
            "metric": METRIC_NAME,
            "material": Path(args.material).name,
            "ragas_version": outcome["ragas_version"],
            # **샘플링이 자[尺] 안에 있다** (모듈 docstring 참고) — 재현되지 않는다는 사실이
            # 표에 남아야 해서 판마다 싣는다
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
    print(f"{len(rows)}행 채점 → {out}")
    print(f"  ragas {outcome['ragas_version']} · strictness {outcome['strictness']}")
    print(f"  토큰 입력 {usage['input_tokens']} · 출력 {usage['output_tokens']}")
    if failed:
        # 실패를 0 으로 접지 않는다 — 죽은 채점자가 만점을 받는 것의 거울상이다
        print(f"  **못 잰 행 {failed}건** — 0 이 아니라 None 으로 나갔다")


if __name__ == "__main__":
    main()
