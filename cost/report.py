"""비용 로그를 읽어 표로 낸다.

    python -m cost.report                      # 전부
    python -m cost.report --label 리랭커-cohere-60건
    python -m cost.report --since 2026-08-29 --by day

## 회계 줄이 지표보다 **먼저** 나온다

이 저장소가 한 번 크게 물린 자리다 — Cohere Trial 키의 분당 10회 한도로 60건 중 30건이
429 로 떨어졌는데, 지표 여덟 개가 전부 **그럴듯하게 나빴다.** 가른 것은 `rerank_calls` 가
60이 아니라 30이었다는 사실 하나였다(`verify_log/2026-08-27-리랭커-cohere.md`).

`evaluation/run_eval.py` 의 `usage` 블록과 `answer_eval._print()` 의 회계 게이트가 그 사고에
이미 답했지만 **둘 다 그 리포트 안에서만 산다.** 서버 질의도, 손으로 돌린 프로브도, 에이전트
재시도 루프도 그 검사를 못 한다. 여기가 그것을 밖으로 꺼내는 자리라, `--label` 로 자른 어떤
실행에서도 **폴백 수를 지표보다 먼저** 본다.

## 금액을 못 내는 줄을 0으로 안 채운다

단가표에 없는 모델(`llamaparse`·`tavily`, 그리고 새로 붙인 모델)은 **「금액 못 냄」으로 따로
센다.** 0으로 채우면 안 쓴 것처럼 보이는데, 그것이 바로 이 도구가 막으려는 종류의 거짓이다.

## 아낀 금액은 "그 호출이 났다면" 의 추정이다

캐시 히트 줄에는 토큰이 없다(캐시는 응답만 담고 usage 를 안 담는다). 그래서 같은
`(stage, provider, model)` 의 **성공 호출 평균 사용량**을 곱해 낸다 — 관측이 아니라 추정이라
표에서 `~` 를 달아 가른다. 평균을 낼 성공 호출이 하나도 없으면 그 칸은 비운다.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cost import use_utf8_stdout
from cost.pricing import CHECKED_ON, cost_usd, rate_for

__all__ = ["Bucket", "load_rows", "main", "summarize"]

# 기본 로그 경로. **`PROJECT_ROOT` 를 안 쓴다** — 이 폴더는 통째로 옮길 수 있어야 한다
# (`cost/__init__.py`). 환경변수가 먼저이고, 없으면 현재 디렉터리 기준이다
_DEFAULT_LOG = Path(os.environ.get("COST_LOG_PATH") or "data/cost.jsonl")


# 한 묶음의 누적. **`call`·`cache_hit`·`failed` 를 갈라 센다** (모듈 docstring 참고)
@dataclass
class Bucket:
    calls: int = 0
    cache_hits: int = 0
    failures: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    search_units: int = 0
    # 응답이 과금 단위를 안 알려준 성공 호출 수. Cohere 전용 신호다
    units_unknown: int = 0
    usd: float = 0.0
    # 단가표에 없어 금액을 못 낸 성공 호출 수
    unpriced: int = 0
    # 「금액 못 냄」이 어느 모델 때문인지. 표 아래에 이름을 찍는다
    unpriced_models: set[str] = field(default_factory=set)


# JSONL 을 읽어 줄 목록으로. **깨진 줄은 건너뛰되 몇 줄인지 돌려준다** —
# 조용히 버리면 "적게 썼다"로 읽힌다
def load_rows(path: Path) -> tuple[list[dict[str, Any]], int]:
    if not path.is_file():
        return [], 0

    rows: list[dict[str, Any]] = []
    broken = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            broken += 1
            continue
        if isinstance(row, dict):
            rows.append(row)
        else:
            broken += 1
    return rows, broken


# 축 하나로 묶어 누적한다. 축은 줄의 키 이름이다(`stage`·`provider`·`label`·`model`)
def summarize(rows: list[dict[str, Any]], axis: str) -> dict[str, Bucket]:
    buckets: dict[str, Bucket] = defaultdict(Bucket)
    for row in rows:
        key = _axis_value(row, axis)
        bucket = buckets[key]
        outcome = row.get("outcome")

        if outcome == "cache_hit":
            bucket.cache_hits += 1
            continue
        if outcome == "failed":
            bucket.failures += 1
            continue

        bucket.calls += 1
        input_tokens = _int(row.get("input_tokens"))
        output_tokens = _int(row.get("output_tokens"))
        units = row.get("search_units")
        bucket.input_tokens += input_tokens
        bucket.output_tokens += output_tokens
        if isinstance(units, int):
            bucket.search_units += units

        provider = str(row.get("provider") or "")
        model = str(row.get("model") or "")
        rate = rate_for(provider, model)
        amount = cost_usd(
            rate,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            search_units=units if isinstance(units, int) else None,
        )
        if amount is None:
            bucket.unpriced += 1
            bucket.unpriced_models.add(f"{provider}/{model}")
            # **단가를 아는데 응답이 단위를 안 준 경우를 따로 센다** — 그것은 표의 결함이
            # 아니라 그 호출의 응답이 비었다는 뜻이라 사람이 할 일이 다르다
            if rate is not None:
                bucket.units_unknown += 1
        else:
            bucket.usd += amount
    return dict(buckets)


# 그 줄의 축 값. 비어 있으면 `-` 로 모은다 — 키를 빼면 «없음» 이 «0» 과 섞인다
def _axis_value(row: dict[str, Any], axis: str) -> str:
    value = row.get(axis)
    return str(value) if value not in (None, "") else "-"


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


# 캐시가 아낀 금액의 **추정**. 같은 묶음의 성공 호출 평균을 곱한다 (모듈 docstring 참고)
def _saved_usd(bucket: Bucket) -> float | None:
    if not bucket.cache_hits or not bucket.calls or bucket.usd <= 0:
        return None
    return bucket.usd / bucket.calls * bucket.cache_hits


# 표 한 장을 찍는다
def _print_table(title: str, buckets: dict[str, Bucket]) -> None:
    print(f"\n== {title} ==")
    if not buckets:
        print("  (줄이 없습니다)")
        return

    header = f"  {'축':<22} {'호출':>6} {'캐시':>6} {'폴백':>6} {'입력토큰':>10} {'출력토큰':>9} {'USD':>10} {'아낀USD':>10}"
    print(header)
    for key in sorted(buckets):
        bucket = buckets[key]
        saved = _saved_usd(bucket)
        saved_text = f"~{saved:.4f}" if saved is not None else "-"
        usd_text = f"{bucket.usd:.4f}" if bucket.usd else "-"
        print(
            f"  {key:<22} {bucket.calls:>6} {bucket.cache_hits:>6} {bucket.failures:>6} "
            f"{bucket.input_tokens:>10,} {bucket.output_tokens:>9,} {usd_text:>10} {saved_text:>10}"
        )


# **지표보다 먼저 읽는 줄** (모듈 docstring 참고)
def _print_accounting(buckets: dict[str, Bucket]) -> None:
    print("\n== 회계 (지표보다 먼저 읽는다) ==")
    clean = True
    for key in sorted(buckets):
        bucket = buckets[key]
        settled = bucket.calls + bucket.cache_hits
        line = f"  {key:<16} 성사 {settled:>5} (호출 {bucket.calls} + 캐시 {bucket.cache_hits})"
        if bucket.failures:
            clean = False
            line += f"  ✗ 폴백 {bucket.failures}"
        if bucket.unpriced:
            clean = False
            line += f"  ⚠ 금액 못 냄 {bucket.unpriced} ({', '.join(sorted(bucket.unpriced_models))})"
        if bucket.units_unknown:
            line += f"  ⚠ 과금단위 없음 {bucket.units_unknown}"
        print(line)
    if clean:
        print("  폴백 0 · 금액 못 낸 줄 0 — 아래 표를 그대로 읽어도 된다")
    else:
        print(
            "  **폴백이 있으면 그 회차의 품질 지표를 먼저 의심한다** — 한도 초과가 "
            "품질 저하로 읽힌 전례가 있다 (모듈 docstring)"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="유료 API 비용 로그를 표로 낸다")
    parser.add_argument("--log", type=Path, default=_DEFAULT_LOG, help="비용 로그 JSONL 경로")
    parser.add_argument("--label", help="이 라벨의 줄만 본다 (COST_LABEL 로 찍힌 값)")
    parser.add_argument("--run-id", help="이 실행의 줄만 본다")
    parser.add_argument("--since", help="이 날짜(YYYY-MM-DD) 이후만 본다")
    parser.add_argument(
        "--by",
        default="stage",
        help="표를 묶을 축. stage(기본)·provider·model·label·run_id·day 중 하나",
    )
    args = parser.parse_args(argv)
    use_utf8_stdout()

    rows, broken = load_rows(args.log)
    if not rows:
        print(f"줄이 없습니다: {args.log}")
        return 0

    if args.label:
        rows = [row for row in rows if row.get("label") == args.label]
    if args.run_id:
        rows = [row for row in rows if row.get("run_id") == args.run_id]
    if args.since:
        rows = [row for row in rows if str(row.get("at") or "")[:10] >= args.since]

    # `day` 는 줄에 없는 파생 축이라 여기서 만든다
    if args.by == "day":
        for row in rows:
            row["day"] = str(row.get("at") or "")[:10]

    print(f"로그: {args.log}  ·  줄 {len(rows)}개" + (f"  (깨진 줄 {broken})" if broken else ""))
    _print_accounting(summarize(rows, "stage"))
    _print_table(f"{args.by} 별", summarize(rows, args.by))

    total = summarize(rows, "provider")
    grand = sum(bucket.usd for bucket in total.values())
    unpriced = sum(bucket.unpriced for bucket in total.values())
    print(f"\n합계 ${grand:.4f}" + (f"  (+ 금액 못 낸 호출 {unpriced}건)" if unpriced else ""))

    if CHECKED_ON is None:
        print(
            "\n⚠ **단가표를 아직 사람이 확인하지 않았습니다** (`cost/pricing.py:CHECKED_ON`).\n"
            "  공급자 가격 페이지와 대조한 뒤 날짜를 넣으세요. 그 전의 금액은 verify_log/ 에\n"
            "  수치로 옮기면 안 됩니다 — 호출 수와 토큰은 관측이라 그대로 써도 됩니다."
        )
    else:
        print(f"\n단가표 기준일: {CHECKED_ON} (`cost/pricing.py`)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
