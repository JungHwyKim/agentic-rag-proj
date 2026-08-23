"""답변 생성. 모킹 이음새는 모듈 레벨 _make_client() 다 (generation/answer.py)."""

from __future__ import annotations

import pytest

from rag_pipeline.config import GenerationConfig
from rag_pipeline.exceptions import GenerationError
from rag_pipeline.generation import NO_EVIDENCE_ANSWER, generate_answer
from rag_pipeline.generation import answer as answer_module
from rag_pipeline.generation.prompts import build_user_prompt
from rag_pipeline.schemas.retrieval import RetrievedChunk


# 근거 청크 하나를 만든다
def _chunk(text: str, *, source: str = "안내서.pdf", page: int = 4) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=f"{source}:0001",
        document_id=source,
        text=text,
        metadata={"source_name": source, "page": page},
        dense_score=0.8,
    )


# OpenAI 응답 모양을 흉내낸다. **문자열로 만들면 안 된다** — 실제는 choices 를 가진 객체이고,
# 가짜를 단순화하면 테스트가 그 오해를 그대로 검증한다 (LlamaParse markdown 사고와 같은 종류)
class FakeResponse:
    def __init__(self, content: str | None, *, empty_choices: bool = False) -> None:
        if empty_choices:
            self.choices = []
        else:
            message = type("Message", (), {"content": content})()
            self.choices = [type("Choice", (), {"message": message})()]


# client.chat.completions.create(...) 경로를 그대로 흉내내는 가짜 클라이언트
class FakeClient:
    def __init__(self, response: FakeResponse) -> None:
        self._response = response
        self.calls: list[dict] = []
        self.chat = type("Chat", (), {"completions": self})()

    def create(self, **kwargs):  # noqa: ANN003, ANN201
        self.calls.append(kwargs)
        return self._response


# 키가 있는 설정. 실제 호출은 _make_client 를 모킹해 막는다
def _config(**overrides) -> GenerationConfig:  # noqa: ANN003
    return GenerationConfig(openai_api_key="test-key", **overrides)


# 근거가 있으면 모델 응답을 그대로 돌려준다
def test_returns_model_text(mocker):
    client = FakeClient(FakeResponse("위약금은 잔여 계약금액의 20%입니다 [1]"))
    mocker.patch.object(answer_module, "_make_client", return_value=client)

    text = generate_answer("위약금은?", [_chunk("제12조 …")], _config())

    assert text == "위약금은 잔여 계약금액의 20%입니다 [1]"
    assert len(client.calls) == 1


# **검색 0건은 실패가 아니다.** 모델을 아예 안 부른다 —
# 근거 없이 부르면 돈을 쓰면서 환각할 기회만 준다
def test_no_chunks_skips_model(mocker):
    make_client = mocker.patch.object(answer_module, "_make_client")

    assert generate_answer("아무거나", [], _config()) == NO_EVIDENCE_ANSWER
    make_client.assert_not_called()


# 키가 없으면 빈 문자열이 아니라 예외다. 로더 쪽 "빈 결과 + warn" 규약은 폴백 층이 있어서
# 성립하는 것이고 생성에는 그 층이 없다
def test_missing_key_raises(mocker):
    mocker.patch.object(answer_module, "_make_client", return_value=None)

    with pytest.raises(GenerationError) as excinfo:
        generate_answer("질문", [_chunk("근거")], GenerationConfig(openai_api_key=None))
    assert "API 키" in excinfo.value.user_message


# API 예외를 GenerationError 로 바꿔 올린다. 날것으로 새면 backend 매핑표에 없어 500 이 된다
def test_api_failure_becomes_generation_error(mocker):
    client = FakeClient(FakeResponse("x"))
    mocker.patch.object(client, "create", side_effect=RuntimeError("boom"))
    mocker.patch.object(answer_module, "_make_client", return_value=client)

    with pytest.raises(GenerationError):
        generate_answer("질문", [_chunk("근거")], _config())


# choices=[] 에서 IndexError 가 새면 안 된다 — image_vision 이 실제로 그렇게 샜던 자리다
def test_empty_choices_raises_generation_error(mocker):
    client = FakeClient(FakeResponse(None, empty_choices=True))
    mocker.patch.object(answer_module, "_make_client", return_value=client)

    with pytest.raises(GenerationError):
        generate_answer("질문", [_chunk("근거")], _config())


# 본문이 비었거나 공백뿐이면 빈 말풍선을 만들지 않고 예외로 올린다
@pytest.mark.parametrize("content", [None, "", "   \n "])
def test_blank_content_raises(mocker, content):
    client = FakeClient(FakeResponse(content))
    mocker.patch.object(answer_module, "_make_client", return_value=client)

    with pytest.raises(GenerationError):
        generate_answer("질문", [_chunk("근거")], _config())


# 설정값이 실제로 요청에 실린다. 안 실리면 죽은 설정이 된다
def test_config_reaches_request(mocker):
    client = FakeClient(FakeResponse("답변"))
    mocker.patch.object(answer_module, "_make_client", return_value=client)

    generate_answer(
        "질문",
        [_chunk("근거")],
        _config(generation_model="gpt-test", generation_max_tokens=77),
    )

    call = client.calls[0]
    assert call["model"] == "gpt-test"
    assert call["max_tokens"] == 77


# 근거는 번호가 달려 프롬프트에 실린다. 번호가 있어야 답변의 [1] 과 소스 태그가 맞춰진다
def test_prompt_numbers_evidence():
    prompt = build_user_prompt("질문", [_chunk("첫째 근거"), _chunk("둘째 근거")])

    assert "[1]" in prompt
    assert "[2]" in prompt
    assert "첫째 근거" in prompt
    assert "둘째 근거" in prompt
    assert "질문: 질문" in prompt


# **쪽 번호는 프롬프트에 안 넣는다** — HWPX 쪽 수가 문서에 따라 부풀어(AI가이드 28 vs 97)
# 모델이 틀린 쪽을 인용하게 된다. 쪽은 화면이 metadata 에서 직접 그린다
def test_prompt_omits_page_numbers():
    prompt = build_user_prompt("질문", [_chunk("근거", page=42)])
    assert "42" not in prompt
