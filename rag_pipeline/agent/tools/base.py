"""도구의 공통 계약.

## 왜 템플릿 메서드인가

*"도구는 예외를 던지지 않고 실패를 값으로 돌려준다"* 를 **주석이 아니라 구조로** 지킨다.
`run()` 이 `_run()` 을 감싸 어떤 예외든 `ToolOutput(success=False)` 로 바꾸므로, 구현체가
규약을 잊어도 호출부는 안 깨진다.

적재 쪽 외부 API 핸들러가 *"예외를 던지지 않고 빈 리스트를 반환"* 을 **약속으로만** 두고
있어 한 번 어겼을 때 적재 전체가 멈춘 적이 있다(2026-08-18, LlamaParse `markdown` 을
문자열로 안 것). 같은 규약을 여기서는 상위 클래스가 강제한다.

`BaseDocumentLoader.load()` 가 `detect_regions()` 만 추상으로 두는 것과 같은 모양이다.

## `name` 이 왜 클래스 속성인가

Router 와 `ToolCallLog` 가 **인스턴스를 안 만들고도** 도구를 가리켜야 한다. 생성자에서
받으면 같은 도구가 이름 둘로 불릴 수 있고, 그러면 `tool_call_log` 집계가 조용히 갈린다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from rag_pipeline.schemas.agent import ToolInput, ToolOutput

__all__ = ["BaseTool"]


# 모든 도구의 공통 뼈대. 구현체는 `_run()` 하나만 짠다
class BaseTool(ABC):
    # Router·로그가 인스턴스 없이 가리키는 이름 (모듈 docstring 참고)
    name: str = ""
    # 무슨 도구인지 사람이 읽는 한 줄. Phase 8 의 Router 프롬프트가 쓸 자리다
    description: str = ""

    # 도구를 부른다. **어떤 경우에도 예외를 안 낸다** (모듈 docstring 참고)
    def run(self, tool_input: ToolInput) -> ToolOutput:
        try:
            return self._run(tool_input)
        except Exception as exc:  # noqa: BLE001 - 도구 실패는 값으로 내려간다. 그것이 계약이다
            return ToolOutput(success=False, error=f"{type(exc).__name__}: {exc}")

    # 실제로 하는 일. 여기서는 예외를 던져도 된다 — run() 이 받아 값으로 바꾼다
    @abstractmethod
    def _run(self, tool_input: ToolInput) -> ToolOutput: ...
