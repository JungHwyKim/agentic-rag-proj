"""부정 예시 후보를 사람이 검수하도록 상위 k 를 뽑아 보여준다. **재지 않고 읽는다.**

## 무엇을 담보하려는 도구인가

부정 예시의 라벨은 *"이 코퍼스에 답이 없다"* 이고, 그것은 **문서 축**의 판정이다.
*"검색이 못 찾았다"* 는 **검색 축**이라 다른 것인데, 둘을 섞으면 검색 실패가 부정으로
들어가고 그 질의에서 Evaluator 는 **우연히** 옳게 맞힌다. 그러면 Phase 4 의 점수가
Evaluator 의 실력이 아니라 검색기의 실패를 재게 된다.

그래서 이 도구는 사람을 대신하지 않는다. 사람이 판정할 재료를 모아 줄 뿐이다.

## 검색기를 **한 방향으로만** 쓴다

- 검색기가 답을 **찾아내면** 부정 라벨이 틀린 것이다 — 그 건은 부정에서 뺀다. **결정적이다**
- 검색기가 **못 찾은 것은 근거가 아니다** — 답이 있는데 순위가 밀렸을 수 있다

뒤엣것까지 근거로 쓰면 *"검색기가 못 찾은 것을 정답 없음으로 확정"* 하는 자기 채점이 된다.
`verify_log/2026-08-23-사람질의-편향.md` 가 정답 span 을 모델이 찾을 때 **검색기를 일부러
안 거친** 것과 같은 이유다.

한 방향으로만 쓰기 때문에 **반증은 최대한 세게 건다** — BM25 를 늘 얹고 리랭커도 켠다.
찾을 기회를 최대로 준 뒤에도 안 나오는 것만 사람에게 넘어간다. `--hybrid` 같은 스위치를
안 둔 이유가 이것이다(끄면 반증이 약해지기만 한다).

## 임베딩 프로필은 **인자로 안 받고 리포트에서 읽는다**

컬렉션 이름에 임베딩 모델이 들어간다(`references/indexing.md` 2절). 그래서 프로필을 손으로
넘기면 **같은 디렉터리의 다른 컬렉션**을 열어 0건이 나온다 — 실제로 이 도구의 첫 실행이
그렇게 걸렸다(리포트 414건 / 컬렉션 0건). `require_fresh_store()` 가 **소리 내어** 막아서
드러났지만, 그 가드가 없었으면 *"검색기가 아무것도 못 찾았다"* 가 되어 **28건 전부가 부정
으로 확정**될 뻔했다. 반증 도구가 반증에 실패하는 방향으로 조용히 틀리는 자리다.

`rerank_diff.py` 가 같은 이유로 프로필을 리포트에서 읽는다. 여기도 같게 맞춘다.

## 근거를 자르지 않는다 — 절단은 **한 방향으로만** 틀린다

첫 검수(2026-08-26)에서 상위 3개를 **180자로 잘라** 냈다가 두 건이 뒤집혔다. 이 코퍼스의
청크는 **1,100자대**라 `hsch-01` 의 답(*보존기간을 7종(영구, 준영구, 30년, …)으로 구분함*)도
`hsch-08` 의 열람 조항 뒷부분도 절단면 뒤에 있었다. 25건 중 2건(8%)이다.

**이것이 대칭이 아닌 것이 요점이다.** 근거를 짧게 보이면 검수자는 *"답이 없다"* 쪽으로
기울고, 이 세트에서 그 방향이 곧 **부정을 늘리는 방향**이라 오검출이 조용히 쌓인다.
반대 방향의 오검출(답이 없는데 있다고 보는 것)은 잘라도 안 생긴다.

그래서 `--chars` 기본값이 청크 전문에 가깝다. **줄이려면 그 비대칭을 알고 줄인다.**

## 왜 색인을 다시 안 하나

`inspect.py` 와 같다. 기준선·실험 실행이 만든 컬렉션이 `.cache/evaluation/runs/` 에 그대로
있고, 다시 색인하면 **그 사이 로더가 바뀐 만큼 청크가 달라져** 검수자가 본 것과 세트에
들어가는 것이 어긋난다. `require_fresh_store()` 가 낡은 저장소를 막는 것도 그대로 쓴다.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from typing import Any

from evaluation.config import DEFAULT_EMBEDDING_PROFILE, DEFAULT_RERANK_PROFILE, eval_config
from evaluation.inspect import load_report, require_fresh_store
from evaluation.qa_set import QA_SET_PATH, load_qa_set
from evaluation.run_eval import RESULTS_DIR

__all__ = ["Candidate", "Hit", "review_negatives"]


# 검수자에게 보여줄 후보 청크 하나
@dataclass
class Hit:
    rank: int
    chunk_id: str
    page: int | None
    heading_path: list[str]
    rerank_score: float | None
    text: str


# 부정 후보 질의 하나와 그것으로 실제 올라온 것들
@dataclass
class Candidate:
    query_id: str
    query: str
    source_doc: str
    hits: list[Hit]


# 부정 후보마다 상위 k 를 뽑는다. 판정은 안 한다 — 사람이 읽을 재료만 모은다
def review_negatives(
    *,
    label: str,
    fmt: str,
    qa_path: Any = None,
    top: int = 8,
    rerank: str = DEFAULT_RERANK_PROFILE,
) -> tuple[list[Candidate], dict[str, Any]]:
    from rag_pipeline.ingestion.embedding import get_embedder
    from rag_pipeline.ingestion.indexing import get_store
    from rag_pipeline.retrieval import build_bm25_index, get_reranker, retrieve

    qa = load_qa_set(qa_path)
    wanted = [item for item in qa.items if not item.answerable]
    if not wanted:
        raise SystemExit(
            f"이 세트에 부정 예시가 없습니다: {qa.path} "
            "— `answerable` 이 거짓인 레코드를 넣거나 --qa-set 으로 다른 세트를 주세요"
        )

    report = load_report(label, fmt)
    # **프로필을 인자로 안 받는다** (모듈 docstring 참고). 컬렉션 이름에 임베딩 모델이 들어가
    # 손으로 넘기면 다른 컬렉션을 열어 0건이 된다
    embedding = report.get("embedding_profile") or DEFAULT_EMBEDDING_PROFILE
    # 그 실행의 저장 경로다. 이름 규칙은 `run_eval.run_dense()` 와 같아야 한다
    config = eval_config(
        qdrant_path=RESULTS_DIR / f"qdrant-{report['label']}-{fmt}",
        embedding=embedding,
        rerank=rerank,
    )
    embedder = get_embedder(config.embedding)
    store = get_store(config.indexing, config.embedding)
    reranker = get_reranker(config.rerank)

    rows: list[Candidate] = []
    try:
        total = store.count()
        require_fresh_store(
            total, sum(x["chunks"] for x in report["indexed"]), label=report["label"], fmt=fmt
        )
        # 반증은 세게 건다 (모듈 docstring 참고). BM25 는 늘 얹는다
        bm25 = build_bm25_index(store)
        print(f"컬렉션 {total}건 · 부정 후보 {len(wanted)}건 · 리랭커 {rerank}\n")

        for item in wanted:
            hits = retrieve(
                item.query,
                store=store,
                embedder=embedder,
                top_k=top,
                bm25=bm25,
                reranker=reranker,
                config=config.retrieval,
            )
            rows.append(
                Candidate(
                    query_id=item.query_id,
                    query=item.query,
                    source_doc=item.source_doc,
                    hits=[
                        Hit(
                            rank=i,
                            chunk_id=h.chunk_id,
                            page=h.metadata.get("page"),
                            heading_path=list(h.metadata.get("heading_path") or []),
                            rerank_score=h.rerank_score,
                            text=h.text,
                        )
                        for i, h in enumerate(hits, start=1)
                    ],
                )
            )
    finally:
        # 안 닫으면 다음 실행이 배타 잠금으로 통째로 죽는다
        store.close()

    meta = {
        "label": report["label"],
        "format": fmt,
        "embedding_profile": embedding,
        "rerank_profile": rerank,
        "candidates": len(rows),
        "top": top,
        "bm25_warnings": list(bm25.warnings),
        "rerank_warnings": list(getattr(reranker, "warnings", []) or []),
    }
    return rows, meta


# 사람이 읽는 모양. 판정 문구를 안 쓴다 — 이 도구는 "없다"고 말할 자격이 없다
def _print(rows: list[Candidate], meta: dict[str, Any], chars: int) -> None:
    for row in rows:
        print(f"\n{'=' * 78}\n[{row.query_id}] {row.query}\n  문서: {row.source_doc}")
        if not row.hits:
            print("  (검색 결과 0건)")
            continue
        for h in row.hits:
            head = " > ".join(h.heading_path) if h.heading_path else "-"
            score = f"{h.rerank_score:.4f}" if h.rerank_score is not None else "  -   "
            print(f"\n  #{h.rank} {score}  쪽 {h.page}  {head}")
            print("    " + h.text.replace("\n", " ")[:chars])
    print(f"\n{'=' * 78}")
    print("이 중에 질문의 답이 있으면 그 건은 부정 예시가 아닙니다 — answerable 을 되돌리세요.")
    print("없다고 해서 답이 문서에 없다는 뜻은 아닙니다 (모듈 docstring 참고).")
    for key in ("bm25_warnings", "rerank_warnings"):
        if meta[key]:
            print(f"  경고 {key}: {meta[key]}")


# CLI 진입점
def main() -> int:
    parser = argparse.ArgumentParser(description="부정 예시 후보의 상위 k 를 검수용으로 뽑는다")
    parser.add_argument("--label", required=True, help="읽을 실행 라벨 (그 컬렉션을 연다)")
    parser.add_argument("--format", dest="fmt", default="hwpx")
    parser.add_argument("--qa-set", dest="qa_set", default=str(QA_SET_PATH))
    parser.add_argument("--top", type=int, default=8)
    # 기본값이 큰 이유는 모듈 docstring 에 있다 — 자르면 판정이 "답이 없다" 쪽으로 기운다
    parser.add_argument("--chars", type=int, default=1200, help="청크당 보여줄 글자 수")
    parser.add_argument("--rerank", default=DEFAULT_RERANK_PROFILE)
    parser.add_argument("--json", action="store_true", help="결과를 JSON 으로도 남긴다")
    args = parser.parse_args()

    from pathlib import Path

    rows, meta = review_negatives(
        label=args.label,
        fmt=args.fmt,
        qa_path=Path(args.qa_set),
        top=args.top,
        rerank=args.rerank,
    )
    _print(rows, meta, args.chars)

    if args.json:
        out = RESULTS_DIR / f"{meta['label']}-negatives.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(
                {"meta": meta, "candidates": [asdict(r) for r in rows]},
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        print(f"\n남김: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
