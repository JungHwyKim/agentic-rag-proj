"""모델이 따르는 문서가 **실재하지 않는 파일을 가리키는가**.

`.claude/wiki/plan_log/문서-최신성.md` 가 이 검사를 2026-08-15 에 **기각했다** — 이유는
*"경로·심볼이 실존하는지는 잡지만, 이번에 실제로 낡은 것은 **산문**이었다"* 이고,
다시 볼 조건은 *"CLAUDE.md 가 인용하는 경로·심볼이 20개를 넘어가면"* 이었다.

**2026-08-31 에 그 조건이 충족됐고(151개 — 7.5배), 재판정 결과가 기각을 뒤집었다.**
실제로 깨진 참조가 하나 있었다 — `.claude/agents/retrieval-specialist.md` 가
`code_reviewer_plan.md` 를 *"논의를 시작하기 전에 읽는다"* 고 지시하는데 그 파일은
2026-08-17 에 작업트리에서 지워졌다. **읽으라고 지시된 파일이 없는 것**이라 기각 사유
(*"산문만 낡았다"*)가 더 이상 성립하지 않는다. 9회차에 근거가 있다.

**심볼은 일부러 안 본다.** 같은 감사에서 CLAUDE.md 인용 151개를 손으로 훑으니 오탐이
12건이었고 그중 **10건이 심볼**이었다 — 외부 라이브러리 8(`find_tables`·`extract`·
`model_dump`·`requestSubmit` 등)과 클래스 2(`def` 로 안 잡힌다). 진짜 결함은 **경로 1건**
뿐이었다. 오탐이 상시인 검사는 `SKIP_*=1` 과 같은 무시하는 습관을 만들어 **무력화의 한
형태**이므로, 잡는 비율이 낮아도 오탐이 0에 가까운 쪽을 고른다.

**대상은 `.claude/` 안과 CLAUDE.md 뿐이다.** `.agents/`·`.codex/`·`AGENTS.md` 는 gitignore 된
사본이라 뺐다 — 드리프트가 들어오는 순간 테스트가 깨지고, 애초에 **다른 머신에는 없다.**

**매치는 접미사다.** 문서가 `loader/detect.py` 처럼 **부분 경로**를 쓰기 때문이다 — 정확
경로만 인정하면 실측으로 47건이 거짓 실패했다(실제 결함은 6건). 그래서 인용은 저장소 어느
파일의 뒤쪽과 맞으면 통과한다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 검사할 문서. 모델이 실제로 지시로 따르는 것만이다
DOC_PATHS: tuple[Path, ...] = (
    PROJECT_ROOT / "CLAUDE.md",
    *sorted((PROJECT_ROOT / ".claude" / "skills").rglob("*.md")),
    *sorted((PROJECT_ROOT / ".claude" / "agents").glob("*.md")),
)

# 저장소를 훑을 때 건너뛸 디렉터리
_SKIP_DIRS = frozenset(
    {".venv", ".git", "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules"}
)

# 백틱 안의 파일 경로. **한글을 넣는다** — `plan_log/검색-생성.md` 같은 실제 경로가
# 인용 41개를 차지하는데 빼면 그만큼이 통째로 안 걸린다(실측으로 오탐은 0 증가).
#
# `<기능>.md` 같은 자리표시자와 `references/*.md` 같은 글로브는 **문자 집합이 `<`·`*` 를
# 안 받아 저절로 빠진다.** 따로 거르지 않는 이유이고, 거르려 들면 그 규칙이 또 낡는다
_CITATION = re.compile(
    r"`([A-Za-z_.가-힣][A-Za-z0-9_/.\-가-힣]*\.(?:py|json|jsonl|html|md|txt|toml))`"
)


# --- 예외: 실재하지 않는 것이 옳은 인용 ---
#
# **사유를 적는 것이 강제다.** 값이 비면 아래 테스트가 죽는다 — `cost/pricing.py` 가
# "단가를 넣거나 못 넣는 이유를 적거나" 둘 중 하나를 강제하는 것과 같은 모양이고,
# `auto_test_check.UNMAPPED_BY_DESIGN` · `openwork.NOT_A_SOURCE` 와 같은 자리다.
# 셋 다 사유가 같다 — **"빠뜨린 게 아님을 명시하는 자리"**.
#
# **갈래를 셋으로 가른 것은 수명이 다르기 때문이다.** 아래 첫 갈래만 유한하다.

# ① 앞으로 만들 파일. **생기면 이 예외를 빼야 한다** — 아래 반대 방향 테스트가 강제한다
PLANNED_FILES: dict[str, str] = {
    "references/retriever.md": (
        "`next-stages.md` 의 「승격 규칙」이 만들 파일. 검색 설계의 «켤 조합» 이 확정되면 승격한다"
    ),
    "retriever.md": (
        "바로 위 `references/retriever.md` 를 짧게 가리킨 같은 파일이다 — "
        "승격 절차 문장이 두 표기를 섞어 쓴다"
    ),
    "generation.md": "같은 승격 규칙의 생성 쪽 몫. 검색과 한 파일로 둘지가 아직 미결이다",
    "sitecustomize.py": (
        "저장소 파일이 아니라 **만드는 방법**이다 — 선택 의존성이 없는 머신을 흉내 내려고 "
        "`sys.meta_path` 에 파인더를 끼울 때 그 자리에 놓는다(CLAUDE.md 의 "
        "`ModuleNotFoundError` 함정)"
    ),
}

# ② 이력에만 있는 파일. 영원히 예외다
HISTORICAL_FILES: dict[str, str] = {
    "code_reviewer_plan.md": (
        "실행이 끝난 지시서라 2026-08-17 에 «커밋한 뒤 삭제» 했다 — 루트에 "
        "*'이렇게 만들어라'* 가 있으면 아직 안 한 일로 착각하기 때문이다. "
        "읽으려면 `git show 3e291e7:code_reviewer_plan.md` (삭제는 `0c0248a`)"
    ),
}

# ③ 없어졌다고 **말하는** 참조. 문장이 그 부재를 서술하므로 실재하면 오히려 이상하다
GONE_FILES: dict[str, str] = {
    "requirements-local.txt": (
        "CLAUDE.md 가 *'그 겹이 사라졌다'* 며 없어진 것으로 언급한다 — 기본 임베더가 "
        "로컬 KURE-v1 이 되면서 `torch` 가 본체로 와 의존성을 가르던 파일이 없어졌다"
    ),
}

_EXCEPTION_GROUPS: tuple[tuple[str, dict[str, str]], ...] = (
    ("PLANNED_FILES", PLANNED_FILES),
    ("HISTORICAL_FILES", HISTORICAL_FILES),
    ("GONE_FILES", GONE_FILES),
)

ALL_EXCEPTIONS: dict[str, str] = {
    path: reason for _, group in _EXCEPTION_GROUPS for path, reason in group.items()
}


# 저장소의 모든 파일을 posix 경로로. 한 번만 훑는다
def _repo_files() -> frozenset[str]:
    return frozenset(
        p.relative_to(PROJECT_ROOT).as_posix()
        for p in PROJECT_ROOT.rglob("*")
        if p.is_file() and not (_SKIP_DIRS & set(p.relative_to(PROJECT_ROOT).parts))
    )


# 선행 `./` 만 떼어낸다. **`lstrip("./")` 를 쓰면 안 된다** — 문자 집합이라
# `.claude/…` 의 앞 점까지 먹어 `claude/…` 가 되고, 그러면 `.claude/` 아래 인용
# 53건이 통째로 거짓 실패한다(실제로 한 번 그렇게 쟀다)
def _normalise(cited: str) -> str:
    return cited[2:] if cited.startswith("./") else cited


# 인용이 저장소 어느 파일의 뒤쪽과 맞나
def _resolves(cited: str, repo_files: frozenset[str]) -> bool:
    target = _normalise(cited)
    return any(f == target or f.endswith("/" + target) for f in repo_files)


# 문서가 인용한 경로 전부. (인용, 그것을 쓴 문서) 쌍으로 낸다
def _citations() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for doc in DOC_PATHS:
        rel = doc.relative_to(PROJECT_ROOT).as_posix()
        for cited in _CITATION.findall(doc.read_text(encoding="utf-8")):
            found.append((cited, rel))
    return found


@pytest.fixture(scope="module")
def repo_files() -> frozenset[str]:
    return _repo_files()


# 검사할 문서가 실제로 있어야 한다. 없으면 이 테스트가 0건을 훑고 조용히 통과한다 —
# 이 저장소가 반복해서 경계하는 실패 모드이고, `openwork.check_config` 와 같은 자리다
def test_documents_to_check_are_present() -> None:
    assert (PROJECT_ROOT / "CLAUDE.md").is_file(), "CLAUDE.md 가 없다"
    assert len(DOC_PATHS) > 10, f"검사 대상 문서가 {len(DOC_PATHS)}개뿐이다 — 경로가 어긋났나"

    citations = _citations()
    assert len(citations) > 100, (
        f"인용을 {len(citations)}개밖에 못 찾았다 — 정규식이 낡았을 수 있다. "
        "2026-08-31 실측은 226개(중복 제거 전 기준 그 이상)다"
    )


# **본 검사.** 문서가 가리키는 파일이 실재하거나, 예외에 사유와 함께 적혀 있어야 한다
def test_cited_paths_exist(repo_files: frozenset[str]) -> None:
    broken: list[str] = []
    for cited, doc in _citations():
        if cited in ALL_EXCEPTIONS or _resolves(cited, repo_files):
            continue
        broken.append(f"  {cited}  ← {doc}")

    assert not broken, (
        "문서가 실재하지 않는 파일을 가리킵니다:\n"
        + "\n".join(sorted(set(broken)))
        + "\n\n파일이 옮겨졌으면 문서를 고치고, 없는 것을 가리키는 것이 옳으면 "
        "tests/test_docs.py 의 예외 셋 중 맞는 갈래에 **사유와 함께** 넣으세요."
    )


# **반대 방향.** 예외에 적힌 경로가 실재하게 되면 그 예외는 죽은 것이다.
#
# 한 방향만 막으면 죽은 예외가 쌓이고, 그것이 이 저장소가 `openwork.check_config` 와
# CLAUDE.md 에서 반복해 경계하는 모양이다 — *"한 방향만 막으면 새 폴더가 생겼을 때
# 그대로 재현된다"*. 특히 `PLANNED_FILES` 는 **생기라고 적어 둔 것**이라 언젠가 반드시 걸린다
def test_exceptions_are_still_needed(repo_files: frozenset[str]) -> None:
    alive = [
        f"  {path}  ({group})"
        for group, entries in _EXCEPTION_GROUPS
        for path in entries
        if _resolves(path, repo_files)
    ]

    assert not alive, (
        "예외로 적힌 경로가 이제 실재합니다 — 그 예외는 더 이상 필요 없으니 지우세요:\n"
        + "\n".join(alive)
        + "\n\n`PLANNED_FILES` 라면 그 파일이 실제로 생겼다는 뜻이고, "
        "승격이 끝났는지 함께 확인하세요."
    )


# 예외에는 **사유가 반드시 있어야 한다.** 빈 문자열이면 «귀찮아서 추가» 를 막지 못한다
@pytest.mark.parametrize(
    ("group", "path"),
    [(group, path) for group, entries in _EXCEPTION_GROUPS for path in entries],
)
def test_every_exception_states_a_reason(group: str, path: str) -> None:
    reason = ALL_EXCEPTIONS[path]
    assert reason.strip(), f"{group} 의 {path} 에 사유가 없다"
    assert len(reason.strip()) >= 20, (
        f"{group} 의 {path} 사유가 너무 짧다: {reason!r} — "
        "**왜 없는 것이 옳은지**를 다음 사람이 읽고 판단할 수 있어야 한다"
    )


# 한 경로가 두 갈래에 겹쳐 있으면 어느 사유가 참인지 알 수 없다
def test_exception_groups_do_not_overlap() -> None:
    seen: dict[str, str] = {}
    for group, entries in _EXCEPTION_GROUPS:
        for path in entries:
            assert path not in seen, f"{path} 가 {seen[path]} 와 {group} 에 겹쳐 있다"
            seen[path] = group
