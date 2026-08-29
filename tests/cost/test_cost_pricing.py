"""단가표가 **저장소가 실제로 쓰는 모델과 어긋나지 않는지** 본다.

이름을 코드에 못 박은 곳은 대조해야 오타가 산다 — `evaluation/config.py` 의 `EVAL_SETTINGS`
를 `model_fields` 와 대조하는 것과 같은 잣대다. 여기서는 두 방향을 본다.

1. **표에 있는데 저장소가 안 쓰는 모델** — 오타이거나 죽은 줄이다.
2. **저장소가 쓰는데 표에 없는 모델** — 그 회차의 금액이 조용히 「못 냄」이 된다.

②를 실패로 두되 **가격을 지어내라는 뜻이 아니다.** `auto_test_check.py` 의 매핑 규약과
같은 모양이다: 단가를 넣거나, 왜 못 넣는지를 `_KNOWN_UNPRICED` 에 적거나 둘 중 하나여야
통과한다. 새 유료 모델을 붙인 사람이 **그 선택을 하게** 만드는 것이 이 테스트의 일이다.
"""

from __future__ import annotations

import pytest

from cost.pricing import CHECKED_ON, RATES, Rate
from evaluation.config import EVAL_EMBEDDINGS, EVAL_EVALUATORS, EVAL_RERANKERS, EVAL_REWRITERS
from rag_pipeline.config import ContentRoutingConfig, GenerationConfig

# 단가를 못 넣는 모델과 **그 이유**. 비워 두면 안 되고, 이유가 곧 심사다
_KNOWN_UNPRICED: dict[str, str] = {
    "rerank-v4.0-fast": "실측 회차만 쓴 실험 프로필이라 운영 단가를 아직 안 본다",
    "gpt-5.6-luna": "판정 모델 후보였다가 mini 로 닫혔다 (2026-08-29) — 운영에서 안 쓴다",
    # **이쪽은 «안 쓴다» 가 아니라 «못 찾았다» 다.** 실제로 쓰는 유료 모델인데
    # 2026-08-29 확인으로 공개 가격 페이지에 검색 단위 단가가 없었다 —
    # `cohere.com/pricing` 의 시간·월 단가는 전용 배포(Model Vault)라 다른 축이고
    # `docs.cohere.com/docs/how-does-cohere-pricing-work` 는 금액을 그 페이지로 돌려보낸다.
    # 대시보드나 계약서에서 단가를 확인하면 `RATES` 로 옮기고 이 줄을 지운다
    "rerank-v3.5": "공개 가격 페이지에 검색 단위 단가가 안 실린다 (2026-08-29 확인)",
}

# 로컬(무료) 모델은 단가가 없다. HuggingFace 이름은 `조직/모델` 이라 슬래시로 갈린다
def _is_local(name: str) -> bool:
    return "/" in name


# 저장소가 실제로 부르는 모델 이름 전부. **프로필까지 훑는 것이 요점이다** —
# 기본값만 보면 유료 갈래는 전부 꺼져 있어 검사가 아무것도 안 잡는다
def _configured_models() -> set[str]:
    names: set[str] = {
        GenerationConfig().generation_model,
        ContentRoutingConfig().vision_model,
    }
    for profiles, key in (
        (EVAL_EMBEDDINGS, "embedding_model"),
        (EVAL_RERANKERS, "rerank_model"),
        (EVAL_REWRITERS, "rewrite_model"),
        (EVAL_EVALUATORS, "evaluator_model"),
    ):
        for profile in profiles.values():
            value = profile.get(key)
            if isinstance(value, str) and value:
                names.add(value)
    return names


def test_the_table_has_no_dead_or_mistyped_rows() -> None:
    configured = _configured_models()
    unused = {model for _, model in RATES if model not in configured}
    assert not unused, (
        f"단가표에 저장소가 안 쓰는 모델이 있습니다: {sorted(unused)} — "
        "오타이거나 죽은 줄입니다"
    )


def test_every_paid_model_is_priced_or_knowingly_not() -> None:
    priced = {model for _, model in RATES}
    missing = {
        name
        for name in _configured_models()
        if not _is_local(name) and name not in priced and name not in _KNOWN_UNPRICED
    }
    assert not missing, (
        f"단가를 모르는 유료 모델이 있습니다: {sorted(missing)}.\n"
        "단가를 `cost/pricing.py:RATES` 에 넣거나, 왜 못 넣는지를 이 파일의 "
        "`_KNOWN_UNPRICED` 에 적으세요 — 둘 중 하나여야 통과합니다."
    )


def test_known_unpriced_entries_carry_a_reason() -> None:
    for model, reason in _KNOWN_UNPRICED.items():
        assert reason.strip(), f"{model} 의 이유가 비어 있습니다"


# 단위마다 채워야 하는 칸이 다르다. 안 맞으면 `cost_usd()` 가 조용히 `None` 을 돌려주고
# 그 회차가 통째로 「금액 못 냄」이 된다
@pytest.mark.parametrize("rate", list(RATES.values()), ids=lambda rate: rate.model)
def test_rate_shape_matches_its_unit(rate: Rate) -> None:
    if rate.unit == "token":
        assert rate.usd_per_1m_input is not None, f"{rate.model}: 입력 단가가 비어 있습니다"
    elif rate.unit == "search_unit":
        assert rate.usd_per_1k_units is not None, f"{rate.model}: 단위 단가가 비어 있습니다"
    else:
        pytest.fail(f"{rate.model}: 모르는 단위 {rate.unit!r} — cost_usd() 가 못 곱합니다")


@pytest.mark.parametrize("rate", list(RATES.values()), ids=lambda rate: rate.model)
def test_every_rate_names_its_source(rate: Rate) -> None:
    # 출처가 없으면 다음 사람이 무엇과 대조해야 하는지 모른다
    assert rate.source.startswith("http"), f"{rate.model}: 출처 URL 이 없습니다"


# **`CHECKED_ON` 은 날짜이거나 `None` 이다.** "곧"·"확인함" 같은 말을 넣으면 표가 언제
# 것인지 다시 알 수 없어진다
def test_checked_on_is_a_date_or_none() -> None:
    if CHECKED_ON is None:
        return
    import datetime as dt

    dt.date.fromisoformat(CHECKED_ON)
