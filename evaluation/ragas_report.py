"""RAGAS 탐침의 점수를 **갈래를 갈라** 읽는다. 합계 하나로 접지 않는다.

## 왜 가르나

`ResponseRelevancy` 는 기권 답변을 0점으로 깎는다(noncommittal 감지). 그런데 이 저장소의 사람
세트는 **부정 예시 25건에서 기권이 정답**이다 — 실측(`h-cohere-18w3` 실행)으로 부정 25 중 기권
11 · 답한 14 이고, 지표는 **정답인 11을 0으로 깎고 오답인 14를 높게 준다.** 합쳐서 한 수로
내면 그 뒤집힘이 평균 안에 녹아 사라진다.

**«분모에서 빼자» 를 기각한 이유가 이것이다.** 빼면 표는 깨끗해지지만 *왜 뺐나* 가 산문으로만
남아 다음 사람이 같은 질문을 다시 한다. 갈라서 내면 그 한 줄이 곧 관측이 된다.

## 비율을 안 낸다

부정 두 갈래에 어떤 분수를 얹어도 「오류율」로 읽히는데, **기권 11은 이 코퍼스에서 제일 좋은
답변이다.** 그래서 여기서 내는 것은 갈래마다의 **점수 분포**(건수·중앙값·평균·못 잰 수)뿐이고
정답률·성공률 꼴의 칸은 만들지 않는다. `must_not_claim` 이 후보 목록만 내는 것과 같은 신분이다.

## 판정 규칙은 돌리기 전에 정해졌다

`plan_log/검색-생성.md` 59회차에 못 박은 셋을 여기서 그대로 대 본다. **결과를 보고 규칙을
만들면 재는 것이 결론을 따라간다** — `exact` 캐시의 5% 컷과 같은 자리다.

1. 대조군(남의 질문)이 자기 질문과 **안 갈리면 기각** — 지표가 애초에 안 도는 것이다
2. LLM 네 실행의 관측 폭이 **대조군 폭 안이면 축으로 안 씀** — 조건을 안 가르는 지표는 조건을
   견주는 데 못 쓴다
3. 부정에서 «답한 쪽» 이 «기권 쪽» 보다 높으면 **사람 세트에는 안 씀**

**셋 다 통과선이 아니라 갈림이다.** 어느 쪽으로 나오든 그것이 이 회차의 산출이고, 「기각」도
결론이다.

## 이 리포트는 «응답 적합성 · 남의 질문» 한 조합만 읽는다 — 문을 잠가 뒀다

2026-09-09 에 `ragas_export` 가 **대조군 축을 «남의 골든» 으로 갈아끼웠고**, 같은 날 탐침이
지표 넷을 고를 수 있게 됐다. 이 리포트는 그 둘을 **아직 안 따라갔다**(갈래 재작성은 산문
골든 축이 값을 한다고 판정된 뒤의 일이다). 그런데 안 막으면 **조용히 틀린다**:

- `bucket_rows()` 가 `row.get(METRIC_NAME)` 을 써서, 다른 지표의 점수 파일을 먹이면
  `KeyError` 가 아니라 **전부 `None`** 이 되어 「행 34 · 못잼 34 · 규칙 전부 잴 수 없다」라는
  멀쩡해 보이는 표가 나온다
- `ResponseRelevancy` 는 `reference` 를 안 읽으므로, «남의 골든» 축으로 만든 대조군 행은
  self 와 **입력이 글자까지 같다.** 규칙 1 이 «차 0.0000 → 기각» 을 내는데 그것은 지표가
  아니라 재료가 만든 수다

그래서 `render()` 가 `meta.metric` 과 `meta.control_axis` 를 먼저 보고 **사유를 내고 죽는다.**
헛경보가 아니라 **못 읽는 조합을 못 읽는다고 말하는 것**이라 `SKIP_*` 습관과 무관하다.
"""

from __future__ import annotations

import argparse
import json
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any

METRIC_NAME = "answer_relevancy"

# 이 리포트가 읽을 수 있는 대조군 축. **`None` 은 옛 재료다** — `control_axis` 칸이 생기기
# 전(2026-09-08 566행)에는 축이 «남의 질문» 하나뿐이라, 없는 것이 곧 그 축이라는 뜻이다
READABLE_CONTROL_AXES = frozenset({None, "question"})

__all__ = ["Bucket", "bucket_rows", "check_readable", "summarize"]


# 한 갈래의 분포. **비율 칸이 없다** (모듈 docstring 참고)
@dataclass
class Bucket:
    name: str
    rows: int = 0
    scored: int = 0
    unscored: int = 0
    median: float | None = None
    mean: float | None = None
    low: float | None = None
    high: float | None = None


# 점수 목록을 한 갈래로 접는다. **`None` 을 0 으로 안 바꾼다** — 못 잰 것과 «관련 없음» 은
# 다른 뜻이고, 접으면 실패가 점수로 둔갑한다(죽은 판정자가 만점을 받는 것의 거울상)
def summarize(name: str, values: list[float | None]) -> Bucket:
    got = [v for v in values if v is not None]
    bucket = Bucket(name=name, rows=len(values), scored=len(got), unscored=len(values) - len(got))
    if got:
        bucket.median = statistics.median(got)
        bucket.mean = statistics.fmean(got)
        bucket.low = min(got)
        bucket.high = max(got)
    return bucket


# 행을 다섯 갈래로 나눈다. 사람 세트인지는 리포트 meta 의 `qa_set` 으로 가르고, 그 값이 없으면
# **부정 라벨이 하나라도 있는가**로 물러선다 — 옛 리포트에 그 칸이 없을 수 있다
def bucket_rows(payload: dict[str, Any]) -> dict[str, list[float | None]]:
    sources = (payload.get("meta") or {}).get("source") or []
    human_reports = {
        src["report"] for src in sources if "human" in str(src.get("qa_set") or "").lower()
    }
    if not human_reports:
        for row in payload.get("rows") or []:
            if not row.get("answerable", True):
                human_reports.add(row["report"])

    buckets: dict[str, list[float | None]] = {}
    for row in payload.get("rows") or []:
        score = row.get(METRIC_NAME)
        if row["variant"] == "control":
            buckets.setdefault("대조군 (남의 질문)", []).append(score)
            continue
        if row["report"] in human_reports:
            if row.get("answerable", True):
                key = "사람 세트 · 긍정"
            elif row.get("abstained"):
                key = "사람 세트 · 부정 · 기권 (기권이 정답)"
            else:
                key = "사람 세트 · 부정 · 답함 (오답)"
        else:
            key = f"LLM 세트 · {row['report']}"
        buckets.setdefault(key, []).append(score)
    return buckets


# 판정 규칙 셋을 대 본다. **수를 먼저 찍고 판정을 뒤에 붙인다** — 판정만 보이면 근거가 사라진다
def check_rules(buckets: dict[str, Bucket]) -> list[str]:
    lines: list[str] = []

    control = buckets.get("대조군 (남의 질문)")
    selves = [b for name, b in buckets.items() if name != "대조군 (남의 질문)"]
    self_scores = [b.median for b in selves if b.median is not None]
    self_median = statistics.median(self_scores) if self_scores else None

    if control is None or control.median is None or self_median is None:
        lines.append("규칙 1 (대조군) — 잴 수 없다: 대조군 행이 없거나 전부 못 쟀다")
        gap = None
    else:
        gap = self_median - control.median
        verdict = "지표가 돈다" if gap > 0 else "**기각** — 남의 질문에도 안 떨어진다"
        lines.append(
            f"규칙 1 (대조군) — 자기 질문 중앙값 {self_median:.4f} vs 남의 질문 "
            f"{control.median:.4f} · 차 {gap:+.4f} → {verdict}"
        )

    llm = [b for name, b in buckets.items() if name.startswith("LLM 세트")]
    llm_medians = [b.median for b in llm if b.median is not None]
    if len(llm_medians) < 2:
        lines.append("규칙 2 (조건 축) — 잴 수 없다: LLM 실행이 둘 미만이다")
    else:
        spread = max(llm_medians) - min(llm_medians)
        if gap is None:
            lines.append(f"규칙 2 (조건 축) — 네 실행 폭 {spread:.4f} · 대조군 폭을 못 재 판정 보류")
        else:
            verdict = (
                "조건을 가른다" if spread > abs(gap) else "**축으로 안 씀** — 대조군 폭 안이다"
            )
            lines.append(
                f"규칙 2 (조건 축) — 실행 사이 폭 {spread:.4f} vs 대조군 폭 {abs(gap):.4f} "
                f"→ {verdict}"
            )

    answered = buckets.get("사람 세트 · 부정 · 답함 (오답)")
    refused = buckets.get("사람 세트 · 부정 · 기권 (기권이 정답)")
    if not answered or not refused or answered.median is None or refused.median is None:
        lines.append("규칙 3 (기권 뒤집힘) — 잴 수 없다: 부정 갈래 한쪽이 비었다")
    else:
        flipped = answered.median > refused.median
        verdict = (
            "**사람 세트에는 안 씀** — 오답이 정답보다 높다" if flipped else "뒤집히지 않았다"
        )
        lines.append(
            f"규칙 3 (기권 뒤집힘) — 답함 {answered.median:.4f} vs 기권 "
            f"{refused.median:.4f} → {verdict}"
        )
    return lines


# 이 리포트가 읽을 수 있는 조합인지 먼저 본다. **읽을 수 없으면 표를 안 그린다** —
# 못 읽는 것을 «전부 못 쟀다» 나 «차 0» 으로 그리면 재료 문제가 지표 결론으로 읽힌다
# (모듈 docstring 끝 절)
def check_readable(meta: dict[str, Any]) -> None:
    metric = meta.get("metric")
    if metric != METRIC_NAME:
        raise SystemExit(
            f"이 리포트는 `{METRIC_NAME}` 만 읽습니다 (받은 것: {metric!r}).\n"
            "  다른 지표의 점수 파일을 먹이면 표가 전부 «못 잼» 으로 나오는데 그것은 지표가"
            " 아니라 이 도구가 만든 수입니다.\n"
            "  산문 골든 축의 갈래 재작성은 그 축이 값을 한다고 판정된 뒤의 일입니다."
        )
    axis = meta.get("control_axis")
    if axis not in READABLE_CONTROL_AXES:
        raise SystemExit(
            f"이 리포트는 «남의 질문» 대조군만 읽습니다 (받은 축: {axis!r}).\n"
            f"  `{METRIC_NAME}` 은 `reference` 를 안 읽어서, «남의 골든» 축으로 만든 대조군"
            " 행은 self 와 입력이 글자까지 같습니다.\n"
            "  그 재료로 규칙 1 을 대면 «차 0.0000 → 기각» 이 나오는데 그것은 지표가 아니라"
            " 재료가 만든 수입니다."
        )


# 표를 찍는다
def render(payload: dict[str, Any]) -> None:
    meta = payload.get("meta") or {}
    check_readable(meta)
    usage = meta.get("usage") or {}
    print(f"metric={meta.get('metric')} · ragas {meta.get('ragas_version')} ")
    print(
        f"strictness={meta.get('strictness')} — **재는 것 자체에 샘플링이 들어 있어 재현되지 않는다**"
    )
    print(
        f"회계: 모델 {usage.get('model')} · 입력 {usage.get('input_tokens')} · "
        f"출력 {usage.get('output_tokens')} 토큰 · {usage.get('rows')}행"
    )
    print("  (금액은 여기서 안 낸다 — 관측만 남기고 단가는 볼 때 곱한다)")
    print()

    raw = bucket_rows(payload)
    buckets = {name: summarize(name, values) for name, values in raw.items()}

    width = max((len(n) for n in buckets), default=10)
    header = f"{'갈래'.ljust(width)}  {'행':>4} {'잼':>4} {'못잼':>4} {'중앙값':>8} {'평균':>8} {'최소':>8} {'최대':>8}"
    print(header)
    print("-" * len(header))
    for name, b in buckets.items():
        def fmt(v: float | None) -> str:
            return "  —  " if v is None else f"{v:.4f}"

        print(
            f"{name.ljust(width)}  {b.rows:>4} {b.scored:>4} {b.unscored:>4} "
            f"{fmt(b.median):>8} {fmt(b.mean):>8} {fmt(b.low):>8} {fmt(b.high):>8}"
        )
    print()
    print("판정 규칙 (59회차에 돌리기 전에 못 박은 것):")
    for line in check_rules(buckets):
        print(f"  {line}")
    print()
    print("부정 두 갈래에 비율을 안 낸다 — 어떤 분수도 「오류율」로 읽히는데 기권 쪽이")
    print("이 코퍼스에서 제일 좋은 답변이다.")


def main() -> None:
    parser = argparse.ArgumentParser(description="RAGAS 탐침 점수를 갈래를 갈라 읽는다 (과금 0)")
    parser.add_argument("--scores", required=True, help="ragas_probe.py 가 만든 점수 JSON")
    args = parser.parse_args()

    path = Path(args.scores)
    if not path.exists():
        raise SystemExit(f"점수 파일이 없습니다: {path}")
    render(json.loads(path.read_text(encoding="utf-8")))


if __name__ == "__main__":
    main()
