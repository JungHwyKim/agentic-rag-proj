r"""저장된 답변 리포트에서 RAGAS 가 먹을 재료를 뽑는다. **생성·검색을 다시 안 돌려 과금이 0이다.**

## 왜 이 파일이 따로 있나

RAGAS 는 이 저장소의 `.venv` 에 **안 깔린다.** dry-run 실측(2026-09-08) — 최신 `ragas`(0.4.3)가
`openai` 를 **3.0.0 → 2.54.0** 으로 내리는데, 그 3.0.0 은 2026-09-03 타임아웃 실측
(`connect=5.0 · read=600 · max_retries=2`)의 **관측 대상**이다. `openai==3.0.0` 을 못 박으면
ragas 가 0.3.1 로 내려앉아 다운그레이드는 `fsspec` 한 칸으로 줄지만, 그래도 본 환경에 29개
(`langchain`·`pandas`·`pyarrow`·`datasets`)가 들어온다.

그래서 RAGAS 는 `uv run --no-project --with` 로 **쓰고 버리는 환경**에서 돌리고
(`ragas_probe.py`), 이 파일이 그쪽으로 넘길 **재료 JSON** 을 만든다. 두 프로세스는 파일
하나로만 만나므로 서로의 의존성을 모른다.

**그 배치가 서는 전제는 «탐침이 저장소 코드를 import 할 필요가 없다» 이고, 그 전제를
`judge_probe.py` 가 이미 세웠다** — `AnswerRow` 가 *"채점 규칙이 바뀌어도 이 행만으로 다시
먹일 수 있어야 한다"* 며 `query`·`answer` **원문**을 싣기 때문이다.

## 근거 본문은 리포트에 없다 — 컬렉션에서 되살린다

리포트에는 `chunk_ids` 만 있다. `judge_probe` 가 세운 경로를 **그대로** 쓴다:
`store.fetch_by_ids(point_id_for(...))` → `reorder_chunks()`.

**순서가 계약이다.** qdrant 의 반환 순서는 보장이 없는데 그 순서가 근거 번호 `[n]` 을 만든다.
`len(chunks) != len(chunk_ids)` 면 그 행을 **버린다** — 조용히 짧아지면 재료가 본판보다 적은
근거를 담는다.

## 대조군은 «남의 골든» 이다 — 2026-09-09 에 «남의 질문» 에서 갈아끼웠다

`answer_eval.control_partner()` 의 **세 번째 거울상**이다. 저쪽은 같은 답변에 *남의 근거*를
물려 채점자가 그래도 «뒷받침된다» 고 하는지 보고, 이쪽은 같은 답변에 ***남의 산문 골든*** 을
물려 정확도 지표가 그래도 높은지 본다. 안 갈리면 그 지표는 이 코퍼스에서 아무것도 안 재는
것이다(`.claude/wiki/plan_log/평가-세트.md` 17회차가 돌리기 전에 못 박은 판정 규칙 1).

**갈아끼운 것이지 더한 것이 아니다.** 응답 적합성이 2026-09-08 에 기각돼(조건을 안 가르고
부정에서 뒤집힌다) «남의 질문» 축은 소비자가 없어졌고, 둘 다 만들면 행이 늘어 실행마다 비용이
는다. **그 대신 함정이 하나 생긴다** — `ResponseRelevancy` 는 `reference` 를 아예 안 읽어
(실측 2026-09-09 dry-run: 필수 칸이 `user_input`·`response` 둘뿐이다), 골든만 바꾼 대조군
행이 self 와 **글자까지 같은 입력**이 된다. 그러면 규칙 1 이 «차 0.0000 → 기각» 을 내는데
그것은 지표가 아니라 재료가 만든 수다. 그래서 `meta["control_axis"]` 를 싣고
`ragas_report` 가 그 축을 확인한 뒤에만 표를 그린다.

**짝짓기 함수는 새로 안 짜고 그대로 빌린다.** 저쪽의 «근거가 겹치면 짝으로 안 쓴다» 규칙이
여기서도 필요한 규칙이라서다 — 서로 다른 두 질의가 같은 청크를 물어오는 일이 흔한데(같은
절을 묻는 질문 둘), 그런 짝에서는 «남의 골든» 이 실은 **내 답변이 답하는 골든**이라 높게
나오는 것이 정상이고, 그러면 대조군이 대조군 노릇을 못 한다.

**다만 짝짓기를 먹이는 목록이 다르다** — 골든을 가진 행들만 모아서 넘긴다. 리포트 43행에
그대로 대면 부정 25건이 짝으로 뽑혀 **골든이 빈 대조군**이 생기는데, 그건 대조가 아니라
못 잰 행이다.

## 산문 골든은 리포트에 없다 — 세트에서 물어온다

`AnswerRow` 에 `reference` 칸이 없다. 그래서 리포트 `meta["qa_set"]` 이 이름 댄 세트를
`evaluation/` 에 대고 열어 `query_id` 로 잇는다 — `answer_eval.py` 의 `--backfill` 경로가
이미 쓰는 선례(`load_qa_set(QA_SET_HUMAN_PATH.parent / meta["qa_set"])`)이고, 이름 규칙을
유추하는 것이 아니라 **리포트에 기록된 값**을 쓰는 것이다. 세트에 없는 `query_id` 가 있으면
**사유를 내고 죽는다** — 리포트와 세트가 짝이 아닌 것이라, `require_fresh_store()` 가
컬렉션에 대고 하는 일과 같다.

**골든이 빈 행은 두 칸으로 갈라 버린다** — `dropped["negative"]`(부정 예시라 골든이 없는
것이 정상)와 `dropped["reference_pending"]`(긍정인데 라벨이 아직 안 찼다). 한 칸으로 접으면
**라벨 누락이 정상 제외에 묻혀** 「17/18」이 재료에서 안 보이고, 60건 세트로 넓힐 때는
pending 이 60으로 뛰어 그 차이가 더 커진다.

## 기권 행을 안 뺀다

`ResponseRelevancy` 는 기권 답변을 0점으로 깎는데, 이 코퍼스의 사람 세트는 **부정 예시
25건에서 기권이 정답**이다(실측 — `h-cohere-18w3` 실행에서 부정 25 중 기권 11 · 답한 14).
빼면 지표가 깨끗해지지만 *왜 뺐나* 가 산문으로만 남는다. 그래서 **재료에는 전부 싣고**
`abstained`·`answerable` 을 함께 실어 읽는 쪽(`ragas_report.py`)이 갈래를 가른다.

**여기서 `abstained` 를 리포트 값 그대로 안 믿고 `is_abstention()` 을 다시 먹인다** —
`judge_probe` 와 같은 이유이고, 저장된 값은 그 실행의 판정이지 이 재료의 판정이 아니다.

## 절대 경로를 재료에 안 싣는다

`meta` 에 리포트 **이름**만 담고 경로를 안 담는다. 답변 캐시 지문이 `model_dump()` 로
`rerank_cache_dir=C:\...` 를 통째로 삼켰던 자리와 같은 모양이라, 테스트가 그것을 잠근다.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from evaluation.answer_eval import AnswerRow, control_partner, is_abstention
from evaluation.config import DEFAULT_EMBEDDING_PROFILE, eval_config
from evaluation.inspect import load_report, require_fresh_store
from evaluation.judge_probe import reorder_chunks
from evaluation.qa_set import QA_SET_PATH, QAItem, load_qa_set
from evaluation.run_eval import RESULTS_DIR
from rag_pipeline.schemas.vector import point_id_for

__all__ = ["CONTROL_AXIS", "MaterialRow", "build_material", "export", "load_set_for"]

# 대조군이 무엇을 갈아끼우나. **재료에 실어야 하는 값이다** — `ResponseRelevancy` 는
# `reference` 를 안 읽어서, 이 축으로 만든 대조군을 그 지표에 먹이면 self 와 같은 입력이 되어
# 규칙 1 이 «차 0» 을 낸다. 읽는 쪽이 그것을 알아보라고 재료가 스스로 밝힌다 (모듈 docstring)
CONTROL_AXIS = "reference"


# 격리 실행이 읽을 한 행. **`variant` 가 이 표의 축이다** — `self` 는 제 골든, `control` 은
# 남의 골든을 물린 짝이고, 둘의 `user_input`·`response`·`retrieved_contexts` 는 글자까지 같다
@dataclass
class MaterialRow:
    query_id: str
    # self | control
    variant: str
    # 어느 리포트에서 왔나. **파일 이름만 담는다** (경로를 담으면 머신 배치가 재료에 박힌다)
    report: str
    # RAGAS 가 쓰는 이름 그대로. 옮겨 적는 자리를 줄인다
    user_input: str
    response: str
    retrieved_contexts: list[str] = field(default_factory=list)
    # 세트의 산문 골든. **`gold_spans` 가 아니다** — 저쪽은 문서에서 뜬 개조식 조각이고
    # 이쪽은 질문에 답하는 완결 문장이라, 조각을 그대로 쓰면 점수가 «답이 틀려서» 인지
    # «문체가 달라서» 인지 안 갈린다(`.claude/wiki/plan_log/평가-세트.md` 17회차)
    reference: str = ""
    # 갈래를 가르는 라벨. 지표를 안 바꾸고 읽는 쪽이 쓴다
    answerable: bool = True
    abstained: bool = False
    source_doc: str = ""
    # `control` 일 때 **골든을** 빌려온 질의. `self` 는 None.
    # **`question_from` 에서 이름이 바뀌었다** — 대조군 축을 갈아끼우면서 옛 이름이 늘 None 인
    # 죽은 칸이 된다. 옛 재료 파일(2026-09-08 566행)은 디스크에 이미 있고 읽는 쪽이 이 칸을
    # 안 봐서 안 깨진다
    reference_from: str | None = None


# 리포트 meta 가 이름 댄 세트를 연다. **경로가 아니라 이름이라 `evaluation/` 에 댄다** —
# `answer_eval` 의 `--backfill` 이 쓰는 선례 그대로다. 이름이 없으면 죽는다: 세트를 못 찾으면
# 골든이 한 줄도 안 실리는데, 그 재료는 «라벨이 전부 비었다» 와 구분이 안 된다
def load_set_for(payload: dict[str, Any], report_name: str) -> dict[str, QAItem]:
    name = (payload.get("meta") or {}).get("qa_set")
    if not name:
        raise SystemExit(
            f"리포트에 qa_set 이 없습니다: {report_name} — 산문 골든을 어느 세트에서 물어올지"
            " 알 수 없습니다"
        )
    path = QA_SET_PATH.parent / str(name)
    qa = load_qa_set(path)
    if not qa.items:
        raise SystemExit(f"세트가 비었거나 없습니다: {path}")
    return {item.query_id: item for item in qa.items}


# 리포트 하나를 재료 행으로 편다. 저장소는 부르는 쪽이 열고 닫는다 — 배타 잠금 때문에
# 컬렉션 하나에 클라이언트가 하나여야 해서, 같은 컬렉션을 쓰는 리포트 넷을 한 번에 돌린다
def build_material(
    payload: dict[str, Any], store: Any, report_name: str, qa: dict[str, QAItem]
) -> tuple[list[MaterialRow], dict[str, list[str]]]:
    raw_rows = payload.get("rows") or []
    rows = [AnswerRow(**raw) for raw in raw_rows]

    # **`qa` 에 기본값을 안 둔다** — 두면 새 호출부가 빠뜨렸을 때 골든이 전부 빈 재료가
    # 조용히 나온다(`append_query_log(source=)` 가 기본값을 안 둔 것과 같은 기준이다)
    missing = [r.query_id for r in rows if r.query_id not in qa]
    if missing:
        raise SystemExit(
            f"리포트와 세트가 짝이 아닙니다: {report_name} — 세트에 없는 query_id "
            f"{len(missing)}건 ({', '.join(missing[:8])})"
        )

    dropped: dict[str, list[str]] = {
        # 골든이 없는 것이 **정상**인 갈래. 부정 예시는 답이 코퍼스에 없어 골든이 「기권」이고
        # `validate_items()` 가 `reference` 를 금지한다
        "negative": [],
        # 긍정인데 라벨이 **아직 안 찼다**. 위와 한 칸에 담으면 이 수가 정상 제외에 묻힌다
        "reference_pending": [],
        "evidence_missing": [],
        "empty": [],
    }
    # 골든을 가진 행만 남긴다. **짝짓기를 이 목록에 대고 돌려야** 부정이 대조군 짝으로 안 뽑힌다
    scored_rows: list[AnswerRow] = []
    for row in rows:
        reference = (qa[row.query_id].reference or "").strip()
        if not reference:
            key = "negative" if not qa[row.query_id].answerable else "reference_pending"
            dropped[key].append(row.query_id)
            continue
        scored_rows.append(row)

    # 되살린 근거. 짝짓기가 전체 목록을 봐야 하므로 먼저 한 바퀴 돈다
    restored: dict[str, list[str]] = {}
    for row in scored_rows:
        if not row.chunk_ids or not (row.answer or "").strip():
            dropped["empty"].append(row.query_id)
            continue
        stored = store.fetch_by_ids([point_id_for(cid) for cid in row.chunk_ids])
        chunks = reorder_chunks(stored, row.chunk_ids)
        # **길이가 다르면 버린다** — 조용히 짧아지면 재료가 본판보다 적은 근거를 담는다
        if len(chunks) != len(row.chunk_ids):
            dropped["evidence_missing"].append(row.query_id)
            continue
        restored[row.query_id] = [chunk.text for chunk in chunks]
        # 기권 판정은 되살린 근거로 다시 한다 (모듈 docstring 참고)
        row.abstained = is_abstention(row.answer, chunks)

    out: list[MaterialRow] = []
    for index, row in enumerate(scored_rows):
        contexts = restored.get(row.query_id)
        if contexts is None:
            continue
        out.append(
            MaterialRow(
                query_id=row.query_id,
                variant="self",
                report=report_name,
                user_input=row.query,
                response=row.answer,
                retrieved_contexts=contexts,
                reference=qa[row.query_id].reference.strip(),
                answerable=bool(row.answerable),
                abstained=bool(row.abstained),
                source_doc=row.source_doc,
            )
        )
        partner = control_partner(index, scored_rows)
        # 짝이 없으면 대조군 행을 안 만든다. **가짜로 채우지 않는다** — 자기 골든을 물린
        # 「대조군」은 self 와 같은 행이라 대조가 아니다
        if partner is None or partner.query_id not in restored:
            continue
        out.append(
            MaterialRow(
                query_id=row.query_id,
                variant="control",
                report=report_name,
                # **질문·답변·근거는 자기 것 그대로다** — 갈리는 것이 골든 하나여야 한다
                user_input=row.query,
                response=row.answer,
                retrieved_contexts=contexts,
                reference=qa[partner.query_id].reference.strip(),
                answerable=bool(row.answerable),
                abstained=bool(row.abstained),
                source_doc=row.source_doc,
                reference_from=partner.query_id,
            )
        )
    return out, dropped


# 리포트 여럿을 하나의 재료 파일로. 컬렉션이 같은 것끼리 묶어 저장소를 한 번만 연다
def export(report_paths: list[Path], *, limit: int | None = None) -> dict[str, Any]:
    from rag_pipeline.ingestion.indexing import get_store

    payloads: list[tuple[Path, dict[str, Any]]] = []
    for path in report_paths:
        payloads.append((path, json.loads(path.read_text(encoding="utf-8"))))

    # (label, fmt, embedding) 이 같으면 같은 컬렉션이다
    groups: dict[tuple[str, str, str], list[tuple[Path, dict[str, Any]]]] = {}
    for path, payload in payloads:
        meta = payload.get("meta") or {}
        label, fmt = meta.get("label"), meta.get("format")
        if not label or not fmt:
            raise SystemExit(f"리포트에 label·format 이 없습니다: {path}")
        embedding = meta.get("embedding_profile") or DEFAULT_EMBEDDING_PROFILE
        groups.setdefault((label, fmt, embedding), []).append((path, payload))

    rows: list[MaterialRow] = []
    dropped: dict[str, list[str]] = {
        "negative": [],
        "reference_pending": [],
        "evidence_missing": [],
        "empty": [],
    }
    sources: list[dict[str, Any]] = []
    # 세트 파일은 리포트마다 이름이 같을 수 있어 한 번만 읽는다
    sets: dict[str, dict[str, QAItem]] = {}

    for (label, fmt, embedding), members in groups.items():
        config = eval_config(
            qdrant_path=RESULTS_DIR / f"qdrant-{label}-{fmt}", embedding=embedding
        )
        store = get_store(config.indexing, config.embedding)
        try:
            run = load_report(label, fmt)
            # **컬렉션이 그 실행의 것인지 먼저 막는다** — 청킹이 바뀌었으면 같은 `chunk_id` 가
            # 다른 글을 담아 재료가 조용히 거짓이 된다
            require_fresh_store(
                store.count(), sum(x["chunks"] for x in run["indexed"]), label=label, fmt=fmt
            )
            for path, payload in members:
                set_name = str((payload.get("meta") or {}).get("qa_set") or "")
                if set_name not in sets:
                    sets[set_name] = load_set_for(payload, path.name)
                made, drops = build_material(payload, store, path.name, sets[set_name])
                rows.extend(made)
                for key, ids in drops.items():
                    dropped[key].extend(ids)
                sources.append(
                    {
                        "report": path.name,
                        "label": label,
                        "format": fmt,
                        "embedding_profile": embedding,
                        "qa_set": (payload.get("meta") or {}).get("qa_set"),
                        "rows": len(payload.get("rows") or []),
                    }
                )
        finally:
            # **연 쪽이 닫는다** — 안 닫으면 다음 그룹이 배타 잠금으로 죽는다
            store.close()

    if limit is not None:
        rows = rows[:limit]

    return {
        "meta": {
            "tool": "ragas_export",
            "sources": sources,
            # **대조군이 무엇을 갈아끼웠나.** 읽는 쪽이 이것을 안 보면 `reference` 를 안 읽는
            # 지표에 이 재료를 먹여 «차 0» 을 지표 탓으로 읽는다 (모듈 docstring)
            "control_axis": CONTROL_AXIS,
            "self_rows": sum(1 for r in rows if r.variant == "self"),
            "control_rows": sum(1 for r in rows if r.variant == "control"),
            "dropped": dropped,
        },
        "rows": [asdict(r) for r in rows],
    }


# CLI. **리포트를 경로로 직접 받는다** — 이름 규칙을 유추하면 다른 실행을 조용히 집는다
# (`judge_probe` 가 같은 이유로 `--report` 를 경로로 받는다)
def main() -> None:
    parser = argparse.ArgumentParser(description="RAGAS 격리 실행이 읽을 재료를 뽑는다 (과금 0)")
    parser.add_argument("--report", action="append", required=True, help="답변 리포트 경로")
    parser.add_argument("--out", required=True, help="재료 JSON 을 쓸 경로")
    parser.add_argument("--limit", type=int, default=None, help="앞에서 N 행만 (파이프 확인용)")
    args = parser.parse_args()

    paths = [Path(p) for p in args.report]
    for path in paths:
        if not path.exists():
            raise SystemExit(f"리포트가 없습니다: {path}")

    payload = export(paths, limit=args.limit)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    meta = payload["meta"]
    print(f"재료 {len(payload['rows'])}행 → {out}")
    print(f"  self {meta['self_rows']} · control {meta['control_rows']}")
    print(f"  대조군 축: {meta['control_axis']} (남의 골든을 물린다)")
    for key, ids in meta["dropped"].items():
        if ids:
            print(f"  버린 행({key}) {len(ids)}: {', '.join(ids[:8])}")
    # **두 칸을 붙여 다시 찍는다** — `negative` 는 정상이고 `reference_pending` 은 할 일이라,
    # 목록만 나열하면 둘이 같은 무게로 읽힌다
    pending = meta["dropped"]["reference_pending"]
    if pending:
        print(f"  ↑ 그중 라벨이 안 찬 긍정 {len(pending)}건은 **할 일**이다 (부정은 정상 제외)")


if __name__ == "__main__":
    main()
