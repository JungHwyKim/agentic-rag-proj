"""`folder_md_check` 판정 회귀 테스트.

이 훅은 **아무것도 막지 않는다.** 그래서 `heredoc_write_check`·`paid_test_check` 와 비용의
모양이 또 다르다.

  놓치면(false negative)  폴더 지침이 안 실린 채 셸로 파일을 읽는다 — 2026-08-31 에 실측된
                          바로 그 상태이고, **아무 에러도 안 난다.**
  넘치면(false positive)  같은 지침이 컨텍스트에 여러 번 쌓인다. `evaluation/CLAUDE.md` 가
                          37,141바이트라 다섯 번이면 분산 전 뿌리(147,014)보다 나빠진다 —
                          **막는 훅이 아니라서 «헛경보» 가 아니라 «컨텍스트 낭비» 로 아프다.**

그래서 **중복 방지(폴더당 세션 1회)를 실림 자체만큼 촘촘히 박는다.**

**그리고 `permissionDecision` 을 안 내는 것을 따로 잠근다.** `"allow"` 를 내면 이 훅이
배선된 동안 모든 셸 명령이 권한 프롬프트를 건너뛴다 — 목적과 무관한 권한 완화이고,
`additionalContext` 만 내는 것으로 목적은 이미 달성된다.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import folder_md_check
import pytest

HOOK_PATH = Path(folder_md_check.__file__)
PROJECT_ROOT = folder_md_check.PROJECT_ROOT

# 조상이 둘인 파일. loader/ 와 ingestion/ 양쪽에 CLAUDE.md 가 있다.
# **조상 둘의 대우가 갈리는 파일이기도 하다** — ingestion 은 예산에 들어가고 loader 는 안 든다
NESTED_FILE = "rag_pipeline/ingestion/loader/document/pdf.py"

# 지침이 예산에 **들어가는** 폴더의 파일. 중복 방지 테스트는 이쪽이어야 한다 —
# 예산을 넘는 폴더는 포인터라 일부러 되풀이되므로 「두 번째는 침묵」이 성립하지 않는다
FITS_FILE = "rag_pipeline/retrieval/hybrid.py"
FITS_SIBLING = "rag_pipeline/retrieval/fusion.py"
FITS_DOC = "rag_pipeline/retrieval/CLAUDE.md"

# 지침 하나만으로 예산을 넘기는 폴더의 파일(evaluation/CLAUDE.md 는 19,823자)
BIG_FILE = "evaluation/config.py"
BIG_DOC = "evaluation/CLAUDE.md"

# 혼자면 예산에 드는 폴더 넷과 그 폴더를 무는 파일. 합이 11,720자라 한 명령이 넷을 다
# 물면 적어도 하나는 밀린다 — **크기가 아니라 예산에 밀리는** 갈래를 만드는 재료다
SMALL_ANCHORS = {
    "cost/CLAUDE.md": "cost/pricing.py",
    "tests/CLAUDE.md": "tests/conftest.py",
    "rag_pipeline/retrieval/CLAUDE.md": "rag_pipeline/retrieval/hybrid.py",
    "rag_pipeline/ingestion/CLAUDE.md": "rag_pipeline/ingestion/pipeline.py",
}
MANY_SMALL_COMMAND = "wc -c " + " ".join(SMALL_ANCHORS.values())

# 일곱 폴더를 한 명령이 다 무는 경우. **이것이 실사용에서 훅을 깨뜨린 모양이다**
ALL_FOLDERS_COMMAND = (
    "wc -c backend/app/main.py cost/pricing.py evaluation/config.py "
    "rag_pipeline/ingestion/pipeline.py rag_pipeline/ingestion/loader/registry.py "
    "rag_pipeline/retrieval/hybrid.py tests/conftest.py"
)


# 지침이 전문으로 실렸나. 실린 글의 블록 머리를 본다 — 본문 문구로 재면 문서를 고칠 때마다
# 테스트가 깨진다
def has_body(context: str, rel: str) -> bool:
    return f"# 폴더 지침: {rel}" in context


# 포인터로만 실렸나
def has_pointer(context: str, rel: str) -> bool:
    return f"- `{rel}`" in context and not has_body(context, rel)


# 훅이 실제로 내보낸 컨텍스트. 아무것도 안 실었으면 빈 문자열
def context_of(out: dict | None) -> str:
    return "" if out is None else out["hookSpecificOutput"]["additionalContext"]


# 훅을 실제로 돌린다. 반환은 (파싱한 출력 또는 None, 종료코드)
def run_hook(payload: dict) -> tuple[dict | None, int]:
    proc = subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    out = proc.stdout.strip()
    return (json.loads(out) if out else None), proc.returncode


# Bash 명령 하나짜리 payload
def bash(command: str, session_id: str) -> dict:
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "session_id": session_id,
    }


# 테스트가 만든 세션 마커를 지운다. 안 지우면 `.git/` 에 쌓이고, 더 나쁘게는
# **다음 실행에서 이미 집힌 것으로 보여 실림 테스트가 조용히 통과한다**
@pytest.fixture
def session(request: pytest.FixtureRequest) -> str:
    sid = "pytest-foldermd-" + request.node.name.replace("[", "-").replace("]", "")
    yield sid
    folder_md_check.clear_all_markers(sid)


# --- 실리는가 ---


# 조상 폴더의 지침이 실린다. **둘 다** 실려야 한다 — 하나만 오는 것을 실측으로 봤고,
# 그게 이 훅이 조상을 전부 모으는 이유다
def test_ancestor_docs_are_injected(session: str):
    out, code = run_hook(bash(f"cat {NESTED_FILE}", session))

    assert code == 0
    assert out is not None, "지침이 있는 파일을 읽었는데 아무것도 안 실렸다"
    context = out["hookSpecificOutput"]["additionalContext"]
    # **둘의 대우가 갈린다** — ingestion(2,414자)은 예산에 들고 loader(12,681자)는 안 든다.
    # 「경로가 문자열로 있나」로만 재면 포인터와 전문이 안 갈려 이 구분이 조용히 사라진다
    assert has_body(context, "rag_pipeline/ingestion/CLAUDE.md")
    assert has_pointer(context, "rag_pipeline/ingestion/loader/CLAUDE.md")


# **뿌리 `CLAUDE.md` 는 늘 로드되므로 실으면 두 벌이 된다.** 조상 훑기가 뿌리에서 멈추는지
def test_root_claude_md_is_never_injected(session: str):
    out, _ = run_hook(bash("cat pyproject.toml README.md", session))

    if out is not None:
        context = out["hookSpecificOutput"]["additionalContext"]
        assert "# 폴더 지침: CLAUDE.md" not in context


# **막는 훅이 아니라는 계약.** 파이프가 붙어도 실린다 — 차단안을 기각한 이유가
# «파이프 하나로 우회되는데 우회한 그 순간이 원래 문제» 였다
def test_piped_read_still_injects(session: str):
    out, _ = run_hook(bash(f"cat {NESTED_FILE} | head -50", session))

    assert out is not None, "파이프가 붙었다고 안 실으면 차단안과 같은 구멍이 생긴다"


# --- 예산 ---
#
# **이 절이 이 훅의 두 번째 실패를 잠근다.** 첫 판은 «실었다» 까지는 맞았는데 하네스가 큰
# `additionalContext` 를 2KB 프리뷰로 잘라 내용이 모델에 안 닿았다 — 훅은 성공한 것처럼
# 보이고 아무 에러도 안 난다. 실측 경계는 (10,000, 10,239]자다(훅 docstring).


# 예산에 드는 지침은 **전문이 그대로** 온다. 포인터로 다 바꾸는 안을 기각한 이유가 이것이다
def test_small_doc_is_still_delivered_whole(session: str):
    out, _ = run_hook(bash(f"cat {FITS_FILE}", session))

    assert has_body(context_of(out), FITS_DOC), "예산에 드는데도 포인터로 떨어지면 손해다"


# 예산을 넘는 지침은 **본문 대신 경로**가 온다
def test_oversized_doc_becomes_a_pointer(session: str):
    out, _ = run_hook(bash(f"cat {BIG_FILE}", session))

    assert has_pointer(context_of(out), BIG_DOC)


# **한 명령이 일곱 폴더를 다 물어도 예산 안이다.** 상한이 문서당이 아니라 **호출당**이라,
# 지침을 아무리 잘게 쪼개도 이 경우를 못 막는다 — 「쪼개기」안을 기각한 자리가 여기다
def test_a_command_touching_every_folder_stays_under_budget(session: str):
    out, code = run_hook(bash(ALL_FOLDERS_COMMAND, session))

    assert code == 0
    context = context_of(out)
    assert context, "일곱 폴더를 물었는데 아무것도 안 실렸다"
    assert len(context) <= folder_md_check.CONTEXT_BUDGET_CHARS, (
        f"{len(context)}자를 실었다 — 하네스가 자르면 훅은 성공한 것처럼 보이는데 "
        "내용이 모델에 안 닿는다"
    )


# 포인터로 돌린 폴더는 **마커를 안 집는다** — 다음 명령에서 다시 시도된다.
#
# 마커는 «큰 비용을 두 번 치르지 않는다» 는 장치인데 포인터는 큰 비용이 아니다. 집어 버리면
# 세션에 단 한 번뿐인 넛지를 **모델이 안 따라갈 수도 있는 리다이렉트에** 써 버린다
def test_pointer_does_not_consume_the_session_marker(session: str):
    first, _ = run_hook(bash(f"cat {BIG_FILE}", session))
    assert has_pointer(context_of(first), BIG_DOC)

    second, _ = run_hook(bash("cat evaluation/corpus.py", session))

    assert has_pointer(context_of(second), BIG_DOC), (
        "포인터가 마커를 집으면 모델이 안 따라 읽었을 때 그 폴더 지침이 세션 내내 사라진다"
    )


# **크기가 아니라 예산에 밀린** 폴더는 나중에 혼자 오면 전문으로 온다.
#
# 앞 테스트가 「포인터가 되풀이된다」를 봤다면 이쪽은 **놓아 준 마커가 되돌리기로 쓰인다**는
# 것을 본다. 넷 다 혼자면 예산에 드는 폴더인데 합이 11,720자라 적어도 하나는 밀린다.
# **어느 것이 밀리는지는 고정하지 않는다** — 문서 크기가 바뀌면 달라지므로 훅에게 물어본다
def test_a_folder_pushed_out_by_budget_arrives_whole_later(session: str):
    first, _ = run_hook(bash(MANY_SMALL_COMMAND, session))
    context = context_of(first)

    pushed_out = [rel for rel in SMALL_ANCHORS if has_pointer(context, rel)]
    assert pushed_out, "작은 폴더 넷을 한 번에 물었는데 아무것도 안 밀렸다"

    rel = pushed_out[0]
    second, _ = run_hook(bash(f"cat {SMALL_ANCHORS[rel]}", session))

    assert has_body(context_of(second), rel), "놓아 준 마커가 실제로는 안 놓아졌다"


# --- 중복 방지 ---


# 같은 폴더 두 번째 명령에서는 **아무것도 안 낸다**.
#
# **예산에 들어가는 폴더로 재야 한다** — 넘치는 폴더는 포인터라 일부러 되풀이된다
def test_second_command_in_same_folder_is_silent(session: str):
    first, _ = run_hook(bash(f"cat {FITS_FILE}", session))
    assert has_body(context_of(first), FITS_DOC)

    second, code = run_hook(bash(f"cat {FITS_SIBLING}", session))

    assert code == 0
    assert second is None, "같은 폴더 지침이 두 번 실리면 컨텍스트가 그만큼 낭비된다"


# 세션이 다르면 다시 실린다. 마커는 세션 것이지 머신 것이 아니다
def test_a_different_session_gets_it_again(session: str):
    first, _ = run_hook(bash(f"cat {NESTED_FILE}", session))
    other = session + "-other"
    try:
        second, _ = run_hook(bash(f"cat {NESTED_FILE}", other))
        assert first is not None and second is not None
    finally:
        folder_md_check.clear_all_markers(other)


# --- 아무것도 안 하는 자리 ---


# 폴더 지침이 없는 경로만 나오면 침묵한다
def test_paths_without_folder_docs_are_silent(session: str):
    out, code = run_hook(bash("cat pyproject.toml", session))

    assert code == 0
    assert out is None


# 실재하지 않는 경로는 무시한다. 명령 문자열에서 뽑은 토큰이라 쓰레기가 섞인다
def test_nonexistent_paths_are_ignored(session: str):
    out, code = run_hook(bash("cat rag_pipeline/ingestion/loader/없는파일.py", session))

    assert code == 0
    assert out is None


# 탈출구
def test_skip_env_disables_the_hook(session: str, monkeypatch: pytest.MonkeyPatch):
    proc = subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=json.dumps(bash(f"cat {NESTED_FILE}", session)),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**dict(__import__("os").environ), "SKIP_FOLDERMD": "1"},
        timeout=30,
    )

    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


# **판단이 서지 않으면 아무것도 안 한다.** 입력이 깨졌을 때 세션을 막으면 안 된다
@pytest.mark.parametrize(
    "raw",
    ["", "not json", "{}", '{"session_id": "x"}', '{"session_id":"x","tool_input":"문자열"}'],
)
def test_broken_input_passes_quietly(raw: str):
    proc = subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=raw,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )

    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


# --- 권한을 건드리지 않는다 ---


# **`permissionDecision` 을 내면 안 된다.** `"allow"` 는 이 훅이 배선된 동안 모든 셸 명령의
# 권한 프롬프트를 건너뛰게 만든다 — 목적과 무관한 권한 완화다
def test_hook_never_emits_a_permission_decision(session: str):
    out, _ = run_hook(bash(f"cat {NESTED_FILE}", session))

    assert out is not None
    assert "permissionDecision" not in out["hookSpecificOutput"]
    assert "permissionDecisionReason" not in out["hookSpecificOutput"]


# --- 압축 리셋 ---


# 압축으로 컨텍스트가 접히면 마커를 지워 다시 실리게 한다.
#
# **`load_reason == "compact"` 가 실제로 오는 것은 안 쟀다** — payload 에 그 칸이 있고
# `nested_traversal` 이 오는 것만 실측했다. 안 와도 틀리는 방향이 안전하다(안 실릴 뿐)
def test_compact_clears_markers_so_it_loads_again(session: str):
    first, _ = run_hook(bash(f"cat {NESTED_FILE}", session))
    assert first is not None

    run_hook(
        {
            "hook_event_name": "InstructionsLoaded",
            "load_reason": "compact",
            "session_id": session,
        }
    )

    again, _ = run_hook(bash(f"cat {NESTED_FILE}", session))
    assert again is not None, "압축 뒤에는 다시 실려야 한다"


# 압축이 아닌 로드는 마커를 안 건드린다. 여기서 지우면 중복 방지가 통째로 무너진다
def test_other_load_reasons_do_not_clear_markers(session: str):
    first, _ = run_hook(bash(f"cat {FITS_FILE}", session))
    assert has_body(context_of(first), FITS_DOC)

    run_hook(
        {
            "hook_event_name": "InstructionsLoaded",
            "load_reason": "nested_traversal",
            "session_id": session,
        }
    )

    again, _ = run_hook(bash(f"cat {FITS_FILE}", session))
    assert again is None, "nested_traversal 에 마커를 지우면 지침이 되풀이해 실린다"
