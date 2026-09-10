"""답변 캐시 임계값이 **정해질 수 있는지**를 재는 탐침. 정답률이 아니라 **분리 가능성**이다.

## 왜 `run_eval.py` 에 안 얹었나

저쪽은 `hit@k`·`recall@k`·`MRR`·`nDCG@k` 를 낸다 — 검색이 정답을 물었는가, 즉 **정답률**이다.
이 모듈은 검색도 qdrant 도 안 거치고 **쌍별 유사도 두 분포**만 보고 *"임계값을 정할 수 있나"*
라는 **판정**을 낸다. 입력도 출력도 다르다. CLAUDE.md 의 *"재는 기준이 서로 다르니 섞지 않는다"*
가 이 분리의 근거이고, `overlap.py` 가 같은 이유로 따로 사는 선례다.

**그래서 이름에 `probe` 가 붙어 있다** — 이 도구의 정상 동작에는 *수를 못 내고 거절하는 것*이
들어 있다. 실제로 첫 실행이 거절했다(2026-08-25).

## 무엇을 빌리고 무엇을 안 빌리나

`evaluation/config.py` 의 **`embedding_profile()` 하나만** 빌린다. 재현성 핀(`device="cpu"`)이
거기 있어서다 — 안 쓰면 GPU 를 켠 머신에서 같은 명령이 다른 수치를 낸다. 반대로
`eval_config()` 는 **안 쓴다**: 그것이 만드는 ingest·content·cleaner·splitter·retrieval·rerank
를 이 모듈은 하나도 안 건드린다. 안 쓰는 설정을 실어 나르면 리포트가 거짓을 말한다.

## 눈금이 코사인인 근거

답변 캐시 컬렉션은 `Distance.DOT` 이다(`answer_cache/qdrant_cache.py`). 그것이 코사인과 같으려면
벡터가 단위 길이여야 하는데, `LocalEmbedder._encode()` 가 `normalize_embeddings=True` 를 넘겨
못 박는다. **가정하지 않고 실행마다 노름을 재서 리포트에 싣는다** — 정규화를 안 하는 구현체로
갈아끼우면 임계값의 눈금 자체가 바뀌는데 그 사고는 조용하다.

## 음성에 **같은 문서 안의 조합을 반드시 넣는다**

어려운 음성이 거기 있다. 실측(2026-08-25) — 같은 문서 최대 **0.8212** vs 다른 문서 최대
**0.6753**. 다른 문서만 봤으면 틈이 있는 것으로 잘못 읽었을 값이다. 그래서 `negatives()` 가
문서 축으로 가르되 **거르지 않고 함께 센다.**

## 다시 잴 때의 함정 — 임베딩 캐시가 **device 를 안 가른다**

`cache_salt_for()` 는 `embed:{모델}:{차원}` 이라 `embedding_device` 가 안 들어간다. 그래서 같은
모델을 GPU 로 한 번, CPU 로 한 번 돌리면 **두 벡터가 같은 캐시 칸을 쓴다.** 프로필이 `cpu` 를
못 박아도 캐시 적중분은 그 핀을 안 탄다 — 재현성 핀과 캐시 키가 서로 다른 축을 보고 있다.

2026-08-25 실측에서는 무해했다: 캐시를 끄고 다시 재니 **소수 6자리까지 같았다**(양성 최소
0.491669 · 음성 최대 0.821175). 즉 그때 캐시에 있던 것은 CPU 벡터였다. **GPU 를 켠 머신에서
다시 잴 때는 이 확인을 다시 해야 한다** — `embedding_cache_enabled=False` 로 한 번 더 돌려
수치가 같은지 보면 된다. 다르면 캐시가 섞인 것이다.

## 판정 규칙은 재기 전에 정해져 있다

`evaluation/README.md` 2절 — 안 겹치면 틈의 중앙, **겹치면 못 정한다**. 그래서
`separability()` 는 겹칠 때 `threshold=None` 을 돌려주고, 그것 말고 다른 값을 낼 길이 없다.
`IngestConfig.answer_cache_threshold` 가 `float | None` 인 것과 같은 모양이다 — **근거 없는 수가
존재할 수 없는 구조**로 두는 것이 이 저장소의 방식이다.
"""

from __future__ import annotations

import argparse
import itertools
import json
import statistics as st
from dataclasses import dataclass
from pathlib import Path

from evaluation.config import embedding_profile
from rag_pipeline.config import EmbeddingConfig
from rag_pipeline.ingestion.embedding import get_embedder

__all__ = [
    "Distribution",
    "Probe",
    "Separability",
    "describe",
    "load_probe_set",
    "negatives",
    "positives",
    "separability",
]

EVAL_DIR = Path(__file__).resolve().parent
PAIRS_PATH = EVAL_DIR / "paraphrase_pairs.jsonl"
QA_SETS = ("qa_set.jsonl", "qa_set_human.jsonl")


# 한 분포의 요약. **최소·최대를 반드시 들고 있다** — 판정이 그 둘로만 나기 때문이다
@dataclass(frozen=True)
class Distribution:
    name: str
    count: int
    minimum: float
    p50: float
    p95: float
    p99: float
    maximum: float


# 재는 데 필요한 재료. 표현이 둘(원문·패러프레이즈)이고 둘 다 같은 `query_id` 에 매인다
@dataclass(frozen=True)
class Probe:
    query_id: str
    source_doc: str
    base_query: str
    paraphrase: str


# 판정. **`threshold` 가 `None` 이면 "못 정한다" 이고 그것이 정상 결과 중 하나다**
@dataclass(frozen=True)
class Separability:
    positive_min: float
    negative_max: float
    threshold: float | None
    submerged: int

    # 두 분포가 갈렸나. 갈렸을 때만 임계값이 존재한다
    @property
    def separable(self) -> bool:
        return self.threshold is not None

    # 갈렸을 때의 틈. 겹쳤으면 음수이고 그 크기가 곧 겹친 정도다
    @property
    def gap(self) -> float:
        return self.positive_min - self.negative_max


# 검수 끝난 쌍과 그 문서를 함께 읽는다. **검수 전 쌍이 섞이면 멈춘다** —
# `reviewed_by="pending"` 은 *뜻이 같은지 아무도 안 본* 쌍이라 양성 분포에 넣을 수 없다
def load_probe_set(pairs_path: Path | None = None) -> list[Probe]:
    path = pairs_path or PAIRS_PATH
    if not path.exists():
        raise SystemExit(f"쌍 세트가 없습니다: {path}")

    source: dict[str, str] = {}
    for name in QA_SETS:
        qa = EVAL_DIR / name
        if not qa.exists():
            continue
        for line in qa.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                source[record["query_id"]] = record.get("source_doc", "?")

    probes: list[Probe] = []
    pending: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("reviewed_by") != "human":
            pending.append(row.get("pair_id", "?"))
            continue
        probes.append(
            Probe(
                query_id=row["query_id"],
                source_doc=source.get(row["query_id"], "?"),
                base_query=row["base_query"],
                paraphrase=row["paraphrase"],
            )
        )

    if pending:
        raise SystemExit(
            f"검수 안 끝난 쌍 {len(pending)}건: {pending[:5]} — "
            "겹침 수치는 낱말이 다른가만 말하고 뜻이 같은가는 말하지 않습니다 "
            "(evaluation/README.md 「답변 캐시 임계값을 재려면」)"
        )
    return probes


# 양성 — 같은 `query_id` 의 두 표현끼리. 쌍 하나가 값 하나다
def positives(vectors_base: list[list[float]], vectors_para: list[list[float]]) -> list[float]:
    if len(vectors_base) != len(vectors_para):
        raise ValueError(f"양쪽 길이가 다릅니다: {len(vectors_base)} vs {len(vectors_para)}")
    return [_dot(a, b) for a, b in zip(vectors_base, vectors_para, strict=True)]


# 음성 — `query_id` 가 **다른** 모든 조합. 표현 둘을 다 쓰므로 한 질의가 두 번 들어간다.
# **같은 문서 안의 조합을 거르지 않는다**(모듈 docstring). 문서 축은 함께 돌려주기만 한다
def negatives(
    probes: list[Probe],
    vectors_base: list[list[float]],
    vectors_para: list[list[float]],
) -> tuple[list[float], list[float], list[float], list[tuple[float, str, str]]]:
    forms: list[tuple[str, str, list[float]]] = []
    for probe, vb, vp in zip(probes, vectors_base, vectors_para, strict=True):
        forms.append((probe.query_id, probe.source_doc, vb))
        forms.append((probe.query_id, probe.source_doc, vp))

    every: list[float] = []
    same_doc: list[float] = []
    cross_doc: list[float] = []
    labelled: list[tuple[float, str, str]] = []
    for (qa, da, va), (qb, db, vb) in itertools.combinations(forms, 2):
        if qa == qb:
            continue
        score = _dot(va, vb)
        every.append(score)
        (same_doc if da == db else cross_doc).append(score)
        labelled.append((score, qa, qb))
    return every, same_doc, cross_doc, labelled


# 두 분포가 갈리는지 본다. **겹치면 `threshold=None`** — 규칙은 재기 전에 README 2절에 있다
def separability(positive: list[float], negative: list[float]) -> Separability:
    if not positive or not negative:
        raise ValueError("분포가 비어 있습니다")
    pmin, nmax = min(positive), max(negative)
    separated = nmax < pmin
    return Separability(
        positive_min=pmin,
        negative_max=nmax,
        threshold=(pmin + nmax) / 2 if separated else None,
        submerged=sum(1 for value in positive if value <= nmax),
    )


# 분포 하나를 요약한다. 백분위는 정렬 후 자리 지정이라 보간하지 않는다 — 관측값만 싣는다
def describe(name: str, values: list[float]) -> Distribution:
    ordered = sorted(values)
    last = len(ordered) - 1

    def at(fraction: float) -> float:
        return ordered[min(last, round(fraction * last))]

    return Distribution(
        name=name,
        count=len(ordered),
        minimum=ordered[0],
        p50=at(0.50),
        p95=at(0.95),
        p99=at(0.99),
        maximum=ordered[-1],
    )


# 두 벡터의 내적. 단위 길이 벡터에서 코사인과 같고, 그 전제는 main() 이 실측해 싣는다
def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


# 벡터 하나의 노름. **가정하지 않고 잰다** — 정규화가 안 되면 눈금이 통째로 달라진다
def _norm(vector: list[float]) -> float:
    return sum(value * value for value in vector) ** 0.5


# 한 줄 요약 출력
def _print(dist: Distribution) -> None:
    print(
        f"  {dist.name:18s} n={dist.count:6d}  최소 {dist.minimum:.4f}  p50 {dist.p50:.4f}  "
        f"p95 {dist.p95:.4f}  p99 {dist.p99:.4f}  최대 {dist.maximum:.4f}"
    )


# 명령줄 진입점. **로컬 임베더면 과금이 없다** — 유료 프로필을 고르면 그때만 비용이 든다
def main() -> int:
    parser = argparse.ArgumentParser(
        description="답변 캐시 임계값의 분리 가능성을 잰다 (정답률이 아니다)"
    )
    parser.add_argument(
        "--embedding",
        default="kure-v1",
        help="evaluation/config.py 의 EVAL_EMBEDDINGS 프로필 이름. 기본은 운영 기본값과 같은 kure-v1",
    )
    parser.add_argument("--top-negatives", type=int, default=5, help="제일 어려운 음성 몇 개를 보일까")
    args = parser.parse_args()

    probes = load_probe_set()
    docs = sorted({probe.source_doc for probe in probes})
    print(f"쌍 {len(probes)} · 전부 검수 완료 · 문서 {len(docs)}종")

    # **모델 이름을 이 파일에 적지 않는다** — 프로필에서 온다 (README 5절)
    config = EmbeddingConfig(**embedding_profile(args.embedding))
    print(
        f"프로필 {args.embedding} → provider={config.embedding_provider} "
        f"model={config.embedding_model} device={config.embedding_device}"
    )
    embedder = get_embedder(config)

    base = [probe.base_query for probe in probes]
    para = [probe.paraphrase for probe in probes]

    # **서빙은 `embed_query` 를 쓴다**(`answer_cache/qdrant_cache.py`). 배치가 같은 벡터를
    # 내는지 먼저 확인하고서 배치로 잰다 — 접두사를 쓰는 모델로 갈아끼우면 여기서 걸린다
    probe_texts = base[:3]
    one_by_one = [embedder.embed_query(text) for text in probe_texts]
    batched = embedder.embed_documents(probe_texts)
    drift = max(
        (abs(x - y) for a, b in zip(one_by_one, batched, strict=True) for x, y in zip(a, b, strict=True)),
        default=0.0,
    )
    if drift > 1e-6:
        raise SystemExit(
            f"embed_query 와 embed_documents 가 다른 벡터를 냅니다 (최대 차 {drift:.2e}) — "
            "배치로 재면 서빙과 갈립니다"
        )
    print(f"embed_query == embed_documents 확인 (프로브 {len(probe_texts)}건)")

    vectors_base = embedder.embed_documents(base)
    vectors_para = embedder.embed_documents(para)
    norms = [_norm(v) for v in vectors_base + vectors_para]
    print(f"임베딩 {len(norms)}건 · 차원 {len(vectors_base[0])}")
    print(f"노름 최소 {min(norms):.6f} 최대 {max(norms):.6f}", end="  ")
    print("→ DOT == 코사인" if abs(min(norms) - 1.0) < 1e-4 else "→ **단위 길이가 아니다. 눈금이 코사인이 아니다**")

    pos = positives(vectors_base, vectors_para)
    neg_all, neg_same, neg_cross, labelled = negatives(probes, vectors_base, vectors_para)

    print("\n=== 분포 ===")
    _print(describe("양성", pos))
    _print(describe("음성 전체", neg_all))
    _print(describe("음성 같은 문서", neg_same))
    _print(describe("음성 다른 문서", neg_cross))

    print(f"\n제일 어려운 음성 {args.top_negatives}개:")
    for score, left, right in sorted(labelled, reverse=True)[: args.top_negatives]:
        print(f"  {score:.4f}  {left} ↔ {right}")

    verdict = separability(pos, neg_all)
    print("\n=== 판정 (규칙은 README 2절에 재기 전부터 있다) ===")
    if verdict.separable:
        print(f"갈렸다. 틈 {verdict.gap:.4f} → 임계값 = 중앙 **{verdict.threshold:.4f}**")
    else:
        print(f"**겹친다** — 음성 최대 {verdict.negative_max:.4f} >= 양성 최소 {verdict.positive_min:.4f}")
        print(f"  음성 최대에 잠기는 양성 {verdict.submerged}/{len(pos)}건")
        print("  → 임계값을 정하지 않는다. ANSWER_CACHE_THRESHOLD 를 비운 채 둔다")

    print(f"\n평균 양성 {st.fmean(pos):.4f} · 평균 음성 {st.fmean(neg_all):.4f}")
    print(f"usage: {embedder.usage}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
