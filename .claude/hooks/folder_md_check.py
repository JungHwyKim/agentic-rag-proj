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

## 큰 지침은 전문 대신 포인터를 싣는다

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

**「전부 포인터로」도 기각했다.** 일곱 중 넷(retrieval 1,907 · cost 2,271 · ingestion 2,414 ·
tests 5,128자)은 지금 전문이 그대로 온다. 전면 포인터는 **되던 넷을 «모델이 따라 읽어야
하는» 상태로 떨어뜨린다.** 그래서 예산까지 전문을 채우고 넘치는 것만 포인터다.

**포인터는 요약이 아니라 리다이렉트다.** 요약을 두면 본문이 고쳐져도 안 따라가 두 번째
진실이 된다 — 뿌리 `CLAUDE.md` 가 폴더 표에 요약을 안 두는 것과 같은 잣대다.

**포인터로 돌린 폴더는 마커를 놓아 준다.** 마커는 «큰 비용을 두 번 치르지 않는다» 는
장치인데 포인터는 큰 비용이 아니고, 집어 버리면 세션에 단 한 번뿐인 넛지를 **모델이 안
따라갈 수도 있는 리다이렉트에** 써 버린다. 그래서 다음 셸 명령이 그 폴더를 다시 물면 다시
시도된다. **집었다가 되돌리는 순서인 것은 원자성 때문이다** — 「먼저 들여다보고 나중에
집는」 순서로 짜면 병렬로 도는 Bash 훅 둘이 같은 폴더에서 **둘 다 전문을 싣는다**(실제로
한 턴에 Bash 를 넷 띄우는 일이 있다).

**`permissionDecision` 을 일부러 안 낸다.** `"allow"` 를 내면 이 훅이 배선된 동안 **모든
셸 명령이 권한 프롬프트를 건너뛴다** — 이 훅이 만들어도 되는 부작용이 아니다.
`additionalContext` 만으로 목적이 달성된다(2026-08-31 실측: 실제로 실린다).

## 폴더당 세션 1회인 것이 이 훅의 절반이다

**없으면 역효과다.** `evaluation/CLAUDE.md` 는 37,141바이트라 `cat` 마다 실으면 다섯 번에
분산 전 뿌리(147,014)보다 나빠진다. 네이티브 지연 로드도 같은 파일을 두 번 읽는다고 두 번
안 넣으므로, 마커는 **그 동작을 흉내 내는 것**이다.

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
    "이 세션에서 폴더마다 한 번만 실립니다."
)

# 예산을 넘겨 본문을 못 실은 지침의 머리말. **경로만 준다 — 요약을 두면 두 번째 진실이 된다**
#
# **`cat` 경고가 장식이 아니다.** 실측(2026-09-01): 쪽지를 받은 모델이 실제로 따라갔는데
# `Read` 가 아니라 `cat` 으로 열었다(auto mode 가 `cat` 을 지시하므로 그쪽이 이긴다).
# 그때는 통했지만 그건 `loader/CLAUDE.md` 가 23,197바이트라 우연히 셸 출력 상한 아래였기
# 때문이고, **`evaluation/CLAUDE.md`(37,141바이트)는 `cat` 하면 앞 2KB만 오고 조용히 실패한다.**
# 그래서 쪽지가 «Read 를 써라» 로 그치면 제일 큰 폴더에서만 안 듣는다
POINTER_HEAD = (
    "아래 폴더에도 지침이 있는데 한 번에 실을 수 있는 크기를 넘어 본문을 못 실었습니다. "
    "그 폴더의 파일을 고치거나 판단이 필요하면 **`Read` 도구로** 여십시오 — "
    "`cat` 으로 열면 큰 파일은 앞부분만 오고 나머지가 아무 에러 없이 빠집니다."
)

SEPARATOR = "\n\n---\n\n"

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


# 그 폴더 몫의 마커 네임스페이스. 경로가 곧 신원이라 해시로 못 박는다
def namespace_for(doc: Path) -> str:
    return NAMESPACE_PREFIX + hashlib.sha256(rel_of(doc).encode("utf-8")).hexdigest()[:16]


# 이 세션에 아직 안 실은 것만. 집는 순간 마커가 생긴다
def unclaimed(docs: list[Path], session_id: str) -> list[Path]:
    return [doc for doc in docs if _hookutil.claim_session(namespace_for(doc), session_id)]


# 지침 하나가 실릴 때의 본문. 못 읽으면 None — 판단이 안 서면 조용히 뺀다
def block_for(doc: Path) -> str | None:
    try:
        body = doc.read_text(encoding="utf-8")
    except OSError:
        return None
    return f"# 폴더 지침: {rel_of(doc)}\n\n{body}"


# 예산을 넘긴 지침을 가리키는 한 줄. **요약이 아니라 경로다**(위 docstring — 요약은 두 번째
# 진실이 된다). 크기를 함께 주는 것은 요약이 아니라 **매번 다시 재는 값**이라 낡지 않고,
# 「이건 커서 `cat` 으로 열면 잘리겠구나」를 모델이 스스로 판단할 재료가 된다
def pointer_for(doc: Path) -> str:
    try:
        size = f" ({doc.stat().st_size:,}바이트)"
    except OSError:
        size = ""
    return f"- `{rel_of(doc)}`{size}"


# 예산 안에서 전문을 채우고 넘치는 것은 포인터로 돌린다. 반환은 (실어 보낼 본문, 포인터로
# 돌린 것들). 얕은 폴더부터 채우는 순서는 `folder_docs_for()` 가 이미 잡아 뒀다
def compose(docs: list[Path]) -> tuple[str, list[Path]]:
    blocks: list[str] = []
    deferred: list[Path] = []
    used = len(HEAD)

    for doc in docs:
        block = block_for(doc)
        if block is None:
            continue

        cost = len(SEPARATOR) + len(block)
        if used + cost <= CONTEXT_BUDGET_CHARS:
            blocks.append(block)
            used += cost
            continue

        # 포인터도 자리를 먹는다. 안 세면 포인터가 많을 때 예산을 넘겨 **잘림을 피하려던
        # 장치가 잘림을 만든다.** 머리말은 처음 한 번만 센다
        used += len(pointer_for(doc)) + 1
        if not deferred:
            used += len(POINTER_HEAD) + 2
        deferred.append(doc)

    parts: list[str] = []
    if blocks:
        parts.append(HEAD + "\n\n" + SEPARATOR.join(blocks))
    if deferred:
        parts.append(POINTER_HEAD + "\n" + "\n".join(pointer_for(doc) for doc in deferred))

    return "\n\n".join(parts), deferred


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

    docs = unclaimed(folder_docs_for(files_in(command)), session_id)
    context, deferred = compose(docs)

    # 포인터로 돌린 폴더는 마커를 놓아 준다 — 다음 셸 명령에서 다시 시도된다(위 docstring).
    # 집었다가 되돌리는 순서인 것은 병렬 훅에서 둘 다 전문을 싣지 않게 하기 위해서다
    for doc in deferred:
        _hookutil.clear_marker(namespace_for(doc), session_id)

    if context:
        emit(context)

    sys.exit(0)


if __name__ == "__main__":
    main()
