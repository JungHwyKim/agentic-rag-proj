"""리랭커가 **무엇을 밀어냈는지** 읽는 도구. `inspect.py` 처럼 **재지 않고 읽는다.**

## 왜 새 모듈인가 — 기존 둘이 이 축을 못 본다

- `inspect.py` 는 **풀 밖**(리포트의 `rank is None`)을 dense 로 다시 재 *"무엇이 아예 안
  잡히나"* 에 답한다. `sih-06` 처럼 **1위였다가 12위가 된 것**은 그 집합에 없다
- `overlap.py` 는 구간별 **↑↓ 개수**를 센다. *"몇 건이 나빠졌나"* 는 답하지만 *"누가 정답을
  밀어냈나"* 는 못 답한다

리랭커는 집합을 안 바꾸고 **순서만** 바꾸므로, 판정에 필요한 것은 정답 청크의 **자리 변화와
그 위로 올라온 후보들**이다. 그것을 보는 방법이 없어서 5회차가 *"왜인지 안 봤다"* 로 남겼다.

## 왜 공짜로 읽히나 — 캐시를 먼저 본다

`LocalCrossEncoderReranker.rerank()` 가 `(질의, 후보 신원 순서, 모델, 프롬프트)` 로 캐시를
먼저 보고 **미스일 때만** 모델을 올린다. 그러니 **풀을 그대로 재현하면** 그때 그 점수를
모델 적재 없이 읽는다(2026-08-25 기준 `.cache/rerank/` 에 E2·E4 두 실행 120건이 있었다).

**그래서 캐시로 읽었는지 지금 새로 쟀는지를 반드시 찍는다** — `usage.reranked` 가 0 이면
앞엣것이다. 안 찍으면 *"그때 그 점수"* 와 *"지금 다시 잰 점수"* 가 구분되지 않는다.

## 풀을 어떻게 재현하나

`retrieve(..., reranker=None, top_k=limit)` 한 번이다. `hybrid.py` 가 `fuse(..., top_k=limit)`
로 자르고 `limit = max(candidate_k, top_k)` 이므로, 같은 `limit` 을 주면 **리랭커가 봤던 그
목록이 그 순서로** 나온다.

**프로필은 리포트에서 읽는다.** 임베딩·리랭커 프로필을 여기서 고르면 다른 모델로 재현한
풀을 그 실행의 풀이라고 부르게 되는데, **에러가 아니라 수치로만** 드러난다.

**재현이 맞았는지는 순위로 확인한다** — 재현한 정답 순위가 리포트의 `per_query.rank` 와
다르면 풀이 그 풀이 아니다. 컬렉션 크기 검사(`require_fresh_store`)보다 촘촘한 검사다.

## 겹침은 값으로 찍고 구간으로 안 자른다

`overlap.py` 가 *"경계값은 그때그때 관측이지 규칙이 아니라 인용하지 않는다 — 순서 컷이다"*
로 못 박았다. 그래서 여기서도 임계값을 안 박고 **겹침 오름차순으로 줄을 세워** 찍는다.
겹침 0 구간의 진 질의는 그 목록의 앞쪽이다.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from evaluation.config import eval_config
from evaluation.inspect import require_fresh_store
from evaluation.overlap import overlap_ratio
from evaluation.qa_set import load_qa_set
from evaluation.run_eval import RESULTS_DIR
from evaluation.scoring import hit_rank

__all__ = ["Displacer", "QueryDiff", "diff_queries", "load_run"]

# 순위가 없으면(후보 창 밖) 제일 뒤로 민다. `overlap.py:compare_by_overlap` 과 같은 규칙이다
FAR = 10**6


# 정답 청크 위로 올라온 후보 하나. **경로별 점수를 그대로 나른다** —
# `dense_score is None and bm25_score is not None` 이 곧 "BM25 전용 후보" 이고,
# 3회차가 단 융합 재판정 조건이 정확히 이 칸을 본다
@dataclass
class Displacer:
    rank: int
    fused_rank: int
    chunk_id: str
    rerank_score: float | None
    dense_score: float | None
    bm25_score: float | None
    chars: int
    tokens: int | None
    head: str

    # BM25 에만 걸려 풀에 들어온 후보인가. dense 가 안 뽑은 것을 융합이 넣었다는 뜻이다
    @property
    def bm25_only(self) -> bool:
        return self.dense_score is None and self.bm25_score is not None


# 질의 하나의 해부 결과
@dataclass
class QueryDiff:
    query_id: str
    query: str
    # 질의-정답 어휘 겹침. 구간으로 안 자르고 값으로 찍는다 (모듈 docstring 참고)
    overlap: float
    # 두 리포트가 적어둔 순위. 재현한 값과 대조하는 기준이다
    report_before: int | None
    report_after: int | None
    # 이번에 재현한 값
    fused_rank: int | None
    reranked_rank: int | None
    gold_chunk_id: str | None
    gold_fused_score: float | None
    gold_rerank_score: float | None
    gold_chars: int | None
    gold_tokens: int | None
    # 재현이 리포트와 어긋났으면 여기 남는다. **비어 있어야 이 행을 믿을 수 있다**
    warnings: list[str] = field(default_factory=list)
    displacers: list[Displacer] = field(default_factory=list)

    # 밀어낸 후보 중 BM25 전용이 몇인가
    @property
    def bm25_only_count(self) -> int:
        return sum(1 for d in self.displacers if d.bm25_only)


# 실행 JSON 하나를 읽는다. 이름은 `overlap.py` 와 같은 모양이다 (`e4-kure-ce-pdf`)
def load_run(name_or_path: str | Path) -> tuple[dict[str, Any], str]:
    path = Path(name_or_path)
    if not path.exists():
        path = RESULTS_DIR / f"{name_or_path}.json"
    if not path.exists():
        raise SystemExit(f"실행 JSON 이 없습니다: {path}")
    return json.loads(path.read_text(encoding="utf-8")), path.stem


# 토큰 수를 세는 함수를 만든다. **기본은 안 센다** — 토크나이저를 받는 것이 네트워크를 타서,
# 없으면 조용히 문자 수만 쓴다. ko-reranker(상한 512) 판정을 할 때만 `--tokens` 로 켠다
def make_counter(model: str | None, *, enabled: bool) -> Callable[[str], int | None]:
    if not enabled or not model:
        return lambda _text: None
    from transformers import AutoTokenizer

    tok = AutoTokenizer.from_pretrained(model)
    return lambda text: len(tok.encode(text, add_special_tokens=True))


# 순위 비교용 깊이. 없으면 제일 뒤
def _depth(rank: int | None) -> int:
    return rank if rank is not None else FAR


# 해부할 질의를 고른다. `--query` 가 이기고, 없으면 나빠진 것 전부다
def select_queries(
    qa_ids: list[str],
    before: dict[str, int | None],
    after: dict[str, int | None],
    *,
    wanted: list[str],
    worse_only: bool,
) -> list[str]:
    if wanted:
        missing = [q for q in wanted if q not in qa_ids]
        if missing:
            raise SystemExit(f"세트에 없는 질의: {missing}")
        return wanted
    if not worse_only:
        raise SystemExit("--query 로 질의를 고르거나 --worse-only 를 주세요")
    # **한쪽에만 있는 질의는 조용히 안 버린다** (`overlap.py` 와 같은 규약)
    missing = [q for q in qa_ids if q not in before or q not in after]
    if missing:
        raise SystemExit(
            f"두 실행에 다 있지 않은 질의 {len(missing)}건: {missing[:5]}"
            " · 같은 세트·같은 그룹으로 돌린 실행끼리만 견줄 수 있습니다"
        )
    return [q for q in qa_ids if _depth(after[q]) > _depth(before[q])]


# 고른 질의마다 풀을 재현하고 정답 청크의 자리 변화를 읽는다. **색인하지 않는다**
def diff_queries(
    *,
    before_name: str,
    after_name: str,
    wanted: list[str],
    worse_only: bool,
    count_tokens: bool = False,
) -> tuple[list[QueryDiff], dict[str, Any]]:
    from rag_pipeline.ingestion.embedding import get_embedder
    from rag_pipeline.ingestion.indexing import get_store
    from rag_pipeline.retrieval import build_bm25_index, get_reranker, retrieve

    before_report, before_stem = load_run(before_name)
    after_report, after_stem = load_run(after_name)
    fmt = after_report["format"]
    if before_report["format"] != fmt:
        raise SystemExit(f"두 실행의 포맷이 다릅니다: {before_report['format']} vs {fmt}")

    before = {row["query_id"]: row["rank"] for row in before_report["per_query"]}
    after = {row["query_id"]: row["rank"] for row in after_report["per_query"]}

    qa = {item.query_id: item for item in load_qa_set().items}
    picked = select_queries(list(qa), before, after, wanted=wanted, worse_only=worse_only)
    if not picked:
        raise SystemExit("나빠진 질의가 없습니다.")

    # **프로필을 리포트에서 읽는다** (모듈 docstring 참고)
    config = eval_config(
        qdrant_path=RESULTS_DIR / f"qdrant-{after_stem}",
        embedding=after_report["embedding_profile"],
        rerank=after_report["rerank_profile"],
    )
    reranker = get_reranker(config.rerank)
    if reranker is None:
        raise SystemExit(
            f"'{after_stem}' 은 리랭커 없이 돈 실행입니다 (rerank_profile="
            f"{after_report['rerank_profile']!r}) · 리랭커를 켠 실행을 뒤에 두세요"
        )

    limit = max(config.retrieval.candidate_k, after_report["k"])
    count = make_counter(config.rerank.rerank_model, enabled=count_tokens)
    embedder = get_embedder(config.embedding)
    store = get_store(config.indexing, config.embedding)
    rows: list[QueryDiff] = []
    scored_total = 0
    try:
        require_fresh_store(
            store.count(),
            sum(x["chunks"] for x in after_report["indexed"]),
            label=after_report["label"],
            fmt=fmt,
        )
        bm25 = build_bm25_index(store) if after_report["retriever"]["use_bm25"] else None

        for query_id in picked:
            item = qa[query_id]
            # 리랭커가 봤던 그 목록을 그 순서로 (모듈 docstring 참고)
            pool = retrieve(
                item.query,
                store=store,
                embedder=embedder,
                top_k=limit,
                bm25=bm25,
                reranker=None,
                config=config.retrieval,
            )
            fused_order = {chunk.chunk_id: i for i, chunk in enumerate(pool, start=1)}
            fused_rank = hit_rank([c.text for c in pool], item.gold_spans)

            reranker.usage.reset()
            ranked = reranker.rerank(item.query, list(pool))
            scored_total += reranker.usage.reranked
            reranked_rank = hit_rank([c.text for c in ranked], item.gold_spans)

            rows.append(
                build_row(
                    item=item,
                    before_rank=before.get(query_id),
                    after_rank=after.get(query_id),
                    pool_fused=fused_order,
                    ranked=ranked,
                    fused_rank=fused_rank,
                    reranked_rank=reranked_rank,
                    count=count,
                    warnings=list(reranker.warnings),
                )
            )
    finally:
        # 안 닫으면 다음 실행이 배타 잠금으로 통째로 죽는다
        store.close()

    meta = {
        "before": before_stem,
        "after": after_stem,
        "format": fmt,
        "embedding_profile": after_report["embedding_profile"],
        "rerank_profile": after_report["rerank_profile"],
        "rerank_model": config.rerank.rerank_model,
        "rerank_device": config.rerank.rerank_device,
        "pool": limit,
        "queries": len(rows),
        # **0 이면 캐시로 읽은 것이다** (모듈 docstring 참고)
        "scored_pairs": scored_total,
        "source": "캐시" if scored_total == 0 else "이번에 새로 채점",
    }
    return rows, meta


# 재현한 순위와 리포트의 순위를 대조해 한 행을 만든다. **어긋나면 경고를 싣는다**
def build_row(
    *,
    item: Any,
    before_rank: int | None,
    after_rank: int | None,
    pool_fused: dict[str, int],
    ranked: list[Any],
    fused_rank: int | None,
    reranked_rank: int | None,
    count: Callable[[str], int | None],
    warnings: list[str],
) -> QueryDiff:
    notes = [f"리랭커 폴백: {w}" for w in warnings]
    if reranked_rank != after_rank:
        notes.append(
            f"재현한 순위 {reranked_rank} 가 리포트의 {after_rank} 와 다릅니다"
            " — 풀이 그 실행의 풀이 아닙니다"
        )
    if fused_rank != before_rank:
        notes.append(f"재현한 융합 순위 {fused_rank} 가 대조군 리포트의 {before_rank} 와 다릅니다")

    gold = ranked[reranked_rank - 1] if reranked_rank else None
    displacers = [
        Displacer(
            rank=i,
            fused_rank=pool_fused.get(chunk.chunk_id, -1),
            chunk_id=chunk.chunk_id,
            rerank_score=chunk.rerank_score,
            dense_score=chunk.dense_score,
            bm25_score=chunk.bm25_score,
            chars=len(chunk.text),
            tokens=count(chunk.text),
            head=chunk.text.replace("\n", " ")[:70],
        )
        for i, chunk in enumerate(ranked[: (reranked_rank or 1) - 1], start=1)
    ]
    return QueryDiff(
        query_id=item.query_id,
        query=item.query,
        overlap=overlap_ratio(item.query, item.gold_spans),
        report_before=before_rank,
        report_after=after_rank,
        fused_rank=fused_rank,
        reranked_rank=reranked_rank,
        gold_chunk_id=gold.chunk_id if gold else None,
        gold_fused_score=gold.fused_score if gold else None,
        gold_rerank_score=gold.rerank_score if gold else None,
        gold_chars=len(gold.text) if gold else None,
        gold_tokens=count(gold.text) if gold else None,
        warnings=notes,
        displacers=displacers,
    )


# 사람이 읽는 모양. 겹침 오름차순이라 겹침 0 구간의 진 질의가 앞에 온다
def _print(rows: list[QueryDiff], meta: dict[str, Any], *, top: int) -> None:
    print(
        f"{meta['before']} → {meta['after']} · {meta['format']} · 풀 {meta['pool']}\n"
        f"리랭커 {meta['rerank_model']} ({meta['rerank_device']}) · "
        f"점수 출처 **{meta['source']}** (채점한 쌍 {meta['scored_pairs']})\n"
    )
    for row in sorted(rows, key=lambda r: r.overlap):
        print(f"[{row.query_id}] 겹침 {row.overlap:.3f} · {row.query}")
        print(
            f"  정답 순위: 융합 {row.fused_rank} → 리랭크 {row.reranked_rank}"
            f"  (리포트 {row.report_before} → {row.report_after})"
        )
        for note in row.warnings:
            print(f"  !! {note}")
        if row.gold_chunk_id:
            tok = f" · {row.gold_tokens}토큰" if row.gold_tokens is not None else ""
            print(
                f"  정답청크 {row.gold_chunk_id} · fused {row.gold_fused_score:.4f}"
                f" · rerank {row.gold_rerank_score:.4f} · {row.gold_chars}자{tok}"
            )
        shown = row.displacers[:top]
        print(f"  밀어낸 후보 {len(row.displacers)}개 (BM25 전용 {row.bm25_only_count}개):")
        for d in shown:
            dense = f"{d.dense_score:.4f}" if d.dense_score is not None else "  -   "
            bm = f"{d.bm25_score:.3f}" if d.bm25_score is not None else "  -  "
            score = f"{d.rerank_score:.4f}" if d.rerank_score is not None else "None"
            mark = " [BM25전용]" if d.bm25_only else ""
            tok = f" {d.tokens:>4}t" if d.tokens is not None else ""
            print(
                f"    {d.rank:>2}위 (융합 {d.fused_rank:>2}) rerank {score:>9}"
                f" dense {dense} bm25 {bm}{tok}{mark}  {d.head}"
            )
        if len(row.displacers) > len(shown):
            print(f"    … {len(row.displacers) - len(shown)}개 더 (--top 으로 늘린다)")
        print()

    total = sum(len(r.displacers) for r in rows)
    only = sum(r.bm25_only_count for r in rows)
    print(f"질의 {len(rows)}건 · 밀어낸 후보 합계 {total}개 · 그중 BM25 전용 {only}개")
    if any(r.warnings for r in rows):
        print("!! 경고가 있는 행이 있습니다 — 재현이 어긋났으면 위 수치를 인용하지 마세요")


# 명령줄 진입점. 과금 없음 — 기존 실행 JSON 과 그 실행이 남긴 컬렉션만 읽는다
def main() -> int:
    parser = argparse.ArgumentParser(description="리랭커가 무엇을 밀어냈는지 읽는다 (색인 안 함)")
    parser.add_argument("before", help="대조군 실행 (리랭커 없음). 예: ceiling-kure-pdf")
    parser.add_argument("after", help="실험 실행 (리랭커 켬). 예: e4-kure-ce-pdf")
    parser.add_argument("--query", action="append", default=[], help="볼 질의 id (여러 번 가능)")
    parser.add_argument("--worse-only", action="store_true", help="나빠진 질의를 전부 본다")
    parser.add_argument("--top", type=int, default=12, help="밀어낸 후보를 몇 개까지 찍나")
    parser.add_argument("--tokens", action="store_true", help="토큰 수도 센다 (토크나이저를 받는다)")
    parser.add_argument("--json", action="store_true", help="결과를 JSON 으로도 남긴다")
    args = parser.parse_args()

    rows, meta = diff_queries(
        before_name=args.before,
        after_name=args.after,
        wanted=args.query,
        worse_only=args.worse_only,
        count_tokens=args.tokens,
    )
    _print(rows, meta, top=args.top)

    if args.json:
        out = RESULTS_DIR / f"{meta['after']}-rerank-diff.json"
        out.write_text(
            json.dumps(
                {"meta": meta, "queries": [asdict(r) for r in rows]}, ensure_ascii=False, indent=1
            ),
            encoding="utf-8",
        )
        print(f"\n결과: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
