"""산문 골든(`reference`) 라벨을 **사람이 쓰기 쉽게** 차려 준다.

## 이 도구가 하는 것과 안 하는 것

`gold_spans` 는 문서에서 그대로 뜬 원문 조각이라(총길이 중앙 35자) 개조식이고, 답에 필요
없는 것까지 담고, 답의 모양이 아니다. 그것을 **완결 문장으로 옮기는 것은 사람이 한다.**
이 도구는 그 앞에 놓인 기호를 벗겨 «다듬을 거리» 를 만들 뿐이다.

```
Q  행정정보 데이터세트 기록관리 의무는 언제부터 지켜야 하나요?
   gold_span  (시행)공포 후 6개월의 유예기간(기관별 준비 실시, 2020.10.1.)
   초안       공포 후 6개월의 유예기간, 기관별 준비 실시, 2020.10.1.
   사람이     공포 후 6개월의 유예기간을 거쳐 2020년 10월 1일부터 적용됩니다.
```

**종결어미를 안 만든다.** 만들면 초안이 답변처럼 보여서, 읽지 않고 도장만 찍는 길이 생긴다.
초안은 문장이 아니어야 사람이 반드시 손을 댄다.

## LLM 을 안 쓴다

`.claude/wiki/plan_log/평가-세트.md` 2회차가 *"구조로 정답을 자동 생성하는 것"* 을 **자기 채점**으로 기각했고,
골든 답변은 그 잣대에 더 세게 걸린다 — 운영 모델이면 채점 대상과 골든을 같은 모델이 쓰고,
다른 모델이라도 6회차가 «검수로는 말투·어휘 편향이 안 없어진다» 를 이미 겪었다.
그래서 여기 있는 것은 전부 **문자열 규칙**이고, 임계값이 아니라 표기 정리다.

## 되쓰기가 이 파일에서 제일 위험한 자리다

`--write` 는 사람이 라벨한 세트 파일을 고친다. 셋을 지킨다.

1. **`reference_reviewed_by == "human"` 인 레코드는 건드리지 않는다.** 사람이 쓴 골든을
   기계 초안이 덮는 것은 이 세트에서 제일 비싼 것(사람의 시간)을 조용히 지우는 일이다
2. **도장을 안 찍는다.** `reference` 만 채우고 `reference_reviewed_by` 는 `pending` 이다 —
   6회차가 `reviewed_hash` 를 «검수가 끝난 뒤» 백필한 순서 그대로
3. **다른 키·키 순서·주석 줄·빈 줄을 그대로 둔다.** 레코드를 통째로 다시 만들면 이 파일이
   담은 다른 라벨(`note`·`gold_page`·`must_*`)의 순서가 바뀌어 **diff 가 통째로 빨개진다**
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from evaluation.qa_set import QA_SET_PATH, QAItem, load_qa_set

__all__ = ["draft_from_spans", "needs_reference", "fill_drafts"]

QA_SET_HUMAN_PATH = QA_SET_PATH.parent / "qa_set_human.jsonl"

# 조각 앞에 붙는 머리표. 한컴·엑셀 문서가 쓰는 것들이고 **뜻을 안 담는다**
_LEADING_MARKER = re.compile(r"^\s*(?:[*※□○●◦▪▫◯·\-–—]|\(\s*\d+\s*\)|\d+[.)])\s*")
# `(시행)공포 후 …` 처럼 조각 맨 앞에 붙은 짧은 분류 딱지. **뒤에 공백이 없을 때만** 벗긴다 —
# 공백이 있으면 `(주) 아무개` 같은 본문일 수 있어 가른다
_LEADING_TAG = re.compile(r"^\((?P<tag>[^()]{1,6})\)(?=\S)")
# 개조식 화살표. 문장으로 옮기면 쉼표 자리다
_ARROW = re.compile(r"\s*[⇨⇒→▶]\s*")
# 각주 표시 (`관리기준표*` 의 꼬리별). 낱말 끝에 붙은 것만 — 앞에 붙은 것은 머리표가 먹었다
_FOOTNOTE_MARK = re.compile(r"(?<=\S)[*†‡]+(?=\s|$)")
# 글자나 숫자. **이것이 하나도 없으면 뜻을 안 담는다** — 기호만 남은 조각(`***` 처럼 머리표를
# 벗기고도 부스러기가 남는 경우)을 초안으로 실으면 사람이 그 한 글자를 지우는 일부터 한다
_HAS_CONTENT = re.compile(r"[0-9A-Za-z가-힣]")


# 조각 하나를 사람이 다듬을 수 있는 모양으로 편다. **임계값이 아니라 표기 정리다** —
# 규칙마다 «이 기호는 뜻을 안 담는다» 가 근거이고, 고를 여지가 있는 수가 하나도 없다
def _tidy(span: str) -> str:
    text = span.strip()
    text = _LEADING_MARKER.sub("", text)
    text = _LEADING_TAG.sub("", text)
    text = _ARROW.sub(", ", text)
    text = _FOOTNOTE_MARK.sub("", text)
    # 조각 안쪽 괄호는 내용을 담을 수 있어 **안 벗긴다.** 쉼표로 풀지 사람이 정한다
    return re.sub(r"\s+", " ", text).strip(" ,")


# `gold_spans` 를 초안 한 줄로. **종결어미를 안 붙인다**(모듈 docstring 참고)
def draft_from_spans(item: QAItem) -> str:
    pieces = [
        tidy for tidy in (_tidy(span) for span in item.gold_spans) if _HAS_CONTENT.search(tidy)
    ]
    return ", ".join(pieces)


# 이 레코드에 산문 골든을 써야 하나. **부정 예시는 대상이 아니다** — 부정의 골든은 «기권»
# 이고 `summarize()` 의 negative 버킷이 이미 잰다(`qa_set.validate_items` 가 막는 자리와 같다)
def needs_reference(item: QAItem) -> bool:
    return item.answerable and not item.reference.strip()


# 세트 파일에 초안을 채운다. 되쓰기 규약 셋은 모듈 docstring 에 있다.
# 돌려주는 것은 (채운 수, 사람 도장이라 건너뛴 수) 다
def fill_drafts(path: Path, *, write: bool) -> tuple[int, int]:
    raw_lines = path.read_text(encoding="utf-8").splitlines()
    by_id = {item.query_id: item for item in load_qa_set(path).items}

    filled = 0
    protected = 0
    out: list[str] = []
    for line in raw_lines:
        stripped = line.strip()
        # 주석 줄과 빈 줄은 글자 그대로 되싣는다 — 여기가 레코드가 아니다
        if not stripped or stripped.startswith("#"):
            out.append(line)
            continue

        record = json.loads(line)
        item = by_id.get(record.get("query_id"))
        if item is None or not needs_reference(item):
            out.append(line)
            continue
        # 사람이 쓴 골든은 어떤 경우에도 안 덮는다. `needs_reference()` 가 이미 «비었나» 로
        # 걸러 여기 오는 것은 빈 것뿐이지만, **도장이 human 인데 비어 있는 상태**가 실제로
        # 생길 수 있다(사람이 지우고 도장을 안 되돌린 경우). 그때는 손대지 않고 센다
        if record.get("reference_reviewed_by") == "human":
            protected += 1
            out.append(line)
            continue

        draft = draft_from_spans(item)
        if not draft:
            out.append(line)
            continue
        record["reference"] = draft
        # 도장은 안 찍는다 — 키가 없으면 기본값 `pending` 이라 굳이 만들지도 않는다
        out.append(json.dumps(record, ensure_ascii=False))
        filled += 1

    if write and filled:
        # 원본 줄끝을 따른다. 이 파일들은 LF 다
        path.write_text("\n".join(out) + "\n", encoding="utf-8", newline="")
    return filled, protected


# 사람이 읽을 차림표. 초안만 내면 무엇을 보고 고쳐야 할지가 화면에 없다
def _print(path: Path) -> None:
    items = [item for item in load_qa_set(path).items if needs_reference(item)]
    print(f"\n=== 산문 골든이 비어 있는 긍정 질의 {len(items)}건 · {path.name} ===\n")
    for item in items:
        print(f"[{item.query_id}] {item.query}")
        for span in item.gold_spans:
            print(f"    조각   {span}")
        if item.must_values:
            print(f"    값     {' · '.join(item.must_values)}")
        for point in item.must_points:
            print(f"    서술   {point}")
        print(f"    초안   {draft_from_spans(item)}")
        print()
    if items:
        print("초안은 종결어미가 없다 — 완결 문장으로 옮기는 것은 사람이 한다.")
        print("도장(reference_reviewed_by=human · reference_reviewed_hash)은 다듬은 뒤에 찍는다.")


def main() -> int:
    # 부르는 쪽의 `PYTHONIOENCODING` 에 기대지 않는다. 안 붙인 호출에서만 깨지는 규약은
    # 이 저장소가 이미 네 번 밟았다 (CLAUDE.md 「한국어 …」 항목)
    sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="산문 골든(reference) 라벨 초안을 차려 준다")
    parser.add_argument("--qa-set", dest="qa_set", default=str(QA_SET_HUMAN_PATH))
    parser.add_argument(
        "--write",
        action="store_true",
        help="초안을 세트 파일에 채운다. 도장은 안 찍고 사람 도장은 안 덮는다",
    )
    args = parser.parse_args()

    path = Path(args.qa_set)
    if not path.exists():
        print(f"세트가 없다: {path}")
        return 1

    _print(path)
    filled, protected = fill_drafts(path, write=args.write)
    verb = "채웠다" if args.write else "채울 수 있다"
    print(f"초안 {filled}건 {verb}." + (f" 사람 도장이라 건너뛴 것 {protected}건." if protected else ""))
    if filled and not args.write:
        print("실제로 쓰려면 --write 를 붙인다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
