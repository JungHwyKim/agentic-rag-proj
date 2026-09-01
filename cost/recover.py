"""지나간 유료 호출을 `.cache/` 에서 되살린다. **되살아나는 만큼만.**

    python -m cost.recover
    python -m cost.recover --cache-dir .cache --since 2026-08-27 --json

비용 로그는 2026-08-29 에 생겼다. 그 전에 쓴 돈의 흔적은 캐시 파일뿐이다 —
**파일 하나 = 유료 호출 한 번(미스), mtime = 그때.**

## 무엇이 되살아나고 무엇이 안 되나

| 캐시 | 판정 |
|---|---|
| `evaluator`·`rewrite`·`answer_judge` | 전부 유료 — 로컬 구현체가 없다 |
| `content` | 전부 유료 — Vision·LlamaParse 만 이 캐시를 쓴다(무료 OCR 은 안 쓴다) |
| `embedding` | **벡터 길이로 가른다** — OpenAI 차원은 유료, 그 밖은 로컬(무료) |
| `rerank` | **못 가른다** — 로컬·Cohere·OpenAI 가 한 폴더를 쓰고 salt 가 해시라 provider 를 못 되짚는다 |
| 생성·웹 검색 | **캐시가 없어 한 건도 못 되살린다** |

**토큰과 금액은 어느 갈래도 못 되살린다.** 캐시가 담는 것은 응답뿐이고 요청도 usage 도
안 담는다. 그래서 이 도구가 내는 것은 **호출 수와 날짜**까지다.

## 안 되는 것을 0으로 안 채운다

`rerank` 를 0으로 두면 안 쓴 것처럼 보이고, 전부 유료로 세면 로컬 실행이 비용으로
둔갑한다 — **두 방향 다 틀리는데 에러가 안 난다.** 그래서 「못 가름」이라는 칸을 따로 둔다.
생성·웹 검색도 같은 이유로 「캐시 없음」이다: 0으로 찍으면 *안 썼다* 로 읽힌다.

Evaluator 가 실패를 *충분/불충분* 이 아닌 **제3의 상태**로 뺀 것과 같은 사고다.

## 결과를 비용 로그에 안 섞는다

`data/cost.jsonl` 은 관측이고 이것은 추정이다. 섞으면 둘 다 못 믿는다. `--json` 은 따로
뽑을 때만 쓴다.

## 앞으로는 필요 없어진다

`rerank` 를 못 가르는 것은 **과거의 문제다.** 2026-08-29 부터 `provider` 가 로그에 실린다.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cost import use_utf8_stdout

__all__ = ["DirVerdict", "main", "scan"]

# **OpenAI 임베딩 모델의 벡터 길이.** 이 저장소의 로컬 임베더(KURE-v1)는 1024 라 겹치지
# 않는다. 겹치는 로컬 모델을 붙이는 날 이 가름이 조용히 틀리므로, 모르는 길이는
# **로컬로 단정하지 않고** 「못 가름」으로 뺀다
_OPENAI_EMBEDDING_DIMS = frozenset({1536, 3072})

# 이 캐시 폴더의 파일은 전부 유료다 — 로컬 구현체가 없거나(판정·재작성·지지판정)
# 무료 갈래가 이 캐시를 아예 안 쓴다(content: OCR 은 캐시를 안 탄다)
_ALL_PAID = ("evaluator", "rewrite", "answer_judge", "content")

# 캐시가 없어 원리적으로 못 되살리는 갈래. **0이 아니라 「캐시 없음」이다**
_NO_CACHE = ("generate", "web_search")


# 캐시 폴더 하나의 판정
@dataclass
class DirVerdict:
    name: str
    # 유료로 확정된 파일 수
    paid: int = 0
    # 무료로 확정된 파일 수
    free: int = 0
    # 유료인지 무료인지 못 가른 파일 수. **0으로 안 채운다** (모듈 docstring 참고)
    unknown: int = 0
    # 유료 파일의 날짜별 분포
    paid_by_day: Counter[str] = field(default_factory=Counter)
    note: str = ""


# mtime 의 날짜. 로컬 시간이다 — 비용 로그의 `at` 은 UTC 라 하루 경계가 어긋날 수 있고,
# 그것을 맞추려면 캐시가 안 담은 정보가 필요하다. 맞추는 대신 **밝힌다**
def _day(path: Path) -> str:
    return dt.datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")


# 임베딩 캐시 파일 하나의 벡터 길이. 못 읽으면 None
def _vector_dim(path: Path) -> int | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    vector = payload.get("vector") if isinstance(payload, dict) else None
    return len(vector) if isinstance(vector, list) else None


# 캐시 디렉터리를 훑어 폴더별 판정을 낸다
def scan(cache_dir: Path, *, since: str | None = None) -> list[DirVerdict]:
    verdicts: list[DirVerdict] = []

    for name in _ALL_PAID:
        folder = cache_dir / name
        verdict = DirVerdict(name=name, note="전부 유료 (무료 갈래가 이 캐시를 안 쓴다)")
        for path in _files(folder, since):
            verdict.paid += 1
            verdict.paid_by_day[_day(path)] += 1
        verdicts.append(verdict)

    embedding = DirVerdict(
        name="embedding",
        note=f"벡터 길이로 가름 (OpenAI {sorted(_OPENAI_EMBEDDING_DIMS)} = 유료, 그 밖 = 로컬)",
    )
    for path in _files(cache_dir / "embedding", since):
        dim = _vector_dim(path)
        if dim is None:
            embedding.unknown += 1
        elif dim in _OPENAI_EMBEDDING_DIMS:
            embedding.paid += 1
            embedding.paid_by_day[_day(path)] += 1
        else:
            embedding.free += 1
    verdicts.append(embedding)

    rerank = DirVerdict(
        name="rerank",
        note="**못 가름** — 로컬·Cohere·OpenAI 가 한 폴더를 쓰고 salt 가 해시라 되짚을 수 없다",
    )
    rerank.unknown = sum(1 for _ in _files(cache_dir / "rerank", since))
    verdicts.append(rerank)

    for name in _NO_CACHE:
        verdicts.append(
            DirVerdict(name=name, note="**캐시 없음** — 호출 흔적이 원리적으로 안 남는다")
        )
    return verdicts


# 그 폴더의 파일들. 없으면 빈 목록이고, `since` 는 mtime 기준이다
def _files(folder: Path, since: str | None) -> list[Path]:
    if not folder.is_dir():
        return []
    files = [path for path in folder.iterdir() if path.is_file()]
    if since:
        files = [path for path in files if _day(path) >= since]
    return files


def _print(verdicts: list[DirVerdict], cache_dir: Path) -> None:
    print(f"캐시: {cache_dir}\n")
    print(f"  {'폴더':<14} {'유료':>7} {'무료':>7} {'못 가름':>8}  비고")
    for verdict in verdicts:
        paid = str(verdict.paid) if verdict.paid or verdict.name in _ALL_PAID else "-"
        free = str(verdict.free) if verdict.free else "-"
        unknown = str(verdict.unknown) if verdict.unknown else "-"
        if verdict.name in _NO_CACHE:
            paid = free = unknown = "-"
        print(f"  {verdict.name:<14} {paid:>7} {free:>7} {unknown:>8}  {verdict.note}")

    days: Counter[str] = Counter()
    for verdict in verdicts:
        days.update(verdict.paid_by_day)
    if days:
        print("\n  날짜별 유료 호출(가른 것만):")
        for day in sorted(days):
            print(f"    {day}  {days[day]:>6}")

    print(
        "\n  **토큰과 금액은 되살릴 수 없다** — 캐시가 응답만 담고 요청도 usage 도 안 담는다.\n"
        "  이 표를 `data/cost.jsonl` 에 섞지 않는다 (한쪽은 관측, 한쪽은 추정)."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="지나간 유료 호출 수를 캐시에서 되살린다")
    parser.add_argument("--cache-dir", type=Path, default=Path(".cache"))
    parser.add_argument("--since", help="이 날짜(YYYY-MM-DD) 이후 mtime 만 본다")
    parser.add_argument("--json", action="store_true", help="표 대신 JSON 으로 낸다")
    args = parser.parse_args(argv)
    use_utf8_stdout()

    verdicts = scan(args.cache_dir, since=args.since)
    if args.json:
        payload: list[dict[str, Any]] = [
            {
                "name": verdict.name,
                "paid": verdict.paid,
                "free": verdict.free,
                "unknown": verdict.unknown,
                "paid_by_day": dict(sorted(verdict.paid_by_day.items())),
                # **키를 늘 쓴다.** 빼면 "못 되살린다"와 "0이었다"가 안 갈린다
                "tokens": None,
                "usd": None,
                "note": verdict.note,
            }
            for verdict in verdicts
        ]
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    else:
        _print(verdicts, args.cache_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
