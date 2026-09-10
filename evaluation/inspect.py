"""진 질의를 읽는 도구. **재지 않고 읽는다.**

`.claude/wiki/plan_log/평가-세트.md` 는 처음부터 *"판정은 수치 하나로 하지 않는다 — 진 질의를 직접
읽는다"* 로 정해뒀다. 이것이 그 자리다.

## 왜 `--dense` 를 다시 안 돌리나

기준선 실행이 만든 컬렉션이 `.cache/evaluation/runs/qdrant-{label}-{fmt}/` 에 그대로 있다.
그것을 열면 **파싱·색인·문서 임베딩을 전부 건너뛰고** 질의 임베딩만 남는다(그것도 캐시에
있다). 그리고 더 중요한 것 — 다시 색인하면 **그 사이 로더가 바뀐 만큼 청크가 달라져서**
기준선의 순위를 읽는 것이 아니게 된다.

**그래서 낡은 저장소를 조용히 읽으면 안 된다.** `store.count()` 가 리포트의 `indexed` 청크
합과 다르면 멈춘다. 다르면 여기서 나온 순위는 그 리포트의 순위가 아니다.

## 왜 정답 청크의 *전체* 순위를 재나

`per_query` 의 `rank` 는 `--k` 안에서만 유효해 못 찾으면 `None` 이다. 그런데 **21위로 아깝게
밀린 것과 300위로 짝이 아예 안 맞는 것은 고칠 곳이 다르다** — `None` 만으로는 둘이 같아
보인다. `top_k=컬렉션 크기` 로 한 번 검색하면 전체 순위가 나오고, **그 앞 20개가 곧 실제로
올라왔던 후보**라 검색이 한 번으로 끝난다. qdrant 가 로컬이라 k 를 키워도 비용이 0 이다.

순위 판정은 `scoring.hit_rank()` 를 그대로 쓴다. **채점과 해부가 같은 기준을 써야** 여기서
"1위"라고 읽은 것이 기준선에서도 1위다(`squash()` 로 공백을 지우는 규칙까지 같다).

## 왜 정답 청크로 묶나

`sch-02`·`sch-03`·`sch-04`·`sch-05` 의 정답이 **한 청크**에 다 들어 있다(「공공기록물 관리에
관한 법률」의 조문 나열). 질의 단위로 읽으면 같은 청크를 네 번 읽게 되고, *"무엇이 안
잡히나"* 의 분포가 그 중복만큼 기운다. **실패 하나로 센다** — 근거는 `평가-세트.md` 8회차.

묶는 키는 코퍼스 청크 자리가 아니라 **저장소가 돌려준 `chunk_id`** 다. 파싱을 안 타므로
`--check` 쪽 캐시가 낡았는지와 무관하게 성립한다.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from typing import Any

from evaluation.config import eval_config
from evaluation.qa_set import load_qa_set
from evaluation.run_eval import BASELINE_PATH, RESULTS_DIR
from evaluation.scoring import hit_rank

__all__ = ["MissGroup", "group_misses", "heading_ratio", "require_fresh_store"]


# 정답 청크 하나와 그것을 가리키는 질의들. **여럿이면 실패 하나로 센다**
@dataclass
class MissGroup:
    chunk_id: str
    # 그 청크의 전체 순위(1부터). 컬렉션 안에 반드시 있으므로 None 이면 이상 신호다
    rank: int | None
    score: float | None
    page: int | None
    heading_path: list[str]
    text: str
    query_ids: list[str] = field(default_factory=list)
    # 대신 올라온 상위 몇 개 — (순위, chunk_id, 점수, 머리글)
    top: list[tuple[int, str, float, str]] = field(default_factory=list)


# 저장소가 리포트와 같은 것을 담고 있는지. **다르면 여기서 읽는 순위가 그 리포트의 순위가 아니다**
def require_fresh_store(actual: int, expected: int, *, label: str, fmt: str) -> None:
    if actual == expected:
        return
    raise SystemExit(
        f"저장소가 리포트와 다릅니다: 컬렉션 {actual}건 / 리포트 {expected}건.\n"
        f"  기준선을 만든 실행 이후 청크가 바뀌었습니다. 다시 재려면:\n"
        f"    python -m evaluation.run_eval --dense --format {fmt} --label {label}\n"
        f"  (기준선까지 갱신할 생각이면 --save-baseline 을 붙이되, 그건 판정이 필요한 결정입니다)"
    )


# `[제목 > 경로]\n` 머리가 청크 전체에서 차지하는 비율. splitter 가 붙이는 모양 그대로 센다
# (`splitter/__init__.py` 의 `f"[{' > '.join(...)}]\n"`). **정규식으로 본문에서 긁지 않는다** —
# 본문이 대괄호로 시작하는 청크를 머리로 오인한다
def heading_ratio(text: str, heading_path: list[str] | None) -> float:
    if not heading_path or not text:
        return 0.0
    return len(f"[{' > '.join(heading_path)}]\n") / len(text)


# 같은 정답 청크를 가리키는 질의들을 한 덩어리로. 순위가 얕은 것부터
def group_misses(rows: list[tuple[str, MissGroup]]) -> list[MissGroup]:
    merged: dict[str, MissGroup] = {}
    for query_id, group in rows:
        target = merged.setdefault(group.chunk_id, group)
        target.query_ids.append(query_id)
    return sorted(merged.values(), key=lambda g: (g.rank is None, g.rank or 0))


# 읽을 리포트를 고른다. 기준선과 실험 실행 어느 쪽이든 같은 모양이다
def load_report(label: str, fmt: str) -> dict[str, Any]:
    run = RESULTS_DIR / f"{label}-{fmt}.json"
    for path in (run, BASELINE_PATH):
        if path.exists():
            report = json.loads(path.read_text(encoding="utf-8"))
            if report.get("format") == fmt:
                print(f"리포트: {path}")
                return report
    raise SystemExit(f"'{fmt}' 형식 리포트를 못 찾았습니다: {run} · {BASELINE_PATH}")


# 못 찾은 질의마다 정답 청크의 전체 순위를 재고, 같은 청크끼리 묶는다
def inspect_misses(*, label: str, fmt: str) -> list[MissGroup]:
    from rag_pipeline.ingestion.embedding import get_embedder
    from rag_pipeline.ingestion.indexing import get_store
    from rag_pipeline.retrieval import retrieve

    report = load_report(label, fmt)
    missed = {row["query_id"] for row in report["per_query"] if row["rank"] is None}
    if not missed:
        raise SystemExit("이 리포트에는 못 찾은 질의가 없습니다.")

    qa = {item.query_id: item for item in load_qa_set().items}
    # 기준선을 만든 그 실행의 저장 경로다. 이름 규칙은 `run_eval.run_dense()` 와 같아야 한다
    config = eval_config(qdrant_path=RESULTS_DIR / f"qdrant-{report['label']}-{fmt}")

    embedder = get_embedder(config.embedding)
    store = get_store(config.indexing, config.embedding)
    rows: list[tuple[str, MissGroup]] = []
    corpus_ratios: list[float] = []
    try:
        total = store.count()
        require_fresh_store(
            total, sum(x["chunks"] for x in report["indexed"]), label=report["label"], fmt=fmt
        )
        print(f"컬렉션 {total}건 · 못 찾은 질의 {len(missed)}건\n")

        for query_id in sorted(missed):
            item = qa[query_id]
            # **한 번만 검색한다.** 앞 20개가 곧 실제로 올라왔던 후보이고,
            # 전체를 받아야 정답 청크가 몇 위인지 나온다 (모듈 docstring 참고)
            hits = retrieve(item.query, store=store, embedder=embedder, top_k=total)
            texts = [hit.text for hit in hits]
            rank = hit_rank(texts, item.gold_spans)

            if not corpus_ratios:
                corpus_ratios = [
                    heading_ratio(h.text, h.metadata.get("heading_path")) for h in hits
                ]

            gold = hits[rank - 1] if rank else None
            rows.append((
                query_id,
                MissGroup(
                    chunk_id=gold.chunk_id if gold else f"(못 찾음) {query_id}",
                    rank=rank,
                    score=gold.dense_score if gold else None,
                    page=(gold.metadata.get("page") if gold else None),
                    heading_path=list(gold.metadata.get("heading_path") or []) if gold else [],
                    text=gold.text if gold else "",
                    top=[
                        (i, h.chunk_id, h.dense_score or 0.0, h.text.replace("\n", " ")[:60])
                        for i, h in enumerate(hits[:5], start=1)
                    ],
                ),
            ))
    finally:
        # 안 닫으면 다음 실행이 배타 잠금으로 통째로 죽는다
        store.close()

    groups = group_misses(rows)
    _print_groups(groups, corpus_ratios)
    return groups


# 사람이 읽는 모양. 정답 청크를 먼저 보이고 대신 올라온 것을 뒤에 붙인다
def _print_groups(groups: list[MissGroup], corpus_ratios: list[float]) -> None:
    ordered = sorted(corpus_ratios)
    median = ordered[len(ordered) // 2] if ordered else 0.0

    for n, g in enumerate(groups, start=1):
        ratio = heading_ratio(g.text, g.heading_path)
        print(f"[{n:2d}/{len(groups)}] {g.chunk_id}  전체순위 {g.rank}  점수 {g.score:.4f}"
              if g.score is not None else f"[{n:2d}/{len(groups)}] {g.chunk_id}  전체순위 {g.rank}")
        print(f"      질의 {len(g.query_ids)}건: {', '.join(g.query_ids)}")
        print(f"      쪽 {g.page} · 제목머리 {ratio:.0%} (코퍼스 중앙값 {median:.0%})"
              f" · 길이 {len(g.text)}자")
        print(f"      정답청크: {g.text.replace(chr(10), ' ')[:100]}")
        for i, chunk_id, score, head in g.top[:3]:
            print(f"        {i}위 {score:.4f} {chunk_id}  {head}")
        print()

    deep = [g for g in groups if g.rank and g.rank > 50]
    near = [g for g in groups if g.rank and 21 <= g.rank <= 50]
    print(f"덩어리 {len(groups)}개 — 21~50위 {len(near)}개 · 50위 초과 {len(deep)}개")
    print(f"제목머리 비율 중앙값: 코퍼스 {median:.1%}")


# 명령줄 진입점
def main() -> int:
    parser = argparse.ArgumentParser(description="못 찾은 질의를 읽는다 (색인하지 않는다)")
    parser.add_argument("--format", dest="fmt", default="pdf", help="포맷 라벨 (pdf / hwpx)")
    parser.add_argument("--label", default="dense-baseline", help="읽을 실행 이름")
    parser.add_argument("--json", action="store_true", help="결과를 JSON 으로도 남긴다")
    args = parser.parse_args()

    groups = inspect_misses(label=args.label, fmt=args.fmt)

    if args.json:
        out = RESULTS_DIR / f"{args.label}-{args.fmt}-misses.json"
        out.write_text(
            json.dumps([vars(g) for g in groups], ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print(f"\n결과: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
