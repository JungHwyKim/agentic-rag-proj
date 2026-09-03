"""기록 폴더에서 아직 안 끝난 것을 모아 JSON 으로 낸다.

**왜 스킬이 아니라 스크립트인가** — 세 기록 폴더 전체가 4.36MB(약 145만 자, 파일 251개)라
"모델이 다 읽는다"가 원리적으로 막힌다(`.claude/wiki/plan_log/프로젝트-컨텍스트.md` 가 *"세 INDEX와 전체
로그를 기본 문맥으로 읽는 것"* 을 같은 이유로 이미 기각했다). 반면 재료는 규약으로 서 있어
(`## 미결` 19/19 파일 · `## 7. 남은 것` 98/102 파일) **뽑는 일이 판단이 아니다.** 그래서 수집은
여기서 결정적으로 하고, 묶기·선행 순서·설명은 `openwork` 스킬이 한다.

**닫힌 것을 판정하지 않는다 — 취소선만 공짜로 뺀다.** 실측(2026-08-29)으로 `## 미결` 불릿 232개
중 **72개(31%)** 가 `~~취소선~~` 으로 닫힘을 표시하고 모양이 `~~**제목**~~ → **N회차에서
닫혔다.**` 로 일정하다. 규약이 이미 있고 지켜지므로 문자열로 뺀다(호출마다 같은 답). 그 밖의
닫힘은 판정하지 않는다 — `.claude/wiki/plan_log/frontend.md` 의 *"배선은 여전히 미결"* 이 다른 파일의 회차로
이미 닫혔듯, **다른 파일이 닫은 것은 이쪽에 안 따라온다.** 그 비율은 아직 모른다. 그래서 이
도구가 내는 것은 「열린 것」이 아니라 **「열린 채로 적힌 것」**이고, 모든 항목이 출처·회차·날짜를
달고 나가 사람이 되짚는다.

**빠지는 갈래가 하나 더 있고, 그것은 닫힘이 아니라 「애초에 일감이 아니다」다** — 커밋·푸시
상태다(`_COMMIT_STATE`). `task-log` 7절이 요구하는 것은 *"다음에 할 일 · 확인 못 한 것 ·
사용자에게 필요한 것"* 인데 커밋 여부가 관행으로 쌓여, 실측(2026-09-01)으로 열린 불릿 726개
중 **39개**가 그것이었다. **기록을 안 고치고 여기서 거른다** — 그 문장은 쓸 때는 사실이었고
CLAUDE.md 가 기록의 불변을 못 박아 뒀다. 지우면 원칙이 깨지고 두면 표가 39건만큼 거짓말한다.
**두 갈래를 `closed_skipped`·`commit_state_skipped` 로 따로 센다** — 한 칸에 합치면 「닫힌 것」과
「일감이 아니던 것」이 섞여 무엇이 줄었는지 못 읽는다.

**`.claude/wiki/verify_log/` 는 재료가 아니다** — `## 남은…` 절이 **0/129** 다. 수치만 담는 곳이라 미완이
원래 안 적히고, 넣어도 0건이다. 축이 없는 것을 재료로 넣지 않는다.

**경로·절 이름이 여기 상수 한 자리에 있고, 어긋나면 양방향으로 시끄럽다.** 폴더 이름이 산문에
박히면 구조 정리 때 표가 **조용히 짧아진다**(에러가 안 난다). 그래서 ① 설정된 폴더가 없으면
`OpenWorkConfigError` 로 죽고 ② 설정에 없는 기록 폴더(= `INDEX.md` 를 품은 위키 하위 디렉터리)가
생기면 경고하고 ③ 설정된 절 이름이 한 파일에서도 안 나오면 경고한다. 한 방향만 막으면 새 폴더가
생겼을 때 그대로 재현된다.

**그 구조 정리가 2026-08-31 에 실제로 일어났고, 그래서 뿌리와 폴더 이름을 갈라 두었다.** 세
폴더가 최상위에서 `WIKI_ROOT` 아래로 내려갔는데 **`Source.folder` 는 여전히 맨 이름이다** —
`feature_of_file()` 이 세는 것이 파일 경로가 아니라 **기록 산문에 쓰인 `plan_log/<기능>.md`**
라서다. 산문 914건은 그때 쓰인 그대로 두는 것이 옳고(그때의 사실이다), 뿌리까지 `folder` 에
넣었으면 그 세기가 전량 0이 되어 **기능 묶음이 조용히 무너진다.** 출력의 `folder` 키가 맨
이름인 것도 같은 이유다.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 기록 세 폴더를 담은 위키의 뿌리. `Source.folder` 는 이 아래의 **맨 이름**이다 —
# 가른 이유는 위 docstring 마지막 문단에 있다(산문의 `plan_log/<기능>.md` 세기).
# 훅 쪽 짝은 `.claude/hooks/_hookutil.py` 의 `WIKI_ROOT` 이고, 두 상수가 어긋나는 방향이
# 서로 반대다 — 저쪽은 늘 막는 쪽으로, 이쪽은 조용히 짧아지는 쪽으로 고장 난다
WIKI_ROOT = ".claude/wiki"

# 불릿 원문을 이만큼까지만 싣는다. 중앙값이 미결 180자·남은 것 97자라 대부분 온전히 들어가고,
# 최대 1,840자짜리 하나가 출력을 통째로 흔드는 것만 막는다
RAW_LIMIT = 400

# 「왜」 인용의 상한. 잘린 것은 `why_truncated` 로 밝힌다 — **말없이 자르면 인용이 아니라 요약이
# 되는데 표는 그것을 인용이라고 말한다**. 원문 전체는 `--raw` 로 든다
WHY_LIMIT = 220


# 설정이 저장소와 어긋났다. **조용히 적게 내놓는 대신 죽는다**
class OpenWorkConfigError(RuntimeError):
    pass


# 미완이 적히는 자리 하나. 폴더·절 이름이 여기 말고 다른 곳에 박히면 안 된다
@dataclass(frozen=True)
class Source:
    folder: str
    heading: str
    # decision = 아직 안 정한 것 / followup = 만들고 난 뒤 남은 뒷정리
    kind: str
    # 파일 하나가 곧 기능인가(plan_log) 아니면 세션인가(task_log)
    file_is_feature: bool


SOURCES: tuple[Source, ...] = (
    Source(folder="plan_log", heading="미결", kind="decision", file_is_feature=True),
    Source(folder="task_log", heading="7. 남은 것", kind="followup", file_is_feature=False),
)

# **기록 폴더인데 일부러 재료가 아닌 것.** 여기 없으면 경고가 뜬다 —
# "빠뜨린 게 아님"을 명시하는 자리이고 `auto_test_check.UNMAPPED_BY_DESIGN` 과 같은 역할이다
NOT_A_SOURCE: dict[str, str] = {
    "verify_log": "수치만 담는 곳이라 미완이 안 적힌다 — 실측으로 '## 남은…' 절이 0/129 파일",
}

# **미완 절이 없는데 일부러 그런 파일.** 폴더 전체가 0건일 때만 시끄러우면 **파일 하나가 절을
# 빠뜨린 것은 조용하고**, 그 파일의 남은 일이 통째로 0건으로 빠진다 — 실측으로
# `2026-08-29-리랭커-점수분포.md` 가 절 번호를 `5. 남은 것` 으로 달아 5건을 한 달 동안
# 숨기고 있었다(고쳤다). 그래서 파일별로도 경고한다.
#
# **면제 목록이 없으면 경고가 상시가 되어 아무도 안 읽는다** — `NOT_A_SOURCE` ·
# `auto_test_check.UNMAPPED_BY_DESIGN` 과 같은 역할이고, "빠뜨린 게 아님"을 사유와 함께
# 적는 자리다. **여기 적힌 파일은 고치지 않기로 한 것이지 규약을 지킨 것이 아니다.**
# 키는 파일 이름이다 — 폴더까지 적을 만큼 이름이 겹치지 않는다
SECTIONLESS_BY_DESIGN: dict[str, str] = {
    "phase1.md": "형식 규약(2026-08-17 .md 전환) 이전의 1단계 완료 보고문. 알맹이(OCR PSM 6 · "
    "600px 임계값 · preserve_interword_spaces)는 loader/content/image_ocr.py 에 살아 있다",
    "routing-diff-시행령개정안.md": "2026-08-14 실파일 라우팅 검증. 규약 이전이고 "
    "성격은 verify_log 쪽이라 7절이 설 자리가 없다",
    "2026-08-27-임베딩-지연-핸드오프.md": "다음 세션에 넘기는 핸드오프 형식이라 절 구성이 "
    "다르다. 머리말이 스스로 «닫혔다 (2026-08-27)» 를 적어 뒀다",
    "2026-09-01-위키-3계층-핸드오프.md": "같은 핸드오프 형식이라 7절이 「배경」이다. "
    "남은 일은 3·4절(무엇을 어떻게 재나 · 판정 기준)에 있고, 기능 축의 미결은 "
    "plan_log/문서-최신성.md 「미결」이 이미 갖는다 — 둘을 함께 세면 같은 일이 두 번 잡힌다",
    "2026-09-03-함정전수-핸드오프.md": "바로 위와 같은 핸드오프 형식이고 같은 기능이다. "
    "남은 일은 3·6절(어떻게 재나 · 판정이 성립하는가)에 있고, 기능 축의 미결은 "
    "plan_log/문서-최신성.md 「미결」이 갖는다",
}

_CLOSED = "~~"

# **커밋·푸시 상태는 일감이 아니라 세션 상태다.** `task-log` 7절이 요구하는 것은 *"다음에 할
# 일 · 확인 못 한 것 · 사용자에게 필요한 것"* 인데 커밋 여부가 관행으로 쌓였고(실측 2026-09-01:
# 열린 불릿 726개 중 **39개**), **쓴 그날이 지나면 낡는다** — `2026-08-22-표-격자구멍.md` 의
# *"커밋 안 했다"* 는 `86a84ca` 로 이미 커밋된 뒤다. 그런데도 표에는 영원히 열린 일로 선다.
#
# **기록을 안 고치고 여기서 거른다.** 그 문장은 쓸 때는 사실이었고, CLAUDE.md 가
# *"기록 파일 안의 옛 경로는 앞으로도 안 고친다 — 그것은 그때의 사실이다"* 로 불변을 못 박아
# 뒀다. 지우면 원칙이 깨지고 두면 표가 39건만큼 거짓말하는데, 거르면 둘 다 안 잃는다.
#
# **`what_of()` 가 낸 머리만 본다.** 불릿 본문까지 훑으면 커밋을 *언급하는* 정상 항목이 함께
# 걸린다 — 실측으로 여섯이 그 자리다(`세트를 커밋할지` · `regression/baseline.json 은
# 커밋되는데 입력은 gitignore 다` · `pre_commit_check 를 실제 커밋 경로로 안 돌렸다` 등).
# **덜 거르는 쪽이 안전한 방향**이라 그 여섯은 일부러 남는다(헛무는 것 0건).
#
# **갈래가 둘인 것은 실측이 시켰다.** 첫 판은 앞줄(조사 + 부정)뿐이었는데, 재는 사이에 피어가
# 커밋한 기록이 `커밋만 했고 푸시는 안 했다` 를 들고 나왔다 — 커밋 낱말과 부정 사이에 다른
# 말이 낀다. 둘째 줄이 그 자리이고, **넓혀도 위 여섯은 그대로 남는 것을 다시 쟀다**
_COMMIT_STATE = re.compile(
    r"^(미?커밋|스테이징|푸시|push)"
    r"((은|는|도|만|하지)?\s*(안|않|이 |없|—|\.|$)"  # 커밋 안 했다 · 커밋하지 않았다 · 커밋
    r"|.*?(안 했|안 함|않았|안 한다))"  # 커밋만 했고 푸시는 안 했다
)

_BOLD_HEAD = re.compile(r"^\*\*(.+?)\*\*")
_WHY = re.compile(r"정하려면\s*[:：]|—\s*이유\s*[:：]|이유\s*[:：]")
_ROUND_HEAD = re.compile(r"^##\s*(\d+)회차\s*—\s*(\d{4}-\d{2}-\d{2})")
_ROUND_IN_TEXT = re.compile(r"(\d+)회차")
_MD_REF = re.compile(r"([A-Za-z가-힣0-9_.-]+\.md)")
_SYMBOL_REF = re.compile(r"`([A-Za-z_][A-Za-z0-9_./]*(?:\(\)|\.py|\.html|\.json|\.jsonl))`")
_ORDER_WORD = re.compile(r"먼저|선행|있어야|앞에|뒤에|막고|막는|딸려|전제|선결|기다")
_FILE_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


# 안 끝난 것 하나
@dataclass
class Item:
    id: str
    kind: str
    group: str | None
    # 이 항목이 어느 묶음에 붙는 근거. explicit=파일을 직접 가리킴 · name=이름이 나옴 · own=제 파일
    group_basis: str | None
    what: str
    # 원문 인용이면 quote, 원문에 없으면 None (스킬이 채우고 summary 로 표시한다)
    why: str | None
    why_basis: str | None
    why_truncated: bool
    source: str
    round: int | None
    date: str | None
    refs: dict[str, list[str]] = field(default_factory=dict)
    raw: str = ""
    raw_truncated: bool = False


# `## <머리>` 부터 다음 `## ` 까지를 뽑는다
def section_of(text: str, heading: str) -> str:
    out: list[str] = []
    inside = False
    for line in text.splitlines():
        if line.startswith("## "):
            inside = line.strip() == "## " + heading
            continue
        if inside:
            out.append(line)
    return "\n".join(out).strip()


# 최상위 `- ` 만 새 불릿으로 본다. 들여쓴 줄은 앞 불릿에 이어 붙이고 빈 줄이 끊는다
def bullets_of(section: str) -> list[str]:
    out: list[str] = []
    current: list[str] = []
    for line in section.splitlines():
        if line.startswith("- "):
            if current:
                out.append(" ".join(current))
            current = [line[2:].strip()]
        elif current and line.strip():
            current.append(line.strip())
        elif current:
            out.append(" ".join(current))
            current = []
    if current:
        out.append(" ".join(current))
    return [b for b in out if b]


# 회차 헤딩에서 {회차: 날짜} 를 만든다. 파일명에 날짜가 없는 plan_log 가 시점을 아는 유일한 자리다
def round_dates(text: str) -> dict[int, str]:
    found: dict[int, str] = {}
    for line in text.splitlines():
        m = _ROUND_HEAD.match(line)
        if m:
            found[int(m.group(1))] = m.group(2)
    return found


# 불릿 앞머리의 **굵은 글씨**가 "뭔지"다. 실측으로 미결 68% · 남은 것 85% 가 이 모양이다
def what_of(bullet: str) -> str:
    m = _BOLD_HEAD.match(bullet)
    if m:
        return m.group(1).strip()
    # 머리가 굵지 않은 32%. 첫 조각을 쓰되 **강조 표시는 벗긴다** — 안 벗기면 표 한가운데에
    # 별표가 그대로 찍힌다(실측: `1회차 몫은 **없음**`)
    head = bullet.split(" — ", 1)[0].replace("**", "")
    return head[:80].strip()


# "왜"는 「정하려면:／이유:」 뒤다. **미결 60% 인데 남은 것은 1% 라** 없으면 None 을 돌려준다 —
# 없는 것을 지어내면 인용과 요약이 한 칸에 섞여 표의 절반이 호출마다 달라진다
def why_of(bullet: str) -> tuple[str | None, str | None, bool]:
    m = _WHY.search(bullet)
    if not m:
        return None, None, False
    tail = bullet[m.end() :].strip()
    if not tail:
        return None, None, False
    return tail[:WHY_LIMIT], "quote", len(tail) > WHY_LIMIT


# 선행을 되짚을 근거. 실측으로 회차 61% · 코드 30% · 파일 21% · 말 17% 이고 셋 중 하나라도 75% 다
def refs_of(bullet: str) -> dict[str, list[str]]:
    rounds = sorted({m for m in _ROUND_IN_TEXT.findall(bullet)}, key=int)
    files = sorted(set(_MD_REF.findall(bullet)))
    symbols = sorted(set(_SYMBOL_REF.findall(bullet)))
    words = sorted(set(_ORDER_WORD.findall(bullet)))
    out = {"rounds": rounds, "files": files, "symbols": symbols, "order_words": words}
    return {k: v for k, v in out.items() if v}


# 파일 전체가 가리키는 기능. **불릿만 보면 82% 가 미상이라(실측)** 파일 링크를 함께 본다 —
# task_log 는 파일이 기능이 아니라 세션이라, 기능은 본문의 plan_log 링크에만 남는다
def feature_of_file(text: str, file_name: str, features: list[str]) -> str | None:
    counts: dict[str, int] = {}
    for feature in features:
        n = text.count(f"plan_log/{feature}.md") + text.count(f"({feature}.md)")
        if n:
            counts[feature] = n
    if not counts:
        return next((f for f in features if f in file_name), None)
    # 여럿 가리키면 제일 자주 나온 것. 동수면 이름순으로 고정해 호출마다 안 흔들리게 한다
    return sorted(counts, key=lambda f: (-counts[f], f))[0]


# task_log 는 파일이 세션이라 기능이 공짜가 아니다. 불릿 → 파일 순으로 되짚어 붙인다
def group_for_followup(
    bullet: str, file_feature: str | None, features: list[str]
) -> tuple[str | None, str | None]:
    for feature in features:
        if f"{feature}.md" in bullet:
            return feature, "explicit"
    for feature in features:
        if feature in bullet:
            return feature, "name"
    if file_feature:
        return file_feature, "file"
    return None, None


# 한 파일에서 열린 항목을 뽑는다. **빠지는 갈래가 둘이고 각각 세어 돌려준다** —
# 세지 않으면 표가 조용히 짧아지는데, 그것이 이 도구의 유일한 실패 모드다
def _items_in_file(
    path: Path, source: Source, features: list[str]
) -> tuple[list[Item], int, int, bool]:
    text = path.read_text(encoding="utf-8")
    section = section_of(text, source.heading)
    if not section:
        return [], 0, 0, False

    dates = round_dates(text)
    latest = dates[max(dates)] if dates else None
    file_date = None
    m = _FILE_DATE.match(path.name)
    if m:
        file_date = m.group(1)

    file_feature = None if source.file_is_feature else feature_of_file(text, path.name, features)

    items: list[Item] = []
    closed = 0
    commit_state = 0
    for index, bullet in enumerate(bullets_of(section)):
        if _CLOSED in bullet:
            closed += 1
            continue
        if _COMMIT_STATE.match(what_of(bullet)):
            commit_state += 1
            continue

        round_no: int | None = None
        marker = re.search(r"\((\d+)회차", bullet)
        if marker:
            round_no = int(marker.group(1))

        if source.file_is_feature:
            group: str | None = path.stem
            basis: str | None = "own"
            date = dates.get(round_no or -1) or latest
        else:
            group, basis = group_for_followup(bullet, file_feature, features)
            date = file_date

        why, why_basis, why_cut = why_of(bullet)
        items.append(
            Item(
                id=f"{source.folder}/{path.name}#{index}",
                kind=source.kind,
                group=group,
                group_basis=basis,
                what=what_of(bullet),
                why=why,
                why_basis=why_basis,
                why_truncated=why_cut,
                source=f"{source.folder}/{path.name}",
                round=round_no,
                date=date,
                refs=refs_of(bullet),
                raw=bullet[:RAW_LIMIT],
                raw_truncated=len(bullet) > RAW_LIMIT,
            )
        )
    return items, closed, commit_state, True


# 기록 폴더 하나의 실제 자리. 뿌리와 맨 이름을 잇는 **유일한** 자리다
def folder_path(root: Path, folder: str) -> Path:
    return root / WIKI_ROOT / folder


# 설정이 저장소와 어긋났는지 본다. 없는 폴더는 죽고, 모르는 기록 폴더와 사라진 절은 경고다
def check_config(root: Path) -> list[str]:
    # 뿌리 자체가 없으면 아래 폴더 검사가 전부 걸려 사유가 세 줄이 된다. 원인은 하나이므로
    # 먼저 가려내 한 줄로 말한다 — 사유가 길면 진짜 원인이 안 읽힌다
    wiki = root / WIKI_ROOT
    if not wiki.is_dir():
        raise OpenWorkConfigError(
            f"위키 뿌리가 없습니다: {WIKI_ROOT}/. "
            "구조를 정리했다면 openwork/collect.py 의 WIKI_ROOT 를 함께 고치세요 — "
            "그러지 않으면 목록이 조용히 짧아집니다."
        )

    missing = [s.folder for s in SOURCES if not folder_path(root, s.folder).is_dir()]
    if missing:
        raise OpenWorkConfigError(
            f"설정된 기록 폴더가 없습니다: {', '.join(missing)} (아래: {WIKI_ROOT}/). "
            "구조를 정리했다면 openwork/collect.py 의 SOURCES 를 함께 고치세요 — "
            "그러지 않으면 목록이 조용히 짧아집니다."
        )

    configured = {s.folder for s in SOURCES} | set(NOT_A_SOURCE)
    warnings: list[str] = []
    # 훑는 곳이 저장소 최상위가 아니라 **위키 안**이다. 최상위를 계속 훑으면 새 기록 폴더가
    # 위키 안에 생겼을 때 이 경고가 한 번도 안 뜬다 — 그것이 이 검사의 존재 이유였다
    for child in sorted(wiki.iterdir()):
        if not child.is_dir() or child.name in configured:
            continue
        if (child / "INDEX.md").is_file():
            warnings.append(
                f"기록 폴더로 보이는데 설정에 없습니다: {WIKI_ROOT}/{child.name}/ "
                "(INDEX.md 를 갖고 있습니다). SOURCES 나 NOT_A_SOURCE 에 넣으세요."
            )
    return warnings


# 저장소 전체에서 열린 항목을 모은다
def collect(root: Path | None = None) -> dict:
    root = root or PROJECT_ROOT
    warnings = check_config(root)

    features = sorted(
        p.stem
        for p in folder_path(root, SOURCES[0].folder).glob("*.md")
        if p.name != "INDEX.md"
    )

    items: list[Item] = []
    stats: list[dict] = []
    for source in SOURCES:
        # **비재귀 glob 이다.** 하위 폴더로 내린 파일은 0건으로 조용히 빠지고, 일부만
        # 내리면 `with_section` 이 0이 아니라 경고조차 안 뜬다 — 그래서 기록 폴더 안은
        # 평평하게 유지한다(나누려면 파일이 아니라 INDEX.md 를 나눈다)
        files = sorted(
            p for p in folder_path(root, source.folder).glob("*.md") if p.name != "INDEX.md"
        )
        closed_total = 0
        commit_state_total = 0
        with_section = 0
        sectionless: list[str] = []
        before = len(items)
        for path in files:
            found, closed, commit_state, had_section = _items_in_file(path, source, features)
            items.extend(found)
            closed_total += closed
            commit_state_total += commit_state
            with_section += int(had_section)
            if not had_section and path.name not in SECTIONLESS_BY_DESIGN:
                sectionless.append(path.name)
        if sectionless:
            warnings.append(
                f"'{source.folder}/' 의 {len(sectionless)}개 파일에 '## {source.heading}' 절이 "
                f"없습니다: {', '.join(sectionless)}. 절 번호가 밀렸으면 그 파일을 고치고, "
                "일부러 규약 밖이면 openwork/collect.py 의 SECTIONLESS_BY_DESIGN 에 사유와 함께 "
                "넣으세요 — 그러지 않으면 그 파일의 남은 일이 0건으로 조용히 빠집니다."
            )
        if with_section == 0:
            warnings.append(
                f"'{source.folder}/' 에서 '## {source.heading}' 절을 한 파일에서도 못 찾았습니다. "
                "절 이름이 바뀌었다면 SOURCES 를 고치세요 — 지금은 0건으로 나옵니다."
            )
        stats.append(
            {
                "folder": source.folder,
                "heading": source.heading,
                "kind": source.kind,
                "files_scanned": len(files),
                "files_with_section": with_section,
                "open_items": len(items) - before,
                "closed_skipped": closed_total,
                # 커밋·푸시 상태라 뺀 수. **닫힘과 따로 세는 것이 요점이다** — 한 칸에 합치면
                # 「닫힌 것」과 「애초에 일감이 아니던 것」이 섞여 어느 쪽이 줄었는지 못 읽는다
                "commit_state_skipped": commit_state_total,
            }
        )

    return {
        "generated_at": datetime.now(UTC).astimezone().isoformat(timespec="seconds"),
        "root": str(root),
        "warnings": warnings,
        "sources": stats,
        "features": features,
        "items": [asdict(i) for i in items],
    }


# 원문을 뺀다. **기본이 빼는 쪽인 것이 요점이다** — 안 빼면 출력이 101KB 라 「전부 읽는다」를
# 스크립트로 옮겨 놓은 꼴이 된다. `what`·`why` 가 표에 쓰는 칸이고 원문은 펼칠 때만 든다
def without_raw(items: list[dict]) -> list[dict]:
    return [{k: v for k, v in item.items() if k not in ("raw", "raw_truncated")} for item in items]


# 묶음별로 접어 낸다. decision 은 다 보이고 followup 은 수만 남기는 것이 기본이다
def fold(result: dict, expand: str | None = None, followups: int = 0) -> dict:
    groups: dict[str, dict] = {}
    for raw in result["items"]:
        name = raw["group"] or "(묶음 미상)"
        bucket = groups.setdefault(
            name, {"group": name, "decisions": [], "followups": [], "followup_count": 0}
        )
        if raw["kind"] == "decision":
            bucket["decisions"].append(raw)
        else:
            bucket["followup_count"] += 1
            bucket["followups"].append(raw)

    ordered: list[dict] = []
    for name in sorted(groups, key=lambda n: (n == "(묶음 미상)", n)):
        bucket = groups[name]
        shown = bucket["followups"] if (expand == name) else bucket["followups"][:followups]
        ordered.append(
            {
                "group": name,
                "decision_count": len(bucket["decisions"]),
                "followup_count": bucket["followup_count"],
                "decisions": bucket["decisions"],
                "followups": shown,
                "followups_folded": bucket["followup_count"] - len(shown),
            }
        )

    folded = dict(result)
    folded.pop("items", None)
    folded["groups"] = ordered
    return folded


# 출력을 UTF-8 로 못 박는다. **Windows 에서 이걸 안 하면 파이프로 나가는 한국어가 cp949 로
# 나가고, 받는 쪽이 UTF-8 로 읽다가 깨진다** — CLAUDE.md 가 「출력」 얼굴로 적어 둔 그 함정이다.
# `PYTHONIOENCODING` 을 부르는 쪽이 지키는 규약으로 두면 안 붙인 호출에서 조용히 깨진다
def _force_utf8(stream) -> None:
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8")


# CLI. 기본은 접힌 JSON 이고 --group 으로 한 묶음을 펼친다
def main(argv: list[str] | None = None) -> int:
    _force_utf8(sys.stdout)
    _force_utf8(sys.stderr)
    parser = argparse.ArgumentParser(description="기록 폴더에서 안 끝난 것을 모은다")
    parser.add_argument("--group", default=None, help="이 묶음의 뒷정리까지 펼친다")
    parser.add_argument(
        "--followups", type=int, default=0, help="묶음마다 미리 보일 뒷정리 개수 (기본 0 = 다 접음)"
    )
    parser.add_argument("--flat", action="store_true", help="접지 않고 항목을 그대로 낸다")
    parser.add_argument("--raw", action="store_true", help="불릿 원문도 싣는다 (기본은 뺀다)")
    parser.add_argument("--pretty", action="store_true", help="들여쓴 JSON (크기가 2배가 된다)")
    parser.add_argument("--root", default=None, help="저장소 경로 (기본: 이 파일 기준)")
    args = parser.parse_args(argv)

    try:
        result = collect(Path(args.root) if args.root else None)
    except OpenWorkConfigError as exc:
        print(f"[openwork] {exc}", file=sys.stderr)
        return 2

    if not args.raw:
        result = dict(result, items=without_raw(result["items"]))
    payload = result if args.flat else fold(result, args.group, args.followups)
    # **기본이 압축인 것이 요점이다** — 들여쓰면 같은 내용이 63KB → 121KB 로 두 배가 된다.
    # 읽는 쪽이 모델이라 들여쓰기가 주는 것은 없고 드는 것만 두 배다
    if args.pretty:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    for warning in result["warnings"]:
        print(f"[openwork] 경고: {warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
