"""heredoc 으로 **파일을 쓰거나 백슬래시를 흘려보내는** 셸 호출을 막는 PreToolUse 훅.

Bash 도구의 heredoc 은 이 저장소에서 **파일을 쓰는 수단으로 신뢰할 수 없다.** 실측이
`.claude/wiki/task_log/` **15개 세션**에 쌓여 있고(전체 101개), 2026-08-29 하루에만 4건이다 —
`도구계층-tavily` · `리랭커-점수분포` · `지지판정-강등` · `판정표-재작성축`.

관측된 실패는 둘이다.

  ① **파일이 통째로 안 만들어진다.** 400줄 모듈을 `cat > file <<'EOF'` 로 쓰려다
     `unexpected EOF while looking for matching '` 로 죽고 파일이 아예 안 생겼다
     (`채점자-민감도`). 같은 형태로 테스트 파일 둘을 잃은 적도 있다(`프론트엔드-1·2회차`).
  ② **백슬래시가 한 겹 벗겨진다.** 따옴표를 씌운 `<<'PY'` 인데도 그렇다. 소스가
     `SyntaxError` 로 죽거나(`질의화면-커서`), 테스트 파일에 `CR CR LF` 710개가 생겼다
     (`preserve-line-endings-when-scripting-edits`).

**둘 다 조용하거나 엉뚱한 곳에서 터지는 것이 이 훅의 이유다.** ①은 다음 명령이 없는
파일을 읽으며 실패하고, ②는 문자열 매칭이 안 맞아 `count()` 가 0을 내거나 줄끝 join 이
엉뚱한 문자로 된다. 원인이 heredoc 이라는 신호가 에러 메시지에 없다.

`CLAUDE.md` · `memory/heredoc-strips-backslashes.md` 에 이미 적혀 있었는데 **그 상태로
당일 4건이 났다.** `paid_test_check.py` 가 남긴 교훈이 정확히 이것이다 — *"다섯 곳에
적어뒀지만 전부 모델을 향한 설득"*. 산문은 시험됐고 졌다. 그래서 훅으로 내린다.

**세 번째 갈래는 일부러 안 막는다.** 백슬래시 없는 짧은 `python <<PY` → stdout 은 같은
세션들에서 멀쩡히 돌았다. 거기까지 막으면 헛경보가 상시가 되고, 그것은
`.claude/wiki/plan_log/하네스-확장.md` 4회차 기준으로 **무력화의 한 형태**다(*"일을 하고도 막히는
게이트는 `SKIP_*=1` 을 습관으로 만든다"*). 그래서 이 훅이 보는 것은 딱 둘이다.

  ① heredoc 본문이 **파일 내용이 되는** 경우 — `cat > f <<EOF` · `tee f <<EOF`.
     Write/Edit 로 100% 대체되므로 오탐이 원리적으로 없다.
  ② heredoc 본문에 **백슬래시가 있는** 경우 — 관측된 기전의 정확한 자리.

**PowerShell 은 ① 만 본다.** matcher 가 양쪽 셸을 덮는 것은
`test_pretooluse_covers_both_shells` 의 요구이고 그 근거는 *"도구 하나만 바꾸면 통째로
우회된다"* 인데, ②(백슬래시 벗겨짐)는 **Bash heredoc 에서만 실측됐다.** 안 잰 것을 잰
것처럼 막지 않는다. PowerShell 의 here-string 은 `git commit -m @'...'@` 처럼 파일과
무관한 정상 용도가 있어(도구 설명이 권하는 형태다) 파일 쓰기일 때만 막는다.

**그런데 그 문장이 2026-09-01 까지 거짓이었다.** 파일 쓰기 판정을 명령 **전체**에 대는 바람에
here-string **본문 글자를 명령으로 읽어**, 바로 그 «정상 용도» 가 사실상 늘 막혔다. 자세한
기전과 기각한 대안은 `powershell_reason()` 주석에 있다. **테스트는 내내 초록이었다** —
`ALLOWED_PS` 의 커밋 케이스에 `>` 가 한 번도 안 나와, 의도는 맞는데 케이스가 실사용을
안 닮았다. 그래서 지금은 **하네스가 요구하는 트레일러가 붙은 진짜 커밋 메시지**가 케이스에 있다.

공통 규약은 다른 훅과 같다 — **판단이 서지 않으면 막지 않는다.** 훅 입력을 못 읽거나
heredoc 의 끝 구분자를 못 찾으면 통과시킨다. 탈출구는 `SKIP_HEREDOC=1` 이다.

시간 제한 상수를 안 두는 것은 이 훅이 정규식만 돌리기 때문이다(subprocess·git 호출 없음).
`test_every_hook_timeout_is_budgeted` 가 `*_TIMEOUT_SEC` 를 예산 표와 대조하므로,
나중에 외부 호출을 더하면 그 표에도 함께 넣어야 한다.
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import NamedTuple

# heredoc 여는 자리. `<<` · `<<-` 와 따옴표 있는/없는 구분자를 모두 잡는다.
#
# `(?!<)` 로 here-string(`<<<`)을 뺀다 — 그쪽은 본문이 없어 이 훅의 대상이 아니다.
# 구분자를 `[A-Za-z_]` 로 시작하게 못 박는 것은 **비트 시프트 오탐을 막기 위해서다** —
# `python -c "x = y << 2"` 의 `<< 2` 가 구분자 `2` 로 잡히면 명령 전체가 본문이 된다
_HEREDOC_OPEN_RE = re.compile(r"<<(?!<)(-?)\s*([\"']?)([A-Za-z_][\w.-]*)\2")

# 파일로 내보내는 리다이렉트. `>&2` 같은 fd 복제는 파일 쓰기가 아니라 뺀다
_FILE_REDIRECT_RE = re.compile(r">>?\s*(?!&)[^\s|&;<>]+")

# 본문을 그대로 흘려보내는 명령. 이 둘일 때만 heredoc 본문이 곧 파일 내용이 된다
_CAT_RE = re.compile(r"(?:^|\||&&|\|\||;)\s*(?:[\w./\\-]*[/\\])?cat\b")
_TEE_RE = re.compile(r"(?:^|\||&&|\|\||;)\s*(?:[\w./\\-]*[/\\])?tee\b\s+(?:-a\s+)?[^\s|&;<>-]")

# PowerShell 에서 그 문자열이 파일이 되는 자리
_PS_FILE_WRITE_RE = re.compile(r"(?:Set-Content|Out-File|Add-Content|New-Item)\b", re.IGNORECASE)


# heredoc 하나. 구분자와 본문, 그리고 그것을 연 줄
class _Heredoc(NamedTuple):
    delimiter: str
    body: str
    opener_line: str


# 명령에서 heredoc 을 전부 뽑는다. 끝 구분자를 못 찾은 것은 담지 않는다 —
# 그건 heredoc 이 아니거나(비트 시프트 등) 어차피 셸이 스스로 죽는 형태라, 여기서
# 추측으로 막으면 오탐만 는다
def _heredocs(command: str) -> list[_Heredoc]:
    lines = command.split("\n")
    found: list[_Heredoc] = []

    for index, line in enumerate(lines):
        for match in _HEREDOC_OPEN_RE.finditer(line):
            strips_tabs, _quote, delimiter = match.group(1), match.group(2), match.group(3)

            body: list[str] = []
            closed = False
            for rest in lines[index + 1 :]:
                candidate = rest.lstrip("\t") if strips_tabs else rest
                # 셸은 구분자만 있는 줄을 끝으로 본다. 뒤 공백은 너그럽게 봐준다 —
                # 여기서 엄격해도 얻는 것이 없고, 못 찾으면 검사를 통째로 건너뛴다
                if candidate.rstrip() == delimiter:
                    closed = True
                    break
                body.append(rest)

            if closed:
                found.append(_Heredoc(delimiter, "\n".join(body), line))

    return found


# heredoc 을 연 줄에서, 그 본문이 파일 내용이 되는지 본다.
#
# `cat <<EOF | python` 처럼 파이프로 넘기는 것은 **파일 쓰기가 아니다** — 본문이 스크립트라
# ② 쪽에서 백슬래시로 걸러진다. 여기서 함께 막으면 위 docstring 의 세 번째 갈래를
# 막는 것이 된다
def _writes_a_file(opener_line: str) -> bool:
    if _TEE_RE.search(opener_line):
        return True
    return bool(_CAT_RE.search(opener_line) and _FILE_REDIRECT_RE.search(opener_line))


# Bash heredoc 검사. 막을 사유가 있으면 문자열로, 없으면 None
def bash_reason(command: str) -> str | None:
    for heredoc in _heredocs(command):
        if _writes_a_file(heredoc.opener_line):
            return (
                f"`<<{heredoc.delimiter}` 본문이 그대로 파일이 됩니다 "
                f"(`{heredoc.opener_line.strip()}`).\n"
                "실측: 400줄 heredoc 이 `unexpected EOF` 로 죽고 **파일이 아예 안 생긴** "
                "사례가 여럿입니다."
            )
        if "\\" in heredoc.body:
            return (
                f"`<<{heredoc.delimiter}` 본문에 백슬래시가 있습니다.\n"
                "실측: 따옴표를 씌운 heredoc 에서도 **항상 한 겹 벗겨집니다** — "
                '`"a\\\\nb"` 를 써도 디스크에는 길이 3 · 백슬래시 0 으로 들어갑니다.'
            )
    return None


# PowerShell here-string 본문. 여는 `@'`/`@"` 부터 **줄 첫머리의** `'@`/`"@` 까지다 —
# 셸이 종료를 그렇게 판정한다. `(?:.*?\r?\n)?` 가 선택인 것은 본문이 빈 here-string
# (`@'` 다음 줄이 바로 `'@`)도 닫힌 것으로 세기 위해서다. 안 그러면 `@'\n'@ > out.md` 가
# «못 찾음» 으로 빠져 **진짜 파일 쓰기가 통과한다**
_PS_HERESTRING_BODY_RE = re.compile(r"@(['\"])[ \t]*\r?\n(?:.*?\r?\n)?\1@", re.DOTALL)


# PowerShell here-string 검사. 파일 쓰기일 때만 막는다 (docstring 의 비대칭 참고).
#
# **본문을 들어내고 그 밖만 본다.** 2026-09-01 전까지는 명령 **전체**에 정규식을 대서
# **here-string 본문 글자를 명령으로 읽었다** — `Co-Authored-By: … <noreply@anthropic.com>`
# 의 닫는 `>` 하나가 `>>?\s*(?!&)[^\s|&;<>]+` 에 걸려(`\s*` 가 줄바꿈을 먹고 종료 기호 `'@`
# 가 대상 파일명 자리에 들어간다) **`git commit -m @'...'@` 가 사실상 늘 막혔다.** 그
# 트레일러는 하네스가 모든 커밋에 요구하므로 우연이 아니라 상시였다. 증상은 `>` 하나가
# 아니다 — 본문에 `Set-Content` 라는 낱말이 있어도, `cat > f.md` 라고 적어도 막혔다.
# **이 훅을 설명하는 커밋 메시지가 이 훅에 막히는 모양이다.**
#
# **Bash 쪽은 처음부터 `opener_line` 에만 댔으므로**(`_writes_a_file()`) 이것은 새 규칙이
# 아니라 **PowerShell 을 Bash 에 맞추는 것**이다. 실측(2026-09-01, 케이스 15) — 오탐 3 → 0,
# 누락 0 유지. **「`>` 뒤에 경로꼴을 요구」안은 기각했다**: `'@` 를 거르려면 경로꼴에서
# 따옴표를 빼야 하는데 그러면 `@'…'@ > "out file.md"` 같은 **진짜 파일 쓰기가 샌다**(누락 1).
# 게다가 `_FILE_REDIRECT_RE` 는 Bash 와 공유라 그쪽까지 함께 움직인다.
#
# **끝 구분자를 못 찾으면 판단하지 않는다** — `_heredocs()` 가 닫힌 것만 모으는 것과 같은
# 규약이고, 2026-09-01 전에는 여기만 어긋나 있었다(여는 자리만 봤다). 안 닫힌 here-string 은
# 셸이 스스로 죽으므로 여기서 추측하면 오탐만 는다
def powershell_reason(command: str) -> str | None:
    outside, closed = _PS_HERESTRING_BODY_RE.subn("@@", command)
    if not closed:
        return None
    if _PS_FILE_WRITE_RE.search(outside) or _FILE_REDIRECT_RE.search(outside):
        return (
            "here-string(`@'...'@`) 본문이 그대로 파일이 됩니다.\n"
            "Bash heredoc 과 같은 자리이고, 도구를 바꿔서 우회할 대상이 아닙니다."
        )
    return None


# 도구 이름에 맞는 검사를 고른다. 모르는 도구면 판단하지 않는다
def blocked_reason(tool_name: str, command: str) -> str | None:
    if tool_name == "PowerShell":
        return powershell_reason(command)
    if tool_name == "Bash":
        return bash_reason(command)
    return None


# 사유를 붙여 거부한다. paid_test_check 의 출력 규약을 그대로 따른다
def _deny(detail: str) -> None:
    # Windows 콘솔 기본 인코딩(cp949)으로는 한국어 사유를 못 내보내 UnicodeEncodeError 로
    # 훅이 죽는다. 실패 경로에서만 드러나므로 여기서 못 박는다
    sys.stdout.reconfigure(encoding="utf-8")
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "heredoc 으로 파일을 쓰려 해서 막았습니다.\n\n"
                        f"{detail}\n\n"
                        "**Write 도구로 파일을 만드세요.** 편집이면 Edit 를 쓰고, "
                        "스크립트를 돌려야 하면 Write 로 `.py` 파일을 만든 뒤 "
                        "`python <파일>` 로 실행하세요.\n"
                        "백슬래시 없는 짧은 `python <<PY` (stdout 으로만 찍는 것)는 "
                        "막지 않습니다.\n"
                        "정말 heredoc 이어야 하면 `SKIP_HEREDOC=1` 을 설정한 뒤 "
                        "다시 실행하세요."
                    ),
                },
                "systemMessage": "heredoc 파일 쓰기를 막았습니다. Write/Edit 를 쓰세요.",
            },
            ensure_ascii=False,
        )
    )
    sys.exit(0)


# 훅 진입점. stdin 으로 받은 도구 호출을 보고 위 두 갈래일 때만 막는다
def main() -> None:
    # utf-8 이 아니라 utf-8-sig 인 이유는 `_hookutil.read_payload()` 주석에 있다 —
    # BOM 이 붙어 오면 json.load 가 ValueError 로 죽고 검사가 조용히 통과한다
    sys.stdin.reconfigure(encoding="utf-8-sig", errors="replace")

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        # 판단 근거가 없다. 막지 않는다 — `_hookutil` 의 공통 규약과 같다
        return

    if os.environ.get("SKIP_HEREDOC"):
        return

    if not isinstance(payload, dict):
        return

    command = str(payload.get("tool_input", {}).get("command", ""))
    if not command:
        return

    reason = blocked_reason(str(payload.get("tool_name", "")), command)
    if reason:
        _deny(reason)


if __name__ == "__main__":
    main()
