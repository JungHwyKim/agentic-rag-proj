"""자동 테스트 훅의 판정과 **매핑 커버리지**를 검사한다.

앞쪽은 경로 -> 테스트 대상 매핑이 맞는지 본다. 뒤쪽(커버리지)이 이 파일의 진짜 이유다.

새 파이프라인 단계를 만들고 `auto_test_check.py` 의 매핑 표에 줄을 추가하지 않으면,
**그 단계만 자동 테스트가 한 번도 안 도는데 아무 에러도 안 난다.** 훅들이 공통으로
두려워하는 실패 모드이고(`test_hook_wiring.py` 모듈 docstring), CLAUDE.md 에 한 줄 적어
두는 것으로는 못 막는다 — `paid_test_check.py` 가 그 교훈을 남겼다. 그래서 배선 테스트와
같은 모양의 짝을 여기 둔다.

  test_every_hook_script_is_wired  <->  test_every_stage_is_mapped
  test_wired_scripts_exist         <->  test_mapped_targets_exist
  LIBRARY_MODULES                  <->  UNMAPPED_BY_DESIGN
"""

from __future__ import annotations

import time
from pathlib import Path

import _hookutil
import auto_test_check
import pytest

# tests/hooks/test_auto_test_check.py -> 프로젝트 루트
PROJECT_ROOT = Path(__file__).resolve().parents[2]

STAGES_DIR = PROJECT_ROOT / "rag_pipeline" / "ingestion"


# 소스 경로가 그 영역의 테스트로 간다
@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("rag_pipeline/ingestion/cleaner/headers.py", "tests/ingestion/cleaner"),
        ("rag_pipeline/ingestion/loader/registry.py", "tests/ingestion/loader"),
        ("backend/app/api/routes/documents.py", "tests/api"),
        (".claude/hooks/auto_test_check.py", "tests/hooks"),
        ("rag_pipeline/ingestion/pipeline.py", "tests/ingestion/test_pipeline.py"),
        # **최상위 모듈은 커버리지 테스트가 안 잡는다** — 저쪽은 ingestion/ 아래 디렉터리만
        # 훑는다. 그래서 이 둘은 여기서 직접 못 박아야 조용히 빠지는 것을 막는다
        ("rag_pipeline/query.py", "tests/test_query.py"),
        ("rag_pipeline/runtime.py", "tests/test_runtime.py"),
    ],
)
def test_source_maps_to_area(source: str, expected: str):
    assert auto_test_check.test_target_for(source) == expected


# 더 깊은 접두사가 이겨야 한다. loader/document 가 loader 로 떨어지면 6배 넓게 돈다
def test_longest_prefix_wins():
    assert (
        auto_test_check.test_target_for("rag_pipeline/ingestion/loader/document/hwpx.py")
        == "tests/ingestion/loader/document"
    )
    assert (
        auto_test_check.test_target_for("rag_pipeline/ingestion/loader/content/router.py")
        == "tests/ingestion/loader/content"
    )


# 전 영역에 영향을 주는 것은 부분 실행이 무의미하다. Stop 게이트가 전체로 잡아야 한다
@pytest.mark.parametrize(
    "source",
    [
        "rag_pipeline/config.py",
        "rag_pipeline/exceptions.py",
        "rag_pipeline/schemas/document.py",
        "tests/conftest.py",
    ],
)
def test_broad_impact_is_not_scoped(source: str):
    assert auto_test_check.test_target_for(source) is None


# 테스트 파일을 직접 고치면 그 파일만 돌린다 — 제일 좁고 제일 빠르다
def test_editing_a_test_file_runs_that_file():
    rel = "tests/ingestion/cleaner/test_characters.py"
    assert auto_test_check.test_target_for(rel) == rel


# 코드가 아닌 것에는 아무것도 안 돌린다. 문서를 고칠 때마다 테스트가 돌면 못 쓴다
@pytest.mark.parametrize(
    "path",
    [
        "CLAUDE.md",
        # 하위 CLAUDE.md 는 `SOURCE_PREFIXES` 안에 살아 이름이 아니라 `.py` 필터로만 빠진다
        # (2026-08-31, 함정 절이 폴더별로 내려갔다). 느슨해지면 문서를 고칠 때마다 그 영역
        # 테스트가 도는데, 위 문단이 말하는 "못 쓴다"가 바로 그 상태다
        "rag_pipeline/ingestion/loader/CLAUDE.md",
        ".claude/wiki/plan_log/하네스-확장.md",
        ".claude/wiki/task_log/phase1.md",
        "regression/baseline.json",
        ".claude/skills/rag-code-review/SKILL.md",
    ],
)
def test_non_source_maps_to_nothing(path: str):
    assert auto_test_check.test_target_for(path) is None


# 저장소 밖 경로(스크래치패드 등)는 우리가 볼 것이 아니다
def test_outside_repo_is_ignored(tmp_path: Path):
    assert auto_test_check.test_target_for(str(tmp_path / "scratch.py")) is None


# 절대 경로로 와도 같은 결과여야 한다. 훅 입력의 file_path 는 절대 경로다
def test_absolute_path_is_normalized():
    absolute = str(PROJECT_ROOT / "rag_pipeline" / "ingestion" / "cleaner" / "whitespace.py")
    assert auto_test_check.test_target_for(absolute) == "tests/ingestion/cleaner"


# 한국어 경로가 깨지지 않아야 한다. 이 저장소는 한국어 파일명이 흔하다
def test_korean_path_does_not_crash():
    assert auto_test_check.test_target_for(".claude/wiki/plan_log/평가-세트.md") is None
    assert auto_test_check.test_target_for("tests/fixtures/시행령 개정안.hwpx") is None


# 빈 입력에 죽지 않는다. 판단이 안 서면 막지 않는 것이 훅 전체의 규약이다
@pytest.mark.parametrize("raw", ["", "   "])
def test_empty_path_is_ignored(raw: str):
    assert auto_test_check.test_target_for(raw) is None


# ---- 매핑 커버리지 ----


# 만들어 놓고 매핑을 잊은 단계가 없어야 한다.
# 새 단계를 만든 사람의 선택지는 둘뿐이다 — 매핑을 넣거나, UNMAPPED_BY_DESIGN 에 이유를 적거나
def test_every_stage_is_mapped():
    stages = [
        p.name
        for p in STAGES_DIR.iterdir()
        if p.is_dir() and not p.name.startswith(("_", "."))
    ]
    assert stages, "rag_pipeline/ingestion/ 아래에 단계 디렉터리가 하나도 없다"

    unmapped = [
        stage
        for stage in stages
        if auto_test_check.test_target_for(f"rag_pipeline/ingestion/{stage}/x.py") is None
    ]
    assert unmapped == [], (
        f"auto_test_check.py 의 _MAPPINGS 에 없는 단계가 있다: {unmapped}. "
        "매핑을 추가하거나, 부분 실행이 무의미한 이유를 UNMAPPED_BY_DESIGN 에 적어라"
    )


# 매핑이 없는 테스트 경로를 가리키면 안 된다. 이름을 바꾸면 훅이 조용히 아무것도 안 돌린다
def test_mapped_targets_exist():
    for prefix, target in auto_test_check._MAPPINGS:
        assert (PROJECT_ROOT / target).exists(), f"{prefix} 가 없는 대상을 가리킨다: {target}"


# 일부러 안 맵핑한 것들도 실재해야 한다. 파일이 사라졌는데 예외만 남으면 의도가 흐려진다
def test_unmapped_by_design_entries_exist():
    for item in auto_test_check.UNMAPPED_BY_DESIGN:
        assert (PROJECT_ROOT / item).exists(), f"UNMAPPED_BY_DESIGN 에 없는 경로가 있다: {item}"


# ---- 종료코드 판정 ----


# pytest 종료코드 5 는 "수집된 테스트가 없음"이지 실패가 아니다. 실패로 보면
# integration 전용 파일을 고칠 때마다 훅이 막는다 — addopts 가 전부 deselect 해서 5가 난다
def test_no_tests_collected_is_not_a_failure():
    assert 5 in auto_test_check._OK_RETURNCODES
    assert 0 in auto_test_check._OK_RETURNCODES
    # 진짜 실패(1)와 사용 오류(4)는 통과시키면 안 된다
    assert 1 not in auto_test_check._OK_RETURNCODES
    assert 4 not in auto_test_check._OK_RETURNCODES


# ---- 루프 방지 ----


# session_id 가 없으면 "세션당 한 번"을 보장할 수 없다. 그때는 반드시 통과해야 한다.
# 여기서 막으면 종료코드 2 가 대화를 이어가 영원히 안 끝난다
def test_stop_without_session_id_passes(monkeypatch):
    called: list[str] = []
    monkeypatch.setattr(
        auto_test_check, "_run_pytest", lambda *a, **k: called.append("ran") or (True, "")
    )

    auto_test_check._handle_stop({"hook_event_name": "Stop"})

    assert called == [], "session_id 가 없는데 테스트를 돌렸다 = 루프 위험"


# stop_hook_active 가 들어오면 존중한다. 마커가 주 안전장치이고 이건 덤이다
def test_stop_respects_stop_hook_active(monkeypatch):
    called: list[str] = []
    monkeypatch.setattr(
        auto_test_check, "_run_pytest", lambda *a, **k: called.append("ran") or (True, "")
    )

    auto_test_check._handle_stop({"stop_hook_active": True, "session_id": "abc"})

    assert called == []


# 탈출구가 실제로 통하는지. 문서에만 있고 안 통하면 사용자가 갇힌다
def test_skip_env_disables_everything(monkeypatch):
    called: list[str] = []
    monkeypatch.setenv("SKIP_AUTOTEST", "1")
    monkeypatch.setattr(
        auto_test_check._hookutil,
        "read_payload",
        lambda: {"hook_event_name": "Stop", "session_id": "abc"},
    )
    monkeypatch.setattr(
        auto_test_check, "_run_pytest", lambda *a, **k: called.append("ran") or (True, "")
    )

    auto_test_check.main()

    assert called == []


# --- file_path 가 없는 도구(Bash)로 고친 경우 ---
#
# 기록 훅이 2026-08-19 에 겪은 것과 같은 구멍인데 **방향이 반대다.** 저쪽은 오탐(막힘)이라
# 눈에 띄었지만 여기는 조용히 아무 검사도 안 돈다. auto mode 에서는 Bash 편집이 기본이다.


# Bash 로 소스를 고치면 tool_input 에 file_path 가 없다. 그래도 영역을 찾아야 한다
def test_bash_edit_is_mapped_by_filesystem(tmp_path, monkeypatch) -> None:
    (tmp_path / ".git").mkdir()
    stage = tmp_path / "rag_pipeline" / "ingestion" / "cleaner"
    stage.mkdir(parents=True)
    monkeypatch.setattr(_hookutil, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(auto_test_check, "PROJECT_ROOT", tmp_path)

    session = "sess-bash-edit"
    _hookutil.mark_session_start(session)
    time.sleep(0.01)
    (stage / "characters.py").write_text("# 고쳤다\n", encoding="utf-8")

    targets = auto_test_check._targets_for_call({"tool_input": {"command": "sed -i ..."}}, session)

    assert targets == [auto_test_check.test_target_for("rag_pipeline/ingestion/cleaner/x.py")]


# 아무것도 안 고쳤으면 돌릴 것이 없다. Bash 호출마다 테스트가 돌면 안 된다
def test_bash_call_without_edit_runs_nothing(tmp_path, monkeypatch) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "rag_pipeline").mkdir()
    monkeypatch.setattr(_hookutil, "PROJECT_ROOT", tmp_path)

    session = "sess-bash-noop"
    _hookutil.mark_session_start(session)

    assert auto_test_check._targets_for_call({"tool_input": {"command": "git status"}}, session) == []


# file_path 가 있으면 종전대로 그것 하나만 본다. 파일시스템을 훑지 않는다
def test_write_edit_still_uses_file_path(tmp_path, monkeypatch) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(_hookutil, "PROJECT_ROOT", tmp_path)

    payload = {"tool_input": {"file_path": "rag_pipeline/ingestion/cleaner/characters.py"}}

    assert auto_test_check._targets_for_call(payload, "sess-write") == [
        auto_test_check.test_target_for("rag_pipeline/ingestion/cleaner/characters.py")
    ]
