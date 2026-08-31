"""정답 질의 세트의 레코드 스키마와 로더.

## 왜 `chunk_id` 가 아니라 `gold_spans` 인가

`.claude/wiki/plan_log/평가-세트.md` 2회차. 요약하면 둘이다.

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

## 부정 예시는 `answerable: false` 다 — 빈 `gold_spans` 가 아니라

Phase 4(Relevance Evaluator)를 재려면 **답이 문서에 없는 질의**가 있어야 한다. 지금 세트는
전부 정답이 실재해서 *"항상 충분"* 이라고만 답하는 Evaluator 가 100점을 받는다.

그것을 **빈 `gold_spans` 로 표시하지 않는다.** 빈 값은 지금 *오류의 신호*(오타·누락)이고,
뜻을 뒤집으면 진짜 누락이 조용히 부정 예시가 된다. `answer_type` 에 `none` 을 더하는 것도
아니다 — 그 필드는 *"답이 어디서 왔나"*(text/table/image) 축이라 한 필드가 두 축을 겸하게
된다. 그래서 **필드를 하나 더 둔다.** 기본값이 `True` 라 기존 레코드는 그대로 산다.

부정 레코드에는 `gold_spans` 도 `gold_page` 도 **비어 있어야** 한다. 답이 없으니 span 도
쪽도 있을 수 없고, 차 있으면 라벨과 내용이 어긋난 것이라 `validate_items()` 가 막는다.

**검수 도장은 그대로 돈다.** `content_hash(query, [])` 가 질의 하나로 지문을 만드는데,
부정에서 사람이 판단한 것이 *"이 질문의 답이 이 코퍼스에 없다"* 하나라 그게 정확하다.

**채점은 `scoring.py` 가 아니라 Evaluator 쪽 자[尺]가 한다.** `score_query()` 는 빈
`gold_spans` 에 예외를 던지고 그대로 둔다 — `hit@k = 0 이면 정답` 으로 정의하면 정답이 없어
**자동 만점**이라 검색기를 못 가르고 `recall` 은 분모가 0이다. 러너가 부정을 건너뛰되
건너뛴 수를 리포트에 찍는다(`run_eval.py`).

## 모든 span 은 그룹의 **모든 포맷**에서 찾을 수 있어야 한다

포맷 비교(`.claude/wiki/plan_log/검색-생성.md` 비교 5번)가 성립하는 조건이다. HWPX 에만 있는 문장을
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
    # 답이 이 코퍼스에 있는가. 거짓이면 **부정 예시**이고 gold_spans·gold_page 가 비어 있어야
    # 한다 (모듈 docstring 참고). 기본값이 참이라 기존 레코드는 이 키가 없어도 그대로 산다
    answerable: bool = True
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
    # 답변이 **반드시 담아야 할 것**. `gold_spans` 와 축이 다르다 — 저쪽은 *검색이 이 청크를
    # 물어왔나* 이고 이쪽은 *생성이 이 사실을 답에 담았나* 다(`.claude/wiki/plan_log/평가-세트.md` 13회차).
    #
    # **둘로 가른 것이 이 스키마의 요점이다.** `must_values` 는 값·날짜라 어휘 대조로
    # **결정적**이고, `must_points` 는 서술이라 LLM 판정이 든다. 한 칸으로 접으면 자[尺]가
    # 통째로 LLM 이 되어 이 형태를 고른 이유가 사라진다 — 고른 이유는 `judge_probe` 가 놓친
    # 셋(`3월→4월`·`206개→207개`·`2026년→2027년`)이 **전부 값**이었다는 것이다.
    #
    # **`must_points` 에 원문을 그대로 뜨면 안 된다.** 부분 문자열이면 베낀 답변이 통과하고
    # 바꿔 쓴 답변이 떨어져, 재는 것이 completeness 가 아니라 *원문을 옮겼나* 가 된다 —
    # 인용 정합성(`generation/citations.py`)이 이미 재는 것이다
    must_values: list[str] = field(default_factory=list)
    must_points: list[str] = field(default_factory=list)
    # 위 둘의 검수 도장. **`reviewed_by`/`reviewed_hash` 와 일부러 갈라 뒀다** — 합치면
    # 기존 43건 도장이 전부 깨져 재검수를 강요하는데, 그것이 `.claude/wiki/plan_log/검색-생성.md` 56회차가
    # *"기대 열이 움직여 혼동행렬 여섯 판이 못 견주게 된다"* 며 라벨을 안 건드린 자리다.
    # 지문은 `content_hash(query, must_values + must_points)` 이고 `validate_items()` 가
    # 본다 — 6회차가 `reviewed_hash` 를 검수가 끝난 뒤 백필하고 게이트를 그 뒤에 무장한
    # 것과 같은 순서다(라벨 18/18 이 찬 뒤인 2026-08-29 에 붙었다)
    must_reviewed_by: str = "pending"
    must_reviewed_hash: str = ""
    # 답변이 **단정하면 안 되는 것**. 위 둘과 축이 반대다 — 저쪽은 *담았나* 이고 이쪽은
    # *덧붙였나* 라, `must_points` 로는 원리적으로 못 잰다(실측: `hai-05` 에서 답변이 근거의
    # 「이해관계자」를 담은 **채로** 「국민」을 덧붙였고 어휘 겹침은 두 판이 같았다).
    #
    # **이름이 「환각이 없다」로 읽히면 안 된다.** 환각은 열거할 수 없어, 이 칸이 재는 것은
    # «환각을 안 했나» 가 아니라 **«미리 적어 둔 오류를 냈나»** 다. 그래서 `claim` 이다 —
    # 낱말이 나온 것(mention)이 아니라 **단정한 것**만 오류다(`.claude/wiki/plan_log/평가-세트.md` 16회차).
    #
    # **값/서술로 안 가른다.** `must_values`/`must_points` 를 가른 축은 «어휘로 결정적인가»
    # 인데, 여기서는 그 축이 실측으로 무너졌다 — 다섯 건 중 셋은 금지어가 **질의 자체의
    # 낱말**이라(전제가 틀린 질문이 이 칸을 만든다) 전제를 바로잡은 답변도 그 낱말을 쓴다.
    # 즉 «있나» 로는 값 축조차 결정적이지 않아, 가를 근거가 없다
    must_not_claim: list[str] = field(default_factory=list)
    # 이 칸만의 검수 도장. **`must_reviewed_*` 에 안 묶었다** — 묶으면 같은 문자열을
    # `must_points` ↔ `must_not_claim` 으로 **옮긴 편집이 지문에 안 걸린다**(확인함:
    # payload 가 이어 붙인 한 목록이라 경계가 안 남는다). 뜻이 제일 크게 뒤집히는 편집이
    # 그것이라 도장이 그것만은 잡아야 한다. 지문은 `content_hash(query, must_not_claim)` 다
    must_not_claim_reviewed_by: str = "pending"
    must_not_claim_reviewed_hash: str = ""

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
        if item.source_doc not in DOCUMENT_GROUPS:
            problems.append(f"{item.query_id}: 모르는 source_doc {item.source_doc!r}")
        if item.reviewed_by not in REVIEW_STATES:
            problems.append(f"{item.query_id}: reviewed_by 가 {item.reviewed_by!r}")

        # 긍정과 부정은 요구하는 것이 반대다 (모듈 docstring 참고). 부정에서 answer_type 을
        # 안 보는 것은 답이 없어 매체가 없기 때문이고, 부정은 검색 채점 자체에 안 들어간다
        if item.answerable:
            if not item.gold_spans or any(not s.strip() for s in item.gold_spans):
                problems.append(f"{item.query_id}: gold_spans 가 비었거나 빈 문자열을 담고 있다")
            if item.answer_type not in ANSWER_TYPES:
                problems.append(f"{item.query_id}: answer_type 이 {item.answer_type!r}")
        elif item.gold_spans:
            problems.append(
                f"{item.query_id}: answerable 이 거짓인데 gold_spans 가 차 있다 "
                "— 답이 있으면 부정 예시가 아니다"
            )

        # 검수 도장이 그때 본 문장에 붙어 있는지 (모듈 docstring 참고)
        if item.reviewed_by == "human":
            expected = content_hash(item.query, item.gold_spans)
            if item.reviewed_hash != expected:
                problems.append(
                    f"{item.query_id}: 검수 뒤에 질의나 정답이 바뀌었다 "
                    f"(기록 {item.reviewed_hash or '없음'} / 지금 {expected}) "
                    "— 다시 검수하고 reviewed_hash 를 갱신하거나 pending 으로 되돌려라"
                )

        # 골든 답변 라벨의 도장 둘도 같은 규칙 아래 있다. **도장이 셋인 것은 되풀이가 아니라
        # 가리는 내용이 셋이기 때문이다** — 사람이 판단한 것이 각각 «이 질문에 이 정답이 맞나» ·
        # «답변이 이것을 담아야 하나» · «답변이 이것을 단정하면 안 되나» 로 다르다.
        # 셋을 한 지문으로 묶으면 라벨을 한쪽에서 다른 쪽으로 **옮긴 편집이 안 걸린다**
        # (`.claude/wiki/plan_log/평가-세트.md` 16회차).
        #
        # **`pending` 을 문제로 안 센다** — 기계가 문구를 제안하고 사람이 거르는 절차라
        # (14·16회차) 검수 대기가 정상 상태다. 커밋된 세트가 다 찼는지는 `tests/evaluation`
        # 쪽이 본다. 여기는 6회차와 같이 **«고치고 도장을 안 고친 것»** 만 잡는다
        for state, digest, payload, what, field_name in (
            (
                item.must_reviewed_by,
                item.must_reviewed_hash,
                [*item.must_values, *item.must_points],
                "필수 사실",
                "must_reviewed_hash",
            ),
            (
                item.must_not_claim_reviewed_by,
                item.must_not_claim_reviewed_hash,
                item.must_not_claim,
                "단정 금지",
                "must_not_claim_reviewed_hash",
            ),
        ):
            if state not in REVIEW_STATES:
                problems.append(f"{item.query_id}: {what} 도장이 {state!r} 이다")
            elif state == "human":
                expected = content_hash(item.query, payload)
                if digest != expected:
                    problems.append(
                        f"{item.query_id}: 검수 뒤에 {what} 라벨이 바뀌었다 "
                        f"(기록 {digest or '없음'} / 지금 {expected}) "
                        f"— 다시 읽고 {field_name} 를 갱신하거나 pending 으로 되돌려라"
                    )

        # **부정 예시에는 골든 답변 라벨을 안 단다** — 부정의 골든은 «기권» 이고 그것을
        # `summarize()` 의 `negative` 버킷이 이미 채점한다(13회차). 달면 *«답할 수 없다»* 가
        # 두 곳에 적혀 한쪽이 조용히 낡는다. 결정만 기록에 있고 코드가 안 보던 자리다
        if not item.answerable and (item.must_values or item.must_points or item.must_not_claim):
            problems.append(
                f"{item.query_id}: answerable 이 거짓인데 골든 답변 라벨이 달려 있다 "
                "— 부정의 골든은 «기권» 이고 negative 버킷이 이미 잰다"
            )

        # gold_page 의 키는 그 그룹의 포맷 라벨 전부여야 한다. 라벨을 바꾸고 세트를 안 고치는
        # 것을 여기서 막는다 — 안 막으면 조용히 빈 칸이 되고 검수자가 원문을 못 찾는다.
        # **부정은 반대로 비어 있어야 한다** — 답이 없으니 열어 볼 쪽도 없다
        if not item.answerable:
            if item.gold_page:
                problems.append(
                    f"{item.query_id}: answerable 이 거짓인데 gold_page 가 차 있다 "
                    "— 답이 없어 가리킬 쪽이 없다"
                )
        else:
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
