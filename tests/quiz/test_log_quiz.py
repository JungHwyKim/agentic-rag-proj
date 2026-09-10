"""퀴즈 기록기가 「어겨도 에러가 안 나는」 규칙 셋을 실제로 강제하는지 본다.

이 셋은 손으로 JSON 을 쓰면 전부 조용히 어겨진다 — 그래서 도구가 지키고, 그 지킴을 여기서
잠근다. 지키지 않으면 `data/quiz_log.jsonl` 이 **에러 없이 거짓**이 된다.
"""

from __future__ import annotations

import json

import pytest

from quiz import log as L
from quiz.collect import Question, QuizConfigError

QUESTIONS = [
    Question(
        id="abc12345",
        file="03-검색",
        doc_title="03. 검색",
        unit="6. 평가 세트",
        starred=True,
        question="검색 품질을 어떻게 측정하셨나요?",
        followup_count=1,
    )
]


# 한 줄을 만든다. 지적이 없어도 칸이 빈 목록으로 실린다
def row(**overrides):
    kwargs = {
        "question_id": "abc12345",
        "mode": "speak",
        "questions": QUESTIONS,
        "missed": [],
        "wrong": [],
        "overclaim": [],
        "style": [],
        "followups_asked": 0,
    }
    kwargs.update(overrides)
    return L.build_row(**kwargs)


# 문항 메타는 인자로 안 받고 노트에서 찾아 채운다. 옮겨 적으면 두 번째 진실이 된다
def test_metadata_comes_from_the_note():
    written = row()
    assert written["file"] == "03-검색"
    assert written["starred"] is True
    assert written["question"] == "검색 품질을 어떻게 측정하셨나요?"


# **지적이 없어도 칸을 쓴다.** 빼면 «지적이 없었다» 와 «옛 형식이라 칸이 없다» 가 안 갈린다
def test_empty_findings_are_written_as_empty_lists():
    written = row()
    for key in ("missed", "wrong", "overclaim", "style"):
        assert written[key] == [], key


# **쓰기 모드의 `style` 은 `[]` 가 아니라 `null` 이다.** 빈 목록은 «봤는데 없었다» 이고
# null 은 «안 봤다» 라 뜻이 다르다 — 섞이면 쓰기로만 연습한 회차가 «말투는 문제없었다» 가 된다
def test_write_mode_records_style_as_null_not_empty():
    assert row(mode="write")["style"] is None
    assert row(mode="speak")["style"] == []


# 쓰기 모드에 말투 지적을 주면 거절한다. 받아 주면 위 구분이 그 줄에서 무너진다
def test_write_mode_refuses_style_findings():
    with pytest.raises(QuizConfigError) as caught:
        row(mode="write", style=["문장이 깁니다"])
    assert "쓰기 모드" in str(caught.value)


# 모르는 모드는 죽는다. 조용히 통과하면 그 줄의 `style` 을 아무도 못 읽는다
def test_unknown_mode_dies():
    with pytest.raises(QuizConfigError):
        row(mode="말하기")


# 노트에 없는 id 는 죽는다. 오타 하나가 되짚을 수 없는 줄을 만든다
def test_unknown_id_dies_unless_it_is_a_repo_question():
    with pytest.raises(QuizConfigError) as caught:
        row(question_id="ffffffff")
    assert "--kind repo" in str(caught.value)

    written = row(question_id="ffffffff", kind="repo")
    assert written["kind"] == "repo"
    assert written["file"] is None


# 판정을 안 싣는다. `retry` 같은 칸이 곧 점수이고, 「다시 볼 것」은 읽을 때 파생한다
def test_no_verdict_column_is_written():
    written = row(missed=["빠뜨림"])
    assert set(written) == {
        "ts",
        "id",
        "file",
        "starred",
        "question",
        "kind",
        "mode",
        "missed",
        "wrong",
        "overclaim",
        "style",
        "followups_asked",
    }


# 한 줄씩 덧붙이고, 폴더가 없으면 만든다
def test_append_writes_one_json_line(tmp_path):
    path = tmp_path / "data" / "quiz_log.jsonl"
    L.append_row(row(), path)
    L.append_row(row(mode="write"), path)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["mode"] == "speak"
    assert json.loads(lines[1])["style"] is None


# CLI 는 `--mode` 와 `--id` 에 기본값이 없다. 빠뜨리면 조용히 잘못 적히는 대신 죽는다
@pytest.mark.parametrize("argv", [["--id", "abc12345"], ["--mode", "speak"]])
def test_cli_requires_id_and_mode(argv):
    with pytest.raises(SystemExit):
        L.main(argv)
