"""모델별 단가표. **로그가 아니라 여기가 금액을 안다.**

## 왜 로그에 금액을 안 박았나

단가는 바뀐다. 로그 줄에 금액을 박아 두면 단가가 바뀐 날 **이미 쌓인 줄이 조용히
거짓**이 되고, 되돌릴 방법이 없다. 관측(토큰·호출)만 남기고 금액은 읽을 때 곱하면
표만 고쳐서 과거까지 다시 계산된다 — `verdict`·`citation` 이 답변을 안 바꾸고 흔적만
싣는 것과 같은 잣대다.

## 낡는 것을 막을 수 없으니 보이게 한다

가격표는 **반드시** 낡는데 낡아도 에러가 안 난다. 그래서 이 모듈이 하는 일은 정확함을
약속하는 것이 아니라 **언제 것인지 말하는 것**이다 — `CHECKED_ON` 이 `None` 이면
`report.py` 가 표 아래에 "사람이 확인하지 않은 값" 을 찍는다.

**아래 값은 사람이 확인하지 않았다.** 모델이 아는 값으로 채워 둔 초기값이고, 공급자
가격 페이지와 대조한 사람이 `CHECKED_ON` 에 날짜를 넣는 것이 그 확인의 정의다.
확인 전에는 이 표로 낸 금액을 `.claude/wiki/verify_log/` 에 수치로 옮기면 안 된다.

## 모르는 것은 비워 둔다 — 0으로 안 채운다

`llamaparse`(쪽 단위) · `tavily`(크레딧)는 로그가 그 단위를 안 담는다. LlamaParse 는
응답이 쪽 수를 안 주고, Tavily 는 크레딧 수를 안 준다. 그래서 표에 없고, `report.py` 가
그 줄들을 **「금액 못 냄」으로 따로 센다** — 0으로 채우면 안 쓴 것처럼 보인다.
이 저장소가 이미 두 번 같은 판단을 했다(Evaluator 의 제3의 상태, `recover.py` 의 못 가름).
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["CHECKED_ON", "RATES", "Rate", "cost_usd", "rate_for"]

# **공급자 가격 페이지와 대조한 날.** `None` 이면 아직 아무도 안 봤다는 뜻이고
# `report.py` 가 그것을 표 아래에 찍는다. 대조한 사람이 "YYYY-MM-DD" 를 넣는다.
#
# 2026-08-29 대조 — `developers.openai.com/api/docs/pricing` 에서 아래 두 줄이 글자 그대로
# 맞았다. **Cohere 는 못 했다**: 공개 가격 페이지(`cohere.com/pricing`)에 리랭크의 검색 단위
# 단가가 **안 실려 있고**(거기 있는 `$5.00/시간`·`$3,250/월` 은 전용 배포인 Model Vault 값이라
# 다른 축이다), `docs.cohere.com/docs/how-does-cohere-pricing-work` 도 *"검색 수로 과금한다"*
# 까지만 말하고 금액은 그 페이지로 돌려보낸다. 그래서 **표에 안 넣었다** — 아래 `RATES` 의
# 주석 참고. 이 날짜는 **여기 남은 줄에 대해서만** 참이다
CHECKED_ON: str | None = "2026-08-29"


# 단가 한 줄. **단위가 갈래마다 다르다** — 토큰(OpenAI)과 검색 단위(Cohere)는 서로 못 바꾼다
@dataclass(frozen=True)
class Rate:
    provider: str
    model: str
    # 무엇으로 파나. `report.py` 가 이 값으로 어느 칸을 곱할지 고른다
    unit: str
    usd_per_1m_input: float | None = None
    usd_per_1m_output: float | None = None
    # 토큰이 아닌 단위의 단가(예: Cohere 검색 단위 1,000개당)
    usd_per_1k_units: float | None = None
    source: str = ""


# (provider, model) -> 단가. **없으면 「금액 못 냄」이다** (모듈 docstring 참고)
RATES: dict[tuple[str, str], Rate] = {
    ("openai", "gpt-4.1-mini"): Rate(
        provider="openai",
        model="gpt-4.1-mini",
        unit="token",
        usd_per_1m_input=0.40,
        usd_per_1m_output=1.60,
        source="https://openai.com/api/pricing/",
    ),
    ("openai", "text-embedding-3-small"): Rate(
        provider="openai",
        model="text-embedding-3-small",
        unit="token",
        usd_per_1m_input=0.02,
        # 임베딩에는 출력 토큰이 없다. `None` 이 곧 그 뜻이다
        usd_per_1m_output=None,
        source="https://openai.com/api/pricing/",
    ),
    # **`text-embedding-3-large` 는 일부러 없다.** 이 저장소가 안 쓰는 모델을 표에 두면
    # 죽은 줄이 되고, 죽은 줄은 나중에 «이건 확인된 값인가» 를 흐린다.
    # `tests/cost/test_cost_pricing.py` 가 첫 실행에서 실제로 그 줄을 잡아냈다.
    #
    # **`rerank-v3.5` 도 일부러 없다 — 이쪽은 이유가 다르다.** 저장소가 실제로 쓰는
    # 유료 모델인데 **공개 가격 페이지에 검색 단위 단가가 안 실려 있다**(2026-08-29 확인 —
    # `cohere.com/pricing` 의 `$5.00/시간`·`$3,250/월` 은 전용 배포 값이라 다른 축이고,
    # 문서는 *"검색 수로 과금한다"* 까지만 말한다). **못 확인한 수를 넣느니 「금액 못 냄」이
    # 낫다** — 넣으면 합계가 확신에 찬 채 틀리고, 안 넣으면 리포트가 그 줄을 따로 세어
    # 사람이 본다. `search_units` 는 계속 쌓이므로 단가를 알게 된 날 곱하기만 하면 된다.
    # 이유는 `tests/cost/test_cost_pricing.py:_KNOWN_UNPRICED` 에도 적혀 있다
}


# 이 (provider, model) 의 단가. 없으면 None — 부르는 쪽이 「금액 못 냄」으로 센다
def rate_for(provider: str, model: str) -> Rate | None:
    return RATES.get((provider, model))


# 로그 한 줄의 금액(USD). **단가를 모르면 `None` 이지 0이 아니다** (모듈 docstring 참고).
#
# `outcome` 을 안 받는 것이 의도다 — 이 함수는 *"이만큼 썼으면 얼마인가"* 만 답한다.
# 캐시 히트를 "아낀 금액" 으로 환산할지는 부르는 쪽의 판단이고, 그쪽은 같은 함수에
# **아꼈을 사용량**을 넣어 부른다
def cost_usd(
    rate: Rate | None,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    search_units: int | None = None,
) -> float | None:
    if rate is None:
        return None

    if rate.unit == "token":
        total = 0.0
        if rate.usd_per_1m_input is not None:
            total += input_tokens * rate.usd_per_1m_input / 1_000_000
        if rate.usd_per_1m_output is not None:
            total += output_tokens * rate.usd_per_1m_output / 1_000_000
        return total

    if rate.unit == "search_unit":
        # **`None` 과 0을 가른다.** `None` 은 응답이 과금 단위를 안 알려준 것이고
        # (`cohere_api._billed_search_units()`), 0은 실제로 0단위다
        if search_units is None or rate.usd_per_1k_units is None:
            return None
        return search_units * rate.usd_per_1k_units / 1_000

    return None
