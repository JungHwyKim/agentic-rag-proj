"""퀴즈 한 문항의 결과를 `data/quiz_log.jsonl` 에 한 줄 남긴다.

**모델이 손으로 JSON 을 쓰게 두지 않는 이유가 이 파일의 전부다.** 이 기록에는 사람이 매번
기억해야 하는 규칙이 셋 있는데, 셋 다 어겨도 **에러가 안 나고 파일만 조용히 거짓이 된다**.

1. **`mode` 를 반드시 싣는다.** 안 실으면 `style` 이 빈 줄이 *지적이 없었다* 인지 *쓰기라 그
   칸을 아예 안 봤다* 인지 안 갈린다 — 쓰기로만 연습한 회차를 나중에 「말투는 문제없었다」로
   읽게 된다. `--mode` 에 기본값을 안 둔 것은 `append_query_log(source=)` 와 같은 기준이다
2. **쓰기 모드의 `style` 은 `[]` 가 아니라 `null` 이다.** 빈 목록은 *봤는데 없었다* 이고
   `null` 은 *안 봤다* 라 뜻이 다르다. 그래서 쓰기 모드에 `--style` 을 주면 거절한다
3. **칸을 늘 쓴다.** 지적이 없어도 `missed: []` 로 쓴다 — 빼면 *지적이 없었다* 와 *옛
   형식이라 칸이 없다* 가 집계에서 안 갈린다(`query_log` 의 `verdict` 규칙과 같다)

**판정을 안 싣는다.** `retry` 같은 칸을 박으면 그것이 곧 점수이고, 단가를 안 박고 볼 때
곱하는 `cost.jsonl` 의 기준과 어긋난다. 「다시 볼 것」은 읽을 때 파생한다
(`collect.weak_ids()`).

문항 메타(단원·★·질문 원문)는 **인자로 안 받고 노트에서 찾아 채운다** — 부르는 쪽이 옮겨
적으면 그 자리가 두 번째 진실이 되고, 오타가 나도 아무도 모른다.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from quiz.collect import QUIZ_LOG_PATH, QuizConfigError, _force_utf8, collect

# 기록에 싣는 질문 원문의 길이. 되짚을 만큼만 담고 파일을 안 부풀린다
QUESTION_PREFIX = 60

MODES = ("speak", "write")


# 한 문항의 결과를 한 줄로 만든다. `mode` 와 `question_id` 는 기본값이 없다
def build_row(
    *,
    question_id: str,
    mode: str,
    questions: list,
    missed: list[str],
    wrong: list[str],
    overclaim: list[str],
    style: list[str] | None,
    followups_asked: int,
    kind: str = "note",
) -> dict:
    if mode not in MODES:
        raise QuizConfigError(f"mode 는 {' | '.join(MODES)} 중 하나입니다: {mode}")
    if mode == "write" and style:
        raise QuizConfigError(
            "쓰기 모드에서는 말투를 안 봅니다. `--style` 을 빼거나 `--mode speak` 로 남기세요."
        )

    found = next((q for q in questions if q.id == question_id), None)
    if found is None and kind == "note":
        raise QuizConfigError(
            f"노트에 없는 문항 id 입니다: {question_id}\n"
            "  저장소에서 만든 질문이면 --kind repo 를 주세요."
        )

    return {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "id": question_id,
        "file": found.file if found else None,
        "starred": found.starred if found else None,
        "question": (found.question if found else "")[:QUESTION_PREFIX] or None,
        "kind": kind,
        "mode": mode,
        "missed": missed,
        "wrong": wrong,
        "overclaim": overclaim,
        # 쓰기 모드는 이 칸을 아예 안 봤다는 뜻으로 null 이다 (위 규칙 2)
        "style": None if mode == "write" else (style or []),
        "followups_asked": followups_asked,
    }


# 한 줄을 덧붙인다. 폴더가 없으면 만든다
def append_row(row: dict, path: Path | None = None) -> Path:
    target = path or QUIZ_LOG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return target


# CLI. `--mode` 와 `--id` 에 기본값이 없어, 빠뜨리면 조용히 잘못 적히는 대신 죽는다
def main(argv: list[str] | None = None) -> int:
    _force_utf8(sys.stdout)
    _force_utf8(sys.stderr)
    parser = argparse.ArgumentParser(description="퀴즈 한 문항의 결과를 남긴다")
    parser.add_argument("--id", required=True, help="문항 id (quiz.collect 가 낸 것)")
    parser.add_argument("--mode", required=True, choices=MODES, help="speak = 구술 · write = 쓰기")
    parser.add_argument("--missed", action="append", default=[], help="빠뜨린 것 (여러 번 가능)")
    parser.add_argument("--wrong", action="append", default=[], help="사실이 틀린 것")
    parser.add_argument("--overclaim", action="append", default=[], help="참여 수준 선을 넘은 것")
    parser.add_argument("--style", action="append", default=[], help="말투·길이 (구술 전용)")
    parser.add_argument("--followups", type=int, default=0, help="던진 꼬리질문 수")
    parser.add_argument("--kind", default="note", choices=("note", "repo"), help="문항 출처")
    parser.add_argument("--dir", default=None, help="노트 폴더")
    parser.add_argument("--log", default=None, help="기록 파일 (기본: data/quiz_log.jsonl)")
    args = parser.parse_args(argv)

    try:
        result = collect(Path(args.dir) if args.dir else None)
        row = build_row(
            question_id=args.id,
            mode=args.mode,
            questions=result["questions"],
            missed=args.missed,
            wrong=args.wrong,
            overclaim=args.overclaim,
            style=args.style,
            followups_asked=args.followups,
            kind=args.kind,
        )
    except QuizConfigError as exc:
        print(f"[quiz] {exc}", file=sys.stderr)
        return 2

    written = append_row(row, Path(args.log) if args.log else None)
    print(json.dumps({"written": str(written), "row": row}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
