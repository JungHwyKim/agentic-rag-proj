"""같은 파이프라인의 **두 답변 리포트**를 짝지어 읽는다. **재지 않고 읽는다.**

## 왜 네 번째 도구인가

`answer_eval.py` 는 한 조건의 답변을 채점한다. 그런데 43회차가 물은 것은 조건 **둘**의 차다 —
*"검색 지표가 소수점까지 같은데 답도 같은가"*(`plan_log/검색-생성.md` E-2). 한 리포트만 보면
그 물음에 못 답하고, 두 리포트를 눈으로 견주면 **합계 하나로 읽게 된다.**

`rerank_diff.py` 는 순위를, `retry_diff.py` 는 회차를 짝짓는다. 둘 다 **답변 본문과 그 답변이
인용한 청크**를 안 본다. 여기가 그 자리다.

## 잡음 바닥이 공짜로 딸려 온다 — 그것이 이 도구의 요점이다

생성에는 캐시가 없고 온도가 0.2 라, **같은 근거를 줘도 두 실행의 답변은 다르다.** 그래서
*"조건을 바꿨더니 답이 갈렸다"* 는 그 자체로는 아무 말도 아니다.

두 조건의 **근거 목록이 글자 그대로 같은 질의**가 그 바닥을 준다 — 거기서 갈린 것은 온도이지
조건이 아니다. 그래서 이 도구는 질의를 **「근거 같음」과 「근거 갈림」 두 군으로 먼저 가르고,
군마다 따로 찍는다.** **합쳐서 총합 하나로 읽으면 바닥과 신호가 한 수에 섞인다** — 질의 로그의
반복률이 *"합계 하나로는 못 읽고 한 갈래만 빼면 더 틀린다"* 로 가르친 것과 같은 모양이다.

## 재현 게이트 — 「그때 그 순위를 다시 밟았다」의 유일한 증거

`answer_eval` 은 컬렉션을 열어 **검색을 다시 돌린다.** 그러니 그 순위가 원래 실행의 순위와
같다는 보장이 저절로 생기지는 않는다. 확인할 수 있는 것이 하나 있다 — 「근거 같음」의 **수**다.
35회차가 리랭킹 후 상위 n 의 일치 수를 이미 쟀으므로(`verify_log/2026-08-28-재작성-리랭커-조합.md`
3-4절: 상위 5에서 49/60 · 상위 10에서 34/60), 그 수가 나오면 같은 순위를 밟은 것이다.

`--expect-same` 로 그 값을 준다. **기대값을 도구에 못 박지 않는 것이 요점이다** — 그것은 세트와
리랭커에 딸린 관측값이지 이 도구의 성질이 아니다. `rerank_diff.py` 가 *"재현이 맞았는지는
순위로 확인한다"* 로 세운 검사와 같은 자리이고, 컬렉션 크기 검사보다 촘촘하다.

## 「답이 갈렸다」를 답변 문자열로 재지 않는다

온도 0.2 라 같은 근거에서도 글자는 늘 다르다. 재는 것은 **답변이 실제로 인용한 청크의 집합**
이다 — 번호가 아니라 청크 신원으로 견준다(같은 번호가 두 실행에서 다른 청크를 가리킨다).

## 무엇을 안 하나

- **재실행 0 · LLM 호출 0.** 리포트의 원자료만 읽는다
- **지표를 새로 짓지 않는다.** 채점표는 `answer_eval.summarize()` 를 군별 부분집합에 다시
  부른 것이다 — 두 벌로 짜면 한쪽만 고쳐지는 날이 온다
- **`supported` 를 «답이 맞다» 로 읽지 않는다.** `judge_probe`(2026-08-28)가 명백한 조작은
  28/28 잡지만 **값 하나 바꾼 것은 13/17** 만 잡는다고 쟀다
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evaluation.answer_eval import AnswerRow, summarize
from evaluation.run_eval import RESULTS_DIR

__all__ = [
    "AXIS_KEYS",
    "PairedRow",
    "axis_problems",
    "cited_chunk_ids",
    "load_answer_report",
    "pair_rows",
    "reproduction_problems",
    "split_by_evidence",
]

# 두 리포트에서 **같아야** 하는 축. 하나라도 다르면 그 표가 무엇을 재는지 못 읽는다.
# `rewrite_profile` 은 여기 없다 — 그것이 이 도구가 견주는 **유일한 축**이다
AXIS_KEYS = (
    "label",
    "format",
    "qa_set",
    "top_k",
    "embedding_profile",
    "rerank_profile",
    "generation_profile",
    "judge_profile",
    "support_prompt_version",
)


# 짝지어진 질의 하나. 두 조건의 행을 그대로 들고 있어 판정을 뒤에서 되짚을 수 있다
@dataclass(frozen=True)
class PairedRow:
    query_id: str
    a: dict[str, Any]
    b: dict[str, Any]

    # 두 조건이 **글자 그대로 같은 근거**를 받았나. 순서까지 본다 —
    # 프롬프트의 번호가 순서에서 나오므로 순서가 다르면 생성 입력이 다른 것이다
    @property
    def same_evidence(self) -> bool:
        return list(self.a["chunk_ids"]) == list(self.b["chunk_ids"])

    # 답변이 **실제로 인용한 청크**가 갈렸나 (모듈 docstring 참고)
    @property
    def cited_changed(self) -> bool:
        return cited_chunk_ids(self.a) != cited_chunk_ids(self.b)


# 리포트를 읽는다. 이름만 주면 `RESULTS_DIR` 에서 찾는다
def load_answer_report(name_or_path: str) -> dict[str, Any]:
    path = Path(name_or_path)
    if not path.exists():
        path = RESULTS_DIR / name_or_path
    if not path.exists():
        raise SystemExit(f"리포트가 없습니다: {name_or_path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if "rows" not in payload or "meta" not in payload:
        raise SystemExit(f"답변 리포트가 아닙니다(--json 으로 남긴 것이어야 합니다): {path}")
    return payload


# 그 답변이 인용한 청크의 **신원**. 번호가 아니라 청크로 견주는 이유는 모듈 docstring 에 있다.
# **범위 밖 번호는 가리킬 것이 없어 뺀다** — `score_citations()` 가 분모에서 빼는 것과 같은 규칙
def cited_chunk_ids(row: dict[str, Any]) -> frozenset[str]:
    chunk_ids = list(row["chunk_ids"])
    inside = [n for n in row.get("cited", []) if 1 <= n <= len(chunk_ids)]
    return frozenset(chunk_ids[n - 1] for n in inside)


# 축 검사. **세는 것으로 끝나지 않고 멈춰 세운다** — 40회차가 *"검사는 세는 것으로 끝나면 안
# 되고 멈춰 세워야 한다"* 를 남겼고, 그 직전 사고가 게이트가 아니라 그냥 찍어 본 것이었다
def axis_problems(a_meta: dict[str, Any], b_meta: dict[str, Any]) -> list[str]:
    problems = [
        f"{key} 가 다릅니다: {a_meta.get(key)!r} vs {b_meta.get(key)!r}"
        for key in AXIS_KEYS
        if a_meta.get(key) != b_meta.get(key)
    ]
    if a_meta.get("rewrite_profile") == b_meta.get("rewrite_profile"):
        problems.append(
            f"재작성 프로필이 같습니다({a_meta.get('rewrite_profile')!r}) — "
            "견줄 축이 없어 이 짝은 같은 조건의 두 실행입니다"
        )
    return problems


# 질의를 짝짓는다. **한쪽에만 있는 것을 조용히 버리지 않는다** — 세트가 갈렸다는 뜻이라
# 그대로 진행하면 두 표의 분모가 다른 채로 나란히 놓인다
def pair_rows(
    a_rows: list[dict[str, Any]], b_rows: list[dict[str, Any]]
) -> tuple[list[PairedRow], list[str]]:
    left = {row["query_id"]: row for row in a_rows}
    right = {row["query_id"]: row for row in b_rows}
    only_a = sorted(set(left) - set(right))
    only_b = sorted(set(right) - set(left))

    problems: list[str] = []
    if only_a or only_b:
        problems.append(
            f"짝이 없는 질의가 있습니다: A 에만 {len(only_a)}건 {only_a[:5]} · "
            f"B 에만 {len(only_b)}건 {only_b[:5]}"
        )
    paired = [PairedRow(qid, left[qid], right[qid]) for qid in left if qid in right]
    return paired, problems


# 재현 게이트. **`_print()` 밖에 사는 것이 요점이다** — 34회차가 «회계 게이트의 생성 팔에
# 테스트가 아예 없었다» 를 변이 검사로 잡았고, 게이트가 출력 함수 안에 살면 그 일이 다시 난다.
# `answer_eval.accounting_problems()` 와 같은 자리다
def reproduction_problems(
    groups: dict[str, list[PairedRow]], expect_same: int | None
) -> list[str]:
    if expect_same is None:
        return []
    same_n = len(groups["same"])
    if same_n == expect_same:
        return []
    return [
        f"재현 게이트: 근거가 같은 질의가 {same_n}건인데 기대값은 {expect_same}건입니다 — "
        "그때 그 순위를 다시 밟았다고 말할 수 없습니다"
    ]


# 근거가 같은 군과 갈린 군으로 가른다 (모듈 docstring 참고)
def split_by_evidence(pairs: list[PairedRow]) -> dict[str, list[PairedRow]]:
    return {
        "same": [pair for pair in pairs if pair.same_evidence],
        "changed": [pair for pair in pairs if not pair.same_evidence],
    }


# 리포트의 dict 행을 `AnswerRow` 로 되돌린다. **모르는 키가 오면 TypeError 로 그 자리에서
# 죽는다** — 리포트 형식이 바뀐 것을 조용히 지나치지 않는 성질을 그대로 쓴다
def _as_rows(raw: list[dict[str, Any]]) -> list[AnswerRow]:
    return [AnswerRow(**row) for row in raw]


# 한 갈래의 채점표를 한 줄로 줄인다. **`summarize()` 를 다시 부른 것이지 새로 세지 않는다**
def _line(bucket: dict[str, Any]) -> str:
    ground = bucket["citation_groundedness"]
    shown = "—" if ground is None else f"{ground:.3f}"
    return (
        f"질의 {bucket['queries']:>3} · 기권 {bucket['abstained']:>2} · "
        f"지지 {bucket['supported']:>3} · 미지지 {bucket['unsupported']:>2} · "
        f"실패 {bucket['failed']:>2} · 인용 정합성 {shown} "
        f"({bucket['cited_gold_hits']}/{bucket['cited_chunks']}) · "
        f"인용 없음 {bucket['citation_missing']} · 범위 밖 {bucket['citation_out_of_range']}"
    )


# 게이트를 먼저, 지표를 나중에 찍는다 (`answer_eval._print()` 와 같은 규약)
def _print(
    pairs: list[PairedRow],
    groups: dict[str, list[PairedRow]],
    *,
    a_meta: dict[str, Any],
    b_meta: dict[str, Any],
    problems: list[str],
    expect_same: int | None,
    show: bool,
) -> None:
    a_name = a_meta.get("rewrite_profile")
    b_name = b_meta.get("rewrite_profile")
    print(
        f"\n{a_meta.get('label')} · {a_meta.get('qa_set')} · top_k {a_meta.get('top_k')} · "
        f"리랭커 {a_meta.get('rerank_profile')} · 채점자 {a_meta.get('judge_profile')}"
    )
    print(f"견주는 축: 재작성 {a_name} vs {b_name} · 짝지은 질의 {len(pairs)}건")

    print("\n[게이트]")
    problems = [*problems, *reproduction_problems(groups, expect_same)]
    if problems:
        for line in problems:
            print(f"  ✗ {line}")
        print("  → 아래 수치를 실력으로 읽지 않는다")
    else:
        print("  ✓ 통과")

    for key, korean in (("same", "근거 같음 (잡음 바닥)"), ("changed", "근거 갈림 (신호)")):
        group = groups[key]
        print(f"\n[{korean}] {len(group)}건")
        if not group:
            continue
        changed = sum(1 for pair in group if pair.cited_changed)
        print(f"  인용한 청크가 갈린 질의 {changed}/{len(group)}")
        for name, side in ((a_name, "a"), (b_name, "b")):
            summary = summarize(_as_rows([getattr(pair, side) for pair in group]))
            for bucket_key, label in (("positive", "정답 있음"), ("negative", "부정 예시")):
                bucket = summary[bucket_key]
                if bucket["queries"]:
                    print(f"    {name:>7} · {label} — {_line(bucket)}")

    if show:
        print("\n[근거가 갈린 질의]")
        for pair in groups["changed"]:
            mark = "인용 갈림" if pair.cited_changed else "인용 같음"
            print(
                f"  {pair.query_id} ({pair.a['source_doc']}) — {mark} · "
                f"{a_name} {pair.a['support']} / {b_name} {pair.b['support']}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="두 답변 리포트를 짝지어, 근거가 갈린 자리에서 답이 갈리나 본다"
    )
    parser.add_argument("--a", required=True, help="리포트 이름 또는 경로 (기준 조건)")
    parser.add_argument("--b", required=True, help="리포트 이름 또는 경로 (견줄 조건)")
    parser.add_argument(
        "--expect-same",
        type=int,
        default=None,
        help="근거가 같아야 하는 질의 수. 재현 게이트다 (모듈 docstring 참고)",
    )
    parser.add_argument("--show", action="store_true", help="근거가 갈린 질의를 하나씩 보여준다")
    args = parser.parse_args()

    a = load_answer_report(args.a)
    b = load_answer_report(args.b)
    problems = axis_problems(a["meta"], b["meta"])
    pairs, pair_problems = pair_rows(a["rows"], b["rows"])
    groups = split_by_evidence(pairs)
    _print(
        pairs,
        groups,
        a_meta=a["meta"],
        b_meta=b["meta"],
        problems=problems + pair_problems,
        expect_same=args.expect_same,
        show=args.show,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
