"""평가 러너.

두 가지를 한다. **`--check` 는 과금이 없고 `--dense` 는 있다.**

- `--check` — 세트가 성립하는지만 본다. 레코드 형식 + 모든 정답 span 이 그룹의 모든 포맷에서,
  그리고 청크 하나 안에서 발견되는지. 파싱만 하므로 **키가 필요 없다**
- `--dense` — dense 검색 baseline 을 잰다. 문서를 색인하고 질의를 임베딩하므로 **OpenAI 과금이
  일어난다.** 이것이 이후 모든 비교의 기준선이다(`plan_log/평가-세트.md` 2회차)

## 왜 포맷마다 저장 경로를 가르나

포맷 비교(`plan_log/검색-생성.md` 비교 5번)는 "같은 질의를 HWPX 판만 색인했을 때와 PDF 판만
색인했을 때" 를 견주는 것이다. 한 컬렉션에 둘 다 넣으면 같은 내용의 청크가 **서로 경쟁**해서
비교가 성립하지 않는다. 그래서 `--format` 으로 고른 판만 색인하고 저장 경로에 그 이름을 붙인다.

## 왜 확장자가 아니라 포맷 라벨인가

시행령 그룹에 `.hwpx` 가 **둘**이다(세로·가로). 확장자로는 애초에 못 가른다 —
`documents.py` 가 라벨을 도입한 이유와 같다. 라벨은 `format_labels(group)` 이 답한다.

## 어느 라벨로 baseline 을 재나

**PDF 다.** 네 그룹 모두 PDF 판이 있어 60건 전부가 유효하다. HWPX 로 돌리면 신뢰가능 AI
가이드에 HWPX 가 없어(`documents.py` 참고) **그 15건이 문서 없이 채점되어** 점수가 가짜로
낮아진다. 포맷 비교는 **두 판을 다 가진 그룹으로 제한**해야 성립한다 — `--groups` 가 그 자리다.

qdrant 로컬 파일 모드가 디렉터리에 배타 잠금을 걸어 **서버를 띄운 채로는 못 돌린다**
(CLAUDE.md). 기본 `.qdrant` 를 안 쓰고 실행마다 갈라 두는 이유이기도 하다.

## 왜 결과를 파일로 남기나

비교는 두 실행의 차이이고, 판정은 **질의별 승/패/무** 로 한다(수치 하나로 안 본다).
그러려면 이전 실행이 질의 단위로 남아 있어야 한다.
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any

from evaluation.corpus import build_corpus
from evaluation.documents import DOCUMENT_GROUPS, FIXTURES_DIR
from evaluation.qa_set import load_qa_set, validate_items, validate_spans_across_formats
from evaluation.scoring import QueryScore, score_query

# 실행 결과를 남기는 곳. `.gitignore` 에 `.cache/` 가 이미 있다
RESULTS_DIR = Path(__file__).resolve().parents[1] / ".cache" / "evaluation" / "runs"


# 세트가 성립하는지 본다. 문제 목록을 돌려주고 비어 있으면 통과다
def check(refresh: bool = False) -> list[str]:
    qa = load_qa_set()
    if not qa.items:
        return [f"세트가 비어 있습니다: {qa.path}"]

    problems = validate_items(qa.items)

    groups = sorted({item.source_doc for item in qa.items})
    corpus = build_corpus(groups, refresh=refresh)
    if not corpus:
        print("  (fixtures 가 없어 span 검증은 건너뜁니다)")
        return problems

    return problems + validate_spans_across_formats(qa.items, corpus)


# 점수 목록을 한 줄 요약으로. 평균만 내지 않고 못 맞힌 수를 함께 낸다 —
# 평균은 "몇 개를 아예 못 찾았나" 를 숨긴다
def summarize(scores: list[QueryScore], k: int) -> dict[str, Any]:
    if not scores:
        return {}
    return {
        "queries": len(scores),
        "misses": sum(1 for s in scores if not s.hit),
        f"hit@{k}": statistics.fmean(1.0 if s.hit else 0.0 for s in scores),
        f"recall@{k}": statistics.fmean(s.recall for s in scores),
        "MRR": statistics.fmean(s.reciprocal_rank for s in scores),
        f"nDCG@{k}": statistics.fmean(s.ndcg for s in scores),
        f"precision@{k}": statistics.fmean(s.precision for s in scores),
    }


# dense 검색 baseline. **과금이 일어난다** — 색인과 질의 임베딩 둘 다 OpenAI 를 부른다
def run_dense(
    *, fmt: str, k: int, label: str, groups: list[str] | None = None
) -> dict[str, Any]:
    from rag_pipeline.config import IndexingConfig, IngestConfig
    from rag_pipeline.ingestion.embedding import get_embedder
    from rag_pipeline.ingestion.indexing import get_store
    from rag_pipeline.ingestion.pipeline import index_document

    qa = load_qa_set()
    if not qa.items:
        raise SystemExit(f"세트가 비어 있습니다: {qa.path}")

    config = IngestConfig()
    # 실행마다 저장 경로를 가른다 (모듈 docstring 참고)
    store_path = RESULTS_DIR / f"qdrant-{label}"
    object.__setattr__(config, "_indexing", IndexingConfig(qdrant_path=store_path))

    wanted = sorted({item.source_doc for item in qa.items} & set(groups or DOCUMENT_GROUPS))
    items = [item for item in qa.items if item.source_doc in wanted]
    indexed: list[str] = []
    for group in wanted:
        name = DOCUMENT_GROUPS.get(group, {}).get(fmt)
        if not name or not (FIXTURES_DIR / name).exists():
            # 그 그룹에 이 판이 없다. **조용히 넘어가면 그 그룹 질의가 문서 없이 채점된다**
            raise SystemExit(
                f"{group} 에 '{fmt}' 판이 없습니다. --groups 로 그룹을 제한하거나 "
                f"다른 --format 을 고르세요 (있는 라벨: {sorted(DOCUMENT_GROUPS.get(group, {}))})"
            )
        result = index_document(FIXTURES_DIR / name, original_name=name, config=config)
        indexed.append(f"{name} → {result.chunk_count}청크")

    embedder = get_embedder(config.embedding)
    store = get_store(config.indexing, config.embedding)
    scores: list[QueryScore] = []
    try:
        for item in items:
            vector = embedder.embed_query(item.query)
            hits = store.search(vector, top_k=k)
            scores.append(score_query(item.query_id, [h.text for h in hits], item.gold_spans, k))
    finally:
        # 안 닫으면 다음 실행이 배타 잠금으로 통째로 죽는다
        store.close()

    # 그룹별로 갈라 본다. 전체 평균은 "어느 문서가 문제인가"를 숨긴다
    by_group: dict[str, Any] = {}
    for group in wanted:
        ids = {item.query_id for item in items if item.source_doc == group}
        by_group[group] = summarize([s for s in scores if s.query_id in ids], k)

    return {
        "label": label,
        "format": fmt,
        "groups": wanted,
        "k": k,
        "indexed": indexed,
        "usage": {"tokens": embedder.usage.total_tokens, "calls": embedder.usage.api_calls},
        "summary": summarize(scores, k),
        "by_group": by_group,
        "per_query": [vars(s) for s in scores],
    }


# 명령줄 진입점
def main() -> int:
    parser = argparse.ArgumentParser(description="검색 품질 평가 러너")
    parser.add_argument("--check", action="store_true", help="세트만 검증한다 (과금 없음)")
    parser.add_argument("--dense", action="store_true", help="dense baseline 을 잰다 (과금)")
    parser.add_argument("--format", dest="fmt", default="pdf", help="색인할 포맷 라벨 (pdf / hwpx)")
    parser.add_argument("--groups", nargs="*", help="특정 그룹만. 비우면 세트의 전 그룹")
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--label", default="dense-baseline")
    parser.add_argument("--refresh", action="store_true", help="파싱 캐시를 버리고 다시 읽는다")
    args = parser.parse_args()

    if args.check or not args.dense:
        problems = check(refresh=args.refresh)
        for line in problems:
            print("  ✗", line)
        print(f"\n{'통과' if not problems else str(len(problems)) + '건 문제'}")
        return 1 if problems else 0

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    report = run_dense(fmt=args.fmt, k=args.k, label=args.label, groups=args.groups)
    out = RESULTS_DIR / f"{args.label}-{args.fmt}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    for key, value in report["summary"].items():
        print(f"  {key:16s} {value:.4f}" if isinstance(value, float) else f"  {key:16s} {value}")
    print(f"\n결과: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
