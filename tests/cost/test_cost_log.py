"""비용 로그가 쓰는 한 줄의 계약.

**이 파일이 지키는 것은 「무엇이 남는가」다.** 이음새마다 그 줄이 실제로 나오는지는 각
영역 테스트가 본다(`tests/retrieval`·`tests/generation`·`tests/ingestion`·`tests/agent`·
`tests/evaluation`).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from rag_pipeline.config import CostLogConfig
from rag_pipeline.cost_log import append_cost_log, run_id, split_tokens

# 줄에 **늘 있어야 하는** 키. 하나라도 빠지면 집계에서 «그 단계가 그 값을 안 낸다» 와
# «옛 형식이라 칸이 없다» 가 안 갈린다 (`query_log` 의 `verdict` 규칙과 같다)
_REQUIRED_KEYS = {
    "at",
    "run_id",
    "label",
    "stage",
    "provider",
    "model",
    "outcome",
    "input_tokens",
    "output_tokens",
    "search_units",
    "items",
    "detail",
}


def _config(tmp_path: Path, **overrides: object) -> CostLogConfig:
    return CostLogConfig(cost_log_path=tmp_path / "cost.jsonl", **overrides)


def _lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_line_carries_every_key(tmp_path: Path) -> None:
    config = _config(tmp_path)
    assert append_cost_log(
        stage="rerank", provider="cohere", model="rerank-v3.5", outcome="call", config=config
    )

    (record,) = _lines(config.cost_log_path)
    assert set(record) == _REQUIRED_KEYS
    # **안 쓴 칸은 빠지는 게 아니라 채워진다.** `search_units` 는 `null`(그 단위가 없다),
    # 토큰은 0(그 API 가 토큰을 안 쓴다) — 둘이 다른 뜻이라 다른 값이다
    assert record["search_units"] is None
    assert record["input_tokens"] == 0


@pytest.mark.parametrize("outcome", ["call", "cache_hit", "failed"])
def test_three_outcomes_all_land(tmp_path: Path, outcome: str) -> None:
    config = _config(tmp_path)
    append_cost_log(
        stage="generate",
        provider="openai",
        model="gpt-4.1-mini",
        outcome=outcome,  # type: ignore[arg-type]
        config=config,
    )
    (record,) = _lines(config.cost_log_path)
    assert record["outcome"] == outcome


# **끄면 파일 자체가 안 생긴다.** 빈 파일을 만들어 두면 «아직 안 썼다» 와 «껐다» 가 섞인다
def test_disabled_writes_nothing(tmp_path: Path) -> None:
    config = _config(tmp_path, cost_log_enabled=False)
    assert not append_cost_log(
        stage="generate", provider="openai", model="m", outcome="call", config=config
    )
    assert not config.cost_log_path.exists()


# **쓰기 실패가 유료 호출을 죽이면 안 된다.** 관측이 신뢰성을 깎는 자리다 —
# `query_log` · `ResultCache.set()` 이 이미 같은 규약이다
def test_write_failure_is_swallowed(tmp_path: Path) -> None:
    # 부모가 파일이면 `mkdir` 이 OSError 를 낸다
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory", encoding="utf-8")
    config = CostLogConfig(cost_log_path=blocker / "cost.jsonl")

    assert append_cost_log(
        stage="generate", provider="openai", model="m", outcome="call", config=config
    ) is False


# 라벨이 비면 `null` 이다. 빈 문자열로 두면 «이름을 안 줬다» 와 «이름이 빈 문자열» 이 섞인다
def test_blank_label_becomes_null(tmp_path: Path) -> None:
    config = _config(tmp_path, cost_label="")
    append_cost_log(stage="rewrite", provider="openai", model="m", outcome="call", config=config)
    assert _lines(config.cost_log_path)[0]["label"] is None


def test_label_is_carried(tmp_path: Path) -> None:
    config = _config(tmp_path, cost_label="리랭커-cohere-60건")
    append_cost_log(stage="rerank", provider="cohere", model="m", outcome="call", config=config)
    assert _lines(config.cost_log_path)[0]["label"] == "리랭커-cohere-60건"


# **한 프로세스 안에서는 같은 `run_id` 다.** 줄마다 새로 만들면 실행을 묶는 축이 사라진다
def test_run_id_is_stable_within_the_process(tmp_path: Path) -> None:
    config = _config(tmp_path)
    for _ in range(3):
        append_cost_log(
            stage="judge", provider="openai", model="m", outcome="call", config=config
        )
    ids = {record["run_id"] for record in _lines(config.cost_log_path)}
    assert ids == {run_id()}


# 응답 모양을 안 믿는다. **없으면 0이고 예외를 안 낸다** — 여기서 새면 폴백이 아니라 500 이다
@pytest.mark.parametrize(
    ("usage", "expected"),
    [
        (SimpleNamespace(prompt_tokens=100, completion_tokens=20), (100, 20)),
        (SimpleNamespace(total_tokens=120), (0, 0)),
        (None, (0, 0)),
        (SimpleNamespace(prompt_tokens="100", completion_tokens=None), (0, 0)),
    ],
)
def test_split_tokens_never_raises(usage: object, expected: tuple[int, int]) -> None:
    assert split_tokens(SimpleNamespace(usage=usage)) == expected


def test_split_tokens_on_a_response_without_usage() -> None:
    assert split_tokens(object()) == (0, 0)
