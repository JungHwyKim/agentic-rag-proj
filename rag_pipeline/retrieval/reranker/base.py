"""리랭커 구현체들이 공유하는 인터페이스와 비용 누적기.

## 이 단계가 재는 자[尺]는 다른 단계와 다르다

리랭커는 **후보 집합을 안 바꾸고 순서만 바꾼다.** 그래서 `recall@후보수` 로 재면 차이가
**정의상 0** 이고, `hit@10` 으로 재도 바닥이 좋으면 여지가 실제보다 작아 보인다. 바닥은
`hit@1` 이고 천장은 `hit@후보수` 다 — 주지표는 `nDCG@5`·`precision@3`·`recall@5` 다
(`evaluation/README.md`, `plan_log/검색-생성.md` 1·5회차).

## 실패는 예외가 아니라 폴백이다 — 다만 전부 아니면 전무다

`retrieve()` 가 이미 RRF 순서를 들고 있으므로 **내려갈 층이 있다.** 그래서 로더의
*빈 결과 + warn* 규약 쪽이지 embedding·indexing 의 예외 쪽이 아니다.

**다만 반쯤 채우지 않는다.** `표-경로.md` 에 정반대 선례가 둘 있는데(11회차 *모양이 다르면
통째로 버린다* vs 13회차 *채운 만큼 쓰고 경고한다*) 여기서는 앞을 따른다 — 순서를 반쯤
바꾸면 *"리랭커가 나쁜가 응답이 반쪽이었나"* 를 못 가르고, 지금은 이 단계를 **재는 중**이라
그 오염이 곧 판정 오염이다. 켤 조합이 정해진 뒤 실사용 품질을 볼 때 다시 본다.

그래서 구현체의 계약은 이렇다 — **점수를 전부 얻었을 때만 `rerank_score` 를 채우고, 하나라도
못 얻으면 아무것도 안 채운 채 받은 순서 그대로 돌려주며 `warnings` 에 남긴다.**
`RetrievedChunk.ranking_score` 가 `rerank_score` 없으면 `fused_score` 로 떨어지므로, 안 채운
것이 곧 RRF 순서로 내려간 것이다.

## 왜 순위가 아니라 점수를 받나

`RetrievedChunk.rerank_score` 는 `float | None` 이다. 순위만 받으면 그 칸에 역순위 같은
**지어낸 수**를 넣게 되는데, 그것은 점수를 버리고 순위를 쓰는 RRF 를 한 번 더 하는 셈이다
(`fusion.py` 가 그 성질의 대가를 이미 적어 뒀다). cross-encoder 는 본래 점수를 내므로
점수로 맞추면 **두 구현체의 반환 모양이 저절로 같아진다.**

## 왜 warnings 와 usage 가 인스턴스에 있나

`Bm25Index.warnings` · `Embedder.usage` 와 같은 자리다. 반환 계약(`-> list[RetrievedChunk]`)
을 안 바꾸는 것이 요점이다 — 바꾸면 `retrieve()` 를 지나 `query.py`·`run_eval`·backend 의
계약이 전부 따라 바뀐다. 부르는 쪽이 인스턴스를 들고 있으니 밖에서 읽힌다.

**경고를 안 남기면 폴백이 "그냥 효과가 없었다"와 구분이 안 된다.** BM25 가 dense 로 내려갈
때 경고를 남기는 이유와 같다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from rag_pipeline.config import RerankConfig
from rag_pipeline.schemas.retrieval import RetrievedChunk

__all__ = [
    "RerankUsage",
    "Reranker",
    "apply_scores",
    "rerank_cache_key",
    "rerank_cache_salt_for",
]


# 다시 정렬하는 동안 쌓이는 비용. 질의당으로 읽으려면 호출부가 reset() 한다.
# **로컬 갈래도 이것을 채운다** — `api_calls` 는 0 이지만 `reranked` 와 `cache_hits` 가
# 의미를 갖는다. 안 채우면 두 구현체의 리포트 모양이 갈려 비교표를 손으로 맞춰야 한다
@dataclass
class RerankUsage:
    # 실제로 나간 외부 API 호출 수. **성공한 뒤에만** 오른다. 로컬은 늘 0 이다
    api_calls: int = 0
    # 그 호출들이 쓴 토큰. 로컬은 0 이다
    total_tokens: int = 0
    # 캐시로 아낀 질의 수
    cache_hits: int = 0
    # 실제로 점수를 매긴 후보 수. **로컬의 비용은 토큰이 아니라 이것이다**(후보 수에 선형)
    reranked: int = 0

    # 질의 하나를 끝내고 다음으로 넘어갈 때 부른다
    def reset(self) -> None:
        self.api_calls = 0
        self.total_tokens = 0
        self.cache_hits = 0
        self.reranked = 0


# 후보 풀을 다시 정렬하는 구현체의 인터페이스. 상위 계층은 이것만 알면 된다
class Reranker(Protocol):
    # 후보를 점수 순으로 돌려준다. **실패해도 예외를 안 던지고 받은 것을 그대로 돌려준다**
    def rerank(self, query: str, candidates: list[RetrievedChunk]) -> list[RetrievedChunk]: ...

    # 폴백이 일어났을 때 쌓인다. 부르는 쪽이 읽는다
    @property
    def warnings(self) -> list[str]: ...

    # 지금까지 쌓인 비용
    @property
    def usage(self) -> RerankUsage: ...


# 캐시 키. **질의·후보 신원·모델·프롬프트가 전부 들어간다.**
#
# 프롬프트가 들어가는 것이 요점이다 — `표-경로.md` 14회차가 캐시 키에서 프롬프트가 빠져
# 있던 것을 닫은 자리와 같다. 빠지면 프롬프트를 고쳐도 옛 응답이 나오는데 **에러가 아니라
# 수치로만** 드러난다.
#
# 후보를 **순서까지 포함해** 싣는 이유: 같은 집합이어도 들어온 순서가 다르면 프롬프트의
# 번호가 달라져 모델 응답이 달라진다. 집합만 실으면 다른 프롬프트의 답을 재사용한다
def rerank_cache_key(query: str, candidates: list[RetrievedChunk], *, salt: str) -> bytes:
    ids = "\n".join(chunk.chunk_id for chunk in candidates)
    return f"{salt}\n{query}\n{ids}".encode()


# 캐시 salt. **규칙을 구현체가 갖지 않는다** — `embedding/base.py:cache_salt_for()` 가 밟은
# 길 그대로다. 구현체마다 다시 짜면 한쪽이 빠뜨려도 아무 에러가 안 난다.
#
# **`rerank_device` 가 들어가는 것이 요점이다.** 그 필드가 존재하는 이유 자체가 *"CPU 와
# GPU 가 미세하게 다른 점수를 낸다"* 이므로(`config.py`), 안 실으면 GPU 로 돌린 실행이
# **CPU 때 캐시를 그대로 읽어** 품질이 같게 나오고 속도만 빨라 보인다 — 지연을 재려던
# 실행이 아무것도 안 재고 통과한다. 프롬프트를 키에 넣은 것과 같은 잣대다(위 함수 주석).
# `provider="openai"` 는 device 를 안 쓰지만 **같은 규칙을 탄다** — 갈래마다 규칙이 다르면
# 한쪽이 빠져도 아무 에러가 안 난다
def rerank_cache_salt_for(config: RerankConfig, *, prompt_version: str) -> str:
    return (
        f"rerank:{config.rerank_provider}:{config.rerank_model}"
        f":{config.rerank_device}:{prompt_version}"
    )


# 점수를 후보에 얹어 정렬한다. **전부 아니면 전무다** (모듈 docstring 참고).
#
# `scores` 는 후보와 **같은 길이**여야 한다. 아니면 아무것도 안 채우고 받은 순서를 그대로
# 돌려준다 — 그것이 곧 RRF 순서로 내려간 것이다(`ranking_score` 가 `fused_score` 로 떨어진다).
#
# 정렬이 stable 이라 **동점은 들어온 순서(=fused 순서)가 이긴다.** `fusion.py` 의
# *"동점은 dense 가 이긴다"* 와 같은 모양이고, 모델이 여러 후보에 같은 점수를 줬을 때
# 순서를 임의로 흔들 이유가 없다
def apply_scores(
    candidates: list[RetrievedChunk], scores: list[float] | None
) -> list[RetrievedChunk]:
    if scores is None or len(scores) != len(candidates):
        return candidates

    for chunk, score in zip(candidates, scores, strict=True):
        chunk.rerank_score = score
    return sorted(candidates, key=lambda chunk: chunk.ranking_score, reverse=True)
