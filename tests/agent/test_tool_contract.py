"""도구의 공통 계약. **예외가 밖으로 안 나간다**를 구조로 지키는지 본다.

이 검사가 있는 이유는 적재 쪽 선례다 — 외부 API 핸들러의 *"예외를 던지지 않고 빈 리스트를
반환"* 이 약속으로만 있어 한 번 어겼을 때 적재 전체가 멈췄다. 여기서는 상위 클래스가
강제하므로, **구현체가 규약을 어겨도 이 테스트가 통과해야 한다.**
"""

from __future__ import annotations

import pytest

from rag_pipeline.agent.tools.base import BaseTool
from rag_pipeline.schemas.agent import ToolInput, ToolOutput


# 부르면 반드시 죽는 도구. 규약을 어긴 구현체를 흉내 낸다
class ExplodingTool(BaseTool):
    name = "exploding"
    description = "테스트용 — 늘 예외를 던진다"

    def _run(self, tool_input: ToolInput) -> ToolOutput:
        raise RuntimeError("의도된 실패")


# 잘 도는 도구
class EchoTool(BaseTool):
    name = "echo"
    description = "테스트용 — 질의를 그대로 돌려준다"

    def _run(self, tool_input: ToolInput) -> ToolOutput:
        return ToolOutput(success=True, data=tool_input.query)


# 구현체가 예외를 던져도 run() 은 값으로 바꿔 돌려준다. 이것이 이 계층의 존재 이유다
def test_exception_becomes_failed_output():
    out = ExplodingTool().run(ToolInput(query="질문"))

    assert out.success is False
    assert out.data is None
    assert "RuntimeError" in out.error
    assert "의도된 실패" in out.error


# 성공 경로는 그대로 통과한다 — 감싸는 것이 성공까지 건드리면 안 된다
def test_success_passes_through():
    out = EchoTool().run(ToolInput(query="질문"))

    assert out.success is True
    assert out.data == "질문"
    assert out.error is None


# `_run()` 을 안 짜면 인스턴스를 못 만든다. 계약이 주석이 아니라 타입에 있다
def test_subclass_must_implement_run():
    class Incomplete(BaseTool):
        name = "incomplete"

    with pytest.raises(TypeError):
        Incomplete()


# 이름은 인스턴스 없이 읽힌다 — Router 와 로그가 그렇게 쓴다
def test_name_is_class_level():
    assert EchoTool.name == "echo"
    assert EchoTool().name == "echo"
