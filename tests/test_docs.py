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

# 저장소를 훑을 때 건너뛸 디렉터리
_SKIP_DIRS = frozenset(
    {".venv", ".git", "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules"}
)


# 검사할 문서. 모델이 실제로 지시로 따르는 것만이다.
#
# **`CLAUDE.md` 는 뿌리 하나뿐이다** — 2026-08-31 에 폴더별로 쪼갰다가 2026-09-01 에 되돌렸다.
# 되돌린 이유는 크기가 아니라 **전달 채널**이다: 쪼개면 폴더 지침이 지연 로드 대상이 되는데,
# 셸로 읽으면 그것이 안 걸려 훅으로 메워야 하고 그 훅의 파이프(호출당 10,000자)가 좁아
# **전사 재생 실측으로 (세션,폴더) 짝의 15.9%가 전문을 못 받았다.** 뿌리 한 파일은 세션
# 시작에 통째로 실려 그 사슬이 아예 없다. 근거는 `.claude/wiki/plan_log/문서-최신성.md` 15회차
#
# **위키의 `overview/`·`knowledge/` 는 넣고 기록 3폴더는 안 넣는다**(2026-09-01). 기록 안의
# 옛 경로는 «그때의 사실» 이라 안 고치기로 한 것이라 넣으면 전량 거짓 실패가 되고, 앞 둘은
# **지금 따르는 지시**라 참이어야 한다 — CLAUDE.md 가 *"스킬·이 문서·소스 주석은 기록이
# 아니라 지금 따르는 지시라 참이어야 해서 그때 함께 고쳤다"* 로 이미 그은 선이다
DOC_PATHS: tuple[Path, ...] = (
    PROJECT_ROOT / "CLAUDE.md",
    *sorted((PROJECT_ROOT / ".claude" / "skills").rglob("*.md")),
    *sorted((PROJECT_ROOT / ".claude" / "agents").glob("*.md")),
    *sorted((PROJECT_ROOT / ".claude" / "wiki" / "overview").rglob("*.md")),
    *sorted((PROJECT_ROOT / ".claude" / "wiki" / "knowledge").rglob("*.md")),
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


# 함정 항목의 제목. `- **제목**` 꼴에서 첫 굵은 글씨다
_TRAP_TITLE = re.compile(r"^- \*\*(.+?)\*\*", re.MULTILINE)


# **한 사실은 한 자리에만 산다.** 2026-08-31 에 함정을 폴더별 `CLAUDE.md` 로 내리며 정한
# 규칙이고, 그때는 «두 파일에 같은 제목» 을 쟀다. 2026-09-01 에 한 파일로 되돌리면서
# **«한 파일 안에서 같은 제목이 두 번»** 으로 축을 옮겼다 — 지키려던 것이 파일 경계가 아니라
# **사실의 유일성**이고, 그쪽은 되돌린 뒤에도 그대로 살아 있기 때문이다.
#
# 되돌리기가 곧 **일곱 파일을 한 파일로 부어넣는 편집**이라 중복 위험이 제일 큰 순간이고,
# 그래서 이 검사를 지우는 대신 여기로 옮겼다. 재는 것은 내용이 아니라 **제목 문자열**이다:
# 같은 함정을 두 번 적으면 제목이 같을 수밖에 없고, 제목까지 바꿔 적었다면 이미 다른 항목이다
def test_no_trap_title_appears_twice() -> None:
    doc = PROJECT_ROOT / "CLAUDE.md"
    titles = _TRAP_TITLE.findall(doc.read_text(encoding="utf-8"))

    dupes = sorted({t for t in titles if titles.count(t) > 1})

    assert not dupes, (
        "같은 항목 제목이 CLAUDE.md 안에 두 번 있습니다 — 한 사실은 한 자리에만 삽니다:\n"
        + "\n".join(f"  {t}" for t in dupes)
    )


# 한 경로가 두 갈래에 겹쳐 있으면 어느 사유가 참인지 알 수 없다
def test_exception_groups_do_not_overlap() -> None:
    seen: dict[str, str] = {}
    for group, entries in _EXCEPTION_GROUPS:
        for path in entries:
            assert path not in seen, f"{path} 가 {seen[path]} 와 {group} 에 겹쳐 있다"
            seen[path] = group
# --- 크기: Knowledge·Overview 문서는 `Read` 한 번에 들어와야 한다 ---
#
# `Read` 는 **호출당 25,000토큰**에서 자른다(실측: `.claude/wiki/plan_log/INDEX.md` 99,048자가
# `88160 tokens, cap 25000` 으로 잘렸다 = **1.124 자/토큰**). 잘리면 **그 신호가 사람에게 안
# 가고** 모델이 앞부분만 읽고 답한다 — Knowledge 계층의 목적(*필요한 문서만 골라 읽는다*)이
# 조용히 깨지는 자리다.
#
# **20,000자를 고른 것은 환산비가 틀려도 버티기 때문이다.** 관측점이 하나뿐이라 1.124 를 그대로
# 믿지 않는다 — 20,000자는 관측비에서 17,801토큰(캡의 71%)이고, 비가 **0.8 자/토큰까지
# 나빠져도 정확히 캡**이다. CLAUDE.md 의 *"한국어 청크는 문자 ≈ 토큰이다"* 는 cl100k_base
# (임베딩용) 기준이라 **다른 토크나이저인데**, 관측값이 같은 자리에 떨어졌다.
#
# **범위를 두 폴더로 좁힌 것도 결정이다.** 기록 3폴더는 «파일이 곧 세션/기능» 이라 크기로 못
# 가르고(`plan_log/검색-생성.md` 270,017자), 코드 옆 README(`evaluation/README.md` 47,376자)는
# 코드 소유라 축이 다르다. 저장소 전체에 걸면 예외 목록이 상시가 되어 아무도 안 읽는다 —
# 이 저장소가 `SKIP_*=1` 을 두고 반복해 경계하는 모양이다.
#
# **넘겼을 때의 처방은 폴더 승격이다** — `x.md` → `x/README.md` + 갈래별 파일. 갈래는 크기가
# 아니라 **의미**로 고른다. 승격으로 깨지는 링크는 위 `test_cited_paths_exist` 가 이미 잡는다
MAX_DOC_CHARS = 20_000

WIKI_DOC_ROOTS: tuple[Path, ...] = (
    PROJECT_ROOT / ".claude" / "wiki" / "overview",
    PROJECT_ROOT / ".claude" / "wiki" / "knowledge",
)

# **상한을 넘기는데 일부러 그런 문서.** 사유가 비면 아래 테스트가 죽는다 —
# `PLANNED_FILES` · `openwork.NOT_A_SOURCE` · `auto_test_check.UNMAPPED_BY_DESIGN` 과 같은 자리다
OVERSIZED_BY_DESIGN: dict[str, str] = {}


# 검사할 문서를 실제로 찾았는지. **없으면 이 테스트가 0건을 훑고 조용히 통과한다** —
# 지금 상한을 넘는 문서가 하나도 없어(제일 큰 것이 `knowledge/query/retriever.md`) 이 게이트는
# 순전히 예방이고, 그래서 «대상이 0개면 죽는다» 가 유일한 무장 확인이다.
# `test_documents_to_check_are_present` 와 같은 모양이다
def test_wiki_docs_are_present() -> None:
    found = [p for root in WIKI_DOC_ROOTS for p in root.rglob("*.md")]

    assert found, (
        f"{[str(r) for r in WIKI_DOC_ROOTS]} 아래에 `.md` 가 하나도 없습니다 — "
        "폴더가 사라졌거나 이름이 바뀌었고, 그러면 아래 크기 게이트가 아무것도 안 재면서 통과합니다."
    )


# **본 검사.** Knowledge·Overview 문서가 `Read` 한 번에 들어오는가
def test_wiki_docs_fit_one_read() -> None:
    too_big: list[str] = []
    for root in WIKI_DOC_ROOTS:
        for p in sorted(root.rglob("*.md")):
            rel = p.relative_to(PROJECT_ROOT).as_posix()
            if rel in OVERSIZED_BY_DESIGN:
                continue
            n = len(p.read_text(encoding="utf-8"))
            if n > MAX_DOC_CHARS:
                too_big.append(f"  {rel}  {n:,}자 (상한 {MAX_DOC_CHARS:,})")

    assert not too_big, (
        "`Read` 한 번에 안 들어오는 문서가 있습니다:\n"
        + "\n".join(too_big)
        + "\n\n파일을 폴더로 승격하세요 — `x.md` → `x/README.md` + 갈래별 파일. "
        "갈래는 크기가 아니라 **의미**로 고릅니다(loader 면 포맷, reranker 면 구현체). "
        "일부러 큰 것이면 `OVERSIZED_BY_DESIGN` 에 **사유와 함께** 적으세요."
    )


# **반대 방향.** 크기 예외에 적힌 문서가 상한 아래로 내려왔으면 그 예외는 죽은 것이다
def test_oversize_exceptions_are_still_needed() -> None:
    dead: list[str] = []
    for rel, _ in OVERSIZED_BY_DESIGN.items():
        p = PROJECT_ROOT / rel
        if not p.is_file():
            dead.append(f"  {rel}  (파일이 없다)")
        elif len(p.read_text(encoding="utf-8")) <= MAX_DOC_CHARS:
            dead.append(f"  {rel}  (이제 상한 아래다)")

    assert not dead, (
        "크기 예외가 더 이상 필요 없습니다 — 지우세요:\n" + "\n".join(dead)
    )


# 크기 예외에도 사유가 있어야 한다
def test_every_oversize_exception_states_a_reason() -> None:
    thin = [rel for rel, why in OVERSIZED_BY_DESIGN.items() if len(why.strip()) < 20]

    assert not thin, (
        "`OVERSIZED_BY_DESIGN` 사유가 없거나 너무 짧습니다: "
        + ", ".join(thin)
        + " — **왜 가르지 않는 것이 옳은지**를 다음 사람이 읽고 판단할 수 있어야 합니다."
    )
