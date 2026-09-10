"""산술식 하나를 계산하는 도구.

## 왜 `eval()` 이 아닌가

`eval("__import__('os').system('...')")` 이 곧 임의 코드 실행이다. 이 도구의 입력은 **모델이
쓴 문자열**이라 그것을 신뢰할 근거가 없고, 신뢰할 수 있더라도 프롬프트가 바뀌면 근거가
사라진다. `ast.literal_eval()` 도 답이 아니다 — 그쪽은 리터럴만 읽고 **연산을 아예 못 한다**
(`1+1` 이 파이썬 3.8 이후로 거절된다).

그래서 `ast.parse()` 로 구문 트리를 얻고 **허용한 노드만** 밟는다. 화이트리스트라
새 문법(왈러스·f-string·comprehension)이 생겨도 자동으로 거절된다 — 블랙리스트였으면
그때마다 이 파일을 따라 고쳐야 하고, 안 고치면 **조용히 열린다.**

## 화이트리스트만으로 안 막히는 것이 하나 있다

`2 ** 99999999` 는 **문법이 멀쩡하고 허용 노드만 쓰는데** 계산하다 메모리를 태운다. 즉
「무엇을 쓰나」와 「얼마나 크나」는 다른 축이라, 지수에 상한을 따로 둔다. 나머지 연산은
피연산자 크기가 결과 크기를 대충 따라가지만 거듭제곱만 지수적이라 이 축이 여기 하나다.

## 실패 사유를 직접 만든다

`BaseTool.run()` 이 예외를 값으로 바꿔 주지만, 그러면 사유가 `ZeroDivisionError: division by
zero` 로 시작해 **사람이 못 읽는다.** 웹 검색이 `NOT_CONFIGURED` 를 예외 이름으로 시작하지
않게 한 것과 같은 기준이다 — 이 사유는 화면에 그대로 나갈 수 있는 문장이어야 한다.

**다만 계약을 대신하지는 않는다** — 여기서 못 잡은 예외는 `run()` 이 받는다. 두 겹이다.
"""

from __future__ import annotations

import ast
import operator
from typing import Any

from rag_pipeline.agent.tools.base import BaseTool
from rag_pipeline.schemas.agent import ToolInput, ToolOutput

__all__ = ["MAX_EXPONENT", "CalculatorTool"]

# 거듭제곱 지수의 절대값 상한. **관측값이 아니라 안전장치다** — 분포의 틈에서 나온 이
# 저장소의 임계값들과 성격이 다르다. 100 은 사람이 손으로 쓸 만한 지수를 넉넉히 덮으면서
# `10 ** 100` 이 즉시 끝나는 크기다 (모듈 docstring 참고)
MAX_EXPONENT = 100

_BIN_OPS: dict[type[ast.operator], Any] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_UNARY_OPS: dict[type[ast.unaryop], Any] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


# 사람이 읽을 사유를 달고 올라오는 실패. 밖으로는 ToolOutput 으로만 나간다
class CalculationError(ValueError):
    pass


# 숫자 계산만 한다. 질의가 곧 식이다
class CalculatorTool(BaseTool):
    name = "calculator"
    description = "산술식 하나를 계산한다 (사칙연산·나머지·거듭제곱·괄호)"

    def _run(self, tool_input: ToolInput) -> ToolOutput:
        expression = tool_input.query.strip()
        if not expression:
            return ToolOutput(success=False, error="계산할 식이 비어 있습니다.")
        try:
            tree = ast.parse(expression, mode="eval")
        except SyntaxError:
            return ToolOutput(success=False, error=f"식을 읽지 못했습니다: {expression}")

        try:
            result = _evaluate(tree)
        except CalculationError as exc:
            return ToolOutput(success=False, error=str(exc))
        except ZeroDivisionError:
            return ToolOutput(success=False, error="0 으로 나눌 수 없습니다.")
        # OverflowError 는 float 거듭제곱에서 난다. MAX_EXPONENT 가 정수 쪽만 막는다
        except OverflowError:
            return ToolOutput(success=False, error="계산 결과가 너무 큽니다.")

        return ToolOutput(success=True, data={"expression": expression, "result": result})


# 허용한 노드만 밟는다. **모르는 것은 전부 거절**이라 새 문법이 저절로 막힌다
def _evaluate(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _evaluate(node.body)

    if isinstance(node, ast.Constant):
        # **bool 을 먼저 거른다** — `isinstance(True, int)` 가 참이라 안 거르면
        # `True + 1` 이 2 로 계산되고, 그것은 산술식이 아니다
        if isinstance(node.value, bool) or not isinstance(node.value, int | float):
            raise CalculationError(f"숫자가 아닌 값은 계산할 수 없습니다: {node.value!r}")
        return node.value

    if isinstance(node, ast.UnaryOp):
        op = _UNARY_OPS.get(type(node.op))
        if op is None:
            raise CalculationError("계산기가 다룰 수 없는 단항 연산입니다.")
        return op(_evaluate(node.operand))

    if isinstance(node, ast.BinOp):
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise CalculationError("계산기가 다룰 수 없는 연산입니다.")
        left = _evaluate(node.left)
        right = _evaluate(node.right)
        # 화이트리스트가 못 막는 축 (모듈 docstring 참고)
        if isinstance(node.op, ast.Pow) and abs(right) > MAX_EXPONENT:
            raise CalculationError(
                f"거듭제곱의 지수는 절대값 {MAX_EXPONENT} 이하여야 합니다: {right}"
            )
        return op(left, right)

    raise CalculationError("계산기는 숫자와 사칙연산만 다룹니다.")
