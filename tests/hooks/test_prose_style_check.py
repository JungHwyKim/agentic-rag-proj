"""`prose_style_check` 판정 회귀 테스트.

이 훅은 **막지 않는다.** 그래서 두 방향의 대가가 다른 훅들과 모양이 반대다.

  놓치면(false negative)  새 글에 옛 낱말이 들어간 채로 지나간다. 대가는 한 줄이 안 뜨는 것
  헛세면(false positive)  규칙을 적은 파일이나 동사 「팔다」가 매번 걸린다. 상시가 되면
                          `SKIP_PROSECHECK=1` 이 습관이 되고, 그러면 검사가 통째로 죽는다

**그래서 «안 세야 하는 것» 을 «세야 하는 것» 보다 촘촘히 박는다.** 규칙을 설명하는 문구와
복합어(판정·확장판·초판)와 동사가 그 자리다.

**제일 값나가는 테스트는 «파일을 안 읽는다» 쪽이다.** 첫 구현이 고친 파일을 디스크에서
읽어 통째로 셌고, 그래서 **291개 파일이 울렸다** — 거의 전부 일부러 안 고치기로 한 옛
기록이다. 지금은 `tool_input` 의 새 글만 본다. 그 결정이 뒤집히면 검사가 통째로 죽으므로
`test_only_the_written_text_is_counted` 가 그 자리를 잠근다.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import prose_style_check as hook

HOOK_PATH = Path(hook.__file__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# 세야 하는 것
# ---------------------------------------------------------------------------


def test_retired_words_are_counted() -> None:
    found = hook.count("답변 품질을 재는 자[尺]가 없고, 같은 잣대다. 비[比]도 안 낸다.")

    assert found["자[尺]·잣대"] == 3


def test_the_noun_pan_is_counted() -> None:
    found = hook.count("계단 세 판으로 다시 만들었고 HWPX 판은 0개다.")

    assert found["낱말 판"] == 2


def test_a_nominal_subject_is_counted() -> None:
    found = hook.count("그 뒤집힘은 완전 동점을 stable 정렬이 가른 것이다. 그 밀림이 남는다.")

    assert found["명사화 주어"] == 2


def test_a_vague_first_header_cell_is_counted() -> None:
    text = "| 항목 | 판정 |\n|---|---|\n| a | b |\n\n| | 값 |\n|---|---|\n"
    found = hook.count(text)

    assert found["표 헤더 첫 칸(항목·빈 칸)"] == 2


# ---------------------------------------------------------------------------
# 안 세야 하는 것 — 이쪽이 더 촘촘하다
# ---------------------------------------------------------------------------


# 규칙을 적은 문서가 자기 규칙에 걸리면 «규칙을 쓸 수 없다» 가 된다.
# 실제로 미러 작업에서 이 자리를 한 번 밟았다
def test_the_rule_text_itself_is_not_counted() -> None:
    text = "낱말 `자[尺]`·`잣대`·`판` 은 쓰지 않는다. 대신 `기준`·`실행`·`버전` 을 쓴다."

    assert hook.count(text) == {}


# 용어집의 「이제 쓰지 않는 말」 표. 옛 기록을 읽는 사람을 위해 남긴 자리다
def test_the_glossary_retirement_table_is_not_counted() -> None:
    text = (
        "| 옛 말 | 옛 기록에서 읽는 법 | 지금 쓰는 말 |\n"
        "|---|---|---|\n"
        "| **자[尺]** | 무엇을 재는 도구 | `재는 방법` |\n"
        "| **잣대** | 판정에 쓰는 기준 | `기준` |\n"
        "| 판 (① 한 번 돌린 것) | 세 번 쟀다는 뜻 | `실행` |\n"
    )

    assert "자[尺]·잣대" not in hook.count(text)


# 동사 「파다·팔다·되팔다」는 낱말 「판」이 아니다.
# 정규식으로 가르려 하면 `다른 판이다` 같은 정상 히트를 잃는다
def test_verbs_are_not_counted() -> None:
    text = (
        '토큰 칸은 안 채운다 — 0 은 *"이 API 는 토큰으로 안 판다"* 이다.\n'
        "여기서는 이름만 되판다.\n"
        "이것을 짐작하지 않고 판 이유는 적재 쪽 선례다.\n"
        "부정도 판다.\n"
    )

    assert "낱말 판" not in hook.count(text)


# 복합어는 낱말이 아니다. 「판정」이 이 저장소에 3,432건이라 여기가 제일 시끄러울 자리다
def test_compound_words_are_not_counted() -> None:
    text = (
        "판정이 답변을 안 바꾼다. 판단도 마찬가지다. 판별·판례·판매·판사도 아니다.\n"
        "이 문서들의 함정 절은 그 확장판이다. 초판은 2026-08-17 이다.\n"
        "재판정 조건은 없다.\n"
    )

    assert "낱말 판" not in hook.count(text)


# 첫 칸이 그 행을 말하는 헤더는 규칙이 요구하는 모양이다
def test_a_named_first_header_cell_is_not_counted() -> None:
    text = "| 질의 | 판정 | 근거 |\n|---|---|---|\n| q1 | 통과 | — |\n"

    assert "표 헤더 첫 칸(항목·빈 칸)" not in hook.count(text)


# 관용구는 **한 문서에 한 번까지** 허용한다. 한 번을 세면 규칙보다 엄해진다
def test_a_single_idiom_is_allowed() -> None:
    assert "되풀이된 관용구" not in hook.count("그것을 개선으로 읽으면 틀린다.")


def test_a_repeated_idiom_is_counted() -> None:
    text = "A 로 읽으면 틀린다. B 로 읽으면 틀린다. C 로 읽으면 틀린다."
    found = hook.count(text)

    # 세 번 나왔으면 되풀이는 둘이다
    assert found["되풀이된 관용구"] == 2


# ---------------------------------------------------------------------------
# 범위
# ---------------------------------------------------------------------------


def test_watched_paths_are_in_scope() -> None:
    for rel in ("CLAUDE.md", "AGENTS.md", ".claude/wiki/plan_log/x.md",
                "rag_pipeline/query.py", "tests/hooks/test_x.py"):
        assert not hook.out_of_scope(rel), rel


# 사람이 검수한 라벨과 승인된 기대값은 산문이 아니라 데이터다.
# 세기 시작하면 «라벨을 고쳐라» 로 읽히는데 그것이 검수 도장을 깨뜨린다
def test_label_data_is_out_of_scope() -> None:
    for rel in ("evaluation/qa_set.jsonl", "evaluation/qa_set_human.jsonl",
                "evaluation/paraphrase_pairs.jsonl", "regression/baseline.json"):
        assert hook.out_of_scope(rel), rel


def test_unwatched_paths_are_out_of_scope() -> None:
    for rel in ("data/queries.jsonl", "README.md", "requirements.txt"):
        assert hook.out_of_scope(rel), rel


# 규칙을 정의하거나 보관하는 문서는 걷어낸 낱말을 적는 것이 자기 일이다.
# 문구 단위로는 못 걸러낸다 — 용어집의 표 한 칸이 곧 그 낱말이다
def test_rule_documents_are_out_of_scope() -> None:
    for rel in hook.RULE_DOCS:
        assert hook.out_of_scope(rel), rel
        assert (PROJECT_ROOT / rel).is_file(), f"{rel} 이 없다 — 죽은 예외를 지워라"


# ---------------------------------------------------------------------------
# 무엇을 세는가 — 파일이 아니라 이번에 쓴 글
# ---------------------------------------------------------------------------


# **첫 구현이 여기서 틀렸다.** 파일을 읽어 통째로 셌더니 291개 파일이 울렸다 —
# 거의 전부 일부러 안 고치기로 한 옛 기록이다. 이 결정이 뒤집히면 검사가 통째로 죽는다
def test_only_the_written_text_is_counted() -> None:
    assert hook.written_text({"content": "새로 쓴 글"}) == "새로 쓴 글"
    assert hook.written_text({"new_string": "고친 글", "old_string": "옛 글"}) == "고친 글"
    assert hook.written_text({"file_path": "x.md"}) == ""


# `Edit` 에서 **옛 글은 안 센다** — 옛 기록의 낱말을 지우는 편집이 그 낱말로 걸리면
# 고치는 사람을 벌주는 셈이 된다
def test_the_old_string_is_not_counted() -> None:
    written = hook.written_text({"old_string": "자[尺]가 없다", "new_string": "재는 방법이 없다"})

    assert hook.count(written) == {}


# ---------------------------------------------------------------------------
# 프로세스로 — 안 막는지, 안 죽는지
# ---------------------------------------------------------------------------


def _run(file_path: str, content: str = "", *, env: dict[str, str] | None = None):
    payload = json.dumps(
        {"tool_name": "Write", "tool_input": {"file_path": file_path, "content": content}}
    ).encode()
    return subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=payload,
        capture_output=True,
        timeout=30,
        env=env,
    )


# **이 훅은 절대 막지 않는다.** 코드 2 를 내면 UI 에 «blocking error» 로 뜨고,
# 그 모양이 상시가 되는 것이 이 훅이 피하려는 바로 그 일이다
def test_the_hook_never_blocks() -> None:
    proc = _run(str(PROJECT_ROOT / "CLAUDE.md"), "계단 세 판으로 자[尺]를 다시 만들었다")

    assert proc.returncode == 0
    assert "산문 검사" in proc.stdout.decode("utf-8")


# 파일이 없어도 돈다 — 새 파일을 만드는 `Write` 가 그 경우다
def test_a_missing_file_still_reports() -> None:
    proc = _run(str(PROJECT_ROOT / ".claude/wiki/task_log/2099-01-01-없는파일.md"), "두 판을 견줬다")

    assert proc.returncode == 0
    assert "낱말 판 1" in proc.stdout.decode("utf-8")


# **출력이 JSON 이라야 모델에게 닿는다.** 첫 구현은 맨 텍스트를 찍어 전사에만 남았고,
# 그래서 «알려준다» 가 성립하지 않았다. 이 자리가 뒤집히면 검사가 조용해진다
def test_the_output_is_context_json() -> None:
    proc = _run(str(PROJECT_ROOT / "CLAUDE.md"), "계단 세 판을 견줬다")
    parsed = json.loads(proc.stdout.decode("utf-8"))

    assert parsed["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "산문 검사" in parsed["hookSpecificOutput"]["additionalContext"]


# 한국어가 이스케이프되면 문맥에 실려도 사람이 못 읽는다
def test_korean_is_not_escaped() -> None:
    proc = _run(str(PROJECT_ROOT / "CLAUDE.md"), "계단 세 판을 견줬다")

    assert "산문 검사".encode() in proc.stdout


def test_a_path_outside_the_repo_is_silent() -> None:
    proc = _run(str(Path(sys.executable).parent / "안읽어야한다.md"), "자[尺]가 없다")

    assert proc.returncode == 0
    assert proc.stdout == b""


def test_broken_payload_is_silent() -> None:
    proc = subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=b"not json",
        capture_output=True,
        timeout=30,
    )

    assert proc.returncode == 0
    assert proc.stdout == b""


# 탈출구. 이 테스트만 픽스처가 지운 변수를 다시 켠다
def test_the_escape_hatch_turns_it_off() -> None:
    env = dict(os.environ, SKIP_PROSECHECK="1")
    proc = _run(str(PROJECT_ROOT / "CLAUDE.md"), "자[尺]가 없다", env=env)

    assert proc.returncode == 0
    assert proc.stdout == b""


# 보고 한 줄에 규칙의 출처가 들어가야 한다 — 세기만 하므로 «어디를 읽나» 가 전부다
def test_the_report_points_at_the_rule() -> None:
    line = hook.report("CLAUDE.md", {"낱말 판": 3})

    assert "CLAUDE.md" in line
    assert "낱말 판 3" in line
    assert "기록-형식.md" in line
