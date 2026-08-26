"""Evaluator 의 판정을 채점한다. **`run_eval` 이 못 재는 것을 재는 자[尺]다.**

## 왜 `run_eval` 이 아니라 새 도구인가

`run_eval` 이 재는 것은 **순위**다(`hit@k`·`MRR`·`nDCG`). verdict 는 순위를 안 바꾸므로 저쪽
지표는 판정이 맞든 틀리든 **똑같은 수를 낸다** — 리랭커를 `recall@후보수` 로 재면 차이가
정의상 0 이던 것과 같은 함정이다(`plan_log/검색-생성.md` 10회차가 이 자를 가르라고 적었다).
게다가 `run_eval` 의 리포트 JSON 은 `as_baseline()`·`compare_to_baseline()` 에 물려 있어,
거기에 판정 지표를 얹으면 그 파일이 무엇을 재는 파일인지가 흐려진다.

뼈대는 `negatives.py`·`rerank_diff.py` 와 같다 — **실행 라벨의 컬렉션을 열어 다시 검색한다.**
색인을 다시 하지 않는 이유도 같다(그 사이 로더가 바뀐 만큼 청크가 달라진다).

## 정답표를 세트에 고정하지 않는다 — 이 도구의 핵심이다

부정 예시 23건은 답이 코퍼스에 없으므로 늘 *불충분* 이 옳다. **긍정 20건은 아니다** —
검색이 근거를 안 물어왔으면 *불충분* 이 **옳은 답**이다. 실측(`h-ceiling-kure`, 리랭커 off)
으로 `hit@5` 8/20 · `hit@10` 14/20 이라, `@10` 에서도 여섯 건은 근거 없이 온다.

그래서 기대 verdict 를 **그 실행의 순위로 매번 다시 만든다.** 세트에 박아 두면 옳게
*불충분* 이라 답한 건이 오답으로 세어지고, 그 순간 이 표는 Evaluator 가 아니라 **검색기를
재게 된다.** 반대로 말하면 이 표는 *"검색이 가져온 것을 Evaluator 가 제대로 읽었나"* 만 잰다.

## 실패는 칸이 아니라 **열**이다

`verdict="failed"` 는 *충분* 도 *불충분* 도 아니라 판정을 못 한 것이다. 혼동행렬 칸에 섞으면
죽은 Evaluator 가 부정 23건에서 만점을 받아 **점수가 실력이 아니라 실패율을 잰다**(10회차).
그래서 열을 따로 두고 사유(`warnings`)를 버킷으로 함께 센다.

## `--top-k` 를 둘 이상 받는 이유

**판정 대상이 몇 개인가가 분모를 통째로 바꾼다.** 5면 기대가 *충분 8 / 불충분 12*, 10이면
*충분 14 / 불충분 6* 이다(부정 23은 그대로). 한쪽만 재면 그 기울기를 못 본다. 그리고
`GENERATION_TOP_K=5` 는 config 에 *"대가를 재는 쪽이 없어 못 정한다"* 로 적힌 임시값인데,
이 채점이 그 대가를 재는 첫 자다.

## 임베딩 프로필은 인자로 안 받고 리포트에서 읽는다

컬렉션 이름에 임베딩 모델이 들어가서, 손으로 넘기면 같은 디렉터리의 **다른 컬렉션**을 열어
0건이 나온다(`negatives.py` 의 첫 실행이 실제로 걸렸다). `require_fresh_store()` 가 소리 내어
막는 것까지 그대로 쓴다.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from evaluation.config import DEFAULT_EMBEDDING_PROFILE, DEFAULT_RERANK_PROFILE, eval_config
from evaluation.inspect import load_report, require_fresh_store
from evaluation.qa_set import QA_SET_PATH, load_qa_set
from evaluation.run_eval import RESULTS_DIR
from evaluation.scoring import hit_rank

__all__ = ["VerdictRow", "confusion", "score_verdicts"]

# 기대와 실제가 가질 수 있는 값. `failed` 는 **실제 쪽에만** 나온다 — 기대는 검색 결과가
# 만들고 검색은 실패라는 상태를 안 갖는다
_JUDGEMENTS = ("sufficient", "insufficient")
_ACTUALS = ("sufficient", "insufficient", "failed")

# 표의 왼쪽 위 칸. f-string 안에 백슬래시를 못 써서 밖에 둔다 (py3.11)
_HEADER = "기대\\실제"


# 질의 하나의 채점 결과. 틀린 칸을 사람이 읽을 수 있게 사유와 순위를 함께 남긴다
@dataclass
class VerdictRow:
    query_id: str
    query: str
    source_doc: str
    # 답이 코퍼스에 있나 (세트 라벨). 부정 예시면 거짓이다
    answerable: bool
    # 이 실행에서 정답이 몇 등으로 왔나. 못 왔으면 None. **기대 verdict 를 만드는 값이다**
    rank: int | None
    expected: str
    actual: str
    reasoning: str = ""
    warnings: list[str] = field(default_factory=list)

    # 맞았나. **실패는 맞은 것도 틀린 것도 아니라 따로 센다**
    @property
    def correct(self) -> bool:
        return self.actual == self.expected


# 기대 verdict. **부정은 늘 불충분이고 긍정은 그 실행의 순위가 정한다** (모듈 docstring 참고)
def expected_verdict(*, answerable: bool, rank: int | None) -> str:
    if not answerable:
        return "insufficient"
    return "sufficient" if rank is not None else "insufficient"


# 세트 하나를 한 `top_k` 로 채점한다. 판정은 evaluator 가 하고 이 함수는 재기만 한다
def score_verdicts(
    *,
    label: str,
    fmt: str,
    top_k: int,
    qa_path: Path | None = None,
    rerank: str = DEFAULT_RERANK_PROFILE,
) -> tuple[list[VerdictRow], dict[str, Any]]:
    from rag_pipeline.generation import get_evaluator
    from rag_pipeline.ingestion.embedding import get_embedder
    from rag_pipeline.ingestion.indexing import get_store
    from rag_pipeline.retrieval import build_bm25_index, get_reranker, retrieve

    qa = load_qa_set(qa_path)
    if not qa.items:
        raise SystemExit(f"세트가 비어 있습니다: {qa.path}")

    report = load_report(label, fmt)
    embedding = report.get("embedding_profile") or DEFAULT_EMBEDDING_PROFILE
    config = eval_config(
        qdrant_path=RESULTS_DIR / f"qdrant-{report['label']}-{fmt}",
        embedding=embedding,
        rerank=rerank,
    )

    evaluator = get_evaluator(config.evaluator)
    if evaluator is None:
        # **조용히 안 넘어간다** — 판정 없이 도는 채점은 전건 실패인데 표는 멀쩡해 보인다
        raise SystemExit(
            "판정이 꺼져 있습니다: EVAL_SETTINGS['evaluator']['evaluator_model'] 이 비었습니다"
        )

    embedder = get_embedder(config.embedding)
    store = get_store(config.indexing, config.embedding)
    reranker = get_reranker(config.rerank)

    rows: list[VerdictRow] = []
    try:
        total = store.count()
        require_fresh_store(
            total, sum(x["chunks"] for x in report["indexed"]), label=report["label"], fmt=fmt
        )
        bm25 = build_bm25_index(store)
        print(f"컬렉션 {total}건 · 세트 {len(qa.items)}건 · top_k {top_k} · 리랭커 {rerank}")

        for item in qa.items:
            chunks = retrieve(
                item.query,
                store=store,
                embedder=embedder,
                top_k=top_k,
                bm25=bm25,
                reranker=reranker,
                config=config.retrieval,
            )
            # 기대는 **이 실행의 결과**로 만든다. 세트에 박힌 정답표를 안 쓴다
            rank = (
                hit_rank([c.text for c in chunks], item.gold_spans)
                if item.answerable and item.gold_spans
                else None
            )

            evaluator.usage.reset()
            seen = len(evaluator.warnings)
            verdict = evaluator.evaluate(item.query, chunks)

            rows.append(
                VerdictRow(
                    query_id=item.query_id,
                    query=item.query,
                    source_doc=item.source_doc,
                    answerable=item.answerable,
                    rank=rank,
                    expected=expected_verdict(answerable=item.answerable, rank=rank),
                    actual=verdict.verdict,
                    reasoning=verdict.reasoning,
                    warnings=list(evaluator.warnings[seen:]),
                )
            )
    finally:
        # 안 닫으면 다음 실행이 배타 잠금으로 통째로 죽는다
        store.close()

    meta = {
        "label": report["label"],
        "format": fmt,
        "top_k": top_k,
        "embedding_profile": embedding,
        "rerank_profile": rerank,
        "queries": len(rows),
        "bm25_warnings": list(bm25.warnings),
        "rerank_warnings": list(getattr(reranker, "warnings", []) or []),
    }
    return rows, meta


# 혼동행렬. **실패는 칸이 아니라 열이다** (모듈 docstring 참고)
def confusion(rows: list[VerdictRow]) -> dict[str, dict[str, int]]:
    table = {want: {got: 0 for got in _ACTUALS} for want in _JUDGEMENTS}
    for row in rows:
        table[row.expected][row.actual] += 1
    return table


# 사람이 읽는 모양. 맞은 수와 실패 수를 **따로** 낸다 — 합치면 실패율이 정확도로 읽힌다
def _print(rows: list[VerdictRow], meta: dict[str, Any], *, show_wrong: bool) -> None:
    table = confusion(rows)
    failed = sum(1 for r in rows if r.actual == "failed")
    correct = sum(1 for r in rows if r.correct)
    judged = len(rows) - failed

    print(f"\n{'=' * 78}")
    print(f"top_k {meta['top_k']} · {meta['label']}-{meta['format']} · 질의 {len(rows)}건")
    print(f"{_HEADER:<14}{'sufficient':>12}{'insufficient':>14}{'failed':>9}")
    for want in _JUDGEMENTS:
        got = table[want]
        print(
            f"{want:<14}{got['sufficient']:>12}{got['insufficient']:>14}{got['failed']:>9}"
        )
    print(f"\n맞은 것 {correct}/{judged} (판정이 난 건만) · 실패 {failed}건")

    buckets = Counter(w for r in rows for w in r.warnings)
    if buckets:
        print("실패 버킷:")
        for text, count in buckets.most_common():
            print(f"  {count:>3}  {text}")
    else:
        print("실패 버킷: (없음)")

    for key in ("bm25_warnings", "rerank_warnings"):
        if meta[key]:
            print(f"  경고 {key}: {meta[key]}")

    if not show_wrong:
        return
    print(f"\n{'-' * 78}\n틀린 것과 실패")
    for row in rows:
        if row.correct and row.actual != "failed":
            continue
        mark = "실패" if row.actual == "failed" else f"{row.expected} -> {row.actual}"
        place = f"정답 {row.rank}위" if row.rank else ("부정 예시" if not row.answerable else "정답 없음")
        print(f"\n  [{row.query_id}] {mark} · {place}\n    {row.query}\n    {row.reasoning}")


# CLI 진입점
def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluator 의 판정을 혼동행렬로 채점한다")
    parser.add_argument("--label", required=True, help="읽을 실행 라벨 (그 컬렉션을 연다)")
    parser.add_argument("--format", dest="fmt", default="pdf")
    parser.add_argument("--qa-set", dest="qa_set", default=str(QA_SET_PATH))
    # **둘 이상을 받는 것이 기본이다** (모듈 docstring 참고)
    parser.add_argument("--top-k", type=int, nargs="+", default=[5, 10])
    parser.add_argument("--rerank", default=DEFAULT_RERANK_PROFILE)
    parser.add_argument("--wrong", action="store_true", help="틀린 것과 실패를 하나씩 보여준다")
    parser.add_argument("--json", action="store_true", help="결과를 JSON 으로도 남긴다")
    args = parser.parse_args()

    payload: dict[str, Any] = {"runs": []}
    for top_k in args.top_k:
        rows, meta = score_verdicts(
            label=args.label,
            fmt=args.fmt,
            top_k=top_k,
            qa_path=Path(args.qa_set),
            rerank=args.rerank,
        )
        _print(rows, meta, show_wrong=args.wrong)
        payload["runs"].append(
            {"meta": meta, "confusion": confusion(rows), "rows": [asdict(r) for r in rows]}
        )

    if args.json:
        out = RESULTS_DIR / f"{args.label}-verdicts.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n남김: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
