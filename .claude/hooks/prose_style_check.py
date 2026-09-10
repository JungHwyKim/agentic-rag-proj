"""새로 쓴 글에 영어 직역체와 걷어낸 낱말이 들어갔는지 **센다.**

2026-09-10 에 사용자가 셋을 지적했다 — 설명이 영어를 직역한 느낌이다, 표 헤더가 뜻을 알
수 없다, 「판」이 아직도 뭔지 모르겠다. 규칙은 `.claude/wiki/plan_log/기록-형식.md` 3회차가
정하고, 이 훅은 그 규칙이 새 글에서 지켜지는지만 본다.

## 파일이 아니라 **이번에 쓴 글**을 센다 — 이것이 제일 중요한 결정이다

처음에는 고친 파일을 디스크에서 읽어 통째로 셌다. 그러자 **291개 파일이 울렸다**(실측) —
거의 전부 기록 3폴더의 옛 파일이고, 거기 남은 낱말은 **일부러 안 고치기로 한 것**이다
(「판」 1,967건 · 「자[尺]」 363건). 그 상태로는 기록을 한 줄만 고쳐도 검사가 비명을 지르고,
그러면 `SKIP_PROSECHECK=1` 이 습관이 된다.

그래서 `tool_input` 의 **새 글만** 본다 — `Write` 는 `content`, `Edit` 는 `new_string`.
옛 기록을 고칠 때는 내가 이번에 타이핑한 글자만 걸리고, 새 기록을 만들 때는 전문이 걸린다.
**남는 정당한 오탐은 하나다** — 새 기록이 옛 기록을 인용하면 그 낱말이 딸려 들어온다.
그것까지 막으면 «옛 기록을 인용하지 말라» 가 되는데 그건 규칙이 아니라서, 보고 한 줄이
«인용한 것이면 그대로 둔다» 를 함께 말한다.

## 막지 않는다 — 그것도 설계다

이 저장소에는 차단 훅이 이미 여섯이고, `CLAUDE.md` 가 *"차단 게이트를 셋으로 늘리면
`SKIP_*=1` 이 습관이 된다"* 를 되풀이해 경고한다. 문체 판정은 특히 오탐이 나기 쉽다.

**그래서 코드 0 으로 나가되 출력은 JSON 이다.** 세 갈래를 비교했다.

  코드 2 + stderr    모델에게 닿지만 UI 에 «blocking error» 로 뜬다(`auto_test_check` 가
                     그 모양이다). 상시가 되면 이 훅이 피하려는 바로 그 일이 된다
  코드 0 + 맨 텍스트  안 막지만 **모델에게 안 닿는다** — 전사에만 남아 «알려준다» 가
                     성립하지 않는다. 실제로 첫 구현이 그래서 조용했다
  코드 0 + JSON      `hookSpecificOutput.additionalContext` 로 문맥에 넣는다. 안 막고 닿는다

셋째를 골랐다. 하네스가 그 JSON 을 모르면 원문이 전사에 찍힐 뿐이라 **틀리는 방향이
조용한 쪽**이고, 그것이 이 훅에서 안전한 쪽이다.

## 「판」의 갈래는 이 훅이 못 가른다

한 글자가 다섯 뜻으로 쓰였다(실행 · 파일 형식 · 버전 · 회차 · 관용구). 정규식으로 갈래를
가를 수 없으므로 **세기만 하고 어느 뜻인지는 사람이 읽는다.** 세는 것 자체가 값을 하는 것은
그 낱말을 새로 쓰지 않기로 했기 때문이다 — 갈래가 무엇이든 대신 쓸 말이 정해져 있다.

## 명사화 주어와 관용구는 열거로 잡는다

정규식으로 열면 *어디까지가 명사화인가* 가 곧 임계값이 되고, 이 저장소는 같은 자리에서
정규식을 두 번 기각했다(단위 정규화 · 부정어 창). 열거는 늘어나도 거짓을 안 만든다 —
빠뜨리면 못 잡을 뿐이고, 잘못 잡는 일은 없다.

**목록이 몇 개면 충분한지는 안 쟀다**(`기록-형식.md` 미결). 씨앗은 실측에서 나온 것들이다.

## 동사 「파다·팔다」 는 먼저 걷어낸다

`토큰으로 안 판다` · `이름만 되판다` · `부정도 판다` 는 낱말 「판」이 아니다. 정규식으로
가르려 하면 `다른 판이다` 같은 정상 히트까지 잃으므로, 알려진 동사 문구를 먼저 지운다.
**새 동사 용법은 못 걸러 한 건이 헛집힌다** — 세기만 하므로 대가가 한 줄이다.

건너뛰려면 `SKIP_PROSECHECK=1`.
"""

from __future__ import annotations

import json
import os
import re
import sys

import _hookutil

# 이 훅이 보는 자리. 기록 3폴더를 포함한다 — 새 기록이 곧 제일 큰 소비자다
WATCHED = (
    "CLAUDE.md",
    "AGENTS.md",
    ".claude/wiki/",
    ".claude/skills/",
    ".claude/agents/",
    ".claude/hooks/",
    ".agents/",
    ".codex/",
    "rag_pipeline/",
    "backend/",
    "evaluation/",
    "tests/",
    "cost/",
    "openwork/",
    "quiz/",
)

# 사람이 검수한 라벨과 승인된 기대값. 산문이 아니라 데이터다
NOT_PROSE = (
    "evaluation/qa_set.jsonl",
    "evaluation/qa_set_human.jsonl",
    "evaluation/paraphrase_pairs.jsonl",
    "regression/baseline.json",
)

# 규칙을 **정의하거나 보관하는** 문서. 걷어낸 낱말을 적는 것이 이 파일들의 일이라
# 문구 단위로 걸러낼 수가 없다(표 한 칸이 곧 그 낱말이다).
#
# **목록으로 명시하는 것이 요점이다** — 폴더째로 열면 새로 생긴 규칙 밖 파일이 조용히
# 빠진다(`NON_SHELL_PRETOOLUSE`·`UNMAPPED_BY_DESIGN` 과 같은 모양이다)
RULE_DOCS = (
    ".claude/wiki/plan_log/기록-형식.md",
    ".claude/wiki/overview/glossary.md",
    ".claude/hooks/prose_style_check.py",
    "tests/hooks/test_prose_style_check.py",
)

# 걷어낸 낱말. `판` 은 아래 정규식으로 따로 센다
RETIRED = re.compile(r"자\[尺\]|잣대|비\[比\]")

# 낱말 「판」. 복합어(판정·판단·확장판·초판…)와 동사 「판다」를 앞뒤 글자로 걸러낸다.
# 조사 실측으로 검증한 형태이고, `장`·`되` 와 뒤의 `다` 는 이 훅에서 더했다
PAN = re.compile(r"(?<![재오초조간심비공본장되])판(?!정|단|별|례|매|사|독|권|형|본|다)")

# 동사라 낱말이 아닌 자리. 정규식으로 가르면 정상 히트를 잃어 문구로 지운다
VERB_PHRASES = (
    "않고 판 이유는",
    "토큰으로 안 판다",
    "이름만 되판다",
    "부정도 판다",
)

# 규칙을 설명하는 문구. 「쓰지 않는다」고 적으려면 그 낱말을 적어야 한다
RULE_PHRASES = (
    "낱말 `자[尺]`·`잣대`·`판` 은 쓰지 않는다",
    "낱말 `자[尺]`·`잣대`·`판` 을 쓰지 않는다",
    "낱말 `자[尺]`·`잣대`·`판` 을 쓰지 않고",
    "| 자[尺] |",
    "| 잣대 |",
    "| **자[尺]** |",
    "| **잣대** |",
    "| **판** |",
    "| 판 (",
    # 규칙이 드는 전후 샘플. 스킬·CLAUDE.md·메모리가 같은 문구를 쓴다
    "`그 뒤집힘은` → `순서가 뒤집힌 것은`",
    "(`나머지 반이다`·`거울상이다`)",
    "`나머지 반이다` · `거울상이다` · `두 입구다`",
)

# 동사를 명사로 만들어 주어에 앉힌 자리. 사용자가 이 갈래를 실제 문장에서 집어냈다
NOMINAL_SUBJECTS = (
    "그 뒤집힘",
    "그 밀림",
    "그 어긋남",
    "그 겹침",
    "그 부풀림",
    "그 미끄러짐",
    "그 쏠림",
    "그 흔들림",
    "그 붕괴는",
    "그 흡수가",
)

# 되풀이되면 강조가 아니라 소음이 되는 관용구. 한 문서에 한 번까지다
IDIOMS = (
    "로 읽으면 틀린다",
    "이 항목의 요점",
    "이 칸의 값어치",
    "거울상",
    "나머지 반이",
    "두 입구",
    "같은 축의 두 끝",
)

# 표 헤더의 첫 칸이 그 행이 무엇인지 안 말하는 자리
BAD_HEADER = re.compile(r"^\|\s*(항목)?\s*\|", re.MULTILINE)


# 셀 이유가 없는 파일인가. 저장소 밖·데이터·규칙 문서·범위 밖이면 True
def out_of_scope(rel: str) -> bool:
    if rel in NOT_PROSE or rel in RULE_DOCS:
        return True
    return not any(rel == w or rel.startswith(w) for w in WATCHED)


# 이번에 쓴 글. `Write` 는 `content`, `Edit` 는 `new_string` 이다.
#
# **파일을 안 읽는 것이 이 함수의 요점이다** — 옛 기록에 남은 낱말은 일부러 안 고친
# 것이라 파일을 읽으면 291개 파일이 울린다(모듈 docstring)
def written_text(tool_input: dict) -> str:
    for key in ("content", "new_string"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


# 규칙 문구와 동사를 걷어낸 본문. 이것을 세야 규칙을 적은 파일이 자기 규칙에 안 걸린다
def strip_allowed(text: str) -> str:
    for phrase in RULE_PHRASES + VERB_PHRASES:
        text = text.replace(phrase, "")
    return text


# 갈래별 건수. 0 인 갈래는 넣지 않는다
def count(text: str) -> dict[str, int]:
    body = strip_allowed(text)
    found: dict[str, int] = {}

    if n := len(RETIRED.findall(body)):
        found["자[尺]·잣대"] = n
    if n := len(PAN.findall(body)):
        found["낱말 판"] = n
    if n := len(BAD_HEADER.findall(body)):
        found["표 헤더 첫 칸(항목·빈 칸)"] = n

    nominal = sum(body.count(s) for s in NOMINAL_SUBJECTS)
    if nominal:
        found["명사화 주어"] = nominal

    # 관용구는 **되풀이만** 센다 — 한 번은 규칙이 허용한다
    repeats = sum(c - 1 for s in IDIOMS if (c := body.count(s)) > 1)
    if repeats:
        found["되풀이된 관용구"] = repeats

    return found


# 한 줄 보고. 막지 않으므로 사유가 아니라 관측이다
def report(rel: str, found: dict[str, int]) -> str:
    parts = ", ".join(f"{name} {n}" for name, n in found.items())
    return (
        f"[산문 검사] {rel} — {parts}. "
        "규칙은 .claude/wiki/plan_log/기록-형식.md 3회차. "
        "옛 기록을 인용한 것이면 그대로 둔다"
    )


# 안 막고 문맥에만 넣는 출력. 모듈 docstring 의 «세 갈래» 중 셋째다
def context_payload(rel: str, found: dict[str, int]) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": report(rel, found),
        }
    }


def main() -> int:
    if os.environ.get("SKIP_PROSECHECK"):
        return 0

    payload = _hookutil.read_payload()
    if payload is None:
        return 0

    tool_input = payload.get("tool_input") or {}
    rel = _hookutil.relative_to_root(tool_input.get("file_path") or "")
    if rel is None or out_of_scope(rel):
        return 0

    text = written_text(tool_input)
    if not text:
        return 0

    found = count(text)
    if not found:
        return 0

    # Windows 콘솔 기본 인코딩(cp949)으로는 한국어를 못 내보내 UnicodeEncodeError 로 훅이
    # 죽는다. 죽어도 파일은 이미 써졌지만 «검사가 있는 줄 알았는데 없는» 상태가 된다
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(context_payload(rel, found), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
