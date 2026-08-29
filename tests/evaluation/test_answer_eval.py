"""답변 채점 도구가 **자기 자신을 못 믿게 되는 자리들**을 잠근다.

이 도구의 위험은 «수치가 나쁘다» 가 아니라 **«틀린 수치가 그럴듯하다»** 다. 그래서 여기서
재는 것은 대부분 지표가 아니라 **지표를 못 읽게 만드는 조건**이다 — 실패를 판정으로 접었나,
정상 경로를 미지지로 셌나, 대조군이 자기 근거를 물었나, 캐시가 본판과 대조군을 섞었나.

키 없이 전부 돈다. 외부 호출은 `_make_client()` 이음새 하나로 갈아끼운다.
"""

from __future__ import annotations

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
    summarize,
)
from evaluation.config import EVAL_JUDGES
from evaluation.qa_set import QAItem
from rag_pipeline.generation.prompts import NO_EVIDENCE_ANSWER
from rag_pipeline.schemas.retrieval import RetrievedChunk


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
        self.usage = type("U", (), {"total_tokens": tokens})()


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
