"""리포트가 **못 내는 것을 0으로 안 채우는지** 본다.

이 도구의 값어치는 합계가 아니라 **경고 두 줄**이다 — 폴백이 몇 건이었나, 금액을 못 낸
줄이 몇 건이었나. 둘 다 0으로 뭉개면 «한도 초과가 품질 저하로 읽힌» 사고가 그대로 되풀이된다.
"""

from __future__ import annotations

import json
from pathlib import Path

from cost import pricing, report
from cost.pricing import Rate, cost_usd
from cost.report import load_rows, main, summarize


def _row(**overrides: object) -> dict:
    record = {
        "at": "2026-08-29T01:00:00+00:00",
        "run_id": "aaaaaaaaaaaa",
        "label": "테스트",
        "stage": "rerank",
        "provider": "cohere",
        "model": "rerank-v3.5",
        "outcome": "call",
        "input_tokens": 0,
        "output_tokens": 0,
        "search_units": 1,
        "items": 50,
        "detail": None,
    }
    record.update(overrides)
    return record


def _write(path: Path, rows: list[dict]) -> Path:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    return path


def test_missing_file_is_not_an_error(tmp_path: Path) -> None:
    rows, broken = load_rows(tmp_path / "nope.jsonl")
    assert (rows, broken) == ([], 0)


# **깨진 줄을 조용히 버리면 «적게 썼다» 로 읽힌다.** 몇 줄인지 돌려준다
def test_broken_lines_are_counted_not_hidden(tmp_path: Path) -> None:
    path = tmp_path / "cost.jsonl"
    path.write_text('{"stage": "rerank"}\n{ 아니다\n[1, 2]\n', encoding="utf-8")

    rows, broken = load_rows(path)
    assert len(rows) == 1
    assert broken == 2


# 세 상태가 각자 다른 칸으로 간다. 합치면 폴백이 성공에 섞인다
def test_outcomes_land_in_their_own_columns() -> None:
    buckets = summarize(
        [_row(), _row(outcome="cache_hit"), _row(outcome="failed"), _row(outcome="failed")],
        "stage",
    )
    bucket = buckets["rerank"]
    assert (bucket.calls, bucket.cache_hits, bucket.failures) == (1, 1, 2)


# **단가표에 없는 모델은 0이 아니라 「금액 못 냄」이다** (모듈 docstring)
def test_unknown_model_is_unpriced_not_zero() -> None:
    buckets = summarize([_row(provider="llamaparse", model="balanced", search_units=None)], "stage")
    bucket = buckets["rerank"]
    assert bucket.unpriced == 1
    assert bucket.usd == 0.0
    assert bucket.unpriced_models == {"llamaparse/balanced"}


# 단가는 아는데 **응답이 과금 단위를 안 준** 경우는 따로 센다 — 사람이 할 일이 다르다
# (표를 고치는 것이 아니라 그 호출의 응답을 보는 것이다).
#
# **단가를 꽂아 넣는 이유**: 2026-08-29 현재 `RATES` 에 `search_unit` 갈래가 하나도 없다
# (Cohere 단가를 공개 페이지에서 못 찾아 일부러 뺐다 — `cost/pricing.py` 참고). 그러면 이
# 분기가 실제 표로는 못 도는데, **코드가 살아 있는 한 검사도 살아 있어야** 단가를 넣는 날
# 그 자리가 이미 잠겨 있다
def test_known_model_without_billing_units_is_flagged(monkeypatch) -> None:
    monkeypatch.setitem(
        pricing.RATES,
        ("cohere", "rerank-v3.5"),
        Rate(
            provider="cohere",
            model="rerank-v3.5",
            unit="search_unit",
            usd_per_1k_units=2.0,
            source="https://example.invalid/",
        ),
    )
    bucket = summarize([_row(search_units=None)], "stage")["rerank"]
    assert (bucket.unpriced, bucket.units_unknown) == (1, 1)


# 표에 아예 없는 모델은 **「과금단위 없음」이 아니라 그냥 「금액 못 냄」이다.**
# 사람이 할 일이 다르다 — 이쪽은 단가를 찾아 넣는 것이고, 위쪽은 응답을 보는 것이다
def test_missing_rate_is_not_counted_as_missing_units() -> None:
    bucket = summarize([_row(search_units=None)], "stage")["rerank"]
    assert (bucket.unpriced, bucket.units_unknown) == (1, 0)


def test_token_cost_is_split_by_direction() -> None:
    rate = Rate(
        provider="openai",
        model="m",
        unit="token",
        usd_per_1m_input=1.0,
        usd_per_1m_output=10.0,
    )
    # 입력 100만 · 출력 100만 → 1.0 + 10.0. **합계 하나로는 못 내는 수**다
    assert cost_usd(rate, input_tokens=1_000_000, output_tokens=1_000_000) == 11.0


# 임베딩처럼 출력 단가가 없는 갈래에서 출력 토큰은 금액에 안 들어간다
def test_missing_output_rate_is_not_charged() -> None:
    rate = Rate(provider="openai", model="m", unit="token", usd_per_1m_input=1.0)
    assert cost_usd(rate, input_tokens=1_000_000, output_tokens=999) == 1.0


# **`None` 과 0을 가른다** — `None` 은 응답이 단위를 안 알려준 것이고 0은 실제로 0단위다
def test_search_units_none_is_not_zero() -> None:
    rate = Rate(provider="cohere", model="m", unit="search_unit", usd_per_1k_units=2.0)
    assert cost_usd(rate, search_units=None) is None
    assert cost_usd(rate, search_units=0) == 0.0


# 캐시가 아낀 금액은 **추정**이라 성공 호출이 없으면 못 낸다
def test_saved_needs_a_priced_call_to_estimate_from() -> None:
    from cost.report import _saved_usd

    only_hits = summarize([_row(outcome="cache_hit")], "stage")["rerank"]
    assert _saved_usd(only_hits) is None


def test_cli_runs_end_to_end(tmp_path: Path, capsys) -> None:
    path = _write(
        tmp_path / "cost.jsonl",
        [_row(), _row(outcome="failed", detail="429"), _row(outcome="cache_hit")],
    )
    assert main(["--log", str(path)]) == 0

    out = capsys.readouterr().out
    # 회계 줄이 표보다 **먼저** 나온다
    assert out.index("회계") < out.index("합계")
    assert "폴백 1" in out
    # 단가표를 언제 대조했는지가 표 아래에 보여야 한다
    assert "단가표 기준일" in out


# **반대 갈래도 잠근다.** `CHECKED_ON` 이 `None` 이면 경고가 나와야 하는데, 2026-08-29 에
# 그 값을 채우자 위 테스트가 깨졌다 — 한 갈래만 잠겨 있었다는 뜻이다. 둘 다 잠근다
def test_unchecked_pricing_table_warns(tmp_path: Path, capsys, monkeypatch) -> None:
    # `report.py` 가 `from cost.pricing import CHECKED_ON` 으로 이름을 떠 왔으므로
    # 원본이 아니라 **그쪽 이름**을 갈아끼운다
    monkeypatch.setattr(report, "CHECKED_ON", None)
    path = _write(tmp_path / "cost.jsonl", [_row()])
    main(["--log", str(path)])

    out = capsys.readouterr().out
    assert "확인하지 않았습니다" in out
    assert "단가표 기준일" not in out


def test_label_filter_narrows_the_rows(tmp_path: Path, capsys) -> None:
    path = _write(tmp_path / "cost.jsonl", [_row(label="A"), _row(label="B"), _row(label="B")])
    main(["--log", str(path), "--label", "B"])
    assert "줄 2개" in capsys.readouterr().out
