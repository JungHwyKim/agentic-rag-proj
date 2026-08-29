"""평가 러너.

두 가지를 한다. **`--check` 는 과금이 없고 `--dense` 는 있다.**

- `--check` — 세트가 성립하는지만 본다. 레코드 형식 + 모든 정답 span 이 그룹의 모든 포맷에서,
  그리고 청크 하나 안에서 발견되는지. 파싱만 하므로 **키가 필요 없다**
- `--dense` — dense 검색 baseline 을 잰다. 문서를 색인하고 질의를 임베딩하므로 **OpenAI 과금이
  일어난다.** 이것이 이후 모든 비교의 기준선이다(`plan_log/평가-세트.md` 2회차)

**`--dense` 는 pytest 가 아니라 스크립트라 `paid_test_check.py` 가 안 막는다.** 사람이 승인한다.

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

## 한 번 검색하고 여러 k 로 채점한다

검색은 로컬 qdrant 라 **k 를 키워도 비용이 0** 이다(과금은 질의 임베딩에서만 난다). 그래서
후보를 `--k` 까지 한 번에 받아 `K_AXES` 로 각각 채점한다. 공짜로 얻는 것이 둘이다 —
`평가-세트.md` 비교 4번(*"`recall@top_k`(상한) vs `nDCG@5`(실효)"*)과 `indexing.md` 의
`top_k` 기본값 근거.

**`per_query` 는 `--k` 로 채점한 것을 남긴다.** 주지표 k(`--report-k`)로 남기면 `rank` 가
그보다 뒤인 질의가 전부 `None` 이 되어 *"후보를 늘리면 잡히나"* 를 못 읽는다.

## 왜 적재 카운터를 함께 남기나

`image_handler="hybrid"` 는 Vision 을 부르고 **Vision 응답은 실행마다 다르다**(실측:
`plan_log/표-경로.md` 12회차). 캐시가 얼려 주지만 캐시를 잃으면 산출이 움직인다. 그때
`vision_calls`·`ocr_applied`·`warnings` 가 없으면 **검색이 변한 것인지 적재가 변한 것인지**
가릴 수 없다. 막지는 못하고 보이게만 한다.

## 왜 결과를 두 곳에 남기나

- `.cache/.../runs/` — 매 실행. 실험 기록이라 gitignore 다
- `evaluation/baseline.json` — **`--save-baseline` 을 명시했을 때만.** 기준선은 저장소에
  살아야 다른 머신·다른 세션이 견줄 대상을 본다(`regression/baseline.json` 선례)

**수치가 달라졌다고 자동으로 덮어쓰지 않는다.** 의도한 개선과 회귀를 가르는 것은 사람 몫이다.
baseline 이 있는데 `--save-baseline` 이 없으면 **질의별 승·패·무**를 출력한다 —
*"판정은 수치 하나로 하지 않는다"* 를 코드가 지탱하는 자리다.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
from pathlib import Path
from typing import Any

from evaluation.config import (
    DEFAULT_EMBEDDING_PROFILE,
    DEFAULT_RERANK_PROFILE,
    DEFAULT_REWRITE_PROFILE,
    EVAL_RERANKERS,
    EVAL_REWRITERS,
    EVAL_SETTINGS,
    embedding_profile,
    eval_config,
)
from evaluation.corpus import build_corpus
from evaluation.documents import DOCUMENT_GROUPS, FIXTURES_DIR
from evaluation.qa_set import (
    QAItem,
    load_qa_set,
    validate_items,
    validate_spans_across_formats,
)
from evaluation.scoring import QueryScore, score_query

# 실행 결과를 남기는 곳. `.gitignore` 에 `.cache/` 가 이미 있다
RESULTS_DIR = Path(__file__).resolve().parents[1] / ".cache" / "evaluation" / "runs"

# 기준선. **이쪽은 커밋한다** (모듈 docstring 참고)
BASELINE_PATH = Path(__file__).resolve().parent / "baseline.json"

# 어떤 k 들로 채점하나. `--k` 이하인 것만 쓰고 `--k` 자신을 늘 포함한다.
# 1·3·5 는 순위 품질, 10 은 주지표, 20 은 후보 상한을 본다
K_AXES: tuple[int, ...] = (1, 3, 5, 10, 20)


# 세트가 성립하는지 본다. 문제 목록을 돌려주고 비어 있으면 통과다
def check(refresh: bool = False, qa_path: Path | None = None) -> list[str]:
    qa = load_qa_set(qa_path)
    if not qa.items:
        return [f"세트가 비어 있습니다: {qa.path}"]

    problems = validate_items(qa.items)

    # 세트의 구성을 먼저 보여준다. 부정 예시는 아래 span 검증을 통째로 건너뛰므로
    # (gold_spans 가 비어 있어 반복문이 안 돈다) 그것이 몇 건인지가 안 보이면
    # "왜 검사가 이것밖에 안 나왔나" 를 알 수 없다
    negatives = sum(1 for item in qa.items if not item.answerable)
    print(f"  세트 {len(qa.items)}건 — 정답 있음 {len(qa.items) - negatives} · 부정 예시 {negatives}")

    groups = sorted({item.source_doc for item in qa.items})
    corpus = build_corpus(groups, refresh=refresh)
    if not corpus:
        print("  (fixtures 가 없어 span 검증은 건너뜁니다)")
        return problems

    return problems + validate_spans_across_formats(qa.items, corpus)


# 채점할 것과 부정 예시를 가른다. **부정은 검색 채점에서 빠진다** (`qa_set.py` 모듈 docstring)
# — 정답이 없어 `hit@k` 가 정의상 만점이고 `recall` 은 분모가 0이라, 넣으면 검색기를 못 가른다.
# **건너뛴 수를 함께 돌려주는 것이 요점이다.** 안 세면 세트가 48건인데 지표는 20건 기준인
# 것이 리포트 어디에도 안 보인다
def split_scorable(items: list[QAItem]) -> tuple[list[QAItem], int]:
    scorable = [item for item in items if item.answerable]
    return scorable, len(items) - len(scorable)


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


# 이번 실행에서 쓸 k 축들. `--k` 를 넘는 축은 후보가 없어 의미가 없다
def axes_for(k: int) -> tuple[int, ...]:
    return tuple(sorted({*(axis for axis in K_AXES if axis <= k), k}))


# dense 검색 baseline. **과금이 일어난다** — 색인과 질의 임베딩 둘 다 OpenAI 를 부른다
def run_dense(
    *,
    fmt: str,
    k: int,
    report_k: int,
    label: str,
    groups: list[str] | None = None,
    use_bm25: bool = False,
    rerank: str = DEFAULT_RERANK_PROFILE,
    rewrite: str = DEFAULT_REWRITE_PROFILE,
    qa_path: Path | None = None,
    embedding: str = DEFAULT_EMBEDDING_PROFILE,
) -> dict[str, Any]:
    from rag_pipeline.ingestion.embedding import get_embedder
    from rag_pipeline.ingestion.indexing import get_store
    from rag_pipeline.ingestion.pipeline import index_document
    from rag_pipeline.retrieval import build_bm25_index, get_reranker, get_rewriter, retrieve

    qa = load_qa_set(qa_path)
    if not qa.items:
        raise SystemExit(f"세트가 비어 있습니다: {qa.path}")

    # **개발자 `.env` 가 아니라 고정 설정이다** (evaluation/config.py).
    # 실행마다 저장 경로를 가르는 것은 배타 잠금 때문이다 (모듈 docstring 참고)
    config = eval_config(
        qdrant_path=RESULTS_DIR / f"qdrant-{label}-{fmt}",
        embedding=embedding,
        rerank=rerank,
        rewrite=rewrite,
    )

    wanted = sorted({item.source_doc for item in qa.items} & set(groups or DOCUMENT_GROUPS))
    items, skipped_unanswerable = split_scorable(
        [item for item in qa.items if item.source_doc in wanted]
    )
    if not items:
        raise SystemExit(f"채점할 질의가 없습니다 (부정 예시 {skipped_unanswerable}건뿐): {qa.path}")
    indexed: list[dict[str, Any]] = []
    for group in wanted:
        name = DOCUMENT_GROUPS.get(group, {}).get(fmt)
        if not name or not (FIXTURES_DIR / name).exists():
            # 그 그룹에 이 판이 없다. **조용히 넘어가면 그 그룹 질의가 문서 없이 채점된다**
            raise SystemExit(
                f"{group} 에 '{fmt}' 판이 없습니다. --groups 로 그룹을 제한하거나 "
                f"다른 --format 을 고르세요 (있는 라벨: {sorted(DOCUMENT_GROUPS.get(group, {}))})"
            )
        result = index_document(FIXTURES_DIR / name, original_name=name, config=config)
        meta = result.metadata
        # 적재 카운터를 함께 싣는다 — 없으면 나중에 "검색이 변했나 적재가 변했나"를 못 가른다
        indexed.append(
            {
                "group": group,
                "file": name,
                "chunks": result.chunk_count,
                "indexed": result.indexed_count,
                "vision_calls": meta.vision_calls,
                "llamaparse_calls": meta.llamaparse_calls,
                "ocr_applied": meta.ocr_applied,
                "warnings": list(meta.warnings),
            }
        )

    axes = axes_for(k)
    embedder = get_embedder(config.embedding)
    store = get_store(config.indexing, config.embedding)
    # **인덱스를 루프 밖에서 한 번 짓는다.** 안에서 지으면 질의마다 저장소를 통째로 다시 훑어
    # 45건이 45번이 된다. `--hybrid` 가 없으면 None 이고, 그때 결과는 dense 와 글자 그대로
    # 같아야 한다 (rag_pipeline/retrieval/hybrid.py 가 그 동등성을 계약으로 박아 뒀다)
    bm25 = build_bm25_index(store) if use_bm25 else None
    # **리랭커도 루프 밖에서 한 번 만든다.** 로컬 갈래는 모델을 올리므로 질의마다 만들면
    # 그 비용을 60번 낸다. `--rerank` 를 안 주면 프로필이 `off` 라 None 이고, 그때 결과는
    # 리랭커 이전과 글자 그대로 같아야 한다 (hybrid.py 가 그 동등성을 계약으로 박아 뒀다)
    reranker = get_reranker(config.rerank)
    # **재작성기도 루프 밖에서 한 번 만든다.** 올릴 가중치는 없지만 `usage` 가 인스턴스에
    # 쌓이므로, 안에서 지으면 질의마다 새 카운터가 생겨 리포트의 호출 수가 0 이 된다 —
    # 그러면 «성공 횟수 == 질의 수» 검사가 통째로 못 돈다. `--rewrite` 를 안 주면 프로필이
    # `off` 라 None 이고, 그때 결과는 재작성 이전과 글자 그대로 같아야 한다
    rewriter = get_rewriter(config.rewrite)
    # k 축마다 QueryScore 목록. **검색은 한 번이고 채점만 여러 번이다**
    by_axis: dict[int, list[QueryScore]] = {axis: [] for axis in axes}
    try:
        for item in items:
            # **top_k=k 를 반드시 넘긴다.** retrieve() 의 기본값 5 가 실리면 by_axis[10]·
            # [20] 만 후보 부족으로 조용히 낮아져 검색 품질이 나빠진 것처럼 보인다
            hits = retrieve(
                item.query,
                store=store,
                embedder=embedder,
                top_k=k,
                bm25=bm25,
                reranker=reranker,
                rewriter=rewriter,
                config=config.retrieval,
            )
            texts = [hit.text for hit in hits]
            for axis in axes:
                by_axis[axis].append(score_query(item.query_id, texts, item.gold_spans, axis))
    finally:
        # 안 닫으면 다음 실행이 배타 잠금으로 통째로 죽는다
        store.close()

    # 그룹별로 갈라 본다. 전체 평균은 "어느 문서가 문제인가"를 숨긴다
    by_group: dict[str, Any] = {}
    for group in wanted:
        ids = {item.query_id for item in items if item.source_doc == group}
        by_group[group] = summarize([s for s in by_axis[report_k] if s.query_id in ids], report_k)

    return {
        "label": label,
        "format": fmt,
        "groups": wanted,
        # 검색 채점에서 뺀 부정 예시 수. 위 필터의 근거는 그 자리 주석에 있다
        "skipped_unanswerable": skipped_unanswerable,
        "k": k,
        "report_k": report_k,
        # **`per_query` 는 `--k` 채점이다** (모듈 docstring 참고)
        "per_query_k": k,
        # **어느 임베딩 프로필로 쟀나.** 이름과 실제 값을 함께 남긴다 — 이름만 남기면
        # 나중에 그 프로필의 내용이 바뀌었을 때 옛 결과가 무엇으로 쟀는지 알 수 없다
        "embedding_profile": embedding,
        # **`EVAL_SETTINGS` 를 그대로 실으면 안 된다** — 그 안의 `embedding` 은 기본
        # 프로필이라, 다른 프로필로 돌린 실행이 자기가 안 쓴 모델을 기록하게 된다
        # **리랭커 프로필도 같은 이유로 갈아 끼운다** — `EVAL_SETTINGS["rerank"]` 는 기본
        # 프로필(=꺼짐)이라, 그대로 실으면 리랭커를 켜고 돌린 실행이 *"안 켰다"* 로 기록된다
        "rerank_profile": rerank,
        # **재작성 프로필도 같은 이유로 싣는다** — 라벨만으로는 나중에 fuse 와 replace 를
        # 못 가른다. 그리고 상한 프로브(102위 → 2위)와 견줄 수 있는 것은 `replace` 뿐이라
        # 어느 쪽이었는지가 그 비교의 성립 조건이다
        "rewrite_profile": rewrite,
        "settings": {
            **EVAL_SETTINGS,
            "embedding": embedding_profile(embedding),
            "rerank": EVAL_RERANKERS[rerank],
            # **`EVAL_SETTINGS["rewrite"]` 를 그대로 실으면 안 된다** — 그것은 기본
            # 프로필(=꺼짐)이라, 재작성을 켜고 돌린 실행이 *"안 켰다"* 로 기록된다
            "rewrite": EVAL_REWRITERS[rewrite],
        },
        # **어느 갈래로 쟀는지가 리포트에 남아야 한다** — 파일명(label)만으로는 나중에
        # dense 와 hybrid 를 못 가른다. 경고를 함께 싣는 이유는 BM25 실패가 예외가 아니라
        # dense 폴백이라, 안 남기면 "그냥 효과가 없었다"와 구분이 안 되기 때문이다
        "retriever": {
            "use_bm25": use_bm25,
            "bm25_chunks": len(bm25) if bm25 is not None else None,
            "bm25_warnings": list(bm25.warnings) if bm25 is not None else [],
            # **리랭커 경고도 같은 이유로 싣는다** — 실패가 예외가 아니라 RRF 순서로 내려가는
            # 폴백이라, 안 남기면 *"리랭커가 값을 못 했다"* 와 *"리랭커가 안 돌았다"* 가
            # 리포트에서 구분이 안 된다. 비교표의 한 칸이 통째로 거짓이 되는 자리다
            "rerank": rerank,
            "rerank_warnings": list(reranker.warnings) if reranker is not None else [],
            # **재작성 경고도 같은 이유로 싣는다** — 실패가 예외가 아니라 1차 pool 을 그대로
            # 쓰는 폴백이라, 안 남기면 *"재작성이 값을 못 했다"* 와 *"재작성이 안 돌았다"* 가
            # 리포트에서 구분이 안 된다
            "rewrite": rewrite,
            "rewrite_warnings": list(rewriter.warnings) if rewriter is not None else [],
        },
        "indexed": indexed,
        # **리랭커 비용을 임베딩과 갈라 싣는다** — 유료 갈래는 토큰이, 로컬 갈래는 채점한
        # 후보 수(`reranked`)가 비용이다. 합치면 어느 쪽이 얼마를 썼는지 못 읽는다
        "usage": {
            "tokens": embedder.usage.total_tokens,
            "calls": embedder.usage.api_calls,
            "rerank_calls": reranker.usage.api_calls if reranker is not None else 0,
            "rerank_tokens": reranker.usage.total_tokens if reranker is not None else 0,
            "rerank_cache_hits": reranker.usage.cache_hits if reranker is not None else 0,
            "reranked": reranker.usage.reranked if reranker is not None else 0,
            # **재작성 비용도 갈라 싣는다.** 유료 갈래를 켠 실행은 지표를 읽기 **전에**
            # `rewrite_calls + rewrite_cache_hits == 질의 수` 를 확인한다 — Cohere 1차 실행이
            # 한도 초과 30건을 품질 저하로 읽히게 했던 자리와 같은 사고의 입구다(CLAUDE.md)
            "rewrite_calls": rewriter.usage.api_calls if rewriter is not None else 0,
            "rewrite_tokens": rewriter.usage.total_tokens if rewriter is not None else 0,
            "rewrite_cache_hits": rewriter.usage.cache_hits if rewriter is not None else 0,
        },
        "summary": summarize(by_axis[report_k], report_k),
        "by_k": {str(axis): summarize(by_axis[axis], axis) for axis in axes},
        "by_group": by_group,
        "per_query": [vars(score) for score in by_axis[k]],
    }


# 기준선 파일에 담을 부분. **`usage` 는 뺀다** — 캐시 적중에 따라 흔들려 diff 노이즈가 된다.
# `indexed` 의 청크 수는 남긴다: 적재가 바뀌었는지 보는 신호다
def as_baseline(report: dict[str, Any]) -> dict[str, Any]:
    meta = {
        "무엇인가": "dense 검색의 기준값. 이후 모든 검색 비교가 이 값과의 차이로 판정된다.",
        "재는 방법": (
            f"python -m evaluation.run_eval --dense --format {report['format']} "
            f"--k {report['k']} --report-k {report['report_k']}"
            + (
                ""
                if report.get("embedding_profile", DEFAULT_EMBEDDING_PROFILE)
                == DEFAULT_EMBEDDING_PROFILE
                else f" --embedding {report['embedding_profile']}"
            )
            + " --save-baseline"
        ),
        "설정": "settings 블록이 그 실행의 고정 설정이다 (evaluation/config.py).",
        "갱신 규칙": (
            "수치가 달라졌을 때 자동으로 덮어쓰지 않는다. --save-baseline 없이 돌리면 질의별 "
            "승·패·무가 나오므로 그것을 읽고, 의도한 변화일 때만 갱신하며 근거를 task_log/ 에 남긴다."
        ),
        "주의": (
            "입력 파일은 tests/fixtures/ 에 있고 gitignore 다 — 파일이 없는 환경에서는 재현할 수 "
            "없다. 그리고 image_handler=hybrid 라 .cache/content 를 지우면 Vision 응답이 달라져 "
            "이 값이 조용히 흔들린다. indexed 의 vision_calls 를 함께 보는 이유다."
        ),
        "갱신 이력": [],
    }
    # **`retriever` 와 `rerank_profile` 이 빠져 있었다.** 그래서 기준선 파일만 보고는
    # dense 인지 hybrid 인지 못 가렸다 — 이번 회차는 기준선을 안 올리지만, 누군가
    # `--hybrid --save-baseline` 을 돌리는 순간 갈래를 조용히 잃는 자리라 함께 닫는다.
    # `usage` 는 여전히 뺀다(캐시 적중에 따라 흔들려 diff 노이즈가 된다)
    keys = (
        "label",
        "format",
        "embedding_profile",
        "rerank_profile",
        # 같은 이유로 함께 담는다 — 빼면 기준선 파일만 보고 재작성을 켠 실행인지 못 가른다
        "rewrite_profile",
        "retriever",
        "groups",
        "k",
        "report_k",
        "per_query_k",
        "settings",
        "indexed",
        "summary",
        "by_k",
        "by_group",
        "per_query",
    )
    return {"_meta": meta, **{key: report[key] for key in keys}}


# 기준선 대비 질의별 승·패·무. **순위로 본다** — 평균이 숨기는 것이 정확히 이것이다
def compare_to_baseline(report: dict[str, Any], baseline: dict[str, Any]) -> list[str]:
    if baseline.get("format") != report.get("format"):
        return [
            f"  (기준선은 '{baseline.get('format')}' 판이라 "
            f"'{report.get('format')}' 과 직접 비교하지 않습니다)"
        ]

    # **포맷과 같은 이유로 임베딩 프로필도 가른다.** 다른 모델로 잰 순위를 승·패로 세면
    # 검색이 좋아진 것인지 모델이 다른 것인지 못 가른다 — 60건 요약과 45건 요약을 견주는
    # 사고를 막는 그 가드와 같은 종류다. **프로필 축이 생기기 전의 기준선에는 이 키가 없고,
    # 그때는 실제로 기본 프로필이었으므로** 없으면 기본으로 본다
    old_profile = baseline.get("embedding_profile", DEFAULT_EMBEDDING_PROFILE)
    new_profile = report.get("embedding_profile", DEFAULT_EMBEDDING_PROFILE)
    if old_profile != new_profile:
        return [
            f"  (기준선은 '{old_profile}' 임베딩이라 "
            f"'{new_profile}' 과 직접 비교하지 않습니다)"
        ]

    before = {row["query_id"]: row["rank"] for row in baseline.get("per_query", [])}
    lines: list[str] = []
    won = lost = same = 0
    # 못 찾은 것(rank=None)은 제일 뒤로 민다. 그래야 "없음 -> 3" 이 이김으로 세어진다
    far = max(report.get("k", 20), baseline.get("k", 20)) + 1
    for row in report["per_query"]:
        if row["query_id"] not in before:
            continue
        old, new = before[row["query_id"]], row["rank"]
        if old == new:
            same += 1
            continue
        if (new if new is not None else far) < (old if old is not None else far):
            won, mark = won + 1, "↑"
        else:
            lost, mark = lost + 1, "↓"
        lines.append(
            f"  {mark} {row['query_id']:8s} {old if old else '없음'} → {new if new else '없음'}"
        )

    return [f"  이김 {won} · 짐 {lost} · 그대로 {same}", *lines]


# 실행 결과를 사람이 읽는 모양으로. 적재를 먼저 보이는 것은 의도다 —
# 검색 수치가 이상할 때 제일 먼저 의심할 곳이 그쪽이다
def print_report(report: dict[str, Any], report_k: int) -> None:
    print(f"\n색인 ({report['format']}):")
    for row in report["indexed"]:
        flags = f"vision {row['vision_calls']} · ocr {'O' if row['ocr_applied'] else 'X'}"
        warn = f" · 경고 {len(row['warnings'])}" if row["warnings"] else ""
        print(f"  {row['chunks']:4d}청크  {flags}{warn}  {row['file']}")

    print(f"\n요약 (@{report_k}):")
    # **채점에서 뺀 부정 예시를 지표 바로 위에 찍는다.** 아래 `queries` 가 세트 전체가
    # 아니라는 것을 여기서 말해 주지 않으면 아무도 안 알아챈다
    if report.get("skipped_unanswerable"):
        print(f"  {'부정(채점 제외)':16s} {report['skipped_unanswerable']}")
    for key, value in report["summary"].items():
        print(f"  {key:16s} {value:.4f}" if isinstance(value, float) else f"  {key:16s} {value}")

    print("\n후보 수별:")
    for axis, block in report["by_k"].items():
        print(f"  hit@{axis:<3s} {block[f'hit@{axis}']:.4f}   miss {block['misses']}")

    print("\n그룹별:")
    for group, block in report["by_group"].items():
        if block:
            print(f"  {block[f'hit@{report_k}']:.4f}  miss {block['misses']:2d}  {group}")


# 명령줄 진입점
def main() -> int:
    parser = argparse.ArgumentParser(description="검색 품질 평가 러너")
    parser.add_argument("--check", action="store_true", help="세트만 검증한다 (과금 없음)")
    parser.add_argument("--dense", action="store_true", help="dense baseline 을 잰다 (과금)")
    parser.add_argument(
        "--hybrid",
        action="store_true",
        help="BM25 를 얹어 RRF 로 합친다. 빼면 지금까지와 똑같은 dense 다",
    )
    parser.add_argument("--format", dest="fmt", default="pdf", help="색인할 포맷 라벨 (pdf / hwpx)")
    parser.add_argument("--groups", nargs="*", help="특정 그룹만. 비우면 세트의 전 그룹")
    parser.add_argument("--k", type=int, default=20, help="후보를 어디까지 가져오나 (검색은 무료)")
    parser.add_argument("--report-k", type=int, default=10, help="summary 의 주지표 k")
    parser.add_argument("--label", default="dense-baseline")
    parser.add_argument(
        "--embedding",
        default=DEFAULT_EMBEDDING_PROFILE,
        help=(
            "임베딩 프로필 이름 (evaluation/config.py 의 EVAL_EMBEDDINGS). "
            "**모델이 바뀌면 캐시 salt 와 컬렉션이 함께 갈려 전량 재임베딩이다**"
        ),
    )
    parser.add_argument(
        "--rerank",
        default=DEFAULT_RERANK_PROFILE,
        choices=sorted(EVAL_RERANKERS),
        help=(
            "리랭커 프로필 이름 (evaluation/config.py 의 EVAL_RERANKERS). "
            "기본은 off 라 지금까지와 같은 실행이다. **openai 는 질의당 과금이 붙는다**"
        ),
    )
    parser.add_argument(
        "--rewrite",
        default=DEFAULT_REWRITE_PROFILE,
        choices=sorted(EVAL_REWRITERS),
        help=(
            "질의 재작성 프로필 이름 (evaluation/config.py 의 EVAL_REWRITERS). "
            "기본은 off 라 지금까지와 같은 실행이다. **켜면 질의당 LLM 1회가 더 붙는다**"
        ),
    )
    parser.add_argument(
        "--qa-set",
        type=Path,
        help="쓸 세트 파일. 비우면 qa_set.jsonl. **기준선 대조는 기본 세트에서만 뜻이 있다**",
    )
    parser.add_argument("--refresh", action="store_true", help="파싱 캐시를 버리고 다시 읽는다")
    parser.add_argument(
        "--save-baseline",
        action="store_true",
        help="이번 결과를 baseline.json 으로 굳힌다. 없으면 기준선을 안 건드린다",
    )
    args = parser.parse_args()

    # 이 실행이 쓴 돈에 이름을 붙인다. **`setdefault` 라 사람이 준 `COST_LABEL` 이 이긴다** —
    # 한 라벨 아래 여러 회차를 묶고 싶을 때가 있고, 그때 러너가 덮으면 그 묶음이 깨진다.
    # 라벨을 리포트 JSON 과 **같은 값**으로 두는 것이 요점이다: `cost/report.py --label` 이
    # 그 회차의 비용을, `evaluation/results/<label>-<fmt>.json` 이 그 회차의 지표를 낸다
    os.environ.setdefault("COST_LABEL", args.label)

    # **`--rerank` 를 `--dense` 없이 주면 멈춘다.** 아래 갈래가 세트 검증만 돌고 `통과` 를
    # 찍는데, 리랭커를 켰다고 믿은 사람은 그것을 성공으로 읽는다. `--hybrid` 에 같은 함정이
    # 있어 다른 세션이 실제로 한 번 헛돌았다 — 그 가드를 되살릴 때 모양을 맞추기로 했다
    if args.rerank != DEFAULT_RERANK_PROFILE and not args.dense:
        raise SystemExit("--rerank 는 --dense 와 함께 써야 합니다 (검색을 안 돌리면 잴 것이 없습니다)")

    # **`--rewrite` 도 같은 가드를 탄다** — 켰다고 믿은 사람이 세트 검증의 `통과` 를 성공으로
    # 읽는 것이 똑같고, 이쪽은 그 오해가 곧 *"재작성이 효과 없다"* 가 된다
    if args.rewrite != DEFAULT_REWRITE_PROFILE and not args.dense:
        raise SystemExit("--rewrite 는 --dense 와 함께 써야 합니다 (검색을 안 돌리면 잴 것이 없습니다)")

    if args.check or not args.dense:
        problems = check(refresh=args.refresh, qa_path=args.qa_set)
        for line in problems:
            print("  ✗", line)
        print(f"\n{'통과' if not problems else str(len(problems)) + '건 문제'}")
        return 1 if problems else 0

    report_k = min(args.report_k, args.k)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    report = run_dense(
        fmt=args.fmt,
        k=args.k,
        report_k=report_k,
        label=args.label,
        groups=args.groups,
        use_bm25=args.hybrid,
        rerank=args.rerank,
        rewrite=args.rewrite,
        qa_path=args.qa_set,
        embedding=args.embedding,
    )
    out = RESULTS_DIR / f"{args.label}-{args.fmt}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    print_report(report, report_k)

    if args.save_baseline:
        BASELINE_PATH.write_text(
            json.dumps(as_baseline(report), ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print(f"\n기준선 갱신: {BASELINE_PATH}")
    elif args.qa_set is not None:
        # **다른 세트를 기본 기준선과 대조하면 안 된다.** query_id 가 안 겹쳐 전 항목이
        # "없음 -> N" 으로 나오는데, 그것은 회귀가 아니라 애초에 다른 질문들이다
        print(f"\n(기준선 대조 생략 — 기준선은 {load_qa_set().path.name} 것이다)")
    elif BASELINE_PATH.exists():
        baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        print(f"\n기준선 대비 ({baseline.get('label')}-{baseline.get('format')}):")
        for line in compare_to_baseline(report, baseline):
            print(line)

    print(f"\n결과: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
