"""리랭커의 **점수**를 캐시에서 읽어 「왜 `cohere` 가 더 수렴하나」에 답하는 도구.
`rerank_diff.py`·`inspect.py` 처럼 **재지 않고 읽는다.**

## 왜 새 모듈인가 — 기존 둘이 이 축을 못 본다

- `rerank_diff.py` 는 «누가 정답을 밀어냈나» 에 답한다. 재작성기를 **안 넘기므로**
  `fuse`·`replace` 실행의 풀은 원리적으로 못 되살린다 — 이 판이 재는 것이 바로 그 풀이다
- `overlap.py` 는 구간별 ↑↓ 개수를 센다. 점수 자체를 안 본다

39회차가 잰 것은 *얼마나* 수렴하는가였다(리랭킹 뒤 상위 n 이 `off`/`fuse` 사이에 같은 질의
수가 `cohere` 60·55·49·34·13 / `local-gpu` 59·53·47·25·8). *왜* 는 안 쟀다.

## 왜 비용 0인가 — 리랭커 객체를 아예 안 만든다

`.cache/rerank` 의 파일명이 곧 `content_hash(rerank_cache_key(원질의, 풀, salt))` 라,
**키를 손으로 만들어 읽는다.** 유료 클라이언트가 존재하지 않으므로 과금이 **구조적으로
불가능**하고, 미스는 값이 아니라 «키가 없다» 로 시끄럽게 죽는다 — 그것이 곧 *풀 재현이
틀렸다* 는 신호다. salt 규칙은 `reranker/__init__.py:rerank_cache_salt()` 한 벌이다.

**재작성기만은 만든다** — `fuse`·`replace` 풀이 그것 없이는 안 나온다. 대신 질의마다
`usage.api_calls` 를 검사해 **캐시 히트가 아니면 그 자리에서 멈춘다**(미스는 과금이면서
동시에 *그때 그 풀이 아니다* 라는 뜻이다).

## 무엇을 답하나

1. **독립성** — 공유 후보(두 풀 모두에 든 후보)의 점수가 `off`/`fuse` 에서 같은가.
   같으면 상위 n 일치는 **오직 신참이 어디에 앉는가** 로 정해진다. 다르면 그 자체가 답이다
   («그 리랭커는 후보 구성을 탄다»). 값 일치와 **상대 순서 보존**을 따로 재는 것은,
   API 갈래가 미세하게 다른 수를 낼 수 있어서다
2. **신참 배치** — 재작성이 데려온 후보가 리랭킹 뒤 어느 자리에 앉나
3. **예측** — 2 의 분포만으로 **안 쓴 판**의 상위 n 일치 수를 계산해 실측과 견준다
   (`predict_agreement()`). 통과선은 안 둔다 — 판정은 **부호**로만 한다

## 점수 절대값을 두 갈래에서 견주지 않는다

`cohere` 는 relevance score 고 cross-encoder 는 logit 이다. 그래서 이 모듈이 갈래를
가로질러 견주는 값은 전부 **풀 안의 순위·분위수**다. 갈래 **안에서만** 점수 차를 쓴다
(같은 모델이 같은 쌍에 준 점수라 뜻이 있다).
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from evaluation.config import eval_config
from evaluation.inspect import require_fresh_store
from evaluation.qa_set import QAItem, load_qa_set
from evaluation.rerank_diff import load_run
from evaluation.run_eval import RESULTS_DIR, split_scorable
from evaluation.scoring import hit_rank

__all__ = [
    "DEPTHS",
    "PairRow",
    "agreement",
    "compare_pair",
    "newcomer_percentiles",
    "predict_agreement",
    "summarize",
]

# 39회차가 쓴 깊이 그대로다. **바꾸면 그 표와 대조가 안 된다**
DEPTHS = (1, 3, 5, 10, 20)


# 질의 하나에서 두 실행(`off` · 재작성 켠 쪽)을 견준 결과
@dataclass
class PairRow:
    query_id: str
    # 리랭킹 뒤 순서(청크 신원). 두 실행 각각
    off_ranked: list[str]
    on_ranked: list[str]
    # 공유 후보 수와, 그 후보들의 점수가 두 풀에서 같은가
    shared: int
    score_identical: bool
    order_preserved: bool
    max_abs_delta: float
    # 공유 후보끼리 순서가 뒤집힌 쌍 수와, 그 쌍의 `off` 쪽 점수 차 중 최대.
    # **`max_abs_delta` 이하이면 뒤집힘이 「후보 구성을 탄다」가 아니라 동점 근처의 잡음이다**
    inversions: int
    max_inversion_gap: float
    # 재작성이 데려온 후보와 그것이 앉은 자리(1부터). 리랭킹 **뒤** 순서다
    newcomers: int
    newcomer_ranks: list[int]
    # 리랭킹 뒤 정답 순위. 리포트와 대조하는 재현 게이트다
    off_rank: int | None
    on_rank: int | None
    report_off_rank: int | None
    report_on_rank: int | None
    # 깊이별 상위 n 집합이 같은가
    agree: dict[str, bool] = field(default_factory=dict)
    # 재현이 리포트와 어긋났으면 여기 남는다. **비어 있어야 이 행을 믿을 수 있다**
    warnings: list[str] = field(default_factory=list)


# 깊이 n 에서 두 순서의 상위 n 이 **집합으로** 같은가. 39회차의 «같은 질의 수» 가 이것이다
def agreement(left: list[str], right: list[str], depth: int) -> bool:
    return set(left[:depth]) == set(right[:depth])


# 신참이 앉은 자리를 풀 크기로 나눈 분위수. **갈래를 가로질러 견줄 수 있는 유일한 값이다**
# (점수 절대값은 스케일이 달라 못 견준다 — 모듈 docstring)
def newcomer_percentiles(rows: list[PairRow]) -> list[float]:
    out: list[float] = []
    for row in rows:
        size = len(row.on_ranked)
        if size == 0:
            continue
        out.extend(rank / size for rank in row.newcomer_ranks)
    return sorted(out)


# 신참 배치 분포만으로 상위 n 일치 수를 예측한다. **대상 실행의 점수를 안 본다** —
# 보면 예측이 아니라 재계산이다. 쓰는 것은 «신참 수» 하나이고, 배치는 **다른 짝에서 잰
# 경험 분포**에서 온다.
#
# **결정적 배치가 아니라 기대값이다.** 신참 m 개를 분포에서 고르게 뽑아 자리에 앉히면
# 질의마다 같은 모양이 되어 «질의 사이의 흔들림» 이 통째로 사라진다(첫 판에서 @3 을 60 으로,
# @20 을 0 으로 냈다 — 실측은 50 과 5 다). 그래서 자리를 정하는 대신 **상위 n 에 하나도
# 안 앉을 확률**을 쓴다.
#
# 성립 조건이 하나 있다 — **공유 후보의 상대 순서가 보존될 때** «상위 n 이 갈린다» 와
# «신참이 상위 n 에 들어왔다» 가 같은 말이 된다. 그 보존은 이 도구가 함께 재는 값이고,
# 안 지켜진 행만큼 예측이 위로 뜬다(그래서 판정을 절대값이 아니라 **부호**로 한다)
def predict_agreement(rows: list[PairRow], percentiles: list[float]) -> dict[str, float]:
    counts: dict[str, float] = {}
    for depth in DEPTHS:
        total = 0.0
        for row in rows:
            size = len(row.on_ranked)
            if size == 0 or not percentiles:
                continue
            cut = depth / size
            hit = sum(1 for value in percentiles if value <= cut) / len(percentiles)
            total += (1.0 - hit) ** row.newcomers
        counts[str(depth)] = round(total, 1)
    return counts


# 실측 요약. **예측과 같은 모양으로 낸다** — 나란히 놓고 부호만 보면 되게
def summarize(rows: list[PairRow]) -> dict[str, Any]:
    percentiles = newcomer_percentiles(rows)
    mid = percentiles[len(percentiles) // 2] if percentiles else None
    return {
        "queries": len(rows),
        "agreement": {
            str(depth): sum(1 for row in rows if row.agree[str(depth)]) for depth in DEPTHS
        },
        "score_identical": sum(1 for row in rows if row.score_identical),
        "order_preserved": sum(1 for row in rows if row.order_preserved),
        "max_abs_delta": max((row.max_abs_delta for row in rows), default=0.0),
        "inversions": sum(row.inversions for row in rows),
        "max_inversion_gap": max((row.max_inversion_gap for row in rows), default=0.0),
        "newcomers_total": sum(row.newcomers for row in rows),
        "newcomer_percentile_median": mid,
        "newcomer_in_top10": sum(1 for row in rows for rank in row.newcomer_ranks if rank <= 10),
        "warned_rows": sum(1 for row in rows if row.warnings),
    }


# 공유 후보끼리 순서가 뒤집힌 쌍을 센다. **뒤집힘 자체가 「후보 구성을 탄다」의 증거는
# 아니다** — 두 점수가 사실상 같으면 잡음 한 톨이 순서를 바꾼다. 그래서 뒤집힌 쌍의
# `off` 쪽 점수 차를 함께 돌려주고, 그것이 `max_abs_delta` 이하인지를 부르는 쪽이 읽는다
def _inversions(
    off_order: list[str], on_order: list[str], scores: dict[str, float]
) -> tuple[int, float]:
    seat = {cid: i for i, cid in enumerate(on_order)}
    count = 0
    gap = 0.0
    for i, first in enumerate(off_order):
        for second in off_order[i + 1 :]:
            if seat[first] > seat[second]:
                count += 1
                gap = max(gap, abs(scores[first] - scores[second]))
    return count, gap


# 캐시에서 점수를 읽는다. **없으면 None** 이고, 부르는 쪽이 그것을 «재현이 틀렸다» 로 읽는다
def _cached_scores(query: str, pool: list[Any], *, cache: Any, salt: str) -> list[float] | None:
    from rag_pipeline.ingestion.loader.content.base import content_hash
    from rag_pipeline.retrieval.reranker.base import rerank_cache_key

    cached = cache.get(content_hash(rerank_cache_key(query, pool, salt=salt)))
    scores = cached.get("scores") if isinstance(cached, dict) else None
    if isinstance(scores, list) and len(scores) == len(pool):
        return [float(value) for value in scores]
    return None


# 실행 하나의 풀을 되살리고 캐시 점수를 얹는다. `rerank_diff.py` 의 재현 규약을 그대로 쓰되
# **재작성기를 넘긴다** — 그것이 이 모듈이 저쪽을 못 빌려 쓰는 이유다
class _Side:
    def __init__(self, report: dict[str, Any], stem: str) -> None:
        from rag_pipeline.ingestion.embedding import get_embedder
        from rag_pipeline.ingestion.indexing import get_store
        from rag_pipeline.ingestion.loader.content.base import ResultCache
        from rag_pipeline.retrieval import build_bm25_index, get_rewriter
        from rag_pipeline.retrieval.reranker import rerank_cache_salt

        self.report = report
        self.stem = stem
        self.config = eval_config(
            qdrant_path=RESULTS_DIR / f"qdrant-{stem}",
            embedding=report["embedding_profile"],
            rerank=report["rerank_profile"],
            rewrite=report.get("rewrite_profile", "off"),
        )
        self.limit = max(self.config.retrieval.candidate_k, report["k"])
        self.salt = rerank_cache_salt(self.config.rerank)
        # **캐시를 강제로 켠다** — 프로필이 껐으면 읽을 것이 없어 이 도구가 성립하지 않는다
        self.cache = ResultCache(self.config.rerank.rerank_cache_dir, enabled=True)
        self.embedder = get_embedder(self.config.embedding)
        self.store = get_store(self.config.indexing, self.config.embedding)
        require_fresh_store(
            self.store.count(),
            sum(x["chunks"] for x in report["indexed"]),
            label=report["label"],
            fmt=report["format"],
        )
        self.bm25 = build_bm25_index(self.store) if report["retriever"]["use_bm25"] else None
        self.rewriter = get_rewriter(self.config.rewrite)
        self.ranks = {row["query_id"]: row["rank"] for row in report["per_query"]}

    # 질의 하나의 풀과 점수. 재작성기가 캐시를 벗어나면 **거기서 멈춘다**
    def pool_and_scores(self, item: QAItem) -> tuple[list[Any], list[float] | None]:
        from rag_pipeline.retrieval import retrieve

        if self.rewriter is not None:
            self.rewriter.usage.reset()
        pool = retrieve(
            item.query,
            store=self.store,
            embedder=self.embedder,
            top_k=self.limit,
            bm25=self.bm25,
            reranker=None,
            rewriter=self.rewriter,
            config=self.config.retrieval,
        )
        if self.rewriter is not None and self.rewriter.usage.api_calls:
            raise SystemExit(
                f"{self.stem}/{item.query_id}: 재작성이 캐시를 벗어났습니다"
                " (.cache/rewrite 미스) · 그 질의의 풀은 그때 그 풀이 아닙니다"
            )
        pool = pool[: max(self.config.rerank.rerank_candidates, 1)]
        return pool, _cached_scores(item.query, pool, cache=self.cache, salt=self.salt)

    def close(self) -> None:
        self.store.close()


# 두 실행을 질의마다 견준다. **색인하지 않는다**
def compare_pair(
    off_name: str, on_name: str, *, qa_set_path: Path | None = None
) -> tuple[list[PairRow], dict[str, Any]]:
    from rag_pipeline.retrieval.reranker.base import apply_scores

    off_report, off_stem = load_run(off_name)
    on_report, on_stem = load_run(on_name)
    if off_report["format"] != on_report["format"]:
        raise SystemExit(
            f"두 실행의 포맷이 다릅니다: {off_report['format']} vs {on_report['format']}"
        )
    if off_report["rerank_profile"] != on_report["rerank_profile"]:
        raise SystemExit(
            "두 실행의 리랭커 프로필이 다릅니다: "
            f"{off_report['rerank_profile']} vs {on_report['rerank_profile']}"
            " · 이 도구는 **재작성 축만** 가른 짝을 받습니다"
        )

    items, _skipped = split_scorable(load_qa_set(qa_set_path).items)
    off = _Side(off_report, off_stem)
    on: _Side | None = None
    rows: list[PairRow] = []
    try:
        on = _Side(on_report, on_stem)
        for item in items:
            if item.query_id not in off.ranks or item.query_id not in on.ranks:
                continue
            off_pool, off_scores = off.pool_and_scores(item)
            on_pool, on_scores = on.pool_and_scores(item)
            if off_scores is None or on_scores is None:
                missing = "off" if off_scores is None else "on"
                raise SystemExit(
                    f"{item.query_id}: {missing} 쪽 점수가 캐시에 없습니다"
                    " · 풀 재현이 그때와 다릅니다 (모듈 docstring 참고)"
                )
            rows.append(
                _build_row(
                    item=item,
                    off_pool=off_pool,
                    on_pool=on_pool,
                    off_scores=off_scores,
                    on_scores=on_scores,
                    off_ranked=apply_scores(list(off_pool), off_scores),
                    on_ranked=apply_scores(list(on_pool), on_scores),
                    report_off=off.ranks.get(item.query_id),
                    report_on=on.ranks.get(item.query_id),
                )
            )
        device = on.config.rerank.rerank_device
    finally:
        off.close()
        if on is not None:
            on.close()

    meta = {
        "off": off_stem,
        "on": on_stem,
        "format": off_report["format"],
        "rerank_profile": on_report["rerank_profile"],
        "rerank_device": device,
        "off_rewrite": off_report.get("rewrite_profile", "off"),
        "on_rewrite": on_report.get("rewrite_profile", "off"),
        "pool": on_report["k"],
        "summary": summarize(rows),
    }
    return rows, meta


# 한 질의의 행을 만든다. **공유 후보 점수 비교는 갈래 안에서만** 한다 (모듈 docstring)
def _build_row(
    *,
    item: QAItem,
    off_pool: list[Any],
    on_pool: list[Any],
    off_scores: list[float],
    on_scores: list[float],
    off_ranked: list[Any],
    on_ranked: list[Any],
    report_off: int | None,
    report_on: int | None,
) -> PairRow:
    off_by_id = dict(zip([c.chunk_id for c in off_pool], off_scores, strict=True))
    on_by_id = dict(zip([c.chunk_id for c in on_pool], on_scores, strict=True))
    shared = [cid for cid in off_by_id if cid in on_by_id]
    deltas = [abs(off_by_id[cid] - on_by_id[cid]) for cid in shared]

    off_ids = [c.chunk_id for c in off_ranked]
    on_ids = [c.chunk_id for c in on_ranked]
    # 공유 후보만 남긴 순서. 둘이 같으면 «상대 순서 보존» 이다
    off_order = [cid for cid in off_ids if cid in on_by_id]
    on_order = [cid for cid in on_ids if cid in off_by_id]
    newcomer_set = set(on_by_id) - set(off_by_id)
    inversions, max_gap = _inversions(off_order, on_order, off_by_id)
    off_rank = hit_rank([c.text for c in off_ranked], item.gold_spans)
    on_rank = hit_rank([c.text for c in on_ranked], item.gold_spans)

    warnings: list[str] = []
    if off_rank != report_off:
        warnings.append(f"off 순위 재현 불일치: {off_rank} vs 리포트 {report_off}")
    if on_rank != report_on:
        warnings.append(f"on 순위 재현 불일치: {on_rank} vs 리포트 {report_on}")

    return PairRow(
        query_id=item.query_id,
        off_ranked=off_ids,
        on_ranked=on_ids,
        shared=len(shared),
        score_identical=all(delta == 0.0 for delta in deltas),
        order_preserved=off_order == on_order,
        max_abs_delta=max(deltas, default=0.0),
        inversions=inversions,
        max_inversion_gap=max_gap,
        newcomers=len(newcomer_set),
        newcomer_ranks=[i for i, cid in enumerate(on_ids, start=1) if cid in newcomer_set],
        off_rank=off_rank,
        on_rank=on_rank,
        report_off_rank=report_off,
        report_on_rank=report_on,
        agree={str(depth): agreement(off_ids, on_ids, depth) for depth in DEPTHS},
        warnings=warnings,
    )


# 사람이 읽는 표. **재현 경고가 있으면 수치보다 먼저 찍는다**
def render(rows: list[PairRow], meta: dict[str, Any]) -> str:
    s = meta["summary"]
    lines = [
        f"{meta['off']} -> {meta['on']} · 리랭커 {meta['rerank_profile']}"
        f"({meta['rerank_device']}) · 질의 {s['queries']}",
    ]
    if s["warned_rows"]:
        lines.append(f"** 재현 불일치 {s['warned_rows']}행 — 아래 수치를 실력으로 읽지 않는다 **")
        for row in rows:
            for warning in row.warnings:
                lines.append(f"  {row.query_id}: {warning}")
    lines.append(
        f"공유 후보 점수 동일 {s['score_identical']}/{s['queries']}"
        f" · 상대 순서 보존 {s['order_preserved']}/{s['queries']}"
        f" · 최대 절대차 {s['max_abs_delta']:.6g}"
    )
    lines.append(
        f"뒤집힌 공유 쌍 {s['inversions']}개 · 그 쌍의 점수 차 최대 {s['max_inversion_gap']:.6g}"
        " (절대차 이하이면 동점 근처의 잡음이다)"
    )
    lines.append("상위 n 일치: " + " · ".join(f"@{d} {s['agreement'][str(d)]}" for d in DEPTHS))
    mid = s["newcomer_percentile_median"]
    shown = "-" if mid is None else format(mid, ".3f")
    lines.append(
        f"신참 {s['newcomers_total']}개 · 분위수 중앙값 {shown}"
        f" · 상위10 안 {s['newcomer_in_top10']}개"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="리랭커 점수를 캐시에서 읽어 두 실행(재작성 off/on)을 견준다 (비용 0)"
    )
    parser.add_argument("--off", required=True, help="재작성 없는 실행 이름")
    parser.add_argument("--on", required=True, help="재작성 켠 실행 이름")
    parser.add_argument("--qa-set", type=Path, default=None)
    parser.add_argument("--out", default=None, help="리포트 JSON 이름 (기본: <on>-rerank-scores)")
    parser.add_argument(
        "--predict-from",
        default=None,
        help="다른 짝의 리포트 이름 · 그쪽 신참 분포로 이 짝의 일치 수를 예측한다",
    )
    args = parser.parse_args(argv)

    rows, meta = compare_pair(args.off, args.on, qa_set_path=args.qa_set)
    if args.predict_from:
        source, _stem = load_run(args.predict_from)
        meta["predicted_from"] = args.predict_from
        meta["predicted"] = predict_agreement(rows, sorted(source["newcomer_percentiles"]))
    meta["newcomer_percentiles"] = newcomer_percentiles(rows)

    out = RESULTS_DIR / f"{args.out or (meta['on'] + '-rerank-scores')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(meta)
    payload["per_query"] = [asdict(row) for row in rows]
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(render(rows, meta))
    if "predicted" in meta:
        print(
            f"예측({args.predict_from}): "
            + " · ".join(f"@{d} {meta['predicted'][str(d)]}" for d in DEPTHS)
        )
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
