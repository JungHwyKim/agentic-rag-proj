"""채점 규칙과 지표.

## 정답을 청크가 아니라 **문자열**로 센다

`.claude/wiki/plan_log/평가-세트.md` 2회차의 결정이다. 그 결정이 채점에까지 미치는 영향이 하나 있는데
놓치기 쉽다 — **`recall` 의 분모를 청크로 잡으면 안 된다.**

흔한 정의는 `|정답 청크 ∩ 상위 k| / |정답 청크|` 인데, 여기서 분모가 **청킹에 따라 달라진다.**
같은 문서를 HWPX 는 67청크, PDF 는 118청크로 나누므로 같은 정답이 한쪽에서는 1개 청크,
다른 쪽에서는 3개 청크에 흩어진다. 그 상태로 두 포맷을 비교하면 **분모가 서로 다른 값을
비교하는 것**이 된다 — `cleaner/headers.py` 가 쪽 수를 분모로 쓰지 않기로 한 것과 같은 함정이다.

그래서 분모를 `gold_spans` 로 잡는다. 청킹이 바뀌어도 분모가 안 변한다.

## 지표마다 재는 것이 다르다

| 지표 | 무엇이 바뀌면 움직이나 | 쓰는 비교 |
|---|---|---|
| `hit@k` | 후보 집합 | dense vs hybrid |
| `recall@k` | 후보 집합 | dense vs hybrid, 포맷 비교 |
| `MRR` | 순서 | 전부 |
| `nDCG@k` · `precision@k` | 순서 | **리랭커 유무·로컬 vs 상용** |

**리랭커를 `recall@후보수` 로 재면 차이가 정의상 0 이다.** 후보 집합을 안 바꾸고 순서만
바꾸기 때문이다. 그것으로 재고 "효과 없다"고 결론 내리는 것이 이 평가에서 제일 하기 쉬운
실수라, 지표를 축마다 갈라 둔다.

## `nDCG` 의 이상적 순위를 무엇으로 잡나

보통은 "후보 안의 정답 청크 수"를 쓰는데 그 값이 **청킹과 후보 수에 둘 다 걸린다.**
여기서는 `min(k, len(gold_spans))` 로 잡는다 — "이상적으로는 정답 span 하나당 청크 하나가
맨 위에 온다". 청킹이 바뀌어도 안 변하고, 비교 5번이 성립한다.

## 대조는 **공백을 지우고** 한다

`normalize()`(NFC·공백 압축·소문자) 만으로는 두 포맷이 안 맞는다. **PDF 경로가 한국어 낱말
사이 공백을 잃는다** — 실측으로 같은 문장이 HWPX 에서는 `데이터세트는 물리적 이관시 그
특성이 변할 수 있어`, PDF 에서는 `데이터세트는물리적이관시그특성이 변할수있어` 로 나온다
(없어야 할 자리에 공백이 있고 있어야 할 자리에 없다). 이 상태로 공백을 살려 대조하면 **PDF
파일이 정답을 못 맞힌 것이 아니라 띄어쓰기 때문에 0점**이 되고, 포맷 비교가 검색이 아니라
추출 품질을 재게 된다.

그래서 대조는 `squash()`(정규화 + 공백 전부 제거)로 한다. 한국어는 공백을 지워도 뜻이
보존되는 편이라 잃는 것이 적고, 6자 미만 span 을 `qa_set.validate_items()` 가 막아 우연한
부분일치를 줄인다.

제어문자는 cleaner 가 이미 걷어냈으므로(CLAUDE.md "PDF 제어문자") 여기서 또 하지 않는다 —
두 곳에서 같은 일을 하면 한쪽을 고쳤을 때 조용히 갈린다. 소문자를 넣는 이유는 `SIARD_KR`
같은 영문 약어가 포맷마다 대소문자가 달라질 수 있어서다.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass

__all__ = [
    "QueryScore",
    "hit_rank",
    "normalize",
    "score_query",
    "spans_found",
    "squash",
]

# 연속 공백(줄바꿈·탭 포함)을 한 칸으로. 표 마크다운의 `\n` 도 여기서 공백이 된다
_WS = re.compile(r"\s+")


# 비교 전에 텍스트를 같은 모양으로 맞춘다. 공백은 아직 살아 있다
def normalize(text: str) -> str:
    return _WS.sub(" ", unicodedata.normalize("NFC", text)).strip().lower()


# 대조에 쓰는 최종 형태. **공백을 전부 지운다** — PDF 가 낱말 사이 공백을 잃는다
# (모듈 docstring 참고). 채점과 코퍼스 검증이 반드시 같은 함수를 써야 한다
def squash(text: str) -> str:
    return _WS.sub("", normalize(text))


# 상위 k개 청크 텍스트에서 실제로 찾아낸 정답 span 집합
def spans_found(texts: list[str], gold_spans: list[str]) -> set[str]:
    haystack = "".join(squash(t) for t in texts)
    return {span for span in gold_spans if squash(span) in haystack}


# 정답을 하나라도 담은 첫 청크의 순위(1부터). 없으면 None
def hit_rank(texts: list[str], gold_spans: list[str]) -> int | None:
    needles = [squash(s) for s in gold_spans]
    for rank, text in enumerate(texts, start=1):
        hay = squash(text)
        if any(needle in hay for needle in needles):
            return rank
    return None


# 질의 하나의 채점 결과. 지표를 따로 들고 있어야 축마다 다른 것을 볼 수 있다
@dataclass(frozen=True)
class QueryScore:
    query_id: str
    # 정답을 하나라도 상위 k 안에서 찾았나
    hit: bool
    # 찾아낸 정답 span 수 / 전체 정답 span 수. 분모가 청킹과 무관하다
    recall: float
    # 1 / (첫 정답 청크의 순위). 못 찾으면 0
    reciprocal_rank: float
    # 상위 k 중 정답을 담은 청크의 비율
    precision: float
    ndcg: float
    # 첫 정답 청크의 순위. 못 찾으면 None — 진 질의를 눈으로 읽을 때 쓴다
    rank: int | None


# 질의 하나를 채점한다. `texts` 는 **이미 상위 k 로 잘린** 청크 텍스트 목록이다
def score_query(query_id: str, texts: list[str], gold_spans: list[str], k: int) -> QueryScore:
    if not gold_spans:
        raise ValueError(f"{query_id}: gold_spans 가 비어 있으면 채점할 수 없다")

    top = texts[:k]
    needles = [squash(s) for s in gold_spans]
    relevant = [any(n in squash(t) for n in needles) for t in top]

    rank = hit_rank(top, gold_spans)
    found = spans_found(top, gold_spans)

    dcg = sum(1.0 / math.log2(i + 2) for i, ok in enumerate(relevant) if ok)
    # 이상적 순위는 "정답 span 하나당 청크 하나가 맨 위에" 다 (모듈 docstring 참고)
    ideal_n = min(k, len(gold_spans))
    idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_n))

    return QueryScore(
        query_id=query_id,
        hit=rank is not None,
        recall=len(found) / len(gold_spans),
        reciprocal_rank=(1.0 / rank) if rank else 0.0,
        precision=(sum(relevant) / len(top)) if top else 0.0,
        ndcg=(dcg / idcg) if idcg else 0.0,
        rank=rank,
    )
