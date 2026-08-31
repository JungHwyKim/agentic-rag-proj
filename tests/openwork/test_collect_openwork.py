"""미완 수집기의 계약을 잠근다.

**이 도구의 실패는 예외가 아니라 「짧은 목록」이다.** 절 이름이 바뀌거나 폴더가 옮겨가면 0건이
나오는데 아무 에러도 안 난다. 그래서 여기서 잠그는 것은 대부분 *조용히 틀리는* 자리다 —
① 취소선이 실제로 빠지나 ② 절·폴더가 어긋나면 시끄러운가 ③ 인용과 요약이 안 섞이나.

가상 데이터로 규칙을 고립시키고(임계값을 정하는 데는 못 쓴다), 실제 저장소 대조는 마지막 절에
따로 둔다 — 합성 문서는 만든 사람의 기대를 그대로 갖고 있어 자기 확인이 되기 때문이다.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from openwork import collect as oc

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


# 기록 폴더가 실제로 앉는 자리를 **수집기 상수에서 받는다.**
#
# 경로를 베껴 두면 구조가 바뀔 때 이 파일이 통째로 빨개지는데, 그때 깨진 것은 수집기가
# 아니라 테스트가 베낀 경로다. 상수에서 받으면 이 파일이 잠그는 것이 **경로**가 아니라
# *"설정된 자리에서 모으고, 어긋나면 소리낸다"* 라는 **동작**이 된다
def _wiki(root: Path) -> Path:
    path = root / oc.WIKI_ROOT
    path.mkdir(parents=True, exist_ok=True)
    return path


# 최소한의 기록 저장소를 만든다. 실제 규약과 같은 모양이어야 검사가 뜻을 갖는다
@pytest.fixture
def fake_repo(tmp_path: Path) -> Path:
    wiki = _wiki(tmp_path)
    plan = wiki / "plan_log"
    task = wiki / "task_log"
    plan.mkdir()
    task.mkdir()
    (plan / "INDEX.md").write_text("# 색인\n", encoding="utf-8")
    (task / "INDEX.md").write_text("# 색인\n", encoding="utf-8")

    (plan / "검색.md").write_text(
        "# 검색 설계\n\n"
        "## 확정된 것\n\n- 여기 것은 안 센다\n\n"
        "## 미결\n\n"
        "- **임계값을 정한다** (2회차) — 정하려면: 같은 뜻 다른 말 쌍이 필요하다\n"
        "- ~~**옛 항목**~~ → **3회차에서 닫혔다.** 더 볼 것 없다\n"
        "- 굵은 머리가 없는 줄 — `retrieve()` 가 먼저다\n\n"
        "## 안 하기로 한 것\n\n- 여기 것도 안 센다\n\n"
        "---\n\n"
        "## 1회차 — 2026-08-01 · 처음\n\n본문\n\n"
        "## 2회차 — 2026-08-05 · 두 번째\n\n본문\n\n"
        "## 3회차 — 2026-08-09 · 세 번째\n\n본문\n",
        encoding="utf-8",
    )
    (task / "2026-08-07-검색-배선.md").write_text(
        "# 검색 배선\n\n관련 설계: [plan_log/검색.md](../plan_log/검색.md)\n\n"
        "## 7. 남은 것\n\n"
        "- **캐시를 안 붙였다** 다음에 잰다\n"
        "- ~~**이미 했다**~~ 닫힘\n",
        encoding="utf-8",
    )
    return tmp_path


# ---- 절 뽑기 ----


# `## 미결` 만 뽑고 이웃 절은 안 딸려 온다. 딸려 오면 확정된 것이 미완으로 뜬다
def test_section_stops_at_next_heading(fake_repo: Path) -> None:
    text = (oc.folder_path(fake_repo, "plan_log") / "검색.md").read_text(encoding="utf-8")
    section = oc.section_of(text, "미결")
    assert "임계값을 정한다" in section
    assert "여기 것은 안 센다" not in section
    assert "여기 것도 안 센다" not in section


# 없는 절은 빈 문자열이다. 예외가 아니라 「그 파일에는 없다」가 맞는 답이다
def test_missing_section_is_empty(fake_repo: Path) -> None:
    text = (oc.folder_path(fake_repo, "plan_log") / "검색.md").read_text(encoding="utf-8")
    assert oc.section_of(text, "있을 리 없는 절") == ""


# 들여쓴 줄은 앞 불릿에 붙고 빈 줄이 끊는다. 안 붙이면 한 항목이 여러 줄로 쪼개져 수가 부풀어난다
def test_bullets_join_continuation_lines() -> None:
    section = "- 첫 항목이고\n  이어지는 줄이다\n\n- 둘째\n"
    assert oc.bullets_of(section) == ["첫 항목이고 이어지는 줄이다", "둘째"]


# ---- 닫힘 ----


# 취소선이 달린 항목은 빠지고 그 수가 따로 세어진다. **이것이 유일한 닫힘 판정이다**
def test_struck_through_items_are_dropped(fake_repo: Path) -> None:
    result = oc.collect(fake_repo)
    whats = [i["what"] for i in result["items"]]
    assert "옛 항목" not in whats
    assert "이미 했다" not in whats
    closed = {s["folder"]: s["closed_skipped"] for s in result["sources"]}
    assert closed == {"plan_log": 1, "task_log": 1}


# 닫힘 판정을 **더 하지 않는다.** 코드가 있든 없든 안 지운다 — 표는 「열린 채로 적힌 것」이다
def test_open_items_are_not_second_guessed(fake_repo: Path) -> None:
    result = oc.collect(fake_repo)
    whats = [i["what"] for i in result["items"]]
    assert "임계값을 정한다" in whats
    assert "캐시를 안 붙였다" in whats


# ---- 뭔지 / 왜 ----


# 굵은 머리가 "뭔지"다
def test_what_prefers_bold_head() -> None:
    assert oc.what_of("**임계값을 정한다** — 뒷말") == "임계값을 정한다"


# 굵은 머리가 없으면 첫 조각으로 떨어지되 비지 않는다
def test_what_falls_back_without_bold_head() -> None:
    assert oc.what_of("굵은 머리가 없는 줄 — 뒷말") == "굵은 머리가 없는 줄"


# 떨어진 자리에서도 강조 표시는 벗긴다. 안 벗기면 표 한가운데에 별표가 찍힌다
def test_what_strips_emphasis_in_fallback() -> None:
    assert oc.what_of("1회차 몫은 **없음** — 뒷말") == "1회차 몫은 없음"


# 「정하려면:」 뒤가 "왜"이고 인용으로 표시된다
def test_why_is_quoted_when_present() -> None:
    why, basis, cut = oc.why_of("**x** — 정하려면: 쌍이 필요하다")
    assert (why, basis, cut) == ("쌍이 필요하다", "quote", False)


# **없으면 None 이다 — 지어내지 않는다.** 지어내면 인용과 요약이 한 칸에 섞여
# 표의 절반이 호출마다 달라진다(실측: task_log 쪽은 「정하려면」이 1% 뿐이다)
def test_why_is_none_when_absent() -> None:
    assert oc.why_of("**x** 그냥 설명만 있다") == (None, None, False)


# 긴 인용은 자르되 **잘랐다고 밝힌다.** 말없이 자르면 요약인데 표는 인용이라고 말한다
def test_long_why_is_marked_truncated() -> None:
    why, basis, cut = oc.why_of("**x** — 정하려면: " + "가" * (oc.WHY_LIMIT + 50))
    assert basis == "quote"
    assert cut is True
    assert len(why) == oc.WHY_LIMIT


# ---- 날짜와 묶음 ----


# plan_log 는 파일명에 날짜가 없어 회차 헤딩이 시점을 아는 유일한 자리다
def test_plan_item_dates_come_from_its_round(fake_repo: Path) -> None:
    result = oc.collect(fake_repo)
    item = next(i for i in result["items"] if i["what"] == "임계값을 정한다")
    assert item["round"] == 2
    assert item["date"] == "2026-08-05"


# 회차 표시가 없으면 그 파일의 최신 회차 날짜로 떨어진다
def test_plan_item_without_round_uses_latest(fake_repo: Path) -> None:
    result = oc.collect(fake_repo)
    item = next(i for i in result["items"] if i["what"] == "굵은 머리가 없는 줄")
    assert item["round"] is None
    assert item["date"] == "2026-08-09"


# task_log 는 파일이 세션이라 기능이 본문 링크에만 있다. 링크를 안 보면 대부분 묶음 미상이 된다
def test_followup_inherits_feature_from_file_link(fake_repo: Path) -> None:
    result = oc.collect(fake_repo)
    item = next(i for i in result["items"] if i["what"] == "캐시를 안 붙였다")
    assert item["group"] == "검색"
    assert item["group_basis"] == "file"
    assert item["date"] == "2026-08-07"


# 어느 기능도 안 가리키면 **묶음 미상으로 남긴다.** 아무 데나 붙이면 표가 틀린 자리를 가리킨다
def test_followup_without_any_hint_stays_ungrouped(tmp_path: Path) -> None:
    wiki = _wiki(tmp_path)
    (wiki / "plan_log").mkdir()
    (wiki / "task_log").mkdir()
    (wiki / "plan_log" / "검색.md").write_text(
        "## 미결\n\n- **a** — 정하려면: b\n", encoding="utf-8"
    )
    (wiki / "task_log" / "2026-08-07-무관.md").write_text(
        "# 무관\n\n## 7. 남은 것\n\n- **아무것도 안 가리킨다**\n", encoding="utf-8"
    )
    result = oc.collect(tmp_path)
    item = next(i for i in result["items"] if i["kind"] == "followup")
    assert item["group"] is None
    assert item["group_basis"] is None


# ---- 구조가 어긋났을 때 시끄러운가 ----


# **위키 뿌리가 통째로 없으면 폴더 이름이 아니라 뿌리를 사유로 말한다.**
#
# 폴더 검사만 있으면 뿌리 하나가 틀렸을 때 사유가 "plan_log, task_log 가 없다"로 나와,
# 폴더를 찾으러 가게 만든다 — 실제 원인은 그 위 한 칸이다. 구조를 또 정리할 때 제일 먼저
# 밟는 자리라 사유를 따로 둔다
def test_missing_wiki_root_raises_about_the_root(tmp_path: Path) -> None:
    with pytest.raises(oc.OpenWorkConfigError) as excinfo:
        oc.collect(tmp_path)
    assert oc.WIKI_ROOT in str(excinfo.value)


# **설정된 폴더가 없으면 죽는다.** 조용히 짧은 목록을 내놓는 것이 이 도구의 유일한 실패 모드다
def test_missing_source_folder_raises(tmp_path: Path) -> None:
    (_wiki(tmp_path) / "plan_log").mkdir()
    with pytest.raises(oc.OpenWorkConfigError) as excinfo:
        oc.collect(tmp_path)
    assert "task_log" in str(excinfo.value)


# 설정에 없는 기록 폴더(INDEX.md 를 품은 디렉터리)가 생기면 경고한다 — 구조 정리의 반대 방향이다
def test_unknown_record_folder_warns(fake_repo: Path) -> None:
    new = _wiki(fake_repo) / "design_log"
    new.mkdir()
    (new / "INDEX.md").write_text("# 색인\n", encoding="utf-8")
    result = oc.collect(fake_repo)
    assert any("design_log" in w for w in result["warnings"])


# 일부러 재료가 아닌 폴더는 경고하지 않는다. verify_log 가 매번 뜨면 경고를 안 읽게 된다
def test_declared_non_source_does_not_warn(fake_repo: Path) -> None:
    skipped = _wiki(fake_repo) / "verify_log"
    skipped.mkdir()
    (skipped / "INDEX.md").write_text("# 색인\n", encoding="utf-8")
    assert oc.collect(fake_repo)["warnings"] == []


# INDEX.md 가 없는 보통 디렉터리는 기록 폴더가 아니다 — 헛경보는 무력화의 한 형태다
def test_ordinary_directory_does_not_warn(fake_repo: Path) -> None:
    (_wiki(fake_repo) / "data").mkdir()
    assert oc.collect(fake_repo)["warnings"] == []


# 절 이름이 바뀌어 한 파일에서도 안 잡히면 경고한다. 안 하면 0건이 정상처럼 보인다
def test_renamed_heading_warns(tmp_path: Path) -> None:
    wiki = _wiki(tmp_path)
    (wiki / "plan_log").mkdir()
    (wiki / "task_log").mkdir()
    (wiki / "plan_log" / "a.md").write_text(
        "## 열린 질문\n\n- **x**\n", encoding="utf-8"
    )
    (wiki / "task_log" / "2026-08-01-b.md").write_text(
        "## 7. 남은 것\n\n- **y**\n", encoding="utf-8"
    )
    result = oc.collect(tmp_path)
    assert any("plan_log" in w and "미결" in w for w in result["warnings"])


# ---- 접기 ----


# 기본은 뒷정리를 다 접고 수만 남긴다. 자르는 것이 아니라 접는 것이라 숫자가 남는다
def test_fold_hides_followups_but_keeps_the_count(fake_repo: Path) -> None:
    folded = oc.fold(oc.collect(fake_repo))
    group = next(g for g in folded["groups"] if g["group"] == "검색")
    assert group["followup_count"] == 1
    assert group["followups"] == []
    assert group["followups_folded"] == 1
    assert "items" not in folded


# 한 묶음만 펼친다
def test_fold_expands_one_group(fake_repo: Path) -> None:
    folded = oc.fold(oc.collect(fake_repo), expand="검색")
    group = next(g for g in folded["groups"] if g["group"] == "검색")
    assert [i["what"] for i in group["followups"]] == ["캐시를 안 붙였다"]
    assert group["followups_folded"] == 0


# 묶음 미상은 맨 뒤로 간다. 실제 저장소에서 제일 큰 덩어리라 앞에 오면 표를 가린다
def test_ungrouped_sorts_last(tmp_path: Path) -> None:
    wiki = _wiki(tmp_path)
    (wiki / "plan_log").mkdir()
    (wiki / "task_log").mkdir()
    (wiki / "plan_log" / "히읗.md").write_text(
        "## 미결\n\n- **x**\n", encoding="utf-8"
    )
    (wiki / "task_log" / "2026-08-01-b.md").write_text(
        "## 7. 남은 것\n\n- **y**\n", encoding="utf-8"
    )
    folded = oc.fold(oc.collect(tmp_path))
    assert folded["groups"][-1]["group"] == "(묶음 미상)"


# 원문은 기본에서 빠진다. 안 빼면 「전부 읽는다」를 스크립트로 옮겨 놓은 꼴이다
def test_raw_is_dropped_by_default(fake_repo: Path) -> None:
    items = oc.without_raw(oc.collect(fake_repo)["items"])
    assert all("raw" not in i for i in items)
    assert all("what" in i for i in items)


# ---- CLI ----


# CLI 는 JSON 을 stdout 으로 낸다. 경고는 stderr 라 파이프로 넘길 때 안 섞인다
def test_cli_emits_json(fake_repo: Path) -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "openwork.collect", "--root", str(fake_repo)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=PROJECT_ROOT,
        check=False,
    )
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["groups"]


# **부르는 쪽 환경이 cp949 여도 UTF-8 JSON 이 나온다.** 이 검사는 실제로 밟고 나서 들어왔다 —
# `PYTHONIOENCODING` 없이 훅이 돌린 pytest 에서만 두 CLI 테스트가 깨졌고, 파일만 따로 돌리면
# 통과해 원인이 코드 쪽으로 잘못 보였다. 부르는 쪽이 지키는 규약으로 두면 안 붙인 호출이 조용히 깨진다
def test_cli_output_is_utf8_regardless_of_caller_encoding(fake_repo: Path) -> None:
    env = {**os.environ, "PYTHONIOENCODING": "cp949"}
    proc = subprocess.run(
        [sys.executable, "-m", "openwork.collect", "--root", str(fake_repo)],
        capture_output=True,
        cwd=PROJECT_ROOT,
        env=env,
        check=False,
    )
    assert proc.returncode == 0
    payload = json.loads(proc.stdout.decode("utf-8"))
    whats = [i["what"] for g in payload["groups"] for i in g["decisions"]]
    assert "임계값을 정한다" in whats


# 설정이 어긋나면 CLI 도 조용히 성공하지 않는다
def test_cli_fails_loudly_on_bad_config(tmp_path: Path) -> None:
    (_wiki(tmp_path) / "plan_log").mkdir()
    proc = subprocess.run(
        [sys.executable, "-m", "openwork.collect", "--root", str(tmp_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=PROJECT_ROOT,
        check=False,
    )
    assert proc.returncode == 2
    assert "task_log" in proc.stderr


# ---- 실제 저장소 ----


# 실제 기록에서 0건이 나오면 규약이 어긋난 것이다. **합성 데이터로는 이걸 못 잡는다**
def test_real_repository_yields_items() -> None:
    result = oc.collect(PROJECT_ROOT)
    assert result["warnings"] == []
    by_folder = {s["folder"]: s for s in result["sources"]}
    assert by_folder["plan_log"]["open_items"] > 0
    assert by_folder["task_log"]["open_items"] > 0
    # 절이 있는 파일이 대부분이어야 한다 — 실측으로 19/19 과 98/102 였다
    assert by_folder["plan_log"]["files_with_section"] >= by_folder["plan_log"]["files_scanned"]
    assert by_folder["task_log"]["files_with_section"] > by_folder["task_log"]["files_scanned"] * 0.8


# 실제 기록에서 취소선이 실제로 걸러진다 — 규약이 살아 있다는 증거다
def test_real_repository_skips_closed_items() -> None:
    result = oc.collect(PROJECT_ROOT)
    by_folder = {s["folder"]: s for s in result["sources"]}
    assert by_folder["plan_log"]["closed_skipped"] > 0
