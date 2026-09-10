"""면접 노트의 마크다운에서 질문·모범답안·꼬리질문을 뽑아 JSON 으로 낸다.

**왜 스킬이 아니라 스크립트인가** — 노트를 모델이 통째로 못 읽는다. 실측(2026-09-04,
cl100k)으로 `면접노트.html` 이 **96,382 토큰**이고 `Read` 는 호출당 25,000 에서 자르는데
**잘렸다는 신호가 안 온다**. 반면 재료는 규약으로 서 있어(`#### ★ Q.` 29 · `#### Q.` 25 ·
`*꼬리질문* **"…"**` 108) **뽑는 일이 판단이 아니다.** 그래서 수집은 여기서 결정적으로 하고,
문제를 고르고 채점하는 일은 `quiz` 스킬이 한다.

**왜 HTML 이 아니라 마크다운인가** — `면접노트.html` 이 유일한 원본이고 md 8개는
`_html2md.py` 가 뽑은 파생물인데도 md 를 읽는다. 근거가 셋이다.

1. **한 파일이 `Read` 한 번에 들어온다** — md 는 파일마다 2,227~17,465 토큰이다. HTML 은
   태그·CSS 를 걷어내도 72,220 이라 「태그만 지우면 된다」로 안 풀린다
2. **내용이 안 빠진다** — 생성 md 6개가 HTML 과 질문·꼬리질문 수까지 1:1 이다
   (`index` 8/11 · `search` 6/15 · `agent` 4/11 · `web` 8/17 · `rdb` 6/15 · `net` 8/16)
3. **md 쪽이 오히려 많다** — 손으로 쓴 `01-문서읽기.md` 가 HTML 의 `project`+`read` 보다
   질문 2개·꼬리질문 3개를 더 갖고 있다(14/23 vs 12/20). HTML 만 파싱하면 그 다섯을 놓친다

**파생물이라는 유일한 단점은 mtime 으로 닫되, 경고가 아니라 멈춤이다.** 낡은 답으로 채점하면
사용자가 **맞는 답을 하고도 틀렸다고 듣고**, 그 지적을 믿고 답을 고치면 준비가 거꾸로 간다.
`01-문서읽기.md` 는 손으로 쓴 것이라 이 검사에서 뺀다(HTML 에서 안 뽑히므로 늘 낡아 보인다).

**`id` 를 파일명이 아니라 질문 문장에서 만든다.** 설계하는 40분 사이에 md 파일 이름 일곱 개가
실제로 바뀌었다(`00-인덱스` → `00-시작하기` · `04-관계형DB` → `06-관계형DB`). 파일이나 순번
기반이었으면 그 순간 `data/quiz_log.jsonl` 이 통째로 고아가 된다. 질문 문장을 고치면 여전히
새 문항이 되지만, 기록이 `question` 원문을 함께 담아 사람이 되짚을 수 있다.

**경고는 개수가 아니라 구조로 낸다.** 「질문이 54개인가」로 잠그고 싶어지는데, 노트를 고치면
그 수가 **정당하게** 달라져 헛경보가 상시가 된다 — 오탐은 무력화의 한 형태다. 대신 파서가
깨졌을 때만 나는 신호를 본다. 제일 중요한 것은 **답 본문에 `#` 로 시작하는 줄이 섞였나**인데,
그것이 곧 «못 알아본 제목을 답으로 삼켰다» 다. 첫 시도가 실제로 그렇게 52문항 중 49개만
잡았다(부록 A 의 `A-1`·`A-2`·`A-3` 을 놓쳤다).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 노트 폴더. **절대 경로를 코드에 안 박는다** — 커밋되는 파일이라 남의 머신에서 곧바로
# 거짓이 되고, CLAUDE.md 가 「설정 지문에 머신의 파일 배치가 샜다」로 이미 짚은 함정이다
ENV_NOTE_DIR = "QUIZ_NOTE_DIR"
DEFAULT_NOTE_DIR = Path.home() / "Documents" / "면접준비"

# 원본 HTML. md 가 이것보다 낡았는지를 보는 데만 쓴다 (파싱하지 않는다)
HTML_NAME = "면접노트.html"

# 손으로 쓴 예외. `_html2md.py` 가 안 만드는 파일이라 낡음 검사에서 뺀다 —
# 안 빼면 HTML 을 고칠 때마다 이 파일 때문에 늘 «낡았다» 가 된다
HANDWRITTEN = "01-문서읽기.md"

# 질문 제목. **heading 레벨도 mark 도 고정하지 않는다** — 지금은 생성기가 `####` 로 쓰지만
# 손으로 쓴 파일이 다를 수 있고, HTML 쪽 mark 가 `★`·`Q` 만이 아니었던 전례가 있다
QUESTION_RE = re.compile(r"^(?P<hashes>#{2,6})\s+(?P<star>★\s*)?Q\.\s*(?P<text>\S.*?)\s*$")

# 부록 문항. **모양이 아예 다르다** — `## A-1. 워커를 몇 개로 잡았고 왜 그 숫자인가요` 라
# `Q.` 가 없다. 이것을 단원 제목으로 읽으면 부록의 답과 꼬리질문 7개가 통째로 사라지는데
# **에러가 안 난다**(첫 구현이 실제로 그랬다). `UNIT_RE` 보다 먼저 대야 한다
APPENDIX_RE = re.compile(r"^#{2,6}\s+(?P<label>[A-Z]-\d+)\.\s*(?P<text>\S.*?)\s*$")

# 부록으로 넘기는 줄. `#### Q. 왜 전부 병렬로 안 했나요? → 부록 A-2` 는 **질문이 아니라
# 이정표**다(답이 본문에 없다). 문항으로 세면 답이 빈 문항 셋이 생긴다
POINTER_RE = re.compile(r"→\s*부록\s*(?P<label>[A-Z]-\d+)\s*$")

# 꼬리질문. **줄머리에 앵커를 건다** — 「꼬리질문」이라는 낱말이 본문 산문에도 나온다
FOLLOWUP_RE = re.compile(r'^\*꼬리질문\*\s*\*\*["""]?(?P<text>.+?)[""""]?\*\*\s*$')

# 절 제목. **`#{1,3}` 까지만 받는 것이 이 파서의 유일한 임계값이고, 관측에서 나왔다** —
# 이 노트에서 질문은 늘 `####` 이고 `###` 위는 구조(`### 출처` · `### 예상 질문과 답` ·
# 단원 · 문서)다. 그래서 `#{4,6}` 은 일부러 안 끊는다: 못 알아본 `####` 이 답 본문으로
# 흘러들어 아래 «제목이 섞였다» 검사에 걸린다. 전부 끊으면 그 검사가 죽고, 새 질문 형태가
# 생겼을 때 **답이 통째로 사라지는데 아무 신호도 안 난다**
SECTION_RE = re.compile(r"^#{1,3}\s+(?P<text>\S.*?)\s*$")
# 그중 `#{1,2}` 만 단원 이름이 된다. **`#` 도 받는다** — 부록이 `# 부록 A.` 로 열려서,
# `##` 만 보면 부록 문항이 바로 앞 단원(cleaner)에 달린 것으로 적힌다
UNIT_LEVEL_RE = re.compile(r"^#{1,2}\s")
DOC_TITLE_RE = re.compile(r"^#\s+(?P<text>\S.*?)\s*$")

# 기록. `data/` 는 이미 gitignore 라 면접 연습 기록이 커밋되지 않는다
QUIZ_LOG_PATH = PROJECT_ROOT / "data" / "quiz_log.jsonl"


# 설정이 어긋났다. 조용히 빈 목록을 내는 대신 사유를 달고 죽는다
class QuizConfigError(RuntimeError):
    pass


# 마크다운이 HTML 보다 낡았다. **경고가 아니라 이것으로 멈추는 것이 결정이다**
class QuizStaleError(QuizConfigError):
    pass


# 꼬리질문 하나. 답은 `--answers` 일 때만 실린다
@dataclass
class Followup:
    question: str
    answer: str | None = None


# 문항 하나. `id` 는 질문 문장의 해시라 파일 이름이 바뀌어도 안 흔들린다
@dataclass
class Question:
    id: str
    file: str
    doc_title: str
    unit: str | None
    starred: bool
    question: str
    followup_count: int
    kind: str = "note"
    label: str | None = None
    answer: str | None = None
    followups: list[Followup] = field(default_factory=list)


# 질문 문장에서 안정적인 id 를 만든다. 파일·순번을 안 쓰는 이유는 모듈 docstring 에 있다
def question_id(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:8]


# 노트 폴더를 정한다. 없으면 사유를 달고 죽는다
def note_dir(explicit: str | Path | None = None) -> Path:
    if explicit is not None:
        chosen = Path(explicit)
    else:
        env = os.environ.get(ENV_NOTE_DIR)
        chosen = Path(env) if env else DEFAULT_NOTE_DIR
    if not chosen.is_dir():
        raise QuizConfigError(
            f"노트 폴더가 없습니다: {chosen}\n"
            f"  {ENV_NOTE_DIR} 환경변수나 --dir 로 경로를 알려 주세요."
        )
    return chosen


# 퀴즈 재료가 되는 마크다운 파일들. `_` 로 시작하는 것은 도구·인계 문서라 뺀다
def source_files(directory: Path) -> list[Path]:
    return sorted(p for p in directory.glob("*.md") if not p.name.startswith("_"))


# md 가 HTML 보다 낡았으면 멈춘다. **낡은 답으로 채점하면 맞는 답을 하고도 틀렸다고 듣는다**
def check_freshness(directory: Path) -> None:
    html = directory / HTML_NAME
    if not html.exists():
        return  # HTML 이 없는 폴더(테스트·다른 배치)에서는 잴 것이 없다
    html_mtime = html.stat().st_mtime
    stale = [
        p.name
        for p in source_files(directory)
        if p.name != HANDWRITTEN and p.stat().st_mtime < html_mtime
    ]
    if stale:
        raise QuizStaleError(
            "노트를 고친 뒤 마크다운을 다시 안 뽑았습니다. 먼저 이것을 돌리세요:\n"
            f"  .\\.venv\\Scripts\\python.exe {directory / '_html2md.py'}\n"
            f"  낡은 파일: {', '.join(stale)}"
        )


# 답 본문을 문단으로 정리한다. 빈 줄을 접고 앞뒤 공백을 턴다
def _clean_body(lines: list[str]) -> str:
    text = "\n".join(lines).strip()
    return re.sub(r"\n{3,}", "\n\n", text)


# 마크다운 한 파일에서 문항을 뽑는다. 경고는 개수가 아니라 구조로 낸다
def parse_file(path: Path, warnings: list[str]) -> list[Question]:
    lines = path.read_text(encoding="utf-8").splitlines()
    stem = path.stem

    doc_title = stem
    for line in lines:
        matched = DOC_TITLE_RE.match(line)
        if matched:
            doc_title = matched.group("text")
            break

    questions: list[Question] = []
    pointers: list[str] = []
    unit: str | None = None
    seen_doc_title = False
    current: Question | None = None
    # 지금 글자를 담고 있는 곳. 주 답변이면 None, 꼬리질문이면 그 Followup
    target: Followup | None = None
    buffer: list[str] = []

    # 모아 둔 줄을 지금 대상에 넣는다
    def flush() -> None:
        nonlocal buffer
        body = _clean_body(buffer)
        buffer = []
        if current is None:
            return
        if target is None:
            current.answer = body
        else:
            target.answer = body

    # 문항 하나를 열고 이후의 글자를 그리로 보낸다
    def start(text: str, *, starred: bool, label: str | None) -> Question:
        item = Question(
            id=question_id(text),
            file=stem,
            doc_title=doc_title,
            unit=unit,
            starred=starred,
            question=text,
            followup_count=0,
            label=label,
        )
        questions.append(item)
        return item

    for line in lines:
        question_match = QUESTION_RE.match(line)
        if question_match:
            flush()
            text = question_match.group("text")
            pointer = POINTER_RE.search(text)
            if pointer:
                # 이정표는 문항이 아니다. 가리키는 부록이 실재하는지는 아래에서 본다
                pointers.append(pointer.group("label"))
                current = None
                target = None
                continue
            current = start(text, starred=question_match.group("star") is not None, label=None)
            target = None
            continue

        appendix_match = APPENDIX_RE.match(line)
        if appendix_match:
            flush()
            current = start(
                appendix_match.group("text"), starred=False, label=appendix_match.group("label")
            )
            target = None
            continue

        followup_match = FOLLOWUP_RE.match(line)
        if followup_match:
            if current is None:
                warnings.append(
                    f"{path.name}: 질문보다 먼저 나온 꼬리질문을 버렸습니다 "
                    f"— {followup_match.group('text')[:40]}"
                )
                continue
            flush()
            target = Followup(question=followup_match.group("text"))
            current.followups.append(target)
            continue

        section_match = SECTION_RE.match(line)
        if section_match:
            flush()
            if UNIT_LEVEL_RE.match(line):
                # 맨 앞의 `# 제목` 은 문서 제목이지 단원이 아니다
                if line.startswith("# ") and not seen_doc_title:
                    seen_doc_title = True
                else:
                    unit = section_match.group("text")
            current = None
            target = None
            continue

        if current is not None:
            buffer.append(line)

    flush()

    for item in questions:
        item.followup_count = len(item.followups)
        # **이 검사가 이 파서의 핵심이다** — 답 본문에 제목이 섞였다는 것은 못 알아본 질문
        # 형태를 답으로 삼켰다는 뜻이다. 첫 시도가 부록 A 의 `A-1` 을 그렇게 놓쳤다
        for body, where in [(item.answer, "답")] + [
            (f.answer, f"꼬리답({f.question[:20]})") for f in item.followups
        ]:
            if body and re.search(r"^#{1,6}\s", body, re.M):
                swallowed = next(
                    ln for ln in body.splitlines() if re.match(r"^#{1,6}\s", ln)
                )
                warnings.append(
                    f"{path.name}: 못 알아본 제목이 {where} 본문에 섞였습니다 — {swallowed[:60]}"
                )
        if not item.answer:
            warnings.append(f"{path.name}: 답이 비었습니다 — {item.question[:40]}")
        for follow in item.followups:
            if not follow.answer:
                warnings.append(
                    f"{path.name}: 꼬리답이 비었습니다 — {follow.question[:40]}"
                )

    # **이정표를 조용히 버리지 않는다** — 가리키는 부록이 실재할 때만 버려도 되는 것이라,
    # 안 맞으면 문항 하나가 이유 없이 사라진 것이다
    labels = {q.label for q in questions if q.label}
    for label in pointers:
        if label not in labels:
            warnings.append(
                f"{path.name}: 부록 {label} 로 넘기는 줄이 있는데 그 부록 문항이 없습니다"
            )

    if not questions and "예상 질문" in "\n".join(lines):
        warnings.append(f"{path.name}: 예상 질문 절이 있는데 문항을 하나도 못 뽑았습니다")

    return questions


# 지난 회차에서 지적이 있었던 문항 id 를 읽는다. **`style` 은 안 센다** — 모드에 따라 있을
# 수도 없을 수도 있는 칸이라, 세면 구술로 푼 문항만 약한 것으로 몰린다
def weak_ids(log_path: Path | None = None) -> set[str]:
    path = log_path or QUIZ_LOG_PATH
    if not path.exists():
        return set()
    weak: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if any(row.get(key) for key in ("missed", "wrong", "overclaim")):
            weak.add(row.get("id", ""))
    weak.discard("")
    return weak


# 노트 전체를 훑어 문항과 경고를 낸다
def collect(directory: Path | None = None) -> dict:
    root = note_dir(directory)
    check_freshness(root)
    files = source_files(root)
    if not files:
        raise QuizConfigError(f"마크다운이 하나도 없습니다: {root}")

    warnings: list[str] = []
    questions: list[Question] = []
    for path in files:
        questions.extend(parse_file(path, warnings))

    return {
        "note_dir": str(root),
        "files": [p.stem for p in files],
        "counts": {
            "questions": len(questions),
            "starred": sum(1 for q in questions if q.starred),
            "followups": sum(q.followup_count for q in questions),
        },
        "questions": questions,
        "warnings": warnings,
    }


# 고른다. 순서를 안 섞는 것이 기본이다 — `--count` 만 주면 노트 순서대로 앞에서 낸다
def select(
    questions: list[Question],
    *,
    file: str | None = None,
    starred: bool = False,
    ids: list[str] | None = None,
    weak: set[str] | None = None,
    count: int | None = None,
    shuffle_seed: int | None = None,
) -> list[Question]:
    chosen = list(questions)
    if file:
        chosen = [q for q in chosen if file in q.file]
    if starred:
        chosen = [q for q in chosen if q.starred]
    if ids:
        wanted = set(ids)
        chosen = [q for q in chosen if q.id in wanted]
    if weak is not None:
        chosen = [q for q in chosen if q.id in weak]
    if shuffle_seed is not None:
        import random

        random.Random(shuffle_seed).shuffle(chosen)
    if count is not None:
        chosen = chosen[:count]
    return chosen


# 답을 뺀다. **기본이 빼는 쪽인 것이 요점이다** — 문제를 내는 시점에 모델이 모범답안을 보고
# 있으면 힌트가 샌다. 꼬리질문도 같이 뺀다(질문 문구만으로도 답의 방향이 드러난다)
def without_answers(items: list[dict]) -> list[dict]:
    stripped = []
    for item in items:
        copy = {k: v for k, v in item.items() if k not in ("answer", "followups")}
        stripped.append(copy)
    return stripped


# 출력을 UTF-8 로 못 박는다. **Windows 에서 이걸 안 하면 파이프로 나가는 한국어가 cp949 로
# 나가고 받는 쪽이 UTF-8 로 읽다가 깨진다** — CLAUDE.md 가 「출력」 얼굴로 적어 둔 함정이고,
# 부르는 쪽이 `PYTHONIOENCODING` 을 붙이는 규약으로 두면 안 붙인 호출에서만 조용히 깨진다
def _force_utf8(stream) -> None:
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8")


# CLI. 기본은 답 없는 문항 목록이고 `--answers` 로 채점할 때만 답을 가져온다
def main(argv: list[str] | None = None) -> int:
    _force_utf8(sys.stdout)
    _force_utf8(sys.stderr)
    parser = argparse.ArgumentParser(description="면접 노트에서 퀴즈 문항을 뽑는다")
    parser.add_argument("--dir", default=None, help=f"노트 폴더 (기본: ${ENV_NOTE_DIR} 또는 홈)")
    parser.add_argument("--file", default=None, help="이 단원만 (파일 stem 의 일부)")
    parser.add_argument("--star", action="store_true", help="★ 문항만")
    parser.add_argument("--ids", default=None, help="이 id 만 (쉼표로 구분)")
    parser.add_argument("--weak", action="store_true", help="지난 회차에 지적이 있었던 문항만")
    parser.add_argument("--count", type=int, default=None, help="앞에서 N개만")
    parser.add_argument("--seed", type=int, default=None, help="이 씨앗으로 섞는다")
    parser.add_argument("--answers", action="store_true", help="모범답안·꼬리질문을 싣는다")
    parser.add_argument("--pretty", action="store_true", help="들여쓴 JSON")
    args = parser.parse_args(argv)

    try:
        result = collect(Path(args.dir) if args.dir else None)
    except QuizConfigError as exc:
        print(f"[quiz] {exc}", file=sys.stderr)
        return 2

    chosen = select(
        result["questions"],
        file=args.file,
        starred=args.star,
        ids=[s.strip() for s in args.ids.split(",")] if args.ids else None,
        weak=weak_ids() if args.weak else None,
        count=args.count,
        shuffle_seed=args.seed,
    )

    items = [asdict(q) for q in chosen]
    if not args.answers:
        items = without_answers(items)

    payload = {
        "note_dir": result["note_dir"],
        "counts": result["counts"],
        "selected": len(items),
        "with_answers": args.answers,
        "questions": items,
    }
    if args.pretty:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))

    for warning in result["warnings"]:
        print(f"[quiz] 경고: {warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
