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


# 조각으로 실렸나 — 「n/N」 표시가 붙는다
def is_partial(context: str, rel: str) -> bool:
    return f"# 폴더 지침: {rel} (" in context


# 그 지침이 통째로 한 조각에 실렸나(「n/N」 표시가 없다)
def is_whole(context: str, rel: str) -> bool:
    return f"# 폴더 지침: {rel}\n" in context


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

    # **얕은 쪽이 먼저다**(넓은 규칙 → 좁은 규칙). ingestion(2,414자)이 한 조각에 다 들어가고
    # 나면 loader 조각(최대 9,100자)은 남은 예산에 안 들어가 **다음 명령으로 밀린다**
    assert is_whole(context_of(out), "rag_pipeline/ingestion/CLAUDE.md")

    second, _ = run_hook(bash("cat rag_pipeline/ingestion/loader/registry.py", session))

    # **밀린 것이 사라지지 않는다는 것이 이 테스트의 요점이다** — 조상을 전부 모으는 이유가
    # 「하나가 조용히 빠지는 것」을 막는 것인데, 예산이 그 구멍을 다시 만들면 안 된다
    assert is_partial(context_of(second), "rag_pipeline/ingestion/loader/CLAUDE.md")


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


# --- 폴더를 훑는 명령 ---
#
# **첫 판은 「실재하는 파일 이름」만 알아봐서 이 갈래가 통째로 0자였다**(2026-09-01 실측).
# auto mode 가 지시하는 것이 정확히 `grep`·`ls` 라 제일 자주 밟히는 자리였다.
#
# 넓히는 방향이 하나가 아니고 **순진한 쪽은 밀어낸다** — 폴더 이름이 전부 평범한 낱말이라
# `grep` 패턴이 그대로 걸리고, 한 호출에 드는 조각이 사실상 하나라 부딪힌 폴더가 **일하는
# 폴더의 자리를 먹는다.** 그래서 아래 둘은 «실린다» 와 «안 실린다» 를 한 쌍으로 잠근다.


# 폴더를 훑으면 **그 폴더 자신의** 지침이 실린다.
#
# **`parents` 만 보던 때는 여기가 빈손이었다** — `rag_pipeline/retrieval/` 의 부모는
# `rag_pipeline` 이고 거기엔 `CLAUDE.md` 가 없다. 넓히면서 자기 자신을 후보에 안 넣으면
# **한 칸 위를 가리켜** 정작 그 폴더 지침이 안 실린다
def test_a_folder_sweep_injects_that_folders_own_doc(session: str):
    out, code = run_hook(bash("grep -rn retrieve rag_pipeline/retrieval/", session))

    assert code == 0
    assert is_whole(context_of(out), FITS_DOC), (
        "폴더를 훑었는데 그 폴더 지침이 안 실렸다 — 넓히기 전 상태로 돌아간 것이다"
    )


# **검색어가 폴더 이름과 부딪혀도 일하는 폴더를 밀어내면 안 된다.**
#
# 실측(2026-09-01): 맨낱말까지 디렉터리로 세면 `cost/CLAUDE.md` 만 2,286자 실리고
# `backend` 는 한 줄도 안 온다. 구분자를 요구하는 것이 그것을 가른다
def test_a_search_term_matching_a_folder_name_does_not_displace_the_real_folder(session: str):
    out, _ = run_hook(bash('grep -rn "cost" backend/app', session))
    context = context_of(out)

    assert has_body(context, "backend/CLAUDE.md"), "일하는 폴더가 밀려났다"
    assert not has_body(context, "cost/CLAUDE.md"), (
        "검색어를 경로로 셌다 — 이 저장소의 폴더 이름은 전부 평범한 낱말이다"
    )


# **파일을 아예 안 건드리는 명령은 침묵한다.** 산문에 폴더 이름이 있다는 이유로 실으면,
# 예산은 먹으면서 그 폴더에서 일하지도 않는다
def test_bare_folder_names_in_prose_are_not_paths(session: str):
    out, code = run_hook(bash('python -c "print(1)"  # data cost tests 를 손본다', session))

    assert code == 0
    assert out is None


# 한 명령이 폴더를 여럿 물면 **여럿이 후보가 된다.** 훅 docstring 의 「상한은 호출당이라
# 합산된다」가 드는 예시가 이것이고, **이 판 전까지는 거짓이었다**(실린 자 0).
#
# 실린 글이 아니라 후보 목록으로 재는 것은 **예산이 그 뒤에서 하나로 줄이기 때문**이다 —
# 출력으로 재면 합산 여부가 아니라 예산을 재게 된다
def test_a_command_naming_two_folders_makes_both_candidates():
    docs = folder_md_check.folder_docs_for(
        folder_md_check.paths_in("grep -rn retrieve backend/ tests/")
    )

    assert [folder_md_check.rel_of(d) for d in docs] == ["backend/CLAUDE.md", "tests/CLAUDE.md"]


# **구분자 없는 진짜 인자는 놓친다 — 감수한 대가다.**
#
# 살리려면 「인자 위치를 본다」가 되는데 그것이 셸 파싱이라, 훅이 *새 형태의 명령을 조용히
# 빠뜨리는* 쪽으로 틀린다. **놓치는 방향은 넓히기 전과 같아 되돌아갈 뿐이다**(fail-safe)
def test_a_bare_directory_argument_is_a_known_miss(session: str):
    out, code = run_hook(bash("ls backend", session))

    assert code == 0
    assert out is None, "이 누락이 사라졌으면 판별자가 바뀐 것이다 — 오탐 쪽을 다시 재라"


# --- 예산과 이어싣기 ---
#
# **이 절이 이 훅의 두 번째 실패를 잠근다.** 첫 판은 «실었다» 까지는 맞았는데 하네스가 큰
# `additionalContext` 를 2KB 프리뷰로 잘라 내용이 모델에 안 닿았다 — 훅은 성공한 것처럼
# 보이고 아무 에러도 안 난다. 상한은 **정확히 10,000자**다(훅 docstring).
#
# **두 번째 처방인 「쪽지」는 기각됐다** — 세 판 다 우회당했고, 근본 원인은 문구가 아니라
# **모델의 선택에 기댄 설계**였다(`plan_log/문서-최신성.md` 12회차). 지금 계약은 조각을
# 이어서 싣는 것이고, 이 절이 재는 것은 **모델이 아무것도 안 해도 전문이 도착하는가** 다.


# 예산에 드는 지침은 **한 조각에 통째로** 온다. 작은 폴더가 손해를 보면 안 된다
def test_small_doc_is_delivered_whole(session: str):
    out, _ = run_hook(bash(f"cat {FITS_FILE}", session))

    assert is_whole(context_of(out), FITS_DOC), "예산에 드는데 조각으로 쪼개면 손해다"


# 예산을 넘는 지침은 **첫 조각 + 「n/N」 표시**로 온다.
#
# **표시가 계약의 절반이다** — 안 붙이면 모델이 잘린 글을 온전한 것으로 읽고, 뒷부분에만
# 있는 함정을 «없다» 로 판단한다
def test_oversized_doc_arrives_as_a_first_slice(session: str):
    out, _ = run_hook(bash(f"cat {BIG_FILE}", session))
    context = context_of(out)

    assert is_partial(context, BIG_DOC)
    assert "1/" in context, "몇 조각 중 몇 번째인지 안 밝히면 잘린 글이 온전해 보인다"


# **다음 명령이 다음 조각을 잇는다 — 앞 조각을 되풀이하지 않는다.**
# 마커가 «집었나» 가 아니라 «어디까지 보냈나» 인 것이 이것이다
def test_next_command_continues_instead_of_repeating(session: str):
    first = context_of(run_hook(bash(f"cat {BIG_FILE}", session))[0])
    second = context_of(run_hook(bash("cat evaluation/corpus.py", session))[0])

    assert "1/" in first and "2/" in second, f"이어지지 않았다: {second[:120]}"
    body_of_first = first.split("\n\n", 2)[-1]
    assert body_of_first not in second, "같은 조각을 두 번 실으면 이어싣기가 아니다"


# **끝까지 이으면 원문이 한 글자도 안 빠지고 복원된다.** 이 절에서 제일 중요한 검사다 —
# 조각 경계가 글자를 먹거나 겹치면 «실었는데 틀린 글» 이 되는데 아무 에러도 안 난다
def test_slices_reassemble_into_the_original(session: str):
    doc = folder_md_check.PROJECT_ROOT / BIG_DOC
    received = []

    for _ in range(20):
        out, _ = run_hook(bash(f"cat {BIG_FILE}", session))
        context = context_of(out)
        if not context:
            break
        received.append(context.split("\n\n", 2)[-1])

    assert "".join(received) == doc.read_text(encoding="utf-8"), (
        "조각을 이어 붙인 것이 원문과 다르다"
    )


# 다 보낸 뒤에는 **침묵한다.** 안 그러면 되풀이가 예산을 먹는다
def test_exhausted_doc_goes_silent(session: str):
    for _ in range(20):
        out, _ = run_hook(bash(f"cat {BIG_FILE}", session))
        if context_of(out) == "":
            break

    again, code = run_hook(bash(f"cat {BIG_FILE}", session))

    assert code == 0
    assert again is None, "다 보낸 지침을 또 실으면 이어싣기가 무한 반복이 된다"


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


# **조각 하나는 혼자서 반드시 예산에 들어야 한다.** 안 그러면 그 조각이 어떤 명령에서도
# 자리를 못 얻어 커서가 거기 멈추고, 뒤가 통째로 사라지는데 아무 에러도 안 난다
def test_every_slice_of_every_real_doc_fits_alone():
    for doc in sorted(folder_md_check.PROJECT_ROOT.rglob("CLAUDE.md")):
        if doc.parent == folder_md_check.PROJECT_ROOT:
            continue  # 뿌리는 대상이 아니다
        slices = folder_md_check.slices_for(doc)
        for index in range(len(slices)):
            block = folder_md_check.block_for(doc, index, len(slices))
            size = len(folder_md_check.HEAD) + len(folder_md_check.SEPARATOR) + len(block)
            assert size <= folder_md_check.CONTEXT_BUDGET_CHARS, (
                f"{doc.name} 의 {index + 1}번째 조각이 혼자서도 예산을 넘는다 ({size}자)"
            )


# **줄 경계에서 자른다** — 문장 한가운데가 갈리면 조각 하나가 못 읽는 글이 된다
def test_slices_are_cut_on_line_boundaries():
    body = "".join(f"{i}번째 줄입니다\n" for i in range(4000))

    slices = folder_md_check.slices_of(body)

    assert len(slices) > 1, "이 입력은 상한을 넘어야 한다"
    assert "".join(slices) == body
    for piece in slices[:-1]:
        assert piece.endswith("\n"), "줄 한가운데를 잘랐다"


# 상한보다 긴 **한 줄**은 마지막 수단으로 줄 안에서 자른다.
#
# 실측(2026-09-01)으로 지금 폴더 지침의 최장 줄은 2,814자라 이 경로를 안 밟는다. 그래도
# 통째로 넣으면 그 조각이 예산을 넘겨 **영영 안 실리므로** 자르는 쪽이 맞다
def test_a_single_line_longer_than_the_cap_is_split():
    body = "가" * (folder_md_check.MAX_SLICE_CHARS * 2 + 5)

    slices = folder_md_check.slices_of(body)

    assert len(slices) == 3
    assert "".join(slices) == body
    assert all(len(piece) <= folder_md_check.MAX_SLICE_CHARS for piece in slices)


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
