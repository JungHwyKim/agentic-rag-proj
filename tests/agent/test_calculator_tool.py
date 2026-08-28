"""계산기 도구 — 무엇을 계산하고 **무엇을 거절하는지**.

거절 쪽이 이 파일의 무게중심이다. 입력이 모델이 쓴 문자열이라 `eval()` 이면 곧 임의 코드
실행이고, 화이트리스트가 실제로 좁은지는 밖에서 재야 안다.
"""

from __future__ import annotations

import pytest

from rag_pipeline.agent.tools.calculator import MAX_EXPONENT, CalculatorTool
from rag_pipeline.schemas.agent import ToolInput


def _run(expression: str):
    return CalculatorTool().run(ToolInput(query=expression))


# ---- 계산한다 ----


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("1 + 1", 2),
        ("120 - 45", 75),
        ("12 * 3", 36),
        ("10 / 4", 2.5),
        ("10 // 4", 2),
        ("10 % 4", 2),
        ("2 ** 10", 1024),
        ("(3 + 5) * 2", 16),
        ("-7 + 10", 3),
        ("1.5 * 4", 6.0),
        # 실제로 쓸 자리 — 표에서 뽑은 수의 비율
        ("57 / 309 * 100", pytest.approx(18.4466, abs=1e-4)),
    ],
)
def test_arithmetic(expression, expected):
    out = _run(expression)

    assert out.success is True
    assert out.data["result"] == expected
    assert out.data["expression"] == expression


# ---- 거절한다 ----


# **이것이 `eval()` 을 안 쓴 이유 그 자체다.** 하나라도 통과하면 임의 코드 실행이다
@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('echo hi')",
        "os.system('echo hi')",
        "open('/etc/passwd').read()",
        "(lambda: 1)()",
        "[x for x in range(3)]",
        "1 if True else 2",
        "print(1)",
        "x + 1",
        "'가' * 3",
        "True + 1",
    ],
)
def test_rejects_everything_that_is_not_arithmetic(expression):
    out = _run(expression)

    assert out.success is False
    assert out.data is None


# **화이트리스트가 못 막는 축이다** — 문법이 멀쩡하고 허용 노드만 쓰는데 메모리를 태운다
def test_rejects_huge_exponent():
    out = _run(f"2 ** {MAX_EXPONENT + 1}")

    assert out.success is False
    assert str(MAX_EXPONENT) in out.error


# 상한 안쪽은 그대로 계산한다 — 안전장치가 정상 사용을 안 막는지 본다
def test_exponent_at_the_bound_still_works():
    out = _run(f"10 ** {MAX_EXPONENT}")

    assert out.success is True
    assert out.data["result"] == 10**MAX_EXPONENT


# ---- 사유가 사람이 읽을 문장이다 ----


# **예외 이름으로 시작하면 안 된다** — `run()` 이 잡으면 `ZeroDivisionError:` 로 시작한다
def test_division_by_zero_is_a_korean_sentence():
    out = _run("1 / 0")

    assert out.success is False
    assert out.error == "0 으로 나눌 수 없습니다."


def test_empty_expression():
    out = _run("   ")

    assert out.success is False
    assert "비어" in out.error


def test_syntax_error_is_not_an_exception_name():
    out = _run("1 +")

    assert out.success is False
    assert "SyntaxError" not in out.error


# 어떤 입력에도 예외가 밖으로 안 나간다 — 그것이 이 계층의 계약이다
@pytest.mark.parametrize("expression", ["", "((((", "1/0", "2**999999", "del x"])
def test_never_raises(expression):
    out = _run(expression)

    assert out.success is False
