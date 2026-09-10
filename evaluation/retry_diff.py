"""근거가 안 바뀐 재시도 회차의 **다음 회차**가 무엇을 찾았나.

`AgentState.evidence_unchanged_at` 은 *재시도가 질의를 바꿨는데 검색이 글자 그대로 같은 근거를
물어온 회차* 를 세기만 한다. 접을지는 **«접었다면 놓쳤을 회차가 실제로 무엇을 찾았나»** 를 몇 번
모아 본 뒤에 정하기로 했고(`.claude/wiki/plan_log/검색-생성.md` 「다음 승인 단위 A」), 이 도구가 그 재료를 만든다.

## 왜 이 도구가 따로 있나 — `run_eval` 로는 못 잰다

`run_eval` 은 `retrieve()` 를 직접 부른다. 재시도는 **루프**가 만드는 것이라 그 경로에 아예 없다.
반대로 여기서는 순위 지표를 새로 정의하지 않는다 — `scoring.hit_rank()` 를 그대로 쓴다.

## 부정 예시를 뺀다 — 표본이 제일 많은데도 뺀다

부정 예시(답이 코퍼스에 없는 질의)는 판정이 늘 *불충분* 이라 **재시도를 끝까지 돌아 회차를 제일
많이 준다.** 그런데 놓칠 정답이 애초에 없어 *다음 회차가 값을 했나* 에 **원리적으로 못 답한다.**
`run_eval` 이 `skipped_unanswerable` 로 빼는 것과 같은 이유라 같은 함수(`split_scorable`)를 쓴다.

## 재시도를 강제하되 **진짜 판정을 지우지 않는다**

루프는 판정이 *불충분* 일 때만 재시도한다. 그대로 두면 잘 찾은 질의가 한 바퀴에 끝나 회차가
몇 건 안 남는다 — 「표본 1」을 「표본 5」로 바꾸고 끝날 위험이다. 그래서 `_ForcingEvaluator` 가
**진짜 판정을 부르고 그 값을 기록한 뒤** 루프에는 `insufficient` 를 돌려준다.

**그 한 줄이 «한 번 돌려 둘 다 답한다» 의 전부다** — 기록된 진짜 verdict 로 *실사용이었으면
여기까지 돌았을까* 를 뒤에서 되짚는다. 지우고 가짜만 남기면 그 부분집합을 영원히 못 만든다.
`failed` 도 그대로 싣는다: 죽은 판정자가 *불충분* 으로 삼켜지는 것은 `schemas/answer.py` 가
타입으로 막아 둔 바로 그 사고다.

**그래서 이 표의 발생률을 실사용 발생률로 읽으면 안 된다.** 강제가 만든 회차가 섞여 있다.

## 마지막 회차에서 근거가 같으면 «못 봤다» 로 센다

짝지을 다음 회차가 없다. 이것을 *안 도왔다* 와 한 칸에 넣으면 **정보가 없는 것이 근거로
둔갑한다** — 실제로 2026-08-28 단일 프로브가 `max_retries=2` 로 돌아 근거 동일 회차가 마지막
회차였고, 접기 판단에 0비트를 얻고도 «쟀다» 가 될 뻔했다. 그래서 `max_retries` 기본이 제품
기본값인 3이고, 짝 없는 것은 표에서 열이 따로 선다.

## 생성은 안 부른다

측정 축이 `step_retrieve` 안이라 생성이 안 닿는다. 가짜 생성기를 넘겨 질의당 한 번의 유료
호출을 없앤다(27회차 실물 프로브도 같은 이유로 안 불렀다). **판정이 이 실행의 지배항이라**
`--no-record-verdict` 로 그것까지 끌 수 있는데, 그러면 위 «실사용 부분집합» 열이 사라진다.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from evaluation.config import (
    DEFAULT_EMBEDDING_PROFILE,
    DEFAULT_EVALUATOR_PROFILE,
    DEFAULT_RERANK_PROFILE,
    DEFAULT_REWRITE_PROFILE,
    eval_config,
)
from evaluation.inspect import load_report, require_fresh_store
from evaluation.qa_set import QA_SET_PATH, load_qa_set
from evaluation.run_eval import RESULTS_DIR, split_scorable
from evaluation.scoring import hit_rank
from rag_pipeline.agent import RetrieverTool
from rag_pipeline.schemas.agent import ToolInput, ToolOutput
from rag_pipeline.schemas.answer import EvaluatorVerdict
from rag_pipeline.schemas.retrieval import RetrievedChunk

__all__ = [
    "PairRow",
    "RetryRound",
    "RetryRun",
    "collect_runs",
    "pair_rows",
    "production_stop",
    "summarize",
]

# 짝지은 결과의 판정. **«못 봤다» 가 «안 도왔다» 와 다른 칸인 것이 이 도구의 요점이다**
VERDICT_HELPED = "접었으면 놓쳤다"
VERDICT_NO_HELP = "안 도왔다"
VERDICT_UNSEEN = "못 봤다"


# 검색 한 회차의 흔적. `rank` 는 그 회차 근거에서 정답이 몇 위였나다(못 찾으면 None)
@dataclass(frozen=True)
class RetryRound:
    retry_count: int
    query: str
    chunk_ids: list[str]
    rank: int | None
    # 강제 전의 **진짜** 판정. `--no-record-verdict` 면 None
    real_verdict: str | None
    succeeded: bool


# 질의 하나가 루프를 돈 흔적 전체
@dataclass(frozen=True)
class RetryRun:
    query_id: str
    query: str
    source_doc: str
    stop_reason: str | None
    retry_count: int
    # 루프가 «앞 회차와 근거가 같다» 고 센 회차의 `retry_count` 들
    evidence_unchanged_at: list[int]
    rounds: list[RetryRound] = field(default_factory=list)


# 근거가 같았던 회차 N 과 그 다음 회차를 짝지은 한 줄
@dataclass(frozen=True)
class PairRow:
    query_id: str
    n: int
    rank_n: int | None
    rank_next: int | None
    # N+1 회차가 실재했나. 거짓이면 `verdict` 가 «못 봤다» 다
    seen: bool
    verdict: str
    # 실사용이었으면 이 회차에 **닿았을까** — 0~N-1 이 전부 «더 찾고 싶다» 여야 한다.
    # 진짜 판정을 안 받았거나 도중에 도구가 실패했으면 None (`production_stop` 참고)
    would_run_in_production: bool | None


# 회차별 근거를 남기는 껍데기. **`_run()` 만 감싼다** — `run()` 은 예외를 값으로 바꾸는
# 템플릿 메서드라, 거기 손대면 실물과 다른 것을 재게 된다
class _ProbeRetriever(RetrieverTool):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.rounds: list[tuple[str, list[str], list[str], bool]] = []

    def _run(self, tool_input: ToolInput) -> ToolOutput:
        output = super()._run(tool_input)
        chunks: list[RetrievedChunk] = list(output.data or []) if output.success else []
        self.rounds.append(
            (tool_input.query, [c.chunk_id for c in chunks], [c.text for c in chunks], output.success)
        )
        return output

    def reset(self) -> None:
        self.rounds = []


# 판정을 실제로 부르고 그 값을 적어 둔 뒤, 루프에는 *불충분* 을 돌려준다
# (모듈 docstring 참고). `record=False` 면 진짜 판정을 아예 안 부른다 — 그만큼 싸진다
class _ForcingEvaluator:
    def __init__(self, inner: Any, *, record: bool) -> None:
        self._inner = inner
        self._record = record
        self.seen: list[str | None] = []

    def evaluate(self, query: str, chunks: list[RetrievedChunk]) -> EvaluatorVerdict:
        if self._record:
            real = self._inner.evaluate(query, chunks)
            self.seen.append(real.verdict)
        else:
            self.seen.append(None)
        # **강제**. 진짜 값은 위에 남아 있다
        return EvaluatorVerdict(verdict="insufficient", reasoning="retry_diff 가 재시도를 강제함")

    def reset(self) -> None:
        self.seen = []


# 생성은 이 측정에 안 닿는다 (모듈 docstring 참고)
def _no_generate(query: str, chunks: list[RetrievedChunk]) -> str:
    return ""


# 질의마다 루프를 돌려 회차 흔적을 모은다. 반환은 (실행들, 메타)
def collect_runs(
    *,
    label: str,
    fmt: str,
    top_k: int = 5,
    max_retries: int = 3,
    qa_path: Path | None = None,
    rerank: str = DEFAULT_RERANK_PROFILE,
    rewrite: str = DEFAULT_REWRITE_PROFILE,
    evaluator_name: str = DEFAULT_EVALUATOR_PROFILE,
    record_verdict: bool = True,
    limit: int | None = None,
) -> tuple[list[RetryRun], dict[str, Any]]:
    from rag_pipeline.agent import run_agent_loop
    from rag_pipeline.generation import get_evaluator
    from rag_pipeline.ingestion.embedding import get_embedder
    from rag_pipeline.ingestion.indexing import get_store
    from rag_pipeline.retrieval import build_bm25_index, get_reranker, get_rewriter

    qa = load_qa_set(qa_path)
    if not qa.items:
        raise SystemExit(f"세트가 비어 있습니다: {qa.path}")
    # 부정 예시는 이 도구의 물음에 원리적으로 못 답한다 (모듈 docstring 참고)
    items, skipped_unanswerable = split_scorable(qa.items)
    if not items:
        raise SystemExit(f"채점할 질의가 없습니다 (부정 예시 {skipped_unanswerable}건뿐): {qa.path}")
    if limit is not None:
        items = items[:limit]

    report = load_report(label, fmt)
    embedding = report.get("embedding_profile") or DEFAULT_EMBEDDING_PROFILE
    config = eval_config(
        qdrant_path=RESULTS_DIR / f"qdrant-{report['label']}-{fmt}",
        embedding=embedding,
        rerank=rerank,
        rewrite=rewrite,
        evaluator=evaluator_name,
    )

    rewriter = get_rewriter(config.rewrite)
    if rewriter is None:
        # **조용히 안 넘어간다** — 재작성이 꺼지면 승급할 질의가 없어 루프가 첫 회차에서
        # 접히고, 표는 «근거가 같은 회차가 없다» 로 멀쩡해 보인다
        raise SystemExit(
            f"재작성이 꺼져 있습니다: --rewrite {rewrite} 의 rewrite_model 이 비었습니다. "
            "이 도구는 재시도가 질의를 바꾼 회차를 재므로 재작성 없이는 잴 것이 없습니다"
        )
    inner_evaluator = get_evaluator(config.evaluator) if record_verdict else None
    if record_verdict and inner_evaluator is None:
        raise SystemExit(
            "판정이 꺼져 있습니다: EVAL_EVALUATORS 의 evaluator_model 이 비었습니다. "
            "판정 없이 돌리려면 --no-record-verdict 를 주세요"
        )

    embedder = get_embedder(config.embedding)
    store = get_store(config.indexing, config.embedding)
    reranker = get_reranker(config.rerank)

    runs: list[RetryRun] = []
    try:
        total = store.count()
        require_fresh_store(
            total, sum(x["chunks"] for x in report["indexed"]), label=report["label"], fmt=fmt
        )
        # **스택은 루프 밖에서 한 번 만든다** — 안에서 지으면 BM25 가 저장소를 질의마다 훑고,
        # 무엇보다 `usage` 가 질의마다 리셋돼 아래 회계 게이트가 통째로 못 돈다 (`run_eval`)
        bm25 = build_bm25_index(store)
        print(
            f"컬렉션 {total}건 · 세트 {len(items)}건(부정 {skipped_unanswerable}건 제외) · "
            f"top_k {top_k} · max_retries {max_retries} · 리랭커 {rerank} · 재작성 {rewrite} · "
            f"판정 {'끔' if not record_verdict else evaluator_name}"
        )

        tool = _ProbeRetriever(
            store=store,
            embedder=embedder,
            top_k=top_k,
            bm25=bm25,
            reranker=reranker,
            rewriter=rewriter,
            config=config.retrieval,
        )
        forcing = _ForcingEvaluator(inner_evaluator, record=record_verdict)

        for index, item in enumerate(items, start=1):
            tool.reset()
            forcing.reset()
            state = run_agent_loop(
                item.query,
                retriever=tool,
                evaluator=forcing,
                rewriter=rewriter,
                generate=_no_generate,
                max_retries=max_retries,
            )
            runs.append(_run_of(item, state, tool, forcing))
            print(f"  [{index}/{len(items)}] {item.query_id} · 회차 {len(tool.rounds)} · "
                  f"근거동일 {state.evidence_unchanged_at}")
    finally:
        # 안 닫으면 다음 실행이 배타 잠금으로 통째로 죽는다
        store.close()

    searches = sum(len(run.rounds) for run in runs)
    meta = {
        "label": report["label"],
        "format": fmt,
        "top_k": top_k,
        "max_retries": max_retries,
        "embedding_profile": embedding,
        "rerank_profile": rerank,
        "rewrite_profile": rewrite,
        "evaluator_profile": evaluator_name if record_verdict else None,
        "record_verdict": record_verdict,
        "queries": len(runs),
        "skipped_unanswerable": skipped_unanswerable,
        "searches": searches,
        # **회계 게이트** — 지표보다 먼저 읽는다 (`_print` 참고)
        "rewrite_calls": rewriter.usage.api_calls,
        "rewrite_cache_hits": rewriter.usage.cache_hits,
        "rerank_calls": getattr(reranker, "usage", None) and reranker.usage.api_calls,
        "rerank_cache_hits": getattr(reranker, "usage", None) and reranker.usage.cache_hits,
        "bm25_warnings": list(bm25.warnings),
        "rewrite_warnings": list(rewriter.warnings),
        "rerank_warnings": list(getattr(reranker, "warnings", []) or []),
        "evaluator_warnings": list(getattr(inner_evaluator, "warnings", []) or []),
    }
    return runs, meta


# 루프가 남긴 상태와 프로브가 본 회차를 한 레코드로 합친다
def _run_of(item: Any, state: Any, tool: _ProbeRetriever, forcing: _ForcingEvaluator) -> RetryRun:
    # **회차 수와 `retry_count` 가 어긋나면 멈춘다** — 이 도구는 회차 index 를 `retry_count` 로
    # 읽어 `evidence_unchanged_at` 과 짝짓는다. 루프가 재시도마다 정확히 한 번 검색한다는
    # 전제가 깨지면 짝이 한 칸씩 밀리는데, 표는 멀쩡해 보인다
    if len(tool.rounds) != state.retry_count + 1:
        raise RuntimeError(
            f"{item.query_id}: 회차 {len(tool.rounds)}개인데 retry_count {state.retry_count} 다"
        )

    rounds = [
        RetryRound(
            retry_count=i,
            query=query,
            chunk_ids=chunk_ids,
            rank=hit_rank(texts, item.gold_spans) if item.gold_spans else None,
            real_verdict=forcing.seen[i] if i < len(forcing.seen) else None,
            succeeded=succeeded,
        )
        for i, (query, chunk_ids, texts, succeeded) in enumerate(tool.rounds)
    ]
    return RetryRun(
        query_id=item.query_id,
        query=item.query,
        source_doc=item.source_doc,
        stop_reason=state.stop_reason,
        retry_count=state.retry_count,
        evidence_unchanged_at=list(state.evidence_unchanged_at),
        rounds=rounds,
    )


# 근거가 같았던 회차 N 을 N+1 과 짝짓는다. **N+1 이 없으면 «못 봤다»** (모듈 docstring 참고)
def pair_rows(runs: list[RetryRun]) -> list[PairRow]:
    rows: list[PairRow] = []
    for run in runs:
        by_count = {r.retry_count: r for r in run.rounds}
        stop = production_stop(run.rounds)
        for n in run.evidence_unchanged_at:
            here = by_count.get(n)
            nxt = by_count.get(n + 1)
            if here is None:
                continue
            if nxt is None:
                verdict = VERDICT_UNSEEN
            elif _is_better(nxt.rank, here.rank):
                verdict = VERDICT_HELPED
            else:
                verdict = VERDICT_NO_HELP
            rows.append(
                PairRow(
                    query_id=run.query_id,
                    n=n,
                    rank_n=here.rank,
                    rank_next=nxt.rank if nxt else None,
                    seen=nxt is not None,
                    verdict=verdict,
                    would_run_in_production=None if stop is None else stop >= n,
                )
            )
    return rows


# 순위가 좋아졌나. **못 찾음(None)은 제일 나쁜 값이다** — 숫자와 섞어 비교하면 터진다
def _is_better(new: int | None, old: int | None) -> bool:
    if new is None:
        return False
    if old is None:
        return True
    return new < old


# 이 회차 **다음으로** 갔을까. 진짜 판정이 *불충분* 이거나 근거가 0건일 때만 참
# (`router.py` 규칙 4·5 와 같은 조건이다).
#
# **도구가 실패한 회차는 판단 불가다.** `record_tool_call()` 이 실패 시 `retrieved_chunks` 를
# 아예 안 건드리므로 그때 Router 가 본 것은 **앞 회차 근거**이지 0건이 아니다 — 여기 적힌
# `chunk_ids` 는 비어 있어 규칙 5 를 거꾸로 읽게 만든다. 앞 회차를 되짚어 흉내 낼 수는 있지만,
# 그것은 재는 것이 아니라 짐작하는 것이라 None 으로 둔다
def _wants_more(round_: RetryRound) -> bool | None:
    if round_.real_verdict is None or not round_.succeeded:
        return None
    return round_.real_verdict == "insufficient" or not round_.chunk_ids


# 실사용이었으면 **어느 회차에서 멈췄을까**. 판단 불가를 만나면 None —
# 거기서부터 아래가 전부 짐작이 된다.
#
# **회차 하나만 보고 «실사용에서도 돌았다» 고 하면 안 된다.** 회차 N 에 *닿으려면* 0~N-1 이
# 전부 «더 찾고 싶다» 여야 하고, N 자신의 판정이 답하는 것은 *N 다음으로 갔을까* 다 — 둘은
# 다른 물음이라 하나로 겸하면 «닿지도 않았을 회차» 가 실사용으로 세어진다
def production_stop(rounds: list[RetryRound]) -> int | None:
    for round_ in rounds:
        more = _wants_more(round_)
        if more is None:
            return None
        if not more:
            return round_.retry_count
    return rounds[-1].retry_count if rounds else None


# 표를 한 줄 요약으로. **«못 봤다» 를 분모에서 빼지 않는다** — 빼면 «실행이 몇 개인가» 가 사라진다
def summarize(runs: list[RetryRun], rows: list[PairRow]) -> dict[str, Any]:
    seen = [r for r in rows if r.seen]
    return {
        "queries": len(runs),
        "searches": sum(len(run.rounds) for run in runs),
        "unchanged_rounds": len(rows),
        "queries_with_unchanged": sum(1 for run in runs if run.evidence_unchanged_at),
        "paired": len(seen),
        "unseen": sum(1 for r in rows if not r.seen),
        "helped": sum(1 for r in seen if r.verdict == VERDICT_HELPED),
        "no_help": sum(1 for r in seen if r.verdict == VERDICT_NO_HELP),
        # 강제가 만든 회차를 뺀 부분집합. **판단 불가를 0 으로 세지 않는다** —
        # 0 은 «실사용에서 한 번도 안 돈다» 로 읽히는데 그것은 안 잰 것이다.
        # 전부 판단 불가면(진짜 판정을 안 받았으면) 아예 None 이다
        "would_run_in_production": (
            None
            if rows and all(r.would_run_in_production is None for r in rows)
            else sum(1 for r in rows if r.would_run_in_production)
        ),
        "would_run_unknown": sum(1 for r in rows if r.would_run_in_production is None),
    }


# 사람이 읽을 표
def _print(runs: list[RetryRun], rows: list[PairRow], meta: dict[str, Any]) -> None:
    print("\n[회계 게이트] — 지표보다 먼저 읽는다")
    searches = meta["searches"]
    rw = (meta["rewrite_calls"] or 0) + (meta["rewrite_cache_hits"] or 0)
    rr = (meta["rerank_calls"] or 0) + (meta["rerank_cache_hits"] or 0)
    print(f"  검색 {searches}회")
    print(f"  rewrite api={meta['rewrite_calls']} cache={meta['rewrite_cache_hits']} "
          f"합 {rw} {'✔' if rw == searches else '✗ 어긋난다'}")
    print(f"  rerank  api={meta['rerank_calls']} cache={meta['rerank_cache_hits']} "
          f"합 {rr} {'✔' if rr == searches else '✗ 어긋난다'}")
    for key in ("bm25_warnings", "rewrite_warnings", "rerank_warnings", "evaluator_warnings"):
        if meta[key]:
            print(f"  {key}: {meta[key]}")

    print("\n[근거가 같았던 회차 N → N+1]")
    if not rows:
        print("  없음 — 이 실행에서는 근거가 같은 재시도가 안 났다")
    for row in rows:
        nxt = "—" if not row.seen else (row.rank_next if row.rank_next is not None else "못 찾음")
        here = row.rank_n if row.rank_n is not None else "못 찾음"
        prod = {True: "실사용도", False: "강제만", None: "?"}[row.would_run_in_production]
        print(f"  [{row.query_id}] N={row.n} · {here} -> {nxt} · {row.verdict} · {prod}")

    s = summarize(runs, rows)
    print("\n[요약]")
    print(f"  질의 {s['queries']} · 검색 {s['searches']} · 근거 동일 회차 {s['unchanged_rounds']}"
          f"(질의 {s['queries_with_unchanged']}건)")
    print(f"  짝지음 {s['paired']} · 못 봄 {s['unseen']}")
    print(f"  접었으면 놓쳤다 {s['helped']} · 안 도왔다 {s['no_help']}")
    if s["would_run_in_production"] is None:
        print("  실사용 부분집합: 못 만든다 (--no-record-verdict 로 돌렸다)")
    else:
        unknown = f" · 판단 불가 {s['would_run_unknown']}" if s["would_run_unknown"] else ""
        print(f"  그중 실사용에서도 돌았을 회차: {s['would_run_in_production']}{unknown}")
    print("\n  ※ 재시도를 강제했으므로 이 표의 발생률을 실사용 발생률로 읽지 않는다")


# CLI 진입점
def main() -> int:
    parser = argparse.ArgumentParser(
        description="근거가 안 바뀐 재시도 회차의 다음 회차가 무엇을 찾았나"
    )
    parser.add_argument("--label", required=True, help="읽을 실행 라벨 (그 컬렉션을 연다)")
    parser.add_argument("--format", dest="fmt", default="pdf")
    parser.add_argument("--qa-set", dest="qa_set", default=str(QA_SET_PATH))
    parser.add_argument("--top-k", type=int, default=5)
    # **제품 기본값과 같은 3이다** — 2로 두면 근거 동일 회차가 마지막 회차가 되어 짝지을
    # 다음 회차가 없는 경우만 는다 (모듈 docstring 참고)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--rerank", default=DEFAULT_RERANK_PROFILE)
    parser.add_argument("--rewrite", default=DEFAULT_REWRITE_PROFILE)
    parser.add_argument("--evaluator", default=DEFAULT_EVALUATOR_PROFILE)
    parser.add_argument(
        "--no-record-verdict",
        action="store_true",
        help="진짜 판정을 아예 안 부른다. 훨씬 싸지지만 «실사용 부분집합» 열이 사라진다",
    )
    parser.add_argument("--limit", type=int, default=None, help="앞에서 N건만 (배선 확인용)")
    parser.add_argument("--json", action="store_true", help="결과를 JSON 으로도 남긴다")
    args = parser.parse_args()

    runs, meta = collect_runs(
        label=args.label,
        fmt=args.fmt,
        top_k=args.top_k,
        max_retries=args.max_retries,
        qa_path=Path(args.qa_set),
        rerank=args.rerank,
        rewrite=args.rewrite,
        evaluator_name=args.evaluator,
        record_verdict=not args.no_record_verdict,
        limit=args.limit,
    )
    rows = pair_rows(runs)
    _print(runs, rows, meta)

    if args.json:
        # **재작성 프로필이 이름에 들어간다** — `fuse` 와 `replace` 는 다른 질의를 내므로
        # 다른 실험이다. 안 넣으면 라벨이 같은 두 실행이 같은 자리를 다퉈 에러 없이 덮인다
        out = RESULTS_DIR / f"{args.label}-retry-diff-{args.rewrite}-{args.fmt}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "meta": meta,
            "summary": summarize(runs, rows),
            "pairs": [asdict(r) for r in rows],
            "runs": [asdict(r) for r in runs],
        }
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n남김: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
