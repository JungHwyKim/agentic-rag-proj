"""질의-정답 **어휘 겹침**으로 구간을 갈라 두 실행을 견준다.

## 왜 총합으로 재면 안 되나

`.claude/wiki/verify_log/2026-08-24-어휘겹침.md` 가 이 기준을 만든 회차다. 거기서 실제로 한 실수가
이 모듈이 존재하는 이유다 — hybrid 의 총합 이득 `±0.000` 을 *"아무 일 없음"* 으로 읽었다가
**겹침 0 구간에서 11건이 흔들린 것**(↑6 ↓5, 둘은 후보 창 밖)을 통째로 놓쳤다.

겹침이 낮은 구간은 **정의상 낱말이 아니라 뜻으로 찾아야 하는 자리**다. 임베딩 모델을 바꾸면
정확히 그 구간이 움직일 것으로 기대하는 것이므로, 총합으로 재면 찾으려던 것을 못 본다
(그 변화가 나머지 3분의 2에 묻힌다).

## 겹침을 무엇으로 재나

**BM25 가 실제로 보는 토큰**으로 잰다(`rag_pipeline.retrieval.tokenize`). 공백으로 자르면
조사 때문에 겹침이 과소평가되고, 그것은 어느 검색기도 보지 않는 세계다.

## 토크나이저는 BM25 와 같은데 **IDF 가 없다**

그래서 흔한 토큰이 눌리지 않고 그대로 셈에 들어간다. 실측(패러프레이즈 쌍 80건 = 질의 160개,
2026-08-25): 문서빈도 상위가 `나요` **90** · `하나` 31 · `있나` 24 · `되나` 19 로 **전부 어미**다.
BM25 는 이것을 IDF 가 0 에 가깝게 눌러주지만(`bm25.py` docstring), 이 비율은 안 눌러준다.

**그래서 이 값을 "낱말이 얼마나 겹치나"로 읽되 "얼마나 베꼈나"로는 읽지 말 것.** 실제로
겹침 0.300 인 쌍이 공유 토큰 셋이 `나요`·`되나`·`비를` 로 전부 어미인 경우가 있었다
(`.claude/wiki/verify_log/2026-08-25-패러프레이즈-검수1차.md`). **흔한 토큰을 빼고 다시 재는 길은 안 냈다** —
df 분포에 틈이 없어 컷이 임의값이 되고, 그러면 근거 없는 수가 판정에 들어온다.

구간을 가르는 용도(아래)에는 이 성질이 문제가 안 된다 — **모든 질의가 같은 편향을 받아
순서가 유지되기 때문**이고, 이 모듈이 절대값이 아니라 순서 컷을 쓰는 이유이기도 하다.

## 경계를 실행마다 다시 자르지 않는다

겹침은 `질의 ∩ 정답span` 이라 **검색 결과와 무관하다.** 그래서 같은 세트를 쓰는 두 실행은
구성상 같은 경계를 갖는다 — 이 모듈은 두 실행의 순위를 **같은 구간표에 얹어** 보여준다.
경계가 실행마다 흔들리면 구간별 이득이 검색이 아니라 구간 이동을 재게 된다.

**두 실행이 같은 질의 집합인지 먼저 본다.** 한쪽에만 있는 질의를 조용히 버리면 그만큼
구간 크기가 달라지는데 아무 에러도 안 난다.

## 몇 구간으로 자르나

기본 3이고 **임의다.** 겹침 분포에 눈에 띄는 틈이 없어 임계값을 못 골랐다(그 회차 7절이
스스로 적었다). 경계값은 그때그때 관측이지 규칙이 아니므로 **인용하지 않는다** — 순서 컷이다.
"""

from __future__ import annotations

import argparse
import json
import statistics as st
from dataclasses import dataclass
from pathlib import Path

from evaluation.qa_set import QAItem, load_qa_set
from evaluation.run_eval import RESULTS_DIR
from rag_pipeline.retrieval.tokenize import tokenize

__all__ = ["Bucket", "compare_by_overlap", "load_ranks", "overlap_ratio", "spread"]


# 질의 토큰 중 정답 span 에도 있는 것의 비율. 0.0 이면 낱말이 하나도 안 겹친다
def overlap_ratio(query: str, gold_spans: list[str]) -> float:
    q = set(tokenize(query))
    if not q:
        return 0.0
    gold: set[str] = set()
    for span in gold_spans:
        gold |= set(tokenize(span))
    return len(q & gold) / len(q)


# 한 구간의 판정 결과. `gain` 은 뒤 실행의 hit 비율에서 앞 실행의 것을 뺀 값이다
@dataclass
class Bucket:
    low: float
    high: float
    queries: int
    before_hit: float
    after_hit: float
    up: int
    down: int

    @property
    def gain(self) -> float:
        return self.after_hit - self.before_hit


# 실행 결과 JSON 에서 질의별 순위를 꺼낸다. **`per_query` 는 `--k` 채점**이라 주지표 k 보다
# 뒤인 질의도 순위를 갖는다 — 그래서 "후보를 늘리면 잡히나"를 이 값으로 읽을 수 있다
def load_ranks(name_or_path: str | Path) -> dict[str, int | None]:
    path = Path(name_or_path)
    if not path.exists():
        path = RESULTS_DIR / f"{name_or_path}.json"
    if not path.exists():
        raise SystemExit(f"실행 JSON 이 없습니다: {path}")
    report = json.loads(path.read_text(encoding="utf-8"))
    return {row["query_id"]: row["rank"] for row in report["per_query"]}


# 세트 전체의 겹침 분포. 구간을 가르기 전에 **전제가 서는지** 먼저 보는 자리다
def spread(items: list[QAItem]) -> dict[str, float]:
    values = [overlap_ratio(item.query, item.gold_spans) for item in items]
    return {
        "queries": len(values),
        "median": st.median(values),
        "mean": st.fmean(values),
        "min": min(values),
        "max": max(values),
        "zero": sum(1 for v in values if v == 0.0),
    }


# 두 실행을 겹침 구간별로 견준다. 앞이 대조군, 뒤가 실험이다
def compare_by_overlap(
    items: list[QAItem],
    before: dict[str, int | None],
    after: dict[str, int | None],
    *,
    buckets: int = 3,
    hit_k: int = 10,
) -> list[Bucket]:
    # **한쪽에만 있는 질의는 조용히 안 버린다.** 버리면 구간 크기가 달라지는데 신호가 없다
    missing = [i.query_id for i in items if i.query_id not in before or i.query_id not in after]
    if missing:
        raise SystemExit(
            f"두 실행에 다 있지 않은 질의 {len(missing)}건: {missing[:5]}"
            " · 같은 세트·같은 그룹으로 돌린 실행끼리만 견줄 수 있습니다"
        )

    rows = sorted((overlap_ratio(i.query, i.gold_spans), i.query_id) for i in items)
    if buckets < 1 or len(rows) < buckets:
        raise SystemExit(f"질의 {len(rows)}건을 {buckets}구간으로 못 가릅니다")

    # 순위가 없으면(후보 창 밖) 제일 뒤로 민다. 그래야 "없음 -> 3" 이 오름으로 세어진다
    far = 10**6

    def hit(rank: int | None) -> bool:
        return rank is not None and rank <= hit_k

    def depth(rank: int | None) -> int:
        return rank if rank is not None else far

    size = len(rows) // buckets
    out: list[Bucket] = []
    for b in range(buckets):
        end = len(rows) if b == buckets - 1 else (b + 1) * size
        part = rows[b * size : end]
        n = len(part)
        out.append(
            Bucket(
                low=part[0][0],
                high=part[-1][0],
                queries=n,
                before_hit=sum(1 for _, q in part if hit(before[q])) / n,
                after_hit=sum(1 for _, q in part if hit(after[q])) / n,
                up=sum(1 for _, q in part if depth(after[q]) < depth(before[q])),
                down=sum(1 for _, q in part if depth(after[q]) > depth(before[q])),
            )
        )
    return out


# 명령줄 진입점. 과금 없음 — 기존 실행 JSON 과 세트 파일만 읽는다
def main() -> int:
    parser = argparse.ArgumentParser(description="어휘 겹침 구간별로 두 실행을 견준다 (과금 없음)")
    parser.add_argument("before", help="대조군 실행의 label 또는 JSON 경로")
    parser.add_argument("after", help="실험 실행의 label 또는 JSON 경로")
    parser.add_argument("--qa-set", type=Path, help="쓸 세트 파일. 비우면 qa_set.jsonl")
    parser.add_argument("--buckets", type=int, default=3, help="몇 구간으로 가르나 (기본 3, 임의다)")
    parser.add_argument("--hit-k", type=int, default=10, help="hit 판정 k")
    args = parser.parse_args()

    qa = load_qa_set(args.qa_set)
    if not qa.items:
        raise SystemExit(f"세트가 비어 있습니다: {qa.path}")

    dist = spread(qa.items)
    print(f"\n겹침 분포 ({qa.path.name}, n={dist['queries']:.0f}):")
    print(
        f"  중앙 {dist['median']:.3f} · 평균 {dist['mean']:.3f} · "
        f"최소 {dist['min']:.3f} · 최대 {dist['max']:.3f} · 겹침 0 인 질의 {dist['zero']:.0f}건"
    )

    before, after = load_ranks(args.before), load_ranks(args.after)
    rows = compare_by_overlap(qa.items, before, after, buckets=args.buckets, hit_k=args.hit_k)

    print(f"\n겹침 낮은 쪽부터 · {args.before} → {args.after} (@{args.hit_k}):")
    for row in rows:
        print(
            f"  {row.low:.2f}~{row.high:.2f}  n={row.queries:2d}  "
            f"before {row.before_hit:.3f}  after {row.after_hit:.3f}  "
            f"이득 {row.gain:+.3f}  ↑{row.up} ↓{row.down}"
        )
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
