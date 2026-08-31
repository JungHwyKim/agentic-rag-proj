"""폴더별 `CLAUDE.md` 가 **Bash 로 파일을 읽을 때도** 컨텍스트에 닿게 한다.

2026-08-31 에 뿌리 `CLAUDE.md`(147,014바이트)를 폴더별 여덟 개로 쪼갰다. 전제는 «그 폴더
파일을 읽으면 폴더 `CLAUDE.md` 가 자동으로 들어온다» 였는데, 같은 날 **한 세션 안에서
도구만 바꾼 짝**으로 재보니 그것은 **`Read` 도구일 때만 참**이었다.

    Bash `cat` 으로 읽힌 직후  → 그 파일에만 있는 사실을 «모른다»
    이어서 `Read` 로 읽히자    → 안다

**그리고 auto mode 는 «파일은 `cat`·`sed` 로 읽어라» 고 지시한다.** 즉 그 모드에서는 폴더
파일 일곱이 **통째로 안 들어오고 아무 에러도 안 난다** — 분산 작업 전체가 무효가 된다.

## 왜 막지 않고 실어 주나

차단(=`cat` 을 deny 하고 `Read` 를 권한다)도 후보였고 **기각했다.**

  - **우회가 곧 실패다.** `cat f | cat` 한 번이면 통과하는데 **통과한 그 순간이 원래 문제
    상황**이다. `heredoc_write_check` 의 ① 갈래는 우회해도 목적(파일 쓰기)이 달성되지만
    여기서는 반대다
  - **auto mode 와 매 턴 부딪힌다.** 시스템이 «`cat` 을 써라», 훅이 «쓰지 마»
  - 탐지 못 하는 경로가 남는다 — `python -c` 안의 읽기 · 스크립트 · `git show`

주입은 셋 다 없다. 파이프든 리다이렉션이든 가리지 않고, 아무것도 막지 않는다.

## 큰 지침은 나눠서 이어 싣는다

**싣는 것으로 끝이 아니다 — 하네스가 큰 `additionalContext` 를 2KB 프리뷰로 자르고 나머지를
파일로 뺀다.** 훅은 성공한 것처럼 보이는데 내용이 모델에 안 닿아, 이 훅의 첫 판이 실사용에서
그렇게 실패했다(2026-08-31).

**세는 단위가 바이트가 아니라 문자다.** 같은 payload 를 파일로 세면 102,464바이트인데
하네스는 `53.9KB`(= 55,150자 / 1024)로 보고한다 — 한국어가 여기서 바이트당 0.54자라
**바이트로 재면 임계값을 두 배로 잘못 잡는다.** 실측(2026-08-31, `.cache/` 아래 탐침 폴더
아홉. 폴더마다 경로가 달라 마커 네임스페이스도 달라서 한 세션에서 다 잴 수 있다):

    8,192 · 9,216 · 10,000자                                     전문이 온다
    10,001 · 10,006 · 10,011 · 10,030 · 10,050 · 10,075 ·
    10,153 · 10,154 · 10,200 · 10,239 · 10,240 · 11,264 ·
    12,288 · 16,384 · 24,576자                                   2KB 프리뷰

**상수는 정확히 10,000자다** — 10,000 은 통째로 오고 10,001 은 잘린다(2026-08-31, 인접한
두 탐침으로 못 박았다). `CONTEXT_BUDGET_CHARS` 를 그보다 낮게 잡은 것은 그 값이 **하네스
것이라 언제든 내려갈 수 있어서**다.

**두 그럴듯한 가설을 실측이 죽였다. 되살리지 말 것.**

  - **「바이트로 센다」** — 한국어가 섞이면 바이트가 문자보다 크니 자연스러운 추측이고,
    실제로 10,075자(=10,217바이트)가 잘린 것이 «10,240바이트 상한» 처럼 보였다. 그런데
    10,011자는 **10,153바이트**인데도 잘린다. 바이트 상한이면 통과했어야 한다
  - **「훅 stdout(JSON) 을 센다」** — JSON 껍데기 86자를 더하면 10,154자가 정확히
    10,240이 되어 **10 KiB 라는 그럴듯한 상수**가 나온다. 그런데 stdout 이 10,097자뿐인
    10,011자 탐침도 잘린다. 세는 것은 껍데기가 아니라 `additionalContext` 본문이다

**보고되는 크기는 문자수/1024 다**(10,011자 → `9.8KB`). 그래서 `10KB` 근처 숫자를 보고
«10 KiB 가 상한이겠지» 로 읽으면 위 두 가설로 곧장 빠진다 — **상한은 10진수 10,000이다.**

**평범한 Bash 출력 채널은 상한이 다르다** — 같은 판에서 stdout 10,241자가 통째로 왔다.
그래서 **일반 도구 출력으로 이 상한을 대신 재면 안 된다.**

**「지침을 상한 아래로 쪼갠다」는 기각했다 — 상한이 문서당이 아니라 호출당이라 합산된다.**
한 명령이 일곱 폴더를 다 물면 55,150자가 한 번에 실린다(실측). `grep -rn x backend/ tests/`
한 줄이면 두 폴더가 더해지므로, 아무리 잘게 쪼개도 **명령이 폴더를 여럿 무는 순간 넘는다.**

**「짧은 포인터를 대신 싣는다」도 기각했다 — 만들어서 세 판 쟀고 전부 우회당했다**
(2026-09-01, `plan_log/문서-최신성.md` 12회차).

    1차  탐침이 커서 `cat` 이 잘리자 모델이 `Read` 로 되읽었고 **네이티브가 대신 실었다**
    2차  쪽지를 따라갔지만 `Read` 가 아니라 `cat` 이었다 — 통한 것은 그 파일이
         23,197바이트라 **셸 출력 상한 아래였던 우연**이다
    3차  «`cat` 으로 열면 잘린다» 경고와 크기를 넣었더니 **`Grep` 으로 기록 폴더를 뒤져
         그 파일을 아예 안 열었다**

**공통점이 결론이다 — 37KB 파일을 여는 것보다 싼 길이 늘 있고, 쪽지는 `grep` 과 경쟁해서
진다**(`grep` 은 *물어본 것*에 답하고 폴더 지침은 *안 물어본 것*을 준다). 근본 원인은 문구가
아니라 **이 장치의 전제(«모델에게 시키는 일은 지켜질 때도 아닐 때도 있다»)를 어긴 설계**다.
**그래서 이어싣기는 모델의 선택을 회로에서 뺀다** — 시키지 않고 그냥 싣는다.

**「내용이 크다」가 아니라 「훅의 파이프가 좁다」가 옳은 틀이다.** 같은 판에서
`backend/CLAUDE.md` 11,538자가 **네이티브로는 전문이 왔다** — 상한은 이 채널만의 것이다.

**조각은 줄 경계에서만 자르고 순서대로 보낸다.** 「함정 절부터」안은 조각 1의 값어치가 크지만
**어느 절이 중한지를 훅이 판단하게 되고**, 문서마다 절 이름이 달라 조용히 어긋난다.
그리고 **조각마다 「n/N」을 밝힌다** — 안 밝히면 잘린 글이 온전해 보여 뒤에만 있는 함정을
«없다» 로 읽는다.

**`permissionDecision` 을 일부러 안 낸다.** `"allow"` 를 내면 이 훅이 배선된 동안 **모든
셸 명령이 권한 프롬프트를 건너뛴다** — 이 훅이 만들어도 되는 부작용이 아니다.
`additionalContext` 만으로 목적이 달성된다(2026-08-31 실측: 실제로 실린다).

## 같은 조각을 두 번 안 싣는 것이 이 훅의 절반이다

**없으면 역효과다.** `evaluation/CLAUDE.md` 는 37,141바이트라 `cat` 마다 실으면 다섯 번에
분산 전 뿌리(147,014)보다 나빠진다. 네이티브 지연 로드도 같은 파일을 두 번 읽는다고 두 번
안 넣으므로, 마커는 **그 동작을 흉내 내는 것**이다.

**이어싣기가 되면서 마커의 뜻이 «집었나» 에서 «어디까지 보냈나» 로 바뀌었다.** 커서를
*파일에 적힌 숫자*로 두지 않고 **조각 번호를 네임스페이스에 넣은 것**은 원자성 때문이다 —
숫자를 읽고-고치고-쓰면 병렬로 도는 Bash 훅 둘이 같은 조각을 함께 싣는다(한 턴에 Bash 를
넷 띄우는 일이 실제로 있다). 조각마다 이름이 다르면 `claim_session()` 의 `O_EXCL` 이 그대로
쓰이고, 진 쪽은 다음 조각으로 넘어간다.

마커의 대가는 압축이다 — 컨텍스트에서 사라졌는데 마커가 남으면 다시 안 실린다. 그래서
`InstructionsLoaded` 에서 `load_reason == "compact"` 일 때 마커를 지운다. **그 값이 실제로
오는 것은 안 쟀다**(payload 에 `load_reason` 칸이 있고 `nested_traversal` 이 오는 것만
실측했다). 안 와도 **틀리는 방향이 안전하다** — 지금처럼 안 실릴 뿐이다.

## 조상을 전부 모으는 이유

실측(2026-08-31): `rag_pipeline/ingestion/loader/document/pdf.py` 를 `Read` 로 읽었더니
`rag_pipeline/ingestion/CLAUDE.md` 만 실리고 **`…/loader/CLAUDE.md` 는 안 실렸다.** 앞서
이미 로드된 것인지 조상 하나를 건너뛴 것인지는 **못 갈랐다.** 어느 쪽이든 조상을 전부 모아
«이 세션에 아직 안 집은 것» 만 실으면 덮인다 — 이미 실렸으면 한 번 더 실을 뿐이고, 건너뛴
것이면 이 훅이 채운다.

**뿌리 `CLAUDE.md` 는 대상이 아니다.** 늘 로드된다.

**판단이 서지 않으면 아무것도 안 한다** — 입력을 못 읽거나 경로를 못 찾으면 조용히 통과.
다른 훅과 같은 규약이다. 탈출구는 `SKIP_FOLDERMD=1`.

시간 제한 상수를 안 두는 것은 이 훅이 파일 존재 검사와 읽기만 하기 때문이다
(subprocess·git 호출 없음). 나중에 외부 호출을 더하면 `test_every_hook_timeout_is_budgeted`
의 예산 표에도 함께 넣어야 한다.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _hookutil  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 폴더별 메모리 파일 이름. 뿌리 것과 같은 이름이라 뿌리는 따로 뺀다
MEMORY_NAME = "CLAUDE.md"

# 세션 마커 디렉터리 접두사. 폴더마다 달라야 해서 경로를 해시해 붙인다 —
# `_hookutil._marker_path()` 가 네임스페이스를 **디렉터리 이름**으로 쓰므로 슬래시를
# 그대로 넣으면 안 된다
NAMESPACE_PREFIX = "claude_folder_md_"

# 한 번에 실을 수 있는 컨텍스트 상한(문자). 넘으면 하네스가 2KB 프리뷰로 자르고 훅은
# 성공한 것처럼 보인다 — 위 docstring 의 실측 참고. 측정된 경계 (10,000, 10,239] 보다 낮게
# 잡은 것은 그 상수가 우리 것이 아니라서다
CONTEXT_BUDGET_CHARS = 9500

# 실린 글이 어디서 왔는지 밝히는 머리말. 출처를 안 밝히면 모델이 이것을 사용자 지시로 읽는다
HEAD = (
    "방금 셸로 읽은 파일이 속한 폴더에 지침이 있습니다. `Read` 도구가 아니라 "
    "Bash 로 읽으면 이것이 자동으로 안 들어와서, 훅이 대신 싣습니다. "
    "큰 지침은 나눠서 이어 싣고, 같은 조각을 두 번 싣지는 않습니다."
)

SEPARATOR = "\n\n---\n\n"

# 조각 하나가 **혼자서는 반드시 예산에 드는** 크기. 머리말·블록 머리·구분자 몫을 넉넉히 뺀다.
#
# **이 여유가 없으면 그 조각이 영영 안 실린다** — 조각이 예산보다 크면 어떤 명령에서도 자리가
# 안 나고, 커서가 그 자리에 멈춘 채 뒤가 통째로 사라지는데 아무 에러도 안 난다
SLICE_OVERHEAD_CHARS = 400
MAX_SLICE_CHARS = CONTEXT_BUDGET_CHARS - SLICE_OVERHEAD_CHARS

# 명령 문자열에서 경로 후보를 뽑는 정규식. 한글 파일명이 흔한 저장소라 한글을 넣는다.
# 넉넉하게 잡고 **실재하는 파일인지로 거른다** — 여기서 정교하게 굴면 새 형태의 명령이
# 조용히 빠진다
_PATH_TOKEN = re.compile(r"[A-Za-z0-9_.가-힣][A-Za-z0-9_./\\:가-힣-]*")


# 명령에 나온 것 중 저장소 안에 실재하는 파일들. 판단이 안 서면 빈 목록
def files_in(command: str) -> list[Path]:
    found: list[Path] = []
    seen: set[str] = set()

    for raw in _PATH_TOKEN.findall(command):
        token = raw.strip("'\"").replace("\\", "/")
        if not token or token in seen:
            continue
        seen.add(token)

        candidate = Path(token)
        path = candidate if candidate.is_absolute() else PROJECT_ROOT / token
        try:
            if not path.is_file():
                continue
            resolved = path.resolve()
            resolved.relative_to(PROJECT_ROOT)
        except (OSError, ValueError):
            continue

        found.append(resolved)

    return found


# 그 파일들의 조상 폴더에 있는 `CLAUDE.md` 전부. **뿌리 것은 뺀다** — 늘 로드된다
def folder_docs_for(files: list[Path]) -> list[Path]:
    docs: list[Path] = []
    seen: set[Path] = set()

    for path in files:
        for parent in path.parents:
            if parent == PROJECT_ROOT:
                break
            doc = parent / MEMORY_NAME
            if doc.is_file() and doc not in seen:
                seen.add(doc)
                docs.append(doc)

    # 얕은 폴더가 먼저 오게 한다. 넓은 규칙을 읽고 좁은 규칙을 읽는 순서다
    return sorted(docs, key=lambda p: len(p.parts))


# 저장소 기준 posix 경로. 마커 신원과 실리는 글의 출처가 같은 문자열이어야 한다
def rel_of(doc: Path) -> str:
    return doc.relative_to(PROJECT_ROOT).as_posix()


# 그 폴더 몫의 마커 네임스페이스. 경로가 곧 신원이라 해시로 못 박는다.
#
# **조각마다 네임스페이스가 따로인 것이 원자성을 지킨다** — 커서를 «파일에 적힌 숫자» 로 두면
# 읽고-고치고-쓰기가 되어 병렬 훅 둘이 같은 조각을 함께 보낸다. 조각 번호를 이름에 넣으면
# `claim_session()` 의 `O_EXCL` 이 그대로 쓰이고, 진 쪽은 다음 조각으로 넘어간다
def namespace_for(doc: Path, index: int = 0) -> str:
    base = NAMESPACE_PREFIX + hashlib.sha256(rel_of(doc).encode("utf-8")).hexdigest()[:16]
    return base if index == 0 else f"{base}_s{index}"


# 지침 본문을 «혼자서는 예산에 드는» 조각들로 자른다. **줄 경계에서만 자른다** —
# 문장·코드펜스 한가운데가 갈리면 조각 하나가 통째로 못 읽는 글이 된다.
#
# 실측(2026-09-01) 폴더 지침 일곱의 최장 줄은 2,814자로 상한(9,100)의 3분의 1이라 **지금은
# 한 줄이 넘치는 경우가 없다.** 그래도 넘칠 때 그 줄을 통째로 넣으면 그 조각이 예산을 넘겨
# **영영 안 실리므로**, 마지막 수단으로 줄 안에서 자른다
def slices_of(body: str) -> list[str]:
    out: list[str] = []
    current = ""

    for line in body.splitlines(keepends=True):
        while len(line) > MAX_SLICE_CHARS:
            if current:
                out.append(current)
                current = ""
            out.append(line[:MAX_SLICE_CHARS])
            line = line[MAX_SLICE_CHARS:]

        if current and len(current) + len(line) > MAX_SLICE_CHARS:
            out.append(current)
            current = ""
        current += line

    if current:
        out.append(current)
    return out or [""]


# 이 지침의 조각들. 못 읽으면 빈 목록 — 판단이 안 서면 조용히 뺀다
def slices_for(doc: Path) -> list[str]:
    try:
        return slices_of(doc.read_text(encoding="utf-8"))
    except OSError:
        return []


# 조각 하나가 실릴 때의 본문. **몇 조각 중 몇 번째인지 밝힌다** — 안 밝히면 모델이 잘린 글을
# 온전한 것으로 읽고, 뒷부분에만 있는 함정을 «없다» 로 판단한다
def block_for(doc: Path, index: int, total: int) -> str:
    head = f"# 폴더 지침: {rel_of(doc)}"
    if total > 1:
        head += f" ({index + 1}/{total} — 나머지는 이 폴더를 다시 읽을 때 이어서 옵니다)"
    return f"{head}\n\n{slices_for(doc)[index]}"


# 예산 안에서 «아직 안 보낸 다음 조각» 들을 채운다. 반환은 실어 보낼 본문.
#
# 얕은 폴더부터 채우는 순서는 `folder_docs_for()` 가 이미 잡아 뒀다. 한 지침에서 조각을 하나만
# 집는 것은 조각 하나가 이미 예산의 대부분이라 어차피 둘이 안 들어가서다
def compose(docs: list[Path], session_id: str) -> str:
    blocks: list[str] = []
    used = len(HEAD)

    for doc in docs:
        total = len(slices_for(doc))
        if total == 0:
            continue

        for index in range(total):
            namespace = namespace_for(doc, index)
            if _hookutil.is_marked(namespace, session_id):
                continue  # 이미 보낸 조각

            block = block_for(doc, index, total)
            if used + len(SEPARATOR) + len(block) > CONTEXT_BUDGET_CHARS:
                break  # 자리가 없다 — 다음 명령에서 이어서

            # **집는 것과 싣는 것이 한 동작이어야 한다.** 졌으면(병렬 훅이 먼저 집었으면)
            # 싣지 않고 다음 조각으로 넘어간다
            if not _hookutil.claim_session(namespace, session_id):
                continue

            blocks.append(block)
            used += len(SEPARATOR) + len(block)
            break

    return HEAD + "\n\n" + SEPARATOR.join(blocks) if blocks else ""


# 압축으로 컨텍스트가 접히면 마커를 전부 지운다. 그래야 다음 읽기에 다시 실린다
def clear_all_markers(session_id: str) -> None:
    safe = "".join(c for c in session_id if c.isalnum() or c in "-_")[:64]
    if not safe:
        return
    try:
        for folder in (PROJECT_ROOT / ".git").glob(NAMESPACE_PREFIX + "*"):
            marker = folder / safe
            if marker.is_file():
                marker.unlink()
    except OSError:
        return


# 컨텍스트로 흘려보낸다. **`permissionDecision` 은 안 낸다**(위 docstring 참고)
def emit(context: str) -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "additionalContext": context,
                }
            },
            ensure_ascii=False,
        )
    )


# 자기 출력을 스스로 못 박는다. 없으면 한국어 한 글자에 `cp949` 로 죽고, 훅이 죽으면
# 지침이 «안 실린 것» 이 되는데 아무 에러도 안 난다 (CLAUDE.md 의 「출력」 얼굴)
def main() -> None:
    # 삼켜도 되는 자리다 — 못 바꾸면 아래 출력이 죽을 뿐이고, 그건 «지침이 안 실렸다» 와
    # 같은 상태라 세션을 막을 이유가 없다
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8")

    if os.environ.get("SKIP_FOLDERMD"):
        sys.exit(0)

    payload = _hookutil.read_payload()
    if payload is None:
        sys.exit(0)

    session_id = str(payload.get("session_id") or "")
    if not session_id:
        sys.exit(0)

    if payload.get("hook_event_name") == "InstructionsLoaded":
        if payload.get("load_reason") == "compact":
            clear_all_markers(session_id)
        sys.exit(0)

    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        sys.exit(0)

    command = str(tool_input.get("command") or "")
    if not command:
        sys.exit(0)

    context = compose(folder_docs_for(files_in(command)), session_id)
    if context:
        emit(context)

    sys.exit(0)


if __name__ == "__main__":
    main()
