"""정답 질의 세트의 레코드 스키마와 로더.

## 왜 `chunk_id` 가 아니라 `gold_spans` 인가

`plan_log/평가-세트.md` 2회차. 요약하면 둘이다.

1. `chunk_id` 는 `{document_key}:{chunk_index:04d}` 라 **청킹을 고치면 세트가 통째로 무효**가
   된다. 그런데 청크 상한 재판정이 이 세트의 목적 중 하나다 — 정답이 chunk_id 면 자기 자신을
   못 잰다
2. 같은 문서의 HWPX 판과 PDF 판은 chunk_id 가 **애초에 다르다.** 양쪽에 따로 정답을 매기면
   두 포맷을 비교하는 게 아니라 **두 세트를 비교하는 것**이 된다

## `gold_spans` 는 "아무거나 하나" 가 아니라 "전부" 다

`recall` 의 분모라서다(`scoring.py`). 한 질의의 답이 여러 조각이면 전부 적고, 답이 하나면
하나만 적는다. **같은 사실의 다른 표현을 나열하는 칸이 아니다** — 그렇게 쓰면 recall 이
"표현 몇 개를 맞췄나"가 되어 뜻을 잃는다.

## `reviewed_by` 는 **그때 본 문장**에만 붙는다

사람이 검수한 뒤에 질의나 정답을 고치면 `reviewed_by="human"` 이 그대로 남는다. 그러면
**검수자가 보지 않은 문장에 검수 도장이 찍힌다** — 세트에서 제일 비싼 것이 사람의 시간인데
그것을 조용히 무효로 만드는 자리다.

그래서 `reviewed_hash` 에 검수 시점의 `query + gold_spans` 해시를 함께 적는다. 내용이 바뀌면
해시가 어긋나고 `validate_items()` 가 **재검수하라고 말한다.** 값 자체에 뜻은 없고 "같은가"만
본다.

## 정답 청크는 **하나**여야 한다

같은 span 이 문서 안에서 되풀이되면 정답 청크가 둘·셋이 되고, 그 질의는 **다른 것을 잰다** —
정답이 셋이면 상위 k 안에 들 확률이 그만큼 높아 그 그룹 점수만 후해진다. 실측으로 신뢰가능
AI 가이드가 요약절과 상세절에 같은 문장을 되풀이해 15건 중 10건이 그 상태였다.

**다만 인접한 청크는 하나로 센다.** `chunk_overlap_chars` 가 상한 초과로 쪼갠 구간에 겹침을
주므로, 겹친 자리에 걸린 span 은 인접한 두 청크에 **정상적으로** 나타난다. 실측으로 둘이
깨끗하게 갈렸다 — 다중 출현 24건 중 겹침은 `sih-05`(PDF 청크 17·18, 같은 5쪽) 하나뿐이고
나머지는 전부 비연속이다(`ai-02` 는 청크 5·21·23, 쪽 9·22·40).

**쪽으로 판정하면 안 된다.** `sih-08` 은 같은 1쪽 안에서 되풀이돼 쪽으로는 안 갈린다.

## 모든 span 은 그룹의 **모든 포맷**에서 찾을 수 있어야 한다

포맷 비교(`plan_log/검색-생성.md` 비교 5번)가 성립하는 조건이다. HWPX 에만 있는 문장을
정답으로 삼으면 PDF 판은 그 질의를 영원히 못 맞히고, 그 차이는 검색 성능이 아니라 **정답이
한쪽에 없어서** 생긴 것이다. `validate_spans_across_formats()` 가 그것을 본다.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evaluation.corpus import FormatCorpus
from evaluation.documents import DOCUMENT_GROUPS, format_labels
from evaluation.scoring import squash

__all__ = [
    "QAItem",
    "content_hash",
    "QASet",
    "QA_SET_PATH",
    "load_qa_set",
    "validate_items",
    "validate_spans_across_formats",
]

# 세트 파일. jsonl 인 이유: 한 줄 한 레코드라 사람 검수의 diff 가 읽힌다
QA_SET_PATH = Path(__file__).resolve().parent / "qa_set.jsonl"

# 답이 어디서 왔는가. 표는 헤더 반복 때문에 검색 성질이 달라 따로 센다
ANSWER_TYPES = frozenset({"text", "table", "image"})

# 사람이 봤는가. pending 은 LLM/모델 초안이고 아직 검수 전이라는 뜻이다
REVIEW_STATES = frozenset({"human", "pending"})


# 질의 하나. jsonl 한 줄이 이것 하나다
@dataclass(frozen=True)
class QAItem:
    query_id: str
    query: str
    # documents.py 의 그룹 키. 파일명이 아니다 — 한 질의가 그 그룹의 모든 포맷을 함께 잰다
    source_doc: str
    # 답을 담은 원문 조각들. **전부** 찾아야 recall 1.0 이다
    gold_spans: list[str]
    answer_type: str = "text"
    reviewed_by: str = "pending"
    # 사람 검수 때 남긴 말. 채점에 안 쓴다
    note: str = ""
    # 검수한 시점의 `query + gold_spans` 해시. 내용이 바뀌면 어긋난다 (모듈 docstring 참고)
    reviewed_hash: str = ""
    # 포맷 라벨 -> 첫 등장 쪽. **채점에 쓰지 않는다** — 같은 문서를 PDF 28쪽 / HWPX 97쪽으로
    # 세는 미해결 함정이 있어 포맷을 가로지르는 기준이 못 된다 (CLAUDE.md). 사람 검수자가
    # 원문을 바로 열게 하는 것이 목적이고, 두 판의 쪽이 어떻게 어긋나는지가 함께 남는다.
    # 한 개의 int 로는 못 쓴다 — 실측으로 학교기록물은 PDF 가 +1, AI 가이드는 아예 다르다
    gold_page: dict[str, int] = field(default_factory=dict)

    # jsonl 한 줄을 레코드로. 모르는 키는 조용히 버리지 않고 막는다 —
    # 오타 난 키가 조용히 무시되면 그 레코드만 기본값으로 채점된다
    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> QAItem:
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(raw) - known
        if unknown:
            raise ValueError(f"{raw.get('query_id')}: 모르는 키 {sorted(unknown)}")
        return cls(**raw)


# 검수 대상 내용의 지문. **`note` 나 `gold_page` 는 안 넣는다** — 그것들이 바뀌었다고
# 사람이 다시 볼 필요는 없다. 사람이 판단한 것은 "이 질문에 이 정답이 맞는가" 하나다
def content_hash(query: str, gold_spans: list[str]) -> str:
    # 구분자를 두는 이유: 그냥 이어 붙이면 질의 끝과 정답 앞이 붙어, 경계만 다른 두 레코드가
    # 같은 지문을 갖는다. 본문에 안 나오는 제어문자를 쓴다
    payload = chr(31).join([query.strip(), *(s.strip() for s in gold_spans)])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


# 세트 하나를 읽은 결과. 파일이 없을 수도 있어 상태를 함께 들고 있다
@dataclass(frozen=True)
class QASet:
    items: list[QAItem] = field(default_factory=list)
    path: Path = QA_SET_PATH

    def __len__(self) -> int:
        return len(self.items)


# 세트를 읽는다. 빈 줄과 `#` 주석 줄은 건너뛴다
def load_qa_set(path: Path | None = None) -> QASet:
    path = path or QA_SET_PATH
    if not path.exists():
        return QASet(items=[], path=path)

    items: list[QAItem] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            items.append(QAItem.from_json(json.loads(line)))
        except Exception as exc:
            raise ValueError(f"{path.name}:{lineno} 레코드를 읽지 못했습니다: {exc}") from exc
    return QASet(items=items, path=path)


# 레코드 자체의 모양을 본다. 문서를 안 읽으므로 fixtures 없이도 돈다
def validate_items(items: list[QAItem]) -> list[str]:
    problems: list[str] = []
    seen: set[str] = set()

    for item in items:
        if item.query_id in seen:
            problems.append(f"{item.query_id}: query_id 가 중복이다")
        seen.add(item.query_id)

        if not item.query.strip():
            problems.append(f"{item.query_id}: query 가 비었다")
        if not item.gold_spans or any(not s.strip() for s in item.gold_spans):
            problems.append(f"{item.query_id}: gold_spans 가 비었거나 빈 문자열을 담고 있다")
        if item.source_doc not in DOCUMENT_GROUPS:
            problems.append(f"{item.query_id}: 모르는 source_doc {item.source_doc!r}")
        if item.answer_type not in ANSWER_TYPES:
            problems.append(f"{item.query_id}: answer_type 이 {item.answer_type!r}")
        if item.reviewed_by not in REVIEW_STATES:
            problems.append(f"{item.query_id}: reviewed_by 가 {item.reviewed_by!r}")

        # 검수 도장이 그때 본 문장에 붙어 있는지 (모듈 docstring 참고)
        if item.reviewed_by == "human":
            expected = content_hash(item.query, item.gold_spans)
            if item.reviewed_hash != expected:
                problems.append(
                    f"{item.query_id}: 검수 뒤에 질의나 정답이 바뀌었다 "
                    f"(기록 {item.reviewed_hash or '없음'} / 지금 {expected}) "
                    "— 다시 검수하고 reviewed_hash 를 갱신하거나 pending 으로 되돌려라"
                )

        # gold_page 의 키는 그 그룹의 포맷 라벨 전부여야 한다. 라벨을 바꾸고 세트를 안 고치는
        # 것을 여기서 막는다 — 안 막으면 조용히 빈 칸이 되고 검수자가 원문을 못 찾는다
        expected = set(format_labels(item.source_doc))
        if expected:
            got = set(item.gold_page)
            if got != expected:
                problems.append(
                    f"{item.query_id}: gold_page 라벨이 안 맞는다 "
                    f"(빠짐 {sorted(expected - got)} / 모름 {sorted(got - expected)})"
                )
        # 너무 짧은 span 은 다른 곳에도 우연히 있어 오답을 hit 으로 만든다
        for span in item.gold_spans:
            if len(span.strip()) < 6:
                problems.append(f"{item.query_id}: span 이 너무 짧다 {span!r}")

    return problems


# 연속한 자리 번호를 한 덩어리로 묶는다. 겹침이 만든 인접 중복을 하나로 세기 위해서다
def _distinct_runs(indices: list[int]) -> int:
    if not indices:
        return 0
    return 1 + sum(1 for a, b in zip(indices, indices[1:], strict=False) if b - a > 1)


# span 이 그룹의 **모든 포맷**에서, **청크 하나 안에서**, 그리고 **한 자리에서만**
# 발견되는지 본다. 셋을 다 봐야 하는 이유는 이 모듈과 corpus.py 의 docstring 에 있다
def validate_spans_across_formats(
    items: list[QAItem],
    corpus: dict[str, dict[str, FormatCorpus]],
) -> list[str]:
    problems: list[str] = []
    for item in items:
        by_label = corpus.get(item.source_doc)
        if not by_label:
            # 그 문서가 디스크에 없다. 검증할 수 없는 것이지 틀린 것이 아니다
            continue
        for span in item.gold_spans:
            needle = squash(span)

            missing = sorted(n for n, c in by_label.items() if needle not in c.body)
            if missing:
                problems.append(f"{item.query_id}: span {span!r} 이 본문에 없는 판 {missing}")
                continue

            # 본문에는 있는데 어떤 청크에도 통째로 안 들어 있으면 청크 경계가 갈랐다는 뜻이다.
            # 채점이 청크 단위라 이런 질의는 **어떤 검색기로도 못 맞힌다**
            split = sorted(n for n, c in by_label.items() if not c.chunk_indices_for(needle))
            if split:
                problems.append(
                    f"{item.query_id}: span {span!r} 이 청크 경계에 걸린 판 {split} "
                    "— 더 짧게 자르거나 다른 대목을 고른다"
                )
                continue

            # 문서 안에서 되풀이되면 정답 청크가 여럿이 되어 그 질의만 쉬워진다.
            # 인접한 자리는 겹침이므로 하나로 센다 (모듈 docstring 참고)
            repeated = {
                label: c.chunk_indices_for(needle)
                for label, c in by_label.items()
                if _distinct_runs(c.chunk_indices_for(needle)) > 1
            }
            if repeated:
                where = ", ".join(f"{k} {v}" for k, v in sorted(repeated.items()))
                problems.append(
                    f"{item.query_id}: span {span!r} 이 문서에서 되풀이된다 ({where}) "
                    "— 그 대목에만 있는 문장으로 바꾼다"
                )
    return problems
