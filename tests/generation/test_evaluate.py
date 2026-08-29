"""근거 충분성 판정. 모킹 이음새는 모듈 레벨 _make_client() 다 (generation/evaluate.py).

여기서 지키는 계약은 하나로 모인다 — **실패는 판정이 아니다.** 키 없음·호출 실패·응답 손상이
전부 `failed` 로 나가야 하고, 그중 어느 것도 *불충분* 으로 미끄러지면 안 된다. 미끄러지면
부정 예시 질의에서 죽은 Evaluator 가 만점을 받아 점수가 실력이 아니라 실패율을 잰다
(plan_log/검색-생성.md 10회차).
"""

from __future__ import annotations

import json

import pytest

from rag_pipeline.config import EvaluatorConfig
from rag_pipeline.generation import evaluate as evaluate_module
from rag_pipeline.generation import get_evaluator
from rag_pipeline.generation.evaluate import OpenAIEvaluator
from rag_pipeline.schemas.retrieval import RetrievedChunk


# 근거 청크 하나
def _chunk(text: str = "제12조 위약금은 잔여 계약금액의 20%로 한다", *, index: int = 1) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=f"안내서.pdf:{index:04d}",
        document_id="안내서.pdf",
        text=text,
        metadata={"source_name": "안내서.pdf", "page": 4},
    )


# OpenAI 응답 모양을 흉내낸다. 문자열로 만들면 테스트가 오해를 검증한다 (test_generate_answer 참고)
class FakeResponse:
    def __init__(self, content: str | None, *, empty_choices: bool = False, tokens: int = 120) -> None:
        if empty_choices:
            self.choices = []
        else:
            message = type("Message", (), {"content": content})()
            self.choices = [type("Choice", (), {"message": message})()]
        # **셋을 다 낸다.** 실제 응답이 `prompt_tokens`·`completion_tokens`·`total_tokens` 를
        # 함께 주는데 합계만 흉내내면, 입출력을 가르는 코드가 **0을 받고도 통과한다** —
        # 실제로 2026-08-29 에 이 가짜가 그 상태였고 어느 테스트도 안 잡았다
        completion = tokens // 6
        self.usage = type(
            "Usage",
            (),
            {
                "prompt_tokens": tokens - completion,
                "completion_tokens": completion,
                "total_tokens": tokens,
            },
        )()


# client.chat.completions.create(...) 경로를 그대로 흉내낸다
class FakeClient:
    def __init__(self, *responses: FakeResponse) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []
        self.chat = type("Chat", (), {"completions": self})()

    def create(self, **kwargs):  # noqa: ANN003, ANN201
        self.calls.append(kwargs)
        return self._responses[min(len(self.calls) - 1, len(self._responses) - 1)]


# 던지는 클라이언트. 폴백이 아니라 제3의 상태로 가는지 본다
class BoomClient:
    def __init__(self) -> None:
        self.chat = type("Chat", (), {"completions": self})()

    def create(self, **kwargs):  # noqa: ANN003, ANN201
        raise RuntimeError("network down")


# 키가 있는 설정. 캐시는 tmp_path 로 가른다 — 안 그러면 테스트끼리 판정을 물려받는다
def _config(tmp_path, **overrides):  # noqa: ANN001, ANN003
    return EvaluatorConfig(
        openai_api_key="test-key",
        evaluator_model="gpt-4.1-mini",
        evaluator_cache_dir=tmp_path / "evaluator",
        **overrides,
    )


def _body(verdict: str, reasoning: str = "근거에 조항이 그대로 있습니다") -> str:
    return json.dumps({"verdict": verdict, "reasoning": reasoning}, ensure_ascii=False)


# 모델이 충분하다고 하면 그대로 실린다
def test_sufficient_passes_through(mocker, tmp_path):
    client = FakeClient(FakeResponse(_body("sufficient")))
    mocker.patch.object(evaluate_module, "_make_client", return_value=client)

    verdict = OpenAIEvaluator(_config(tmp_path)).evaluate("위약금은?", [_chunk()])

    assert verdict.verdict == "sufficient"
    assert verdict.reasoning == "근거에 조항이 그대로 있습니다"


# 불충분도 그대로. **판정이지 실패가 아니다**
def test_insufficient_is_a_judgement_not_a_failure(mocker, tmp_path):
    client = FakeClient(FakeResponse(_body("insufficient", "주제만 관련됩니다")))
    mocker.patch.object(evaluate_module, "_make_client", return_value=client)

    evaluator = OpenAIEvaluator(_config(tmp_path))
    verdict = evaluator.evaluate("위약금은?", [_chunk()])

    assert verdict.verdict == "insufficient"
    assert evaluator.warnings == []
    assert evaluator.usage.evaluated == 1
    # **성공 경로의 토큰을 재는 유일한 자리다.** 아래 `test_usage_counts_only_success` 는
    # 실패 경로의 0 만 보므로, 여기가 없으면 토큰을 통째로 안 세도 전부 통과한다
    assert evaluator.usage.input_tokens == 100
    assert evaluator.usage.output_tokens == 20
    assert evaluator.usage.total_tokens == 120


# 키가 없으면 failed 다. **insufficient 로 미끄러지면 안 된다** (모듈 docstring)
def test_missing_key_is_failed_not_insufficient(mocker, tmp_path):
    mocker.patch.object(evaluate_module, "_make_client", return_value=None)

    evaluator = OpenAIEvaluator(_config(tmp_path))
    verdict = evaluator.evaluate("위약금은?", [_chunk()])

    assert verdict.verdict == "failed"
    assert evaluator.warnings and "OPENAI_API_KEY" in evaluator.warnings[0]
    assert evaluator.usage.evaluated == 0


# 호출이 터져도 예외가 안 샌다. 실패는 제3의 상태로 나간다
def test_api_error_is_failed(mocker, tmp_path):
    mocker.patch.object(evaluate_module, "_make_client", return_value=BoomClient())

    evaluator = OpenAIEvaluator(_config(tmp_path))
    verdict = evaluator.evaluate("위약금은?", [_chunk()])

    assert verdict.verdict == "failed"
    assert "RuntimeError" in evaluator.warnings[0]


# 응답 모양이 틀리면 failed. 부분 성공을 만들지 않는다
@pytest.mark.parametrize(
    "content",
    [
        None,
        "설명 없이 JSON 이 아님",
        json.dumps({"reasoning": "판정 칸이 없다"}),
        json.dumps({"verdict": "maybe", "reasoning": "없는 값"}),
        json.dumps(["sufficient"]),
    ],
)
def test_broken_response_is_failed(mocker, tmp_path, content):
    client = FakeClient(FakeResponse(content))
    mocker.patch.object(evaluate_module, "_make_client", return_value=client)

    verdict = OpenAIEvaluator(_config(tmp_path)).evaluate("위약금은?", [_chunk()])

    assert verdict.verdict == "failed"


# choices 가 비어도 예외가 아니라 failed (image_vision 이 IndexError 를 낸 적이 있다)
def test_empty_choices_is_failed(mocker, tmp_path):
    client = FakeClient(FakeResponse(None, empty_choices=True))
    mocker.patch.object(evaluate_module, "_make_client", return_value=client)

    assert OpenAIEvaluator(_config(tmp_path)).evaluate("q", [_chunk()]).verdict == "failed"


# **모델은 자기 실패를 선언할 수 없다.** "failed" 를 말하면 그것은 손상된 응답이다
def test_model_cannot_claim_failed(mocker, tmp_path):
    client = FakeClient(FakeResponse(_body("failed", "제가 판정을 못 하겠습니다")))
    mocker.patch.object(evaluate_module, "_make_client", return_value=client)

    evaluator = OpenAIEvaluator(_config(tmp_path))
    verdict = evaluator.evaluate("q", [_chunk()])

    assert verdict.verdict == "failed"
    # 모델의 문장이 사유로 실리면 실패 버킷이 모델의 말버릇을 재게 된다
    assert verdict.reasoning != "제가 판정을 못 하겠습니다"
    assert evaluator.warnings


# 근거가 0건이면 모델을 안 부른다. **실패가 아니라 불충분이다**
def test_no_chunks_skips_the_model(mocker, tmp_path):
    client = FakeClient(FakeResponse(_body("sufficient")))
    mocker.patch.object(evaluate_module, "_make_client", return_value=client)

    verdict = OpenAIEvaluator(_config(tmp_path)).evaluate("q", [])

    assert verdict.verdict == "insufficient"
    assert client.calls == []


# 질의가 비면 판정을 못 한 것이다. 불충분이 아니다
def test_blank_query_is_failed(mocker, tmp_path):
    client = FakeClient(FakeResponse(_body("sufficient")))
    mocker.patch.object(evaluate_module, "_make_client", return_value=client)

    assert OpenAIEvaluator(_config(tmp_path)).evaluate("   ", [_chunk()]).verdict == "failed"
    assert client.calls == []


# 같은 질의·같은 후보면 두 번째는 캐시다. 채점 재실행이 무료여야 한다
def test_cache_hit_skips_the_api(mocker, tmp_path):
    client = FakeClient(FakeResponse(_body("sufficient")))
    mocker.patch.object(evaluate_module, "_make_client", return_value=client)

    evaluator = OpenAIEvaluator(_config(tmp_path))
    first = evaluator.evaluate("위약금은?", [_chunk()])
    second = evaluator.evaluate("위약금은?", [_chunk()])

    assert (first.verdict, second.verdict) == ("sufficient", "sufficient")
    assert len(client.calls) == 1
    assert evaluator.usage.cache_hits == 1
    assert evaluator.usage.api_calls == 1


# 후보 **순서**가 다르면 다른 프롬프트다. 집합만 키에 넣으면 남의 답을 재사용한다
def test_candidate_order_changes_the_key(mocker, tmp_path):
    client = FakeClient(FakeResponse(_body("sufficient")))
    mocker.patch.object(evaluate_module, "_make_client", return_value=client)

    evaluator = OpenAIEvaluator(_config(tmp_path))
    a, b = _chunk(index=1), _chunk("다른 본문", index=2)
    evaluator.evaluate("q", [a, b])
    evaluator.evaluate("q", [b, a])

    assert len(client.calls) == 2


# **못 쓸 응답은 캐시에 안 들어간다.** 들어가면 다음 실행이 그 실패를 그대로 되읽는다
def test_broken_response_is_not_cached(mocker, tmp_path):
    client = FakeClient(FakeResponse("깨진 응답"), FakeResponse(_body("sufficient")))
    mocker.patch.object(evaluate_module, "_make_client", return_value=client)

    evaluator = OpenAIEvaluator(_config(tmp_path))
    assert evaluator.evaluate("q", [_chunk()]).verdict == "failed"
    assert evaluator.evaluate("q", [_chunk()]).verdict == "sufficient"
    assert len(client.calls) == 2


# 프롬프트 버전이 salt 에 들어간다. 고치면 옛 응답을 안 읽는다
def test_prompt_version_is_in_the_salt(mocker, tmp_path):
    client = FakeClient(FakeResponse(_body("sufficient")))
    mocker.patch.object(evaluate_module, "_make_client", return_value=client)

    evaluator = OpenAIEvaluator(_config(tmp_path))
    evaluator.evaluate("q", [_chunk()])
    # **실제 상수와 다른 값이어야 한다.** 지금 값을 그대로 박으면 패치가 no-op 이 되어
    # 두 번째 호출이 캐시에 걸리고 이 테스트가 깨진다. 버전을 올릴 때마다 여기를 고치는
    # 대신, 상수와 무관한 표식을 써서 **버전 값과 독립**으로 만든다
    mocker.patch.object(evaluate_module, "EVALUATOR_PROMPT_VERSION", "다른-버전")
    evaluator.evaluate("q", [_chunk()])

    assert len(client.calls) == 2


# --------------------------------------------------------------------------
# 호출 인자 — 모델을 타는 자리 (generation/evaluate.py 의 "호출 인자가 모델을 탄다")
# --------------------------------------------------------------------------


# **`max_tokens` 로는 안 나간다.** gpt-5 계열이 그 이름을 400 으로 거부해서 바꾼 것이고,
# gpt-4.1-mini 도 새 이름을 받으므로 **분기가 없다** — 그래서 이 한 줄이 두 모델을 다 잠근다
def test_token_budget_uses_the_new_parameter_name(mocker, tmp_path):
    client = FakeClient(FakeResponse(_body("sufficient")))
    mocker.patch.object(evaluate_module, "_make_client", return_value=client)

    OpenAIEvaluator(_config(tmp_path, evaluator_max_tokens=77)).evaluate("질문", [_chunk()])

    call = client.calls[0]
    assert call["max_completion_tokens"] == 77
    assert "max_tokens" not in call


# **temperature=None 이면 인자 자체가 안 실린다.** 1.0 을 대신 보내면 안 된다 —
# luna 는 기본값만 받고, 0.0 이 재현을 위한 값이었다는 사실이 코드에서 지워진다
def test_none_temperature_is_omitted_entirely(mocker, tmp_path):
    client = FakeClient(FakeResponse(_body("sufficient")))
    mocker.patch.object(evaluate_module, "_make_client", return_value=client)

    OpenAIEvaluator(_config(tmp_path, evaluator_temperature=None)).evaluate("질문", [_chunk()])

    assert "temperature" not in client.calls[0]


# 그리고 기본값(0.0)은 그대로 실린다. **gpt-4.1-mini 경로가 안 바뀐 것의 잠금이다** —
# 여기가 흔들리면 v1·v2·v3 로 잰 258건이 다른 조건의 수치가 된다
def test_default_temperature_is_still_sent(mocker, tmp_path):
    client = FakeClient(FakeResponse(_body("sufficient")))
    mocker.patch.object(evaluate_module, "_make_client", return_value=client)

    OpenAIEvaluator(_config(tmp_path)).evaluate("질문", [_chunk()])

    assert client.calls[0]["temperature"] == 0.0


# **온도는 캐시 salt 에 안 들어간다.** 넣으면 mini 쪽 258건이 통째로 죽는데, 모델 이름이
# 이미 salt 에 있어 luna 와 mini 는 그것만으로 갈린다
def test_temperature_is_not_in_the_salt(mocker, tmp_path):
    client = FakeClient(FakeResponse(_body("sufficient")), FakeResponse(_body("insufficient")))
    mocker.patch.object(evaluate_module, "_make_client", return_value=client)

    first = OpenAIEvaluator(_config(tmp_path))
    first.evaluate("질문", [_chunk()])

    # 같은 캐시 디렉터리 · 같은 모델 · 온도만 다르다 -> 캐시가 맞아야 한다
    second = OpenAIEvaluator(_config(tmp_path, evaluator_temperature=None))
    verdict = second.evaluate("질문", [_chunk()])

    assert verdict.verdict == "sufficient"
    assert second.usage.cache_hits == 1
    assert len(client.calls) == 1


# 카운터는 성공한 뒤에만 오른다 (embedding·리랭커와 같은 규약)
def test_usage_counts_only_success(mocker, tmp_path):
    mocker.patch.object(evaluate_module, "_make_client", return_value=BoomClient())

    evaluator = OpenAIEvaluator(_config(tmp_path))
    evaluator.evaluate("q", [_chunk()])

    assert evaluator.usage.api_calls == 0
    assert evaluator.usage.total_tokens == 0


# 모델 이름이 비면 통째로 꺼진다. 그것이 저장소 커밋 상태다
def test_factory_is_off_without_a_model():
    assert get_evaluator(EvaluatorConfig(evaluator_model=None)) is None
    assert isinstance(
        get_evaluator(EvaluatorConfig(evaluator_model="gpt-4.1-mini")), OpenAIEvaluator
    )
