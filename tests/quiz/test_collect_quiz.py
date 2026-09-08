"""퀴즈 수집기가 노트의 형태를 안 놓치는지 본다.

**실노트를 안 쓴다.** 재료가 저장소 밖(`~/Documents/면접준비/`)이라 다른 머신에도 CI 에도
있다는 보장이 없고, **이 기능을 설계하는 40분 사이에 실제로 파일 이름 일곱 개가 바뀌었다.**
그래서 합성 마크다운을 `tmp_path` 에 써서 규칙을 하나씩 고립시킨다.

여기 잠근 것 대부분은 **첫 구현이 실제로 틀렸던 자리**다 — 부록의 다른 제목 형태를 못
알아봐 문항 셋과 꼬리질문 일곱을 통째로 버렸고, `### 출처` 를 답 본문에 삼켰다.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from quiz import collect as C

# 생성 md 가 실제로 쓰는 서식. 질문은 `####` 이고 꼬리질문은 줄머리에 앵커가 있다
GENERATED = """# 03. 검색

**단위 6 ~ 11**

## 6. 평가 세트

무엇을 골랐는지 설명하는 산문이다.

### 예상 질문과 답

#### ★ Q. 검색 품질을 어떻게 측정하셨나요?

구현보다 평가 세트를 먼저 만들었습니다.

두 번째 문단입니다.

*꼬리질문* **"라벨이 틀렸으면 어떻게 되나요?"**

실제로 그 일이 있었습니다.

#### Q. 리랭커는 왜 켰나요?

후보 순서를 다시 매기려고 켰습니다.

### 출처

- `rag_pipeline/retrieval/`
"""

# 손으로 쓴 파일이 쓰는 부록 서식. **`Q.` 가 없고 `##` 레벨이다**
APPENDIX = """# 01. 문서 읽기

## 1. loader

### 예상 질문과 답

#### Q. 왜 전부 병렬로 안 했나요? → 부록 A-2

# 부록 A. loader 심화 3문

## A-2. 그럼 전부 병렬로 돌리지 그랬나요

유료 호출은 직렬로 남겼습니다.

*꼬리질문* **"원자적 연산을 쓰면 되지 않나요?"**

그것만으로는 상한 검사가 안 지켜집니다.
"""


# 합성 노트 폴더를 만든다. HTML 은 md 보다 오래된 것으로 둬서 낡음 검사가 안 걸리게 한다
def make_note(tmp_path: Path, files: dict[str, str], *, html: bool = True) -> Path:
    directory = tmp_path / "면접준비"
    directory.mkdir(parents=True)
    for name, body in files.items():
        (directory / name).write_text(body, encoding="utf-8")
    if html:
        note = directory / C.HTML_NAME
        note.write_text("<html></html>", encoding="utf-8")
        os.utime(note, (1_000_000, 1_000_000))
        for name in files:
            os.utime(directory / name, (2_000_000, 2_000_000))
    return directory


# 생성 md 의 기본 서식에서 질문·★·답·꼬리질문이 전부 나온다
def test_generated_format(tmp_path):
    result = C.collect(make_note(tmp_path, {"03-검색.md": GENERATED}))
    assert result["warnings"] == []
    assert result["counts"] == {"questions": 2, "starred": 1, "followups": 1}

    first, second = result["questions"]
    assert first.starred is True
    assert first.question == "검색 품질을 어떻게 측정하셨나요?"
    assert first.unit == "6. 평가 세트"
    assert first.doc_title == "03. 검색"
    assert "두 번째 문단입니다." in first.answer
    assert second.starred is False


# **꼬리질문이 자기 질문에 붙어야 한다.** 다음 질문으로 새면 채점이 남의 답을 본다
def test_followup_belongs_to_its_own_question(tmp_path):
    result = C.collect(make_note(tmp_path, {"03-검색.md": GENERATED}))
    first, second = result["questions"]
    assert [f.question for f in first.followups] == ["라벨이 틀렸으면 어떻게 되나요?"]
    assert first.followups[0].answer == "실제로 그 일이 있었습니다."
    assert second.followups == []


# **`### 출처` 가 답 본문에 안 섞여야 한다.** 섞이면 채점이 파일 목록을 모범답안으로 읽는다
def test_source_section_does_not_leak_into_the_answer(tmp_path):
    result = C.collect(make_note(tmp_path, {"03-검색.md": GENERATED}))
    last = result["questions"][-1]
    assert "출처" not in last.answer
    assert "rag_pipeline" not in last.answer


# **부록은 `Q.` 없이 `## A-2.` 로 온다.** 첫 구현이 이걸 단원 제목으로 읽어
# 문항 셋과 꼬리질문 일곱을 통째로 버렸고, 그때 아무 에러도 안 났다
def test_appendix_headings_become_questions(tmp_path):
    result = C.collect(make_note(tmp_path, {"01-문서읽기.md": APPENDIX}))
    assert result["warnings"] == []
    assert len(result["questions"]) == 1

    item = result["questions"][0]
    assert item.label == "A-2"
    assert item.question == "그럼 전부 병렬로 돌리지 그랬나요"
    assert item.unit == "부록 A. loader 심화 3문"
    assert [f.question for f in item.followups] == ["원자적 연산을 쓰면 되지 않나요?"]


# **`→ 부록 A-2` 는 질문이 아니라 이정표다.** 문항으로 세면 답이 빈 문항이 생긴다
def test_pointer_line_is_not_a_question(tmp_path):
    result = C.collect(make_note(tmp_path, {"01-문서읽기.md": APPENDIX}))
    assert all("→" not in q.question for q in result["questions"])


# 이정표가 가리키는 부록이 없으면 경고한다. 조용히 버리면 문항 하나가 이유 없이 사라진다
def test_pointer_without_its_appendix_warns(tmp_path):
    body = APPENDIX.replace("## A-2. 그럼 전부 병렬로 돌리지 그랬나요", "## 다른 이야기")
    result = C.collect(make_note(tmp_path, {"01-문서읽기.md": body}))
    assert any("부록 A-2" in w for w in result["warnings"])


# **못 알아본 질문 제목은 답 본문에 섞여 경고로 뜬다.** 이것이 새 질문 형태가 생겼을 때의
# 유일한 신호다 — 전부 끊게 만들면 이 검사가 죽고 답이 통째로 사라져도 조용하다
def test_unknown_question_heading_is_caught(tmp_path):
    body = GENERATED.replace("#### Q. 리랭커는 왜 켰나요?", "#### 물음. 리랭커는 왜 켰나요")
    result = C.collect(make_note(tmp_path, {"03-검색.md": body}))
    assert any("못 알아본 제목" in w for w in result["warnings"])


# `#### A-9.` 같은 새 부록 라벨은 **못 알아본 것이 아니라 부록**이다. 레벨을 안 고정한 것이
# 여기서 값을 한다 — 생성기가 부록을 `####` 로 옮겨도 문항이 안 사라진다
def test_appendix_label_at_question_level_is_still_an_appendix(tmp_path):
    body = GENERATED.replace("#### Q. 리랭커는 왜 켰나요?", "#### A-9. 리랭커는 왜 켰나요")
    result = C.collect(make_note(tmp_path, {"03-검색.md": body}))
    assert result["warnings"] == []
    assert result["questions"][-1].label == "A-9"


# heading 레벨을 고정하지 않는다. 손으로 쓴 파일이 다른 레벨을 쓸 수 있다
@pytest.mark.parametrize("hashes", ["##", "###", "####", "#####"])
def test_question_heading_level_is_not_fixed(tmp_path, hashes):
    body = f"# 제목\n\n## 단원\n\n{hashes} ★ Q. 물음입니까?\n\n답입니다.\n"
    result = C.collect(make_note(tmp_path, {"09-기타.md": body}))
    assert [q.question for q in result["questions"]] == ["물음입니까?"]


# 답이 빈 문항은 경고한다. 파서가 본문을 어디로 흘렸다는 뜻이다
def test_empty_answer_warns(tmp_path):
    body = "# 제목\n\n## 단원\n\n#### Q. 답이 없는 질문입니까?\n\n### 출처\n\n- 없음\n"
    result = C.collect(make_note(tmp_path, {"09-기타.md": body}))
    assert any("답이 비었습니다" in w for w in result["warnings"])


# id 는 질문 문장에서 나온다. **파일 이름이 바뀌어도 안 흔들려야 기록이 안 고아가 된다**
def test_id_survives_a_file_rename(tmp_path):
    before = C.collect(make_note(tmp_path / "a", {"03-검색.md": GENERATED}))
    after = C.collect(make_note(tmp_path / "b", {"07-검색으로개명.md": GENERATED}))
    assert [q.id for q in before["questions"]] == [q.id for q in after["questions"]]
    assert before["questions"][0].file != after["questions"][0].file


# `--answers` 없이는 답도 꼬리질문도 안 실린다. 문제를 낼 때 답이 보이면 힌트가 샌다
def test_answers_are_withheld_by_default(tmp_path):
    directory = make_note(tmp_path, {"03-검색.md": GENERATED})
    assert C.main(["--dir", str(directory)]) == 0


def test_without_answers_strips_answer_and_followups():
    rows = [{"id": "x", "question": "q", "answer": "a", "followups": [{"question": "f"}]}]
    stripped = C.without_answers(rows)
    assert stripped == [{"id": "x", "question": "q"}]


# **HTML 이 md 보다 새로우면 멈춘다.** 낡은 답으로 채점하면 맞는 답을 하고도 틀렸다고 듣는다
def test_stale_markdown_stops_collection(tmp_path):
    directory = make_note(tmp_path, {"03-검색.md": GENERATED})
    os.utime(directory / C.HTML_NAME, (3_000_000, 3_000_000))
    with pytest.raises(C.QuizStaleError) as caught:
        C.collect(directory)
    assert "_html2md.py" in str(caught.value)
    assert "03-검색.md" in str(caught.value)


# 손으로 쓴 파일은 그 검사에서 빠진다. 안 빼면 HTML 을 고칠 때마다 늘 «낡았다» 가 된다
def test_handwritten_file_is_exempt_from_the_freshness_check(tmp_path):
    directory = make_note(tmp_path, {C.HANDWRITTEN: APPENDIX, "03-검색.md": GENERATED})
    os.utime(directory / C.HANDWRITTEN, (500_000, 500_000))
    C.collect(directory)  # 안 죽어야 한다


# 폴더가 없으면 조용히 빈 목록을 내는 대신 사유를 달고 죽는다
def test_missing_note_dir_dies_with_a_reason(tmp_path):
    with pytest.raises(C.QuizConfigError) as caught:
        C.collect(tmp_path / "없는폴더")
    assert C.ENV_NOTE_DIR in str(caught.value)


# `_` 로 시작하는 파일은 도구·인계 문서라 재료가 아니다
def test_underscore_files_are_not_material(tmp_path):
    directory = make_note(tmp_path, {"03-검색.md": GENERATED, "_이어서작업.md": GENERATED})
    result = C.collect(directory)
    assert result["files"] == ["03-검색"]
    assert result["counts"]["questions"] == 2


# **`--weak` 는 `style` 을 안 센다.** 세면 구술로 푼 문항만 약한 것으로 몰린다
def test_weak_ids_ignore_style(tmp_path):
    log = tmp_path / "quiz_log.jsonl"
    log.write_text(
        '{"id":"aaa","missed":[],"wrong":[],"overclaim":[],"style":["길다"]}\n'
        '{"id":"bbb","missed":["빠뜨림"],"wrong":[],"overclaim":[],"style":null}\n'
        '{"id":"ccc","missed":[],"wrong":[],"overclaim":["선을 넘음"],"style":null}\n'
        "깨진 줄\n",
        encoding="utf-8",
    )
    assert C.weak_ids(log) == {"bbb", "ccc"}
