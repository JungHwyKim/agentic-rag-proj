"""답변 채점 도구가 **자기 자신을 못 믿게 되는 자리들**을 잠근다.

이 도구의 위험은 «수치가 나쁘다» 가 아니라 **«틀린 수치가 그럴듯하다»** 다. 그래서 여기서
재는 것은 대부분 지표가 아니라 **지표를 못 읽게 만드는 조건**이다 — 실패를 판정으로 접었나,
정상 경로를 미지지로 셌나, 대조군이 자기 근거를 물었나, 캐시가 본판과 대조군을 섞었나.

키 없이 전부 돈다. 외부 호출은 `_make_client()` 이음새 하나로 갈아끼운다.
"""

from __future__ import annotations

import json

import pytest

from evaluation import answer_eval
from evaluation.answer_eval import (
    AnswerRow,
    JudgeConfig,
    JudgeUsage,
    SupportJudge,
    accounting_problems,
    control_partner,
    get_judge,
    is_abstention,
    score_citations,
    score_must,
    score_must_not_claim,
    summarize,
)
from evaluation.config import EVAL_JUDGES
from evaluation.qa_set import QAItem, QASet
from rag_pipeline.generation.prompts import NO_EVIDENCE_ANSWER
from rag_pipeline.schemas.retrieval import RetrievedChunk
from rag_pipeline.schemas.vector import StoredChunk


def chunk(chunk_id: str, text: str, doc: str = "안내서.pdf") -> RetrievedChunk:
    return RetrievedChunk(chunk_id=chunk_id, document_id=doc, text=text, metadata={})


@pytest.fixture
def config() -> JudgeConfig:
    return JudgeConfig(judge_model="gpt-4.1-mini", judge_temperature=0.0, judge_max_tokens=512)


@pytest.fixture
def judge(config: JudgeConfig, tmp_path) -> SupportJudge:
    # 캐시를 tmp 로 돌린다 — 안 그러면 테스트가 개발자의 실제 판정 캐시를 건드린다
    return SupportJudge(config, api_key="sk-test", cache_dir=tmp_path / "judge")


# 가짜 OpenAI 응답. **모양을 실제와 같게 둔다** — 문자열로 만들면 테스트가 오해를 검증한다
class FakeResponse:
    def __init__(self, content: str | None, tokens: int = 11) -> None:
        message = type("M", (), {"content": content})()
        self.choices = [type("C", (), {"message": message})()]
        # **셋을 다 낸다.** 실제 응답이 `prompt_tokens`·`completion_tokens`·`total_tokens` 를
        # 함께 주는데 합계만 흉내내면, 입출력을 가르는 코드가 0을 받고도 통과한다
        completion = tokens // 6
        self.usage = type(
            "U",
            (),
            {
                "prompt_tokens": tokens - completion,
                "completion_tokens": completion,
                "total_tokens": tokens,
            },
        )()


class FakeClient:
    def __init__(self, content: str | None = None, raises: Exception | None = None) -> None:
        self._content = content
        self._raises = raises
        self.calls: list[dict] = []

    @property
    def chat(self):  # noqa: D401 - OpenAI SDK 모양을 흉내 낸다
        return self

    @property
    def completions(self):
        return self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._raises is not None:
            raise self._raises
        return FakeResponse(self._content)


# ---------------------------------------------------------------------------
# 실패는 제3의 상태다 — 이 도구에서는 함정이 한 겹 더 나쁘다
# ---------------------------------------------------------------------------


# **미지지로 떨어뜨리면 죽은 채점자가 부정 예시에서 만점을 받는다.** 그리고 대조군의
# 기대값도 `unsupported` 라, 실패를 미지지로 접으면 **대조군까지 통과한 것처럼 보인다**
@pytest.mark.parametrize(
    ("content", "raises"),
    [
        (None, None),
        ("json 이 아니다", None),
        ('{"verdict": "maybe"}', None),
        ('{"verdict": "failed"}', None),
        (None, RuntimeError("네트워크")),
    ],
)
def test_a_broken_call_is_failed_not_unsupported(judge, mocker, content, raises) -> None:
    mocker.patch.object(
        answer_eval, "_make_client", return_value=FakeClient(content, raises=raises)
    )

    verdict = judge.judge("질문", "답변", [chunk("c1", "근거")])

    assert verdict.verdict == "failed"
    assert judge.warnings, "사유가 안 남으면 «나빴다» 와 «못 했다» 가 구분되지 않는다"
    # 실패는 판정을 얻은 것이 아니다
    assert judge.usage.judged == 0


# **모델은 자기 실패를 선언할 수 없다.** 두게 두면 실패 버킷이 모델의 말버릇을 잰다
def test_model_cannot_declare_failure(judge, mocker) -> None:
    mocker.patch.object(
        answer_eval,
        "_make_client",
        return_value=FakeClient('{"verdict": "failed", "reasoning": "모르겠다"}'),
    )
    assert judge.judge("질문", "답변", [chunk("c1", "근거")]).verdict == "failed"
    assert "모양이 달라" in " ".join(judge.warnings)


# 키가 없으면 판정을 못 한 것이지 미지지가 아니다
def test_missing_key_is_failed(config, tmp_path) -> None:
    judge = SupportJudge(config, api_key=None, cache_dir=tmp_path / "judge")
    assert judge.judge("질문", "답변", [chunk("c1", "근거")]).verdict == "failed"
    assert "OPENAI_API_KEY" in " ".join(judge.warnings)


# 카운터는 성공한 뒤에만 오른다 (embedding·리랭커와 같은 규약)
def test_counters_only_move_on_success(judge, mocker) -> None:
    mocker.patch.object(
        answer_eval, "_make_client", return_value=FakeClient('{"verdict": "supported"}')
    )
    judge.judge("질문", "답변", [chunk("c1", "근거")])
    assert (judge.usage.api_calls, judge.usage.judged, judge.usage.total_tokens) == (1, 1, 11)


# **못 쓸 응답은 캐시에 안 들어간다** — 들어가면 다음 실행이 손상된 응답을 판정으로 되읽는다
def test_a_broken_response_is_not_cached(judge, mocker) -> None:
    broken = mocker.patch.object(answer_eval, "_make_client", return_value=FakeClient("깨짐"))
    judge.judge("질문", "답변", [chunk("c1", "근거")])

    broken.return_value = FakeClient('{"verdict": "supported"}')
    assert judge.judge("질문", "답변", [chunk("c1", "근거")]).verdict == "supported"
    assert judge.usage.cache_hits == 0


# ---------------------------------------------------------------------------
# 건너뛰는 자리 둘 — 둘 다 정상 경로다
# ---------------------------------------------------------------------------


# **`check_citations()` 의 건너뛰기와 글자 그대로 같아야 한다.** 갈리면 같은 답변이 인용
# 검사에서는 정상이고 지지 판정에서는 미지지가 된다
@pytest.mark.parametrize(
    ("text", "chunks", "expected"),
    [
        ("아무 답", [], True),
        (NO_EVIDENCE_ANSWER, [chunk("c1", "근거")], True),
        (f"  {NO_EVIDENCE_ANSWER}  ", [chunk("c1", "근거")], True),
        ("근거에 따르면 [1] 입니다", [chunk("c1", "근거")], False),
    ],
)
def test_the_two_skip_sites(text, chunks, expected) -> None:
    assert is_abstention(text, chunks) is expected


# **인용이 붙은 기권을 놓치면 회차가 통째로 무효로 찍힌다.** 첫 판(2026-08-28)에서 실제로
# 났다 — 모델이 `문서에서 찾지 못했습니다. [1][2][3][4][5]` 를 2건 냈고, 그것이 판정 대상이
# 되자 *"못 찾았습니다"* 가 **어떤 근거로도 뒷받침되어** 음성 대조군이 `supported` 를 냈다.
# 채점자는 멀쩡했는데(그 둘을 빼면 0/28) 게이트가 회차를 버리게 만든다
def test_an_abstention_wearing_citations_is_still_an_abstention() -> None:
    chunks = [chunk(f"c{i}", "근거") for i in range(1, 6)]
    assert is_abstention(f"{NO_EVIDENCE_ANSWER} [1][2][3][4][5]", chunks) is True


# **`startswith` 로 고치면 안 된다** — 말을 잇는 답변은 기권이 아니라 판정 대상이다.
# 인용만 걷어내고 완전 일치를 보는 이유가 이것이다
def test_an_answer_that_continues_past_the_phrase_is_not_an_abstention() -> None:
    chunks = [chunk("c1", "근거")]
    text = f"{NO_EVIDENCE_ANSWER} 다만 [1] 에 비슷한 내용이 있습니다."
    assert is_abstention(text, chunks) is False


# 기권은 미지지가 아니다. **부정 예시에서는 기권이 곧 정답이라 따로 세야 한다**
def test_abstention_is_counted_apart_from_unsupported() -> None:
    rows = [
        AnswerRow(
            query_id="n1", query="q", source_doc="d", answerable=False,
            answer=NO_EVIDENCE_ANSWER, abstained=True,
        ),
        AnswerRow(
            query_id="n2", query="q", source_doc="d", answerable=False,
            answer="지어낸 답 [1]", chunk_ids=["c1"], support="unsupported",
        ),
    ]
    negative = summarize(rows, judge_on=True)["negative"]

    assert negative["abstained"] == 1
    assert negative["judged"] == 1
    assert negative["unsupported"] == 1


# ---------------------------------------------------------------------------
# 음성 대조군 — 이 도구가 자기 자신을 검사하는 유일한 자리
# ---------------------------------------------------------------------------


# 근거는 기본으로 행마다 다르게 준다 — 실제 실행에서 질의마다 다른 청크가 오는 것이 보통이다
def row(query_id: str, doc: str, chunk_ids: list[str] | None = None) -> AnswerRow:
    return AnswerRow(
        query_id=query_id, query="q", source_doc=doc, answerable=True,
        answer="답", chunk_ids=chunk_ids if chunk_ids is not None else [f"c-{query_id}"],
    )


# **대조군의 근거는 실제로 달라야 한다.** 자기 자신을 안 고르는 것만으로는 부족한데,
# `step` 이 1부터라 그쪽은 구조적으로 이미 성립해 **검사가 아무것도 안 잰다**(변이로 확인).
# 진짜 위험은 **다른 질의가 같은 청크를 물어온 경우**다 — 그때 «남의 근거» 가 실은 제 근거라
# 대조군이 본판과 같은 판정을 내고, 게이트가 그것을 채점자 누출로 읽어 멀쩡한 회차를 버린다
def test_control_needs_different_evidence() -> None:
    rows = [row("a", "문서1", ["c1", "c2"]), row("b", "문서2", ["c1", "c2"])]
    assert control_partner(0, rows) is None, "같은 청크를 물어온 짝은 대조군이 못 된다"


# **순서만 다른 같은 근거도 대조군이 못 된다.** 한때 이 테스트는 반대를 못 박고 있었고
# 이유는 *"프롬프트의 번호가 순서에서 나와 모델이 다른 것을 본다"* 였다 — 그 문장은 맞지만
# 이 자리의 계약이 아니다. 대조군은 **빌린 근거로는 뒷받침될 수 없어야** 성립하는데,
# 순서를 바꾼 같은 근거는 여전히 답변을 뒷받침한다. 43회차 실측에서 뒤집었다
def test_control_rejects_the_same_chunks_in_a_different_order() -> None:
    rows = [row("a", "문서1", ["c1", "c2"]), row("b", "문서2", ["c2", "c1"])]
    assert control_partner(0, rows) is None


# **짝을 못 찾은 행은 셋째 상태로 센다.** 거르는 축을 넓힌 대가라, 안 세면 대조군이 줄어든
# 것이 «supported 0» 으로 보여 **표본 부족이 실력으로 읽힌다**. `judged` 만 보면 그 둘이
# 안 갈린다 — Cohere 한도 초과가 품질로 읽힌 사고와 같은 모양이다
def test_rows_without_a_control_partner_are_counted_separately() -> None:
    rows = [row("a", "문서1", ["c1"]), row("b", "문서2", ["c1"])]
    control = summarize(rows, judge_on=True)["shuffled_control"]

    assert control["judged"] == 0
    assert control["unpaired"] == 2


# **기권한 행은 짝 없음으로 안 센다** — 거기는 대조군이 원래 안 도는 정상 경로다
def test_an_abstained_row_is_not_counted_as_unpaired() -> None:
    abstained = row("a", "문서1", ["c1"])
    abstained.abstained = True

    assert summarize([abstained], judge_on=True)["shuffled_control"]["unpaired"] == 0


# **겹침 하나로 막는다 — 이것이 43회차가 실제로 밟은 자리다.** `ai-01` 의 대조군이 빌린 다섯
# 청크 중 하나가 답변의 이메일 주소를 담은 바로 그 청크였고, 채점자는 옳게 «뒷받침된다» 고
# 했는데 게이트만 무효를 외쳤다. 검색은 문서를 안 가려 오므로 **질의의 `source_doc` 이 다르다는
# 것이 근거의 출처를 전혀 보장하지 않는다** — 실측 60건 중 3건이 이 상태였다
def test_control_rejects_a_partner_that_shares_even_one_chunk() -> None:
    rows = [
        row("a", "문서1", ["c1", "c2"]),
        row("b", "문서2", ["c2", "c9"]),
        row("c", "문서3", ["c7", "c8"]),
    ]

    assert control_partner(0, rows).query_id == "c"


# **다른 문서를 먼저 찾는다** — 같은 문서 근거는 우연히 답변을 뒷받침할 수 있어
# «구성으로 만든 known-bad» 가 아니게 된다
def test_control_prefers_a_different_document() -> None:
    rows = [row("a", "문서1"), row("b", "문서1"), row("c", "문서2")]
    assert control_partner(0, rows).source_doc == "문서2"


# 다른 문서가 없으면 같은 문서의 다른 근거로 물러선다. 그것도 없으면 대조군이 없다(None)
def test_control_falls_back_and_then_gives_up() -> None:
    same_doc = [row("a", "문서1"), row("b", "문서1")]
    assert control_partner(0, same_doc).query_id == "b"
    assert control_partner(0, [row("a", "문서1")]) is None


# 근거가 없는 행은 짝이 못 된다 — 빈 근거로 물으면 판정이 `failed` 라 대조가 아니다
def test_control_skips_rows_without_evidence() -> None:
    rows = [row("a", "문서1"), row("b", "문서2", chunk_ids=[]), row("c", "문서3")]
    assert control_partner(0, rows).query_id == "c"


# ---------------------------------------------------------------------------
# 캐시 키 — 본판과 대조군이 같은 칸을 다투면 대조가 사라진다
# ---------------------------------------------------------------------------


# **근거가 키에 안 들어가면 대조군이 본판의 답을 되읽는다.** 같은 답변에 다른 근거를 물린
# 것이 대조군의 정의라, 그 축이 키에 없으면 검사가 조용히 무력화된다
def test_evidence_identity_splits_the_cache(judge, mocker) -> None:
    client = FakeClient('{"verdict": "supported"}')
    mocker.patch.object(answer_eval, "_make_client", return_value=client)

    judge.judge("질문", "답변", [chunk("c1", "근거")])
    judge.judge("질문", "답변", [chunk("c2", "남의 근거")])

    assert len(client.calls) == 2, "근거가 달라지면 캐시가 갈려야 한다"
    assert judge.usage.cache_hits == 0


# 순서까지 담는다 — 프롬프트의 번호가 순서에서 나오므로 같은 집합이어도 모델이 다른 것을 본다
def test_evidence_order_splits_the_cache(judge, mocker) -> None:
    client = FakeClient('{"verdict": "supported"}')
    mocker.patch.object(answer_eval, "_make_client", return_value=client)

    pair = [chunk("c1", "가"), chunk("c2", "나")]
    judge.judge("질문", "답변", pair)
    judge.judge("질문", "답변", list(reversed(pair)))

    assert len(client.calls) == 2


# 답변 본문도 키에 들어간다 — 같은 근거라도 다른 답변이면 다른 판정이다
def test_answer_text_splits_the_cache(judge, mocker) -> None:
    client = FakeClient('{"verdict": "supported"}')
    mocker.patch.object(answer_eval, "_make_client", return_value=client)

    judge.judge("질문", "답변 하나", [chunk("c1", "근거")])
    judge.judge("질문", "답변 둘", [chunk("c1", "근거")])

    assert len(client.calls) == 2


# 같은 것을 두 번 물으면 캐시가 받는다. 그 히트가 회계 게이트의 한쪽 항이다
def test_the_same_question_hits_the_cache(judge, mocker) -> None:
    client = FakeClient('{"verdict": "supported"}')
    mocker.patch.object(answer_eval, "_make_client", return_value=client)

    judge.judge("질문", "답변", [chunk("c1", "근거")])
    judge.judge("질문", "답변", [chunk("c1", "근거")])

    assert len(client.calls) == 1
    assert judge.usage.cache_hits == 1


# **프롬프트 판이 캐시를 가른다.** 안 가르면 프롬프트를 고쳐도 옛 응답이 새 프롬프트의
# 답인 척 돌아오는데, 에러가 아니라 수치로만 드러난다
def test_prompt_version_is_in_the_salt(judge, mocker) -> None:
    client = FakeClient('{"verdict": "supported"}')
    mocker.patch.object(answer_eval, "_make_client", return_value=client)
    judge.judge("질문", "답변", [chunk("c1", "근거")])

    mocker.patch.object(answer_eval, "SUPPORT_PROMPT_VERSION", "v2")
    judge.judge("질문", "답변", [chunk("c1", "근거")])

    assert len(client.calls) == 2


# 채점자 모델도 키를 가른다 — 두 모델의 판정이 같은 칸을 다투면 표가 조용히 섞인다
def test_model_splits_the_cache(tmp_path, mocker) -> None:
    client = FakeClient('{"verdict": "supported"}')
    mocker.patch.object(answer_eval, "_make_client", return_value=client)
    shared = tmp_path / "judge"

    for model in ("gpt-4.1-mini", "gpt-5.6-luna"):
        SupportJudge(
            JudgeConfig(judge_model=model, judge_temperature=0.0, judge_max_tokens=512),
            api_key="sk-test",
            cache_dir=shared,
        ).judge("질문", "답변", [chunk("c1", "근거")])

    assert len(client.calls) == 2


# ---------------------------------------------------------------------------
# 호출 인자 — gpt-5 계열이 두 자리에서 400 을 낸다
# ---------------------------------------------------------------------------


# `max_tokens` 는 gpt-5 계열이 400 으로 거부하고 `gpt-4.1-mini` 는 새 이름도 받는다.
# 그래서 분기가 없고 **늘 `max_completion_tokens`** 다
def test_token_cap_uses_the_new_name(judge, mocker) -> None:
    client = FakeClient('{"verdict": "supported"}')
    mocker.patch.object(answer_eval, "_make_client", return_value=client)
    judge.judge("질문", "답변", [chunk("c1", "근거")])

    assert "max_tokens" not in client.calls[0]
    assert client.calls[0]["max_completion_tokens"] == 512


# **`None` 은 «보내지 않는다» 는 뜻이다.** 1.0 을 대신 보내면 *0.0 이 재현을 위한 값이었다*
# 는 사실이 코드에서 지워진다
def test_none_temperature_is_not_sent(tmp_path, mocker) -> None:
    client = FakeClient('{"verdict": "supported"}')
    mocker.patch.object(answer_eval, "_make_client", return_value=client)

    SupportJudge(
        JudgeConfig(judge_model="gpt-5.6-luna", judge_temperature=None, judge_max_tokens=512),
        api_key="sk-test",
        cache_dir=tmp_path / "judge",
    ).judge("질문", "답변", [chunk("c1", "근거")])

    assert "temperature" not in client.calls[0]


# 근거 블록은 `build_evidence_block()` 이 만든다 — 채점자가 본 것과 생성이 본 것이
# 글자까지 같아야 «판정은 맞았는데 답이 틀렸다» 가 성립한다
def test_the_prompt_numbers_evidence_the_same_way() -> None:
    prompt = answer_eval.build_support_prompt(
        "질문", "답변", [chunk("c1", "첫째"), chunk("c2", "둘째")]
    )
    assert "[1] (안내서.pdf)\n첫째" in prompt
    assert "[2] (안내서.pdf)\n둘째" in prompt


# ---------------------------------------------------------------------------
# 인용 정합성 — 무료이고 결정적이다
# ---------------------------------------------------------------------------


def item(query_id: str = "q1", *, answerable: bool = True, spans: list[str] | None = None) -> QAItem:
    return QAItem(
        query_id=query_id,
        query="질문",
        source_doc="안내서",
        gold_spans=spans if spans is not None else ["정답 문장"],
        answerable=answerable,
    )


# 인용한 청크가 정답을 담았나. **공백을 지우고 대조한다** — PDF 가 낱말 사이 공백을 잃는다
def test_citation_groundedness_counts_cited_chunks_holding_gold() -> None:
    chunks = [chunk("c1", "여기에정답 문장이있다"), chunk("c2", "무관한 본문")]
    scored = AnswerRow(
        query_id="q1", query="질문", source_doc="안내서", answerable=True,
        answer="근거에 따르면 [1] 과 [2] 입니다",
    )
    score_citations(scored, item(), chunks)

    assert scored.cited == [1, 2]
    assert scored.cited_count == 2
    assert scored.cited_gold_hits == 1


# **범위 밖 번호는 분모에서 뺀다** — 가리킬 청크가 없어 «정답을 담았나» 가 성립하지 않는다.
# 그 사실은 `citation_problem` 이 따로 싣는다
def test_out_of_range_numbers_leave_the_denominator() -> None:
    scored = AnswerRow(
        query_id="q1", query="질문", source_doc="안내서", answerable=True,
        answer="근거 [1] 과 [7] 입니다",
    )
    score_citations(scored, item(), [chunk("c1", "정답 문장")])

    assert scored.citation_problem == "out_of_range"
    assert scored.cited_count == 1
    assert scored.cited_gold_hits == 1


# **부정 예시는 분모가 없다.** 0으로 적으면 «인용을 다 틀렸다» 로 읽히는데, 그것은 이
# 질의에 대해 물을 수 없는 것이다
def test_negative_examples_have_no_citation_denominator() -> None:
    scored = AnswerRow(
        query_id="n1", query="질문", source_doc="안내서", answerable=False,
        answer="지어낸 답 [1]",
    )
    score_citations(scored, item("n1", answerable=False, spans=[]), [chunk("c1", "본문")])

    assert scored.cited_gold_hits is None
    assert scored.cited_count == 1


# 인용을 아예 안 한 답변은 `missing` 이다. 화면 배지와 같은 규칙을 표에서도 쓴다
def test_an_answer_without_citations_is_missing() -> None:
    scored = AnswerRow(
        query_id="q1", query="질문", source_doc="안내서", answerable=True,
        answer="번호 없이 그냥 답한다",
    )
    score_citations(scored, item(), [chunk("c1", "정답 문장")])

    assert scored.citation_problem == "missing"
    assert scored.cited_count == 0


# ---------------------------------------------------------------------------
# 인용 정합성의 대조군 — 무료·결정적이고, 그래서 채점자를 꺼도 산다
# ---------------------------------------------------------------------------


# **인용 안 한 청크도 세어진다.** 이것이 «근거에 애초에 정답이 몇 개 있었나» 라, 여기가
# 인용 적중과 갈리는 자리다 — 같아지면 대조군이 관측을 그대로 베껴 아무것도 안 잰다
def test_the_baseline_counts_gold_chunks_that_were_not_cited() -> None:
    chunks = [chunk("c1", "여기에정답 문장이있다"), chunk("c2", "여기에도 정답 문장"), chunk("c3", "무관")]
    scored = AnswerRow(
        query_id="q1", query="질문", source_doc="안내서", answerable=True,
        answer="근거 [1] 입니다",
    )
    score_citations(scored, item(), chunks)

    assert scored.cited_gold_hits == 1
    assert scored.evidence_gold_chunks == 2


# **부정 예시는 대조군도 못 만든다** — `cited_gold_hits` 와 같은 규칙이고 같은 이유다.
# 0으로 적으면 «근거에 정답이 하나도 없었다» 라는 **다른 뜻**이 된다
def test_negative_examples_have_no_baseline() -> None:
    scored = AnswerRow(
        query_id="n1", query="질문", source_doc="안내서", answerable=False,
        answer="지어낸 답 [1]",
    )
    score_citations(scored, item("n1", answerable=False, spans=[]), [chunk("c1", "본문")])

    assert scored.evidence_gold_chunks is None


# 무작위 = c·G/k 의 합 / c 의 합 · 최대 = min(c,G) 의 합 / c 의 합.
#
# **둘째 행이 `hits < min(c,G)` 여야 한다** — 정답 둘이 근거에 있는데 하나만 인용한 행이다.
# 두 수를 같게 두면 «최대» 를 «관측» 으로 갈아끼워도 테스트가 통과한다(변이로 확인했다).
def test_chance_and_ceiling_come_from_the_evidence() -> None:
    rows = [
        AnswerRow(
            query_id="q1", query="질문", source_doc="안내서", answerable=True, answer="답",
            chunk_ids=[f"a{i}" for i in range(5)],
            cited_count=2, cited_gold_hits=1, evidence_gold_chunks=1,
        ),
        AnswerRow(
            query_id="q2", query="질문", source_doc="안내서", answerable=True, answer="답",
            chunk_ids=[f"b{i}" for i in range(5)],
            cited_count=4, cited_gold_hits=1, evidence_gold_chunks=2,
        ),
    ]
    positive = summarize(rows, judge_on=False)["positive"]

    assert positive["citation_groundedness"] == pytest.approx(2 / 6)
    assert positive["citation_groundedness_chance"] == pytest.approx((2 * 1 / 5 + 4 * 2 / 5) / 6)
    assert positive["citation_groundedness_ceiling"] == pytest.approx((1 + 2) / 6)


# **최대는 관측 아래로 못 내려간다** — `min(c,G) >= hits` 가 늘 참이다.
# **반대쪽(무작위 <= 관측)은 일부러 안 잠근다** — 관측이 무작위보다 낮게 나올 수 있고
# 그것도 이 판이 얻으려는 답이다. 잠그면 자[尺]가 결론을 미리 정한다
def test_the_ceiling_never_falls_below_the_observation() -> None:
    rows = [
        AnswerRow(
            query_id="q1", query="질문", source_doc="안내서", answerable=True, answer="답",
            # 정답 셋이 근거에 있는데 하나만 인용했다 — 둘이 **갈리는** 자리여야 검사가 된다
            chunk_ids=["a", "b", "c"], cited_count=3, cited_gold_hits=1, evidence_gold_chunks=3,
        )
    ]
    positive = summarize(rows, judge_on=False)["positive"]

    assert positive["citation_groundedness_ceiling"] > positive["citation_groundedness"]


# **대조군 칸이 없는 행이 섞이면 둘 다 `None` 이다.** 그냥 건너뛰고 분모를 전체로 두면
# 분자만 작아져 «무작위가 낮다 = 생성이 잘 고른다» 라는 **틀린 결론이 공짜로** 나온다
def test_a_row_without_the_baseline_makes_both_none() -> None:
    rows = [
        AnswerRow(
            query_id="q1", query="질문", source_doc="안내서", answerable=True, answer="답",
            chunk_ids=["a", "b"], cited_count=1, cited_gold_hits=1, evidence_gold_chunks=1,
        ),
        AnswerRow(
            query_id="q2", query="질문", source_doc="안내서", answerable=True, answer="답",
            chunk_ids=["c", "d"], cited_count=1, cited_gold_hits=0, evidence_gold_chunks=None,
        ),
    ]
    positive = summarize(rows, judge_on=False)["positive"]

    assert positive["citation_groundedness"] is not None
    assert positive["citation_groundedness_chance"] is None
    assert positive["citation_groundedness_ceiling"] is None


# **채점자를 꺼도 대조군은 나온다** — 지지 칸이 `None` 이 되는 것과 갈리는 자리이고,
# 강등된 자[尺]에서 실제로 변별하는 축이 이것 하나라는 51회차 결론이 여기 서 있다
def test_the_baseline_survives_a_judgeless_run() -> None:
    rows = [
        AnswerRow(
            query_id="q1", query="질문", source_doc="안내서", answerable=True, answer="답",
            chunk_ids=["a", "b"], cited_count=1, cited_gold_hits=1, evidence_gold_chunks=1,
        )
    ]
    positive = summarize(rows, judge_on=False)["positive"]

    assert positive["supported"] is None
    assert positive["citation_groundedness_chance"] is not None


# 최대 대비 = `Σhits / Σmin(c,G)`. **비율의 나눗셈이 아니라 두 정수의 비다** —
# `usable` 일 때 분모가 `Σc` 로 같아서 약분된다. 그것이 이 칸을 만든 근거였다
def test_vs_ceiling_is_hits_over_the_ceiling_count() -> None:
    rows = [
        AnswerRow(
            query_id="q1", query="질문", source_doc="안내서", answerable=True, answer="답",
            chunk_ids=[f"a{i}" for i in range(5)],
            cited_count=2, cited_gold_hits=1, evidence_gold_chunks=1,
        ),
        AnswerRow(
            query_id="q2", query="질문", source_doc="안내서", answerable=True, answer="답",
            chunk_ids=[f"b{i}" for i in range(5)],
            cited_count=4, cited_gold_hits=1, evidence_gold_chunks=2,
        ),
    ]
    positive = summarize(rows, judge_on=False)["positive"]

    # `min(2,1) + min(4,2) = 3` 이고 적중은 `1 + 1 = 2` 다
    assert positive["cited_gold_ceiling"] == 3
    assert positive["citation_groundedness_vs_ceiling"] == pytest.approx(2 / 3)


# **이 칸을 조건 비교의 대표값으로 고른 근거를 잠근다.** 답변을 한 글자도 안 바꾸고 인용
# 개수만 늘리면 관측은 내려가는데(`top_k` 5→10 의 0.466→0.433 이 그 정체다) 최대 대비는
# 안 움직인다 — `c >= G` 인 행에서 `min(c,G) == G` 라 분모가 인용 개수와 무관해서다.
# **관측이 실제로 내려가는 것까지 같이 단언한다** — 안 그러면 «둘 다 안 움직인다» 로도 통과한다
def test_vs_ceiling_does_not_move_when_only_the_citation_count_grows() -> None:
    def rows(extra: int) -> list[AnswerRow]:
        return [
            AnswerRow(
                query_id="q1", query="질문", source_doc="안내서", answerable=True, answer="답",
                chunk_ids=[f"a{i}" for i in range(5)],
                cited_count=2 + extra, cited_gold_hits=1, evidence_gold_chunks=1,
            ),
            AnswerRow(
                query_id="q2", query="질문", source_doc="안내서", answerable=True, answer="답",
                chunk_ids=[f"b{i}" for i in range(5)],
                cited_count=4 + extra, cited_gold_hits=1, evidence_gold_chunks=2,
            ),
        ]

    lean = summarize(rows(0), judge_on=False)["positive"]
    padded = summarize(rows(2), judge_on=False)["positive"]

    assert padded["citation_groundedness"] < lean["citation_groundedness"]
    assert padded["citation_groundedness_vs_ceiling"] == pytest.approx(
        lean["citation_groundedness_vs_ceiling"]
    )


# **그래도 통과선이 아니다 — 속는 방향이 관측과 반대일 뿐이다.** 근거를 통째로 인용하면
# `hits == G == min(c,G)` 라 이 칸이 **1.0** 이 된다(관측은 반대로 `G/k` 까지 내려간다).
# 그래서 화면이 둘을 한 줄에 같이 찍고, 어느 쪽도 회계 게이트에 안 섞는다
def test_citing_the_whole_evidence_pins_vs_ceiling_at_one() -> None:
    rows = [
        AnswerRow(
            query_id="q1", query="질문", source_doc="안내서", answerable=True, answer="답",
            chunk_ids=["a", "b", "c"],
            cited_count=3, cited_gold_hits=2, evidence_gold_chunks=2,
        )
    ]
    positive = summarize(rows, judge_on=False)["positive"]

    assert positive["citation_groundedness_vs_ceiling"] == pytest.approx(1.0)
    assert positive["citation_groundedness"] == pytest.approx(2 / 3)


# **근거에 정답이 한 번도 안 오면 분모가 없다.** `0` 으로 내면 «인용을 다 틀렸다» 가 없던
# 데서 생기는데, 그것은 백필 자기 검증이 기권 행에서 실제로 잡았던 그 사고와 같은 모양이다.
# **관측·무작위·최대 셋은 그대로 나온다** — 저쪽 분모는 `Σc` 라 살아 있다
def test_vs_ceiling_is_none_when_no_gold_reached_the_evidence() -> None:
    rows = [
        AnswerRow(
            query_id="q1", query="질문", source_doc="안내서", answerable=True, answer="답",
            chunk_ids=["a", "b"], cited_count=2, cited_gold_hits=0, evidence_gold_chunks=0,
        )
    ]
    positive = summarize(rows, judge_on=False)["positive"]

    assert positive["citation_groundedness"] == pytest.approx(0.0)
    assert positive["citation_groundedness_ceiling"] == pytest.approx(0.0)
    assert positive["citation_groundedness_vs_ceiling"] is None


# **`None` 조건이 기존 대조군 둘과 같아야 한다** — 갈리면 셋 중 하나만 나오는 표가 생겨
# «어느 것이 못 잰 것인가» 가 안 읽힌다
def test_vs_ceiling_is_none_wherever_the_baseline_is() -> None:
    rows = [
        AnswerRow(
            query_id="q1", query="질문", source_doc="안내서", answerable=True, answer="답",
            chunk_ids=["a", "b"], cited_count=1, cited_gold_hits=1, evidence_gold_chunks=1,
        ),
        AnswerRow(
            query_id="q2", query="질문", source_doc="안내서", answerable=True, answer="답",
            chunk_ids=["c", "d"], cited_count=1, cited_gold_hits=0, evidence_gold_chunks=None,
        ),
    ]
    positive = summarize(rows, judge_on=False)["positive"]

    assert positive["citation_groundedness_ceiling"] is None
    assert positive["cited_gold_ceiling"] is None
    assert positive["citation_groundedness_vs_ceiling"] is None


# ---------------------------------------------------------------------------
# 회계 게이트 — 지표보다 먼저 읽는다
# ---------------------------------------------------------------------------


def judged_row(query_id: str, *, control: str | None = "unsupported") -> AnswerRow:
    return AnswerRow(
        query_id=query_id, query="q", source_doc="d", answerable=True,
        answer="답 [1]", chunk_ids=["c1"], support="supported",
        control=control, control_from="other",
    )


# 회계가 맞으면 통과다. 이 상태에서만 아래 지표를 실력으로 읽는다
def test_a_matching_ledger_passes() -> None:
    rows = [judged_row("a"), judged_row("b")]
    usage = JudgeUsage(api_calls=4, cache_hits=0)
    assert accounting_problems(rows, usage, judge_on=True, expected_generations=2) == []


# **호출 수가 모자라면 지표를 못 읽는다** — Cohere 한도 초과가 품질 저하로 읽힌 그 선례다.
# 지표 여덟 개가 전부 그럴듯하게 나빴고 가른 것은 호출 수 하나였다
def test_a_short_ledger_is_reported() -> None:
    rows = [judged_row("a"), judged_row("b")]
    problems = accounting_problems(rows, JudgeUsage(api_calls=2), judge_on=True, expected_generations=2)
    assert any("판정 회계" in p for p in problems)


# 캐시 히트도 회계의 한쪽 항이다. 히트만으로 채워도 통과여야 한다 — 실제로 그런 회차가 있었다
def test_cache_hits_count_toward_the_ledger() -> None:
    rows = [judged_row("a"), judged_row("b")]
    assert accounting_problems(rows, JudgeUsage(cache_hits=4), judge_on=True, expected_generations=2) == []


# **대조군이 통과하면 그 회차의 지지 수치는 못 쓴다.** 이것이 이 도구의 죽은 판정자 기준선이다
def test_a_leaking_control_invalidates_the_run() -> None:
    rows = [judged_row("a", control="supported"), judged_row("b")]
    problems = accounting_problems(rows, JudgeUsage(api_calls=4), judge_on=True, expected_generations=2)

    assert any("음성 대조군" in p and "못 씁니다" in p for p in problems)


# **생성 회계도 함께 본다.** 근거를 받은 질의 수와 실제 생성 수가 어긋나면 어떤 질의는
# 답변 없이 채점된 것인데, 그 행은 기권으로 세어져 «기권율이 높다» 로 조용히 읽힌다 —
# 부정 예시에서는 그것이 곧 이 도구의 첫 지표라 제일 나쁜 자리다
def test_a_generation_shortfall_is_reported() -> None:
    rows = [judged_row("a"), judged_row("b")]
    problems = accounting_problems(rows, JudgeUsage(api_calls=4), judge_on=True, expected_generations=1)
    assert any("생성 회계" in p for p in problems)


# 실패는 분모를 줄인다. 조용히 넘어가면 남은 것만으로 낸 비율이 전체인 척한다
def test_failures_are_surfaced() -> None:
    rows = [judged_row("a")]
    rows[0].support = "failed"
    problems = accounting_problems(rows, JudgeUsage(api_calls=2), judge_on=True, expected_generations=1)
    assert any("실패" in p for p in problems)


# 기권한 행은 판정 대상이 아니므로 회계 분모에서 빠진다
def test_abstained_rows_are_not_in_the_ledger() -> None:
    rows = [
        judged_row("a"),
        AnswerRow(
            query_id="b", query="q", source_doc="d", answerable=False,
            answer=NO_EVIDENCE_ANSWER, chunk_ids=["c9"], abstained=True,
        ),
    ]
    assert accounting_problems(rows, JudgeUsage(api_calls=2), judge_on=True, expected_generations=2) == []


# ---------------------------------------------------------------------------
# 리포트 이름 — 같은 라벨의 두 실행이 같은 자리를 다투면 에러 없이 덮인다
# ---------------------------------------------------------------------------


# **재작성 프로필이 이름에 들어가야 한다.** 안 들어가면 같은 라벨에 `--rewrite fuse` 로 한 번
# 더 돌린 회차가 본판 리포트를 **에러 없이 덮는다** — `verdicts.py` 가 13회차에 실제로 잃은
# 사고와 같은 모양이고, 재작성 판을 재는 회차는 그것을 반드시 밟는다
def test_the_rewrite_profile_splits_the_report() -> None:
    common = {
        "label": "L",
        "qa_set": "qa_set.jsonl",
        "judge": "mini",
        "rerank": "cohere",
        "generation": "prod",
        "top_k": 5,
        "fmt": "pdf",
    }

    assert answer_eval.report_name(rewrite="off", **common) != answer_eval.report_name(
        rewrite="fuse", **common
    )


# 채점자도 이름을 가른다 — 라벨이 같은 두 채점자의 표가 같은 자리를 다투면 안 된다
def test_the_judge_profile_splits_the_report() -> None:
    common = {
        "label": "L",
        "qa_set": "qa_set.jsonl",
        "rewrite": "off",
        "rerank": "cohere",
        "generation": "prod",
        "top_k": 5,
        "fmt": "pdf",
    }

    assert answer_eval.report_name(judge="mini", **common) != answer_eval.report_name(
        judge="luna", **common
    )


# **리랭커 프로필도 가른다.** 기본값이 `off` 라 `--rerank` 를 빠뜨린 실행이 조용히 다른
# 실험이 되는데, 이름까지 같으면 그 실행이 제대로 돌린 판을 **에러 없이 덮는다**
def test_the_rerank_profile_splits_the_report() -> None:
    common = {
        "label": "L",
        "qa_set": "qa_set.jsonl",
        "judge": "mini",
        "rewrite": "off",
        "generation": "prod",
        "top_k": 5,
        "fmt": "pdf",
    }

    assert answer_eval.report_name(rerank="off", **common) != answer_eval.report_name(
        rerank="cohere", **common
    )


# **생성 프로필도 가른다 — 이 축만 44회차에 생기고 이름이 안 따라왔다.** `--generation luna`
# 로 한 판 돌리면 짝으로 쓸 `prod` 판을 **에러 없이 덮는데**, 두 모델을 견주는 회차에서
# 그 짝이 판의 전부다. `generation` 을 기본값 없는 필수 인자로 둔 것도 같은 이유다 —
# 새 호출부가 빠뜨리면 조용히 같은 자리를 다투는 대신 `TypeError` 로 터진다
def test_the_generation_profile_splits_the_report() -> None:
    common = {
        "label": "L",
        "qa_set": "qa_set.jsonl",
        "judge": "mini",
        "rerank": "cohere",
        "rewrite": "off",
        "top_k": 5,
        "fmt": "pdf",
    }

    assert answer_eval.report_name(generation="prod", **common) != answer_eval.report_name(
        generation="luna", **common
    )


# **세트도 가른다 — `--qa-set` 은 기본이 사람 세트라 인자를 빠뜨린 실행이 다른 세트의 판을
# 같은 이름으로 낸다.** 43회차의 판 넷은 `qa_set.jsonl`(60건)을 명시해 돌린 것이라, 이름이
# 안 갈리면 43건 판과 60건 판이 **에러 없이 한 자리를 다툰다**. 값이 `Path(...).stem` 인 것은
# 새 세트가 생겨도 저절로 갈리게 하려는 것이다 — 별명표는 거기 없는 세트를 조용히 합친다
def test_the_qa_set_splits_the_report() -> None:
    common = {
        "label": "L",
        "judge": "mini",
        "rerank": "cohere",
        "rewrite": "off",
        "generation": "prod",
        "top_k": 5,
        "fmt": "pdf",
    }

    assert answer_eval.report_name(
        qa_set="evaluation/qa_set.jsonl", **common
    ) != answer_eval.report_name(qa_set="evaluation/qa_set_human.jsonl", **common)


# **경로가 달라도 같은 세트면 같은 이름이다** — meta 는 파일명만, CLI 는 경로를 넘길 수 있어
# 둘이 다른 이름을 내면 같은 판이 두 자리에 앉는다
def test_the_qa_set_path_does_not_change_the_name() -> None:
    common = {
        "label": "L",
        "judge": "mini",
        "rerank": "cohere",
        "rewrite": "off",
        "generation": "prod",
        "top_k": 5,
        "fmt": "pdf",
    }

    assert answer_eval.report_name(
        qa_set="qa_set.jsonl", **common
    ) == answer_eval.report_name(qa_set="evaluation/qa_set.jsonl", **common)


# **`top_k` 는 프로필이 아닌데도 실행을 가른다** — 생성이 보는 근거 수가 곧 그 값이라
# 5와 10은 다른 답변을 낳는다. 43회차가 같은 라벨로 두 짝을 돌리며 이 자리를 밟았다
def test_the_top_k_splits_the_report() -> None:
    common = {
        "label": "L",
        "qa_set": "qa_set.jsonl",
        "judge": "mini",
        "rerank": "cohere",
        "rewrite": "off",
        "generation": "prod",
        "fmt": "pdf",
    }

    assert answer_eval.report_name(top_k=5, **common) != answer_eval.report_name(
        top_k=10, **common
    )


# **프로필로도 안 갈리는 회차가 있다** — 같은 조건을 두 번 돌려 온도 잡음을 재는 자리다.
# 생성에 캐시가 없어 두 실행의 답변이 실제로 다른데, 이름이 같으면 뒤엣것이 앞엣것을 덮는다
def test_a_tag_splits_a_rerun_of_the_same_condition() -> None:
    common = {
        "label": "L",
        "qa_set": "qa_set.jsonl",
        "judge": "mini",
        "rewrite": "off",
        "rerank": "cohere",
        "generation": "prod",
        "top_k": 5,
        "fmt": "pdf",
    }

    assert answer_eval.report_name(**common) != answer_eval.report_name(tag="noise", **common)


# 꼬리표가 없으면 안 붙는다 — 이름에 빈 마디가 생기면 옛 실행과 대조하기 어려워진다
def test_an_empty_tag_leaves_no_trace() -> None:
    name = answer_eval.report_name(
        label="L",
        qa_set="qa_set.jsonl",
        judge="mini",
        rerank="cohere",
        rewrite="off",
        generation="prod",
        top_k=5,
        fmt="pdf",
    )

    assert name == "L-answers-qa_set-mini-cohere-off-prod-k5-pdf.json"

# ---------------------------------------------------------------------------
# 채점자를 끄는 축 — **«안 돌림» 과 «전건 실패» 가 갈려야 한다**
#
# `EVAL_JUDGES` 주석이 꺼짐 프로필을 오래 막아 둔 이유가 *"안 돌면 전건 실패인데 표는
# 멀쩡해 보인다"* 였다. 2026-08-29 에 기본을 `off` 로 내리며 그 우려를 갚기로 했고,
# 갚는 방법이 아래 둘이다 — **요약이 `None` 을 내는 것**과 **회계 게이트가 뒤집히는 것**.
# 이 절이 빨개지면 그 옛 우려가 되살아난 것이다
# ---------------------------------------------------------------------------


# 꺼짐 프로필은 채점자를 아예 안 만든다. **이것이 이 축의 유일한 스위치다**
def test_the_off_profile_builds_no_judge() -> None:
    assert get_judge(JudgeConfig(**EVAL_JUDGES["off"]), api_key="sk-test") is None


# 반대쪽도 못 박는다 — 스위치가 늘 꺼져 있으면 검사가 아무것도 안 잡는다
def test_a_real_profile_builds_a_judge(tmp_path) -> None:
    judge = get_judge(
        JudgeConfig(**EVAL_JUDGES["mini"]), api_key="sk-test", cache_dir=tmp_path / "j"
    )
    assert isinstance(judge, SupportJudge)


# **껐으면 지지 칸이 `0` 이 아니라 `None` 이다.** `0` 으로 내면 «전건 미지지» 로 읽히는데,
# 그것이 정확히 옛 주석이 걱정한 «표는 멀쩡해 보인다» 다
def test_a_judgeless_run_reports_none_not_zero() -> None:
    rows = [judged_row("a")]
    rows[0].support = None
    rows[0].control = None

    bucket = summarize(rows, judge_on=False)["positive"]
    assert bucket["supported"] is None
    assert bucket["unsupported"] is None
    assert bucket["failed"] is None
    # 인용 축은 채점자와 무관하게 그대로 산다 — 껐을 때 실제로 변별하는 것이 이쪽이다
    assert bucket["judged"] == 1


# 대조군 블록도 통째로 `None` 이다 — «0건 누출» 이 아니라 «물은 적이 없다» 가 맞다
def test_a_judgeless_run_has_no_control_block() -> None:
    assert summarize([judged_row("a")], judge_on=False)["shuffled_control"] is None


# **껐을 때는 게이트가 뒤집힌다** — «끈 줄 알았는데 절반 돌았다» 를 잡는 자리이고,
# `--judge off` 회차의 «판정 유료 0» 은 이 줄이 증명한다
def test_a_judgeless_run_must_not_have_called_the_judge() -> None:
    rows = [judged_row("a")]
    problems = accounting_problems(
        rows, JudgeUsage(api_calls=2), expected_generations=1, judge_on=False
    )
    assert any("판정을 껐는데 판정이 돌았습니다" in p for p in problems)


# 캐시 히트도 «돌았다» 로 센다. 무료여도 그 회차는 꺼진 회차가 아니다
def test_a_judgeless_run_counts_cache_hits_as_having_run() -> None:
    problems = accounting_problems(
        [judged_row("a")], JudgeUsage(cache_hits=1), expected_generations=1, judge_on=False
    )
    assert any("판정을 껐는데" in p for p in problems)


# 호출이 정말 0이면 통과다. 이 상태에서만 인용 정합성을 실력으로 읽는다
def test_a_judgeless_run_with_no_calls_passes() -> None:
    rows = [judged_row("a")]
    rows[0].support = None
    rows[0].control = None
    assert (
        accounting_problems(rows, JudgeUsage(), expected_generations=1, judge_on=False) == []
    )


# **미지지는 회차를 무효화하지 않는다.** 40회차의 «무효화 vs 분모 주석» 구분이고, 2026-08-29
# 의 실물 거짓 음성이 그 구분에 값을 매겼다 — 통과선이었으면 멀쩡한 회차가 실패로 남았다
def test_an_unsupported_verdict_does_not_invalidate_the_run() -> None:
    rows = [judged_row("a"), judged_row("b")]
    rows[0].support = "unsupported"
    problems = accounting_problems(
        rows, JudgeUsage(api_calls=4), expected_generations=2, judge_on=True
    )
    assert problems == []


# 채점자 이름이 리포트를 가른다 — 껐던 회차가 켰던 회차를 덮으면 안 된다
def test_the_off_judge_splits_the_report() -> None:
    common = {
        "label": "L",
        "qa_set": "qa_set.jsonl",
        "rewrite": "off",
        "rerank": "cohere",
        "generation": "prod",
        "top_k": 5,
        "fmt": "pdf",
    }

    assert answer_eval.report_name(judge="off", **common) != answer_eval.report_name(
        judge="mini", **common
    )


# ---------------------------------------------------------------------------
# 생성 프롬프트 판 — 리포트가 자기 생성 조건을 말할 수 있어야 한다
# ---------------------------------------------------------------------------


# **`generation_profile` 만으로는 안 잡힌다** — `SYSTEM_PROMPT` 를 고쳐도 이름은 `prod` 다.
# 두 리포트를 나중에 견줄 때 *생성 프롬프트가 달랐다* 를 되짚을 자리가 이 칸 하나다
def test_the_generation_prompt_has_a_version() -> None:
    from rag_pipeline.generation import prompts

    assert prompts.GENERATION_PROMPT_VERSION
    assert "GENERATION_PROMPT_VERSION" in prompts.__all__


# ---------------------------------------------------------------------------
# 백필 — 저장된 리포트에 대조군을 소급한다. 자기 검증이 통과할 때만 쓴다
# ---------------------------------------------------------------------------


# 컬렉션 흉내. `iter_chunks()` 로 텍스트만 읽으므로 임베더가 필요 없다
class _FakeStore:
    def __init__(self, chunks: list[StoredChunk]) -> None:
        self._chunks = chunks
        self.closed = False

    def count(self) -> int:
        return len(self._chunks)

    def iter_chunks(self):
        return iter(self._chunks)

    def close(self) -> None:
        self.closed = True


def stored(chunk_id: str, text: str) -> StoredChunk:
    return StoredChunk(id=chunk_id, chunk_id=chunk_id, document_id="안내서.pdf", text=text)


def _report(rows: list[dict]) -> dict:
    return {
        "meta": {
            "label": "L",
            "format": "pdf",
            "qa_set": "qa_set_human.jsonl",
            "embedding_profile": "baseline",
            "judge_profile": "off",
        },
        "summary": {},
        "rows": rows,
    }


def _wire(mocker, chunks: list[StoredChunk], items: list[QAItem]):
    store = _FakeStore(chunks)
    mocker.patch.object(
        answer_eval, "load_report", return_value={"indexed": [{"chunks": len(chunks)}]}
    )
    mocker.patch.object(answer_eval, "require_fresh_store")
    mocker.patch.object(answer_eval, "eval_config", return_value=mocker.Mock())
    mocker.patch("rag_pipeline.ingestion.indexing.get_store", return_value=store)
    mocker.patch.object(answer_eval, "load_qa_set", return_value=QASet(items=items, path=None))
    return store


def _row(**over) -> dict:
    base = {
        "query_id": "q1", "query": "질문", "source_doc": "안내서", "answerable": True,
        "answer": "근거 [1] 입니다", "chunk_ids": ["c1", "c2"], "rewritten": None,
        "abstained": False, "citation_problem": None, "cited": [1], "cited_gold_hits": 1,
        "cited_count": 1, "support": None, "support_reasoning": "",
        "unsupported_sentences": [], "control_from": None, "control": None, "warnings": [],
    }
    base.update(over)
    return base


# **기권 행은 `score_answers()` 가 인용 칸을 아예 안 계산한다.** 백필이 그것을 모르고 다시
# 돌리면 `None` 이 `0` 이 되어 «인용을 다 틀렸다» 가 없던 데서 생긴다 — 실제로 첫 판이
# 자기 검증에 걸려 잡혔다. **그 자리를 여기서 잠근다**
def test_backfill_skips_abstained_rows_like_the_run_did(tmp_path, mocker) -> None:
    rows = [
        _row(),
        _row(query_id="q2", answer=NO_EVIDENCE_ANSWER, abstained=True, cited=[],
             cited_count=0, cited_gold_hits=None),
    ]
    path = tmp_path / "r.json"
    path.write_text(json.dumps(_report(rows), ensure_ascii=False), encoding="utf-8")
    _wire(mocker, [stored("c1", "정답 문장"), stored("c2", "무관")], [item("q1"), item("q2")])

    assert answer_eval.backfill_baseline(path, write=True) is True
    written = json.loads(path.read_text(encoding="utf-8"))["rows"]
    assert written[0]["evidence_gold_chunks"] == 1
    # 기권 행은 손대지 않는다 — 그 자리는 계산된 적이 없다
    assert written[1]["evidence_gold_chunks"] is None


# **근거를 하나라도 못 찾으면 안 쓴다.** 일부만 찾은 채 계산하면 `k` 가 줄어 무작위 기대값이
# 위로 뜨고, 그러면 «생성이 무작위와 다를 게 없다» 라는 반대쪽 거짓 결론이 나온다
def test_backfill_refuses_when_a_chunk_is_missing(tmp_path, mocker) -> None:
    path = tmp_path / "r.json"
    path.write_text(json.dumps(_report([_row()]), ensure_ascii=False), encoding="utf-8")
    _wire(mocker, [stored("c1", "정답 문장")], [item("q1")])

    assert answer_eval.backfill_baseline(path, write=True) is False
    assert "evidence_gold_chunks" not in json.loads(path.read_text(encoding="utf-8"))["rows"][0]


# **재계산이 저장된 값과 어긋나면 안 쓴다** — 되살린 근거가 그때의 근거가 아니라는 뜻이라,
# 그 위에서 만든 대조군은 무엇을 재는지 모르는 수가 된다
def test_backfill_refuses_when_the_recount_disagrees(tmp_path, mocker) -> None:
    path = tmp_path / "r.json"
    path.write_text(json.dumps(_report([_row()]), ensure_ascii=False), encoding="utf-8")
    # 저장된 행은 적중 1인데 컬렉션의 글에는 정답이 없다
    _wire(mocker, [stored("c1", "무관"), stored("c2", "무관")], [item("q1")])

    assert answer_eval.backfill_baseline(path, write=True) is False
    assert "evidence_gold_chunks" not in json.loads(path.read_text(encoding="utf-8"))["rows"][0]


# 연 쪽이 닫는다 — qdrant 로컬 파일 모드는 배타 잠금이라, 안 닫으면 다음 백필이 죽는다
def test_backfill_closes_the_store(tmp_path, mocker) -> None:
    path = tmp_path / "r.json"
    path.write_text(json.dumps(_report([_row()]), ensure_ascii=False), encoding="utf-8")
    store = _wire(mocker, [stored("c1", "정답 문장"), stored("c2", "무관")], [item("q1")])

    answer_eval.backfill_baseline(path, write=False)

    assert store.closed


# **찍기만 하는 것이 기본이다** — 유료로 산 원자료를 건드리는 자리라 손잡이를 하나 더 뒀다
def test_backfill_writes_nothing_without_the_flag(tmp_path, mocker) -> None:
    path = tmp_path / "r.json"
    path.write_text(json.dumps(_report([_row()]), ensure_ascii=False), encoding="utf-8")
    _wire(mocker, [stored("c1", "정답 문장"), stored("c2", "무관")], [item("q1")])

    assert answer_eval.backfill_baseline(path, write=False) is True
    assert "evidence_gold_chunks" not in json.loads(path.read_text(encoding="utf-8"))["rows"][0]


# **옛 요약 칸이 움직여도 안 쓴다.** 대조군을 «더하는» 판이라, 이미 산 수치가 바뀌면
# 그것은 더하기가 아니라 고쳐 쓰기다
def test_backfill_refuses_when_an_old_summary_cell_moves(tmp_path, mocker) -> None:
    payload = _report([_row()])
    payload["summary"] = {"positive": {"queries": 99}}
    path = tmp_path / "r.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    _wire(mocker, [stored("c1", "정답 문장"), stored("c2", "무관")], [item("q1")])

    assert answer_eval.backfill_baseline(path, write=True) is False
# 필수 사실 라벨이 없는 질의는 네 칸이 **`None` 이지 `0` 이 아니다.** `0` 으로 적으면
# «다 틀렸다» 로 읽히는데 그것은 «아직 라벨을 안 달았다» 와 전혀 다른 뜻이다
def test_must_columns_stay_none_without_labels() -> None:
    scored = AnswerRow(
        query_id="q1", query="질문", source_doc="안내서", answerable=True, answer="아무 답",
    )
    score_must(scored, _item())

    assert scored.must_values_total is None
    assert scored.must_values_hit is None
    assert scored.must_point_tokens_total is None


# 값은 답변 본문과 대조한다. 공백은 `squash()` 가 지운다 — PDF 가 한국어 낱말 사이 공백을
# 잃는 것과 같은 이유이고, 덤으로 `2020. 10. 1.` 이 `2020.10.1.` 과 같아진다
def test_must_values_are_matched_against_the_answer() -> None:
    scored = AnswerRow(
        query_id="q1", query="질문", source_doc="안내서", answerable=True,
        answer="제3조제12호에 따른 행정정보시스템을 말합니다.",
    )
    score_must(scored, _item(must_values=["제3조제12호", "별표1"]))

    assert (scored.must_values_hit, scored.must_values_total) == (1, 2)


# **이 테스트는 실측이 만들었다.** 날짜 정규화 없이 저장된 답변 두 판을 소급했더니 값 6개 중
# `2020.10.1.` 하나가 **두 판 모두** 거짓 음성이었다 — 두 답변 다 `2020년 10월 1일` 로
# 그 날짜를 정확히 말한다. 되돌리면 그 거짓 음성이 그대로 돌아온다
def test_a_date_written_the_other_way_still_counts() -> None:
    scored = AnswerRow(
        query_id="q1", query="질문", source_doc="안내서", answerable=True,
        answer="공포 후 6개월의 유예기간을 거쳐 2020년 10월 1일부터 적용됩니다.",
    )
    score_must(scored, _item(must_values=["2020.10.1.", "6개월"]))

    assert (scored.must_values_hit, scored.must_values_total) == (2, 2)


# **단위 변형은 일부러 안 맞춘다.** 단위는 열거가 안 끝나(교·명·건·부…) «어디까지 같은 값으로
# 볼까» 가 곧 임계값이 된다 — 날짜와 성질이 다르다. 「비대칭이니 통일하자」로 지우면
# 근거 없는 정규화가 생긴다
def test_a_unit_variant_is_deliberately_not_normalized() -> None:
    scored = AnswerRow(
        query_id="q1", query="질문", source_doc="안내서", answerable=True,
        answer="전체 중학교 206개가 대상입니다.",
    )
    score_must(scored, _item(must_values=["206개교"]))

    assert (scored.must_values_hit, scored.must_values_total) == (0, 1)


# 어휘 겹침은 **조사를 벗기고** 센다. 안 벗기면 `학교장은` 이 `학교장이` 를 못 물어
# 탐침이 재는 것이 사실이 아니라 활용형이 된다
def test_point_tokens_ignore_the_particle() -> None:
    scored = AnswerRow(
        query_id="q1", query="질문", source_doc="안내서", answerable=True,
        answer="학교장이 업무분장으로 기록물관리책임자를 정합니다.",
    )
    score_must(scored, _item(must_points=["학교장은 기록물관리책임자를 지정한다"]))

    assert scored.must_point_tokens_hit >= 2


# 라벨이 하나도 없으면 블록이 통째로 `None` 이다 — `judge_on=False` 가 지지 칸을 `None` 로
# 내는 것과 같은 규약이고, 빈 dict 로 내면 «전건 미달» 로 읽힌다
def test_must_block_is_none_when_nothing_is_labeled() -> None:
    rows = [
        AnswerRow(
            query_id="q1", query="질문", source_doc="안내서", answerable=True, answer="답",
            chunk_ids=["a"], cited_count=1, cited_gold_hits=1, evidence_gold_chunks=1,
        )
    ]

    assert summarize(rows, judge_on=False)["positive"]["must"] is None


# **조건부 칸이 이 블록의 요점이다.** 근거에 정답 청크가 안 왔으면 답변이 그 사실을 담을
# 방법이 없어, 조건 없이 세면 «생성이 담았나» 가 아니라 «검색이 물어왔나 × 생성이 담았나» 가
# 된다. 그 행을 빼면 남는 것이 생성 쪽이다
def test_the_conditioned_ratio_drops_rows_whose_evidence_missed_the_gold() -> None:
    rows = [
        AnswerRow(
            query_id="q1", query="질문", source_doc="안내서", answerable=True, answer="답",
            chunk_ids=["a"], evidence_gold_chunks=1,
            must_values_total=1, must_values_hit=1,
            must_point_tokens_total=2, must_point_tokens_hit=2,
        ),
        AnswerRow(
            query_id="q2", query="질문", source_doc="안내서", answerable=True, answer="답",
            chunk_ids=["b"], evidence_gold_chunks=0,
            must_values_total=1, must_values_hit=0,
            must_point_tokens_total=2, must_point_tokens_hit=0,
        ),
    ]
    block = summarize(rows, judge_on=False)["positive"]["must"]

    assert block["labeled"] == 2
    assert block["values_recall"] == pytest.approx(0.5)
    assert block["evidence_had_gold"] == 1
    assert block["values_recall_given_evidence"] == pytest.approx(1.0)




# 라벨이 없으면 두 칸이 **`None` 이지 `[]` 가 아니다.** 빈 목록은 «봤는데 후보가 없다» 라는
# 다른 뜻이고, 접으면 리포트가 «라벨을 안 달았다» 와 그것을 못 가른다
def test_must_not_claim_columns_stay_none_without_labels() -> None:
    scored = _claim_row("무엇이든 답합니다.")
    score_must_not_claim(scored, _item())

    assert scored.must_not_claim_total is None
    assert scored.must_not_claim_candidates is None


# 금지 낱말이 든 문장을 후보로 올린다. **낱말 하나만 겹쳐도 올린다** — 어휘 대조를
# 재현율 쪽으로만 쓰기로 한 결정이라, 정밀도는 사람이 읽어서 낸다
def test_a_sentence_carrying_the_word_becomes_a_candidate() -> None:
    scored = _claim_row("사전에 안전성 검증을 받아야 합니다. 위험 평가도 필요합니다.")
    score_must_not_claim(scored, _item(must_not_claim=["외부 안전성 검증 절차"]))

    assert scored.must_not_claim_total == 1
    assert [hit["sentence"] for hit in scored.must_not_claim_candidates] == [
        "사전에 안전성 검증을 받아야 합니다."
    ]


# **부정문도 후보로 남는다 — 일부러 그렇게 뒀다.**
#
# 실측(2026-08-29, 저장된 답변 두 판)에서 금지어가 나온 문 넷 중 **둘이 부정문**이었고
# 그 둘은 전제가 틀린 질문을 바로잡은 **제일 좋은 답변**이다. 그래서 이 자[尺]는 세지 않고
# 후보만 낸다. 여기서 부정문을 걸러내기 시작하면 «어디까지가 부정문인가» 가 곧 임계값이 되고
# (단위 정규화를 기각한 그 모양이다), 지금 표본은 문 넷뿐이라 그 임계값을 검증할 데이터가 없다
def test_a_denial_is_still_a_candidate_because_the_reader_decides() -> None:
    scored = _claim_row("다만, 매년 지정해야 한다는 내용은 문서에 없습니다.")
    score_must_not_claim(scored, _item(must_not_claim=["매년 지정 주기"]))

    assert len(scored.must_not_claim_candidates) == 1


# 한 문장이 금지 항목의 낱말 여럿에 걸려도 **한 줄이다** — 사람이 읽을 목록이라,
# 낱말마다 되풀이하면 같은 문장이 여러 번 떠서 목록이 못 읽을 것이 된다
def test_one_sentence_is_listed_once_per_entry() -> None:
    scored = _claim_row("구체적 진단 도구 이름을 적습니다.")
    score_must_not_claim(scored, _item(must_not_claim=["구체적 진단 도구 이름"]))

    assert len(scored.must_not_claim_candidates) == 1


# **걸린 낱말을 전부 싣는다 — 이 테스트는 실측이 만들었다.**
#
# 저장된 답변 두 판에 소급했더니 `hai-05` 의 「설명 대상을 국민으로」가 «설명» 하나로만 찍혔다.
# 그 낱말은 그 답변의 거의 모든 문장에 있어 두 판의 목록이 똑같아 보였고, 정작 가르는 낱말
# («국민» — 근거는 「소비자 또는 고객」이라고 적는다)이 목록에서 안 보였다. 첫 낱말만 실으면
# **읽는 사람이 어느 문장을 봐야 하는지 못 본다** — 사람이 읽는 것이 이 칸의 계약이라 치명적이다
def test_every_matching_word_is_listed_not_just_the_first() -> None:
    scored = _claim_row("국민이 설명을 요청할 수 있습니다.")
    score_must_not_claim(scored, _item(must_not_claim=["설명 대상을 국민으로"]))

    assert scored.must_not_claim_candidates[0]["tokens"].split() == ["설명", "국민"]


# **블록에 비[比]가 한 칸도 없어야 한다.** 후보에는 부정문이 섞여 있어 어떤 분수를 내도
# «오류율» 로 읽히고, 그러면 제일 좋은 답변이 분자에 앉는다
def test_the_block_carries_no_ratio() -> None:
    scored = _claim_row("사전에 안전성 검증을 받아야 합니다.")
    score_must_not_claim(scored, _item(must_not_claim=["외부 안전성 검증 절차"]))

    block = summarize([scored], judge_on=False)["positive"]["must_not_claim"]

    assert set(block) == {"labeled", "entries", "candidates", "no_candidate_queries"}
    assert not any(isinstance(value, float) for value in block.values())


# **라벨은 있는데 후보가 0인 질의를 목록에 남긴다.** 안 남기면 그 질의가 리포트에서 아예
# 사라지는데, 그것이 «답변이 안 건드렸다» 인지 «어휘로 못 잡는 종류» 인지는 사람만 안다
def test_a_labeled_row_without_candidates_is_still_named() -> None:
    scored = _claim_row("전혀 다른 이야기를 합니다.")
    score_must_not_claim(scored, _item(must_not_claim=["매년 지정 주기"]))

    block = summarize([scored], judge_on=False)["positive"]["must_not_claim"]

    assert block["labeled"] == 1
    assert block["candidates"] == []
    assert block["no_candidate_queries"] == ["q1"]


# 라벨이 하나도 없으면 블록이 통째로 `None` 이다 — 필수 사실 블록과 같은 규약
def test_the_block_is_none_when_nothing_is_labeled() -> None:
    scored = _claim_row("아무 답")

    assert summarize([scored], judge_on=False)["positive"]["must_not_claim"] is None


# 위 테스트들이 쓰는 최소 행
def _claim_row(answer: str) -> AnswerRow:
    return AnswerRow(
        query_id="q1", query="질문", source_doc="안내서", answerable=True, answer=answer,
    )

# 위 테스트들이 쓰는 최소 질의. 라벨만 갈아 끼운다
def _item(
    *,
    must_values: list[str] | None = None,
    must_points: list[str] | None = None,
    must_not_claim: list[str] | None = None,
) -> QAItem:
    return QAItem(
        query_id="q1", query="질문", source_doc="안내서", gold_spans=["어떤 정답"],
        must_values=list(must_values or []), must_points=list(must_points or []),
        must_not_claim=list(must_not_claim or []),
    )
