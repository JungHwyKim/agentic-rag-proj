"""`heredoc_write_check` 판정 회귀 테스트.

이 훅도 `paid_test_check` 처럼 두 방향으로 다 틀릴 수 있는데, **비용의 모양이 다르다.**

  놓치면(false negative)  파일이 조용히 안 만들어지거나 백슬래시가 벗겨진 채 저장된다.
                          에러가 엉뚱한 곳에서 나서 원인을 heredoc 에서 안 찾는다.
  헛막으면(false positive) 백슬래시 없는 짧은 `python <<PY` 는 이 저장소에서 실제로 잘
                          도는 형태다. 그걸 막으면 `SKIP_HEREDOC=1` 이 상시 켜지고,
                          그러면 첫 번째 실패까지 같이 열린다.

그래서 **통과 케이스를 차단 케이스보다 촘촘히 박는다.** 특히 비트 시프트(`x << 2`)와
here-string(`<<<`)은 문법이 겹쳐 보여 오탐이 나기 쉬운 자리다.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import heredoc_write_check
import pytest

HOOK_PATH = Path(heredoc_write_check.__file__)


# ② 갈래. 소스에 진짜 백슬래시가 들어가야 하므로 raw 문자열로 적는다 —
# 이 파일 자체가 그 함정을 설명하는 자리라 이스케이프로 흐리지 않는다
_BACKSLASH_BODY = r"""python - <<'PY'
text = "a\nb"
PY"""

_BACKSLASH_BODY_UNQUOTED = r"""python - <<PY
pattern = r"\d+"
PY"""


# 막아야 하는 Bash 명령
BLOCKED_BASH = [
    # ① 본문이 그대로 파일이 된다 — 리다이렉트가 heredoc 앞
    "cat > out.md <<'EOF'\nhello\nEOF",
    "cat >> out.md <<EOF\nhello\nEOF",
    # 리다이렉트가 heredoc 뒤
    "cat <<'EOF' > out.md\nhello\nEOF",
    # tee 갈래
    "tee out.md <<'EOF'\nhello\nEOF",
    "tee -a out.md <<'EOF'\nhello\nEOF",
    # 파이프 뒤에 붙은 경우도 같은 자리다
    "echo x | cat > out.md <<'EOF'\nhello\nEOF",
    # ② 본문에 백슬래시 — 따옴표 여부와 무관하다
    _BACKSLASH_BODY,
    _BACKSLASH_BODY_UNQUOTED,
    # `<<-` 도 같은 heredoc 이다
    "cat >out.md <<-EOF\n\thello\n\tEOF",
]

# 막으면 안 되는 Bash 명령
ALLOWED_BASH = [
    # ③ 짧은 스크립트를 stdout 으로만 찍는다. 이 저장소에서 실제로 잘 도는 형태다
    "python - <<'PY'\nprint(1 + 1)\nPY",
    "python3 <<PY\nprint('ok')\nPY",
    # 파이프로 넘기는 것은 파일 쓰기가 아니다
    "cat <<'EOF' | python\nprint(1)\nEOF",
    # cat/tee 가 아니면 본문이 파일 내용이 아니다
    "sort <<EOF\nb\na\nEOF",
    # fd 복제는 파일 리다이렉트가 아니다
    "cat <<'EOF' >&2\nhello\nEOF",
    # 비트 시프트. `<< 2` 를 구분자로 읽으면 명령 전체가 본문이 된다
    'python -c "x = y << 2"',
    r'python -c "mask = 1 << 8; print(chr(92))"',
    # here-string 은 본문이 없다
    "grep -f - foo.txt <<<'pattern'",
    # 끝 구분자가 없다 — 판단하지 않는다(셸이 스스로 죽는다)
    "cat > out.md <<'EOF'\nhello",
    # heredoc 이 아예 없다. cat + 리다이렉트만으로 막으면 안 된다
    "cat file.md > copy.md",
    "echo hi > out.txt",
    r'python -c "print(\"a\\nb\")"',
    # 산문에 heredoc 이 나올 뿐이다
    'git commit -m "heredoc 함정을 기록했다"',
]

# 하네스가 **모든 커밋에 요구하는** 트레일러. 닫는 `>` 가 있는 것이 핵심이라 여기 못 박는다 —
# 이 글자 하나가 2026-09-01 까지 `git commit -m @'...'@` 를 상시로 막았다
_TRAILER = "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"

# 막아야 하는 PowerShell 명령 — ① 만 본다
BLOCKED_PS = [
    "@'\nhello\n'@ | Set-Content out.md",
    '@"\nhello\n"@ | Out-File out.md',
    "@'\nhello\n'@ | Add-Content out.md",
    "@'\nhello\n'@ > out.md",
    # 공백 있는 따옴표 경로. **「`>` 뒤에 경로꼴을 요구」안이 여기서 죽었다** —
    # `'@` 를 거르려고 따옴표를 빼면 이 진짜 파일 쓰기가 함께 샌다
    '@\'\nhello\n\'@ > "out file.md"',
    # 본문이 빈 here-string 도 닫힌 것이다. 「못 찾음」으로 빠지면 이게 통과한다
    "@'\n'@ > out.md",
]

# 막으면 안 되는 PowerShell 명령
ALLOWED_PS = [
    # 도구 설명이 권하는 정상 용도. 파일과 무관하다
    "git commit -m @'\nmessage line\n'@",
    # **실사용은 늘 트레일러를 단다.** 위 케이스에는 `>` 가 한 번도 안 나와서,
    # 의도는 맞는데 실사용을 안 닮은 채 8일을 초록으로 지나갔다
    f"git commit -m @'\nfix: 무언가를 고친다\n\n{_TRAILER}\n'@",
    # 본문의 **낱말**과 **예시**도 명령이 아니다. 트레일러와 무관한 같은 부류다 —
    # 이 훅을 설명하는 커밋 메시지가 이 훅에 막히면 안 된다
    "git commit -m @'\nfix: Set-Content 를 쓰라고 문서에 적었다\n'@",
    "git commit -m @'\ndocs: `cat > f.md` 가 위험하다고 적었다\n'@",
    # here-string 이 아니다
    "Set-Content out.md -Value 'hello'",
    "Get-Content out.md",
]


@pytest.mark.parametrize("command", BLOCKED_BASH)
def test_bash_heredoc_writes_are_blocked(command: str):
    assert heredoc_write_check.bash_reason(command) is not None, command


@pytest.mark.parametrize("command", ALLOWED_BASH)
def test_safe_bash_commands_pass(command: str):
    assert heredoc_write_check.bash_reason(command) is None, command


@pytest.mark.parametrize("command", BLOCKED_PS)
def test_powershell_herestring_writes_are_blocked(command: str):
    assert heredoc_write_check.powershell_reason(command) is not None, command


@pytest.mark.parametrize("command", ALLOWED_PS)
def test_safe_powershell_commands_pass(command: str):
    assert heredoc_write_check.powershell_reason(command) is None, command


# **PowerShell 에 백슬래시 규칙이 없는 것은 실수가 아니다.**
#
# ②(백슬래시가 한 겹 벗겨진다)는 Bash heredoc 에서만 실측됐다. PowerShell here-string 이
# 같은 일을 하는지는 이 저장소에서 **잰 적이 없다** — 안 잰 것을 잰 것처럼 막지 않는다.
# 「비대칭이니 통일하자」로 이 줄을 지우면 근거 없는 차단이 생긴다.
# 반대쪽(Bash 는 백슬래시를 막는다)은 위 BLOCKED_BASH 가 잠근다
def test_powershell_backslash_alone_does_not_block():
    command = "@'\nC:\\Users\\junghwy\\path\n'@ | Write-Output"
    assert heredoc_write_check.powershell_reason(command) is None


# **판정은 here-string 본문 «밖» 만 본다.** 본문은 데이터이지 명령이 아니다.
#
# 2026-09-01 전에는 명령 **전체**에 정규식을 대서 본문 글자가 명령으로 읽혔다. 같은 본문을
# 감싸는 명령만 바꿔 **짝으로** 재는 것이 그 회귀를 잡는 유일한 모양이다 — 한쪽만 재면
# «본문을 무시한다» 와 «아무것도 안 막는다» 가 안 갈린다
@pytest.mark.parametrize("body", ["x > out.md", "Set-Content out.md", _TRAILER])
def test_only_text_outside_the_herestring_is_judged(body: str):
    assert heredoc_write_check.powershell_reason(f"git commit -m @'\n{body}\n'@") is None
    assert heredoc_write_check.powershell_reason(f"@'\n{body}\n'@ > out.md") is not None


# **끝 구분자를 못 찾으면 판단하지 않는다** — `_heredocs()` 가 닫힌 것만 모으는 것과 같은
# 규약이다. 2026-09-01 전에는 여는 자리만 봐서 여기만 어긋나 있었다.
#
# 안 닫힌 here-string 은 셸이 스스로 죽으므로, 여기서 추측하면 **일을 하고도 막히는** 게이트가
# 되고 그것이 `plan_log/하네스-확장.md` 4회차가 말한 무력화의 한 형태다
@pytest.mark.parametrize(
    "command",
    [
        "git commit -m @'\ndocs: cat > f.md 함정을 적었다",
        "@'\nhello\n | Set-Content out.md",
    ],
)
def test_unterminated_herestring_is_not_judged(command: str):
    assert heredoc_write_check.powershell_reason(command) is None, command


# 도구가 갈리면 판정도 갈린다. Bash 규칙을 PowerShell 명령에 먹이면 안 된다
def test_tool_routing():
    bash_command = "cat > out.md <<'EOF'\nhello\nEOF"
    ps_command = "@'\nhello\n'@ | Set-Content out.md"

    assert heredoc_write_check.blocked_reason("Bash", bash_command) is not None
    assert heredoc_write_check.blocked_reason("PowerShell", bash_command) is None
    assert heredoc_write_check.blocked_reason("PowerShell", ps_command) is not None
    assert heredoc_write_check.blocked_reason("Bash", ps_command) is None
    # 모르는 도구는 판단하지 않는다
    assert heredoc_write_check.blocked_reason("Write", bash_command) is None


# 훅을 실제로 실행해 확인한다. 판정 함수가 맞아도 배선이 틀리면 아무 소용이 없다
def _run_hook(command: str, *, tool: str = "Bash", raw: bytes | None = None, env: dict | None = None):
    payload = (
        raw
        if raw is not None
        else json.dumps({"tool_name": tool, "tool_input": {"command": command}}).encode()
    )
    return subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=payload,
        capture_output=True,
        timeout=30,
        env=env,
    )


# 차단 시 PreToolUse deny 형식을 정확히 내야 한다. 형식이 틀리면 하네스가 무시한다
def test_deny_payload_shape():
    proc = _run_hook("cat > out.md <<'EOF'\nhello\nEOF")

    assert proc.returncode == 0
    output = json.loads(proc.stdout.decode("utf-8"))
    specific = output["hookSpecificOutput"]
    assert specific["hookEventName"] == "PreToolUse"
    assert specific["permissionDecision"] == "deny"
    reason = specific["permissionDecisionReason"]
    # 탈출구를 사유에 적어두지 않으면 사용자가 막힌 채로 갇힌다
    assert "SKIP_HEREDOC" in reason
    # 무엇을 대신 쓰라는 말이 없으면 같은 실수를 되풀이한다
    assert "Write" in reason


# 통과할 때는 아무 말도 하지 않아야 한다. 빈 출력이 곧 '허용'이다
def test_safe_command_produces_no_output():
    proc = _run_hook("python - <<'PY'\nprint(1)\nPY")
    assert proc.returncode == 0
    assert proc.stdout.strip() == b""


# 탈출구. 정말 heredoc 이어야 하는 경우에 막히면 안 된다
def test_escape_hatch():
    proc = _run_hook(
        "cat > out.md <<'EOF'\nhello\nEOF",
        env=dict(os.environ, SKIP_HEREDOC="1"),
    )
    assert proc.stdout.strip() == b""


# BOM 이 붙은 입력도 판정돼야 한다. json.load 가 실패하면 "판단 근거 없음" 경로로 빠져
# 조용히 통과하는데, 훅이 죽지도 경고를 내지도 않아 알아챌 방법이 없다.
# `paid_test_check` 가 PowerShell 의 `Out-File -Encoding utf8` 로 실제로 겪은 자리다
def test_bom_prefixed_input_is_still_judged():
    raw = b"\xef\xbb\xbf" + json.dumps(
        {"tool_name": "Bash", "tool_input": {"command": "cat > o.md <<'EOF'\nx\nEOF"}}
    ).encode()
    proc = _run_hook("", raw=raw)
    assert b"deny" in proc.stdout


# 한국어 사유가 cp949 콘솔에서 UnicodeEncodeError 로 죽으면 안 된다.
# 실패 경로에서만 드러나는 종류라 여기서 잠근다
def test_korean_reason_survives_encoding():
    proc = _run_hook(_BACKSLASH_BODY)
    output = json.loads(proc.stdout.decode("utf-8"))
    assert "백슬래시" in output["hookSpecificOutput"]["permissionDecisionReason"]


# 판단이 서지 않으면 막지 않는다 — 훅 입력이 JSON 이 아니면 조용히 통과
def test_unreadable_input_does_not_block():
    proc = _run_hook("", raw=b"not json at all")
    assert proc.returncode == 0
    assert proc.stdout.strip() == b""
