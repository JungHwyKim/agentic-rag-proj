"""재시도 루프가 비용 로그에 **몇 줄을 남기는가.**

루프에는 비용 로그를 **안 붙였다.** 안에서 부르는 `generate_answer` 와 `evaluator.evaluate`
가 이미 자기 자리에서 줄을 쓰므로, 루프에 또 붙이면 같은 호출이 두 번 세진다.

**그 결정이 지켜지는지를 여기서 잠근다.** 그리고 계획 단계에서 틀리게 적었던 것도 함께
잡아 둔다 — *"루프가 생성을 여러 번 부르니 `generate` 줄 수가 곧 재시도 횟수"* 라고 썼는데,
`step_generate` 가 **유일한 정상 출구**라 생성은 늘 1회다. 재시도와 함께 늘어나는 것은
`evaluate` 다(`step_retrieve` 안에 있어 **재시도+1**회).

`step_budget()` 이 그 구조를 이미 못 박고 있다 —
`retrieve` 1 + (`rewrite`+`retrieve`)×`max_retries` + `generate` 1 + `end` 1.

## 스텁을 안 쓰는 자리

`test_retry_loop.py` 는 `StubEvaluator`·`StubGenerate` 로 돌지만 **여기서는 진짜를 쓴다** —
비용 줄은 진짜 구현체 안에서 나오므로 스텁으로 바꾸면 이 검사가 아무것도 안 잰다.
`_make_client()` 만 갈아끼워 유료 호출을 막는다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from _loop_stubs import QUERY, StubRetriever, StubRewriter

from rag_pipeline.agent.loop import run_agent_loop
from rag_pipeline.config import EvaluatorConfig, GenerationConfig
from rag_pipeline.generation import OpenAIEvaluator, generate_answer
from rag_pipeline.generation import answer as answer_module
from rag_pipeline.generation import evaluate as evaluate_module


# 실제 SDK 모양을 지킨다. 합계만 흉내내면 입출력을 가르는 코드가 0을 받고도 통과한다
class _Response:
    def __init__(self, content: str) -> None:
        message = type("M", (), {"content": content})()
        self.choices = [type("C", (), {"message": message, "finish_reason": "stop"})()]
        self.usage = type(
            "U", (), {"prompt_tokens": 90, "completion_tokens": 10, "total_tokens": 100}
        )()


class _Client:
    def __init__(self, content: str) -> None:
        self._content = content
        self.chat = self

    @property
    def completions(self) -> Any:
        return self

    def create(self, **_: Any) -> _Response:
        return _Response(self._content)


def _stages(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return [
        json.loads(line)["stage"]
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


@pytest.mark.parametrize("max_retries", [0, 1, 3])
def test_evaluate_grows_with_retries_but_generate_stays_one(
    mocker, tmp_path: Path, cost_log_path: Path, max_retries: int
) -> None:
    # 판정은 늘 *불충분* 이라 루프가 max_retries 까지 돈다
    mocker.patch.object(
        evaluate_module,
        "_make_client",
        return_value=_Client('{"verdict": "insufficient", "reasoning": "모자람"}'),
    )
    mocker.patch.object(answer_module, "_make_client", return_value=_Client("답변 [1]"))

    generation = GenerationConfig(openai_api_key="test-key")
    evaluator = OpenAIEvaluator(
        EvaluatorConfig(
            openai_api_key="test-key",
            evaluator_model="gpt-4.1-mini",
            evaluator_cache_dir=tmp_path / "evaluator",
            evaluator_cache_enabled=False,
        )
    )
    rewriter = StubRewriter()

    run_agent_loop(
        QUERY,
        retriever=StubRetriever(rewriter=rewriter),
        evaluator=evaluator,
        rewriter=rewriter,
        generate=lambda query, chunks: generate_answer(query, chunks, generation),
        max_retries=max_retries,
    )

    stages = _stages(cost_log_path)
    # **판정은 검색마다 돈다** — 첫 검색 + 재시도 수
    assert stages.count("evaluate") == max_retries + 1
    # **생성은 유일한 출구라 늘 1회다.** 2가 나오면 루프에 로그를 또 붙인 것이다
    assert stages.count("generate") == 1


# 판정을 안 넘기면 그 줄이 아예 없다. **0과 «안 돌았다» 가 같은 뜻인 유일한 자리**이고,
# 그것이 «안 넘기면 그 갈래 없이 돈다» 라는 이 저장소의 스위치 규약이다
def test_without_an_evaluator_no_judge_lines(mocker, cost_log_path: Path) -> None:
    mocker.patch.object(answer_module, "_make_client", return_value=_Client("답변 [1]"))
    generation = GenerationConfig(openai_api_key="test-key")

    run_agent_loop(
        QUERY,
        retriever=StubRetriever(),
        evaluator=None,
        rewriter=StubRewriter(),
        generate=lambda query, chunks: generate_answer(query, chunks, generation),
        max_retries=2,
    )

    stages = _stages(cost_log_path)
    assert "evaluate" not in stages
    assert stages.count("generate") == 1
