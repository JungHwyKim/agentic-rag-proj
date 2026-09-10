"""캐시된 답을 **무엇이 만들었나**를 한 줄로 적는다. 다르면 그 항목은 미스다.

## 왜 필요한가

캐시가 히트하면 `retrieve()` 를 아예 안 부른다 — 그것이 이 기능의 절약 전부인데, 동시에
**리랭킹도 재작성도 건너뛴다는 뜻**이다. 캐시에 실린 `sources` 는 저장 당시의 순서 그대로라,
리랭커를 켜거나 `REWRITE_MODE` 를 바꿔도 히트한 질의는 옛 순서로 서빙된다.
**에러가 안 나고 답도 그럴듯해서** 늦게까지 안 보인다.

캐시가 낡는 축은 넷인데 셋은 이미 막혀 있었다 —

- 임베딩 모델·차원 → 컬렉션 이름(`collection_name_for(..., prefix=)`)
- 문서 삭제·수정 → `_sources_still_valid()`
- 새 문서 색인 → `clear()`
- **검색·생성 설정 → 아무것도 안 막았다. 이 모듈이 그 자리다**

## 왜 `clear()` 가 아니라 payload 인가

기동 시 지문이 달라졌으면 통째로 비우는 안은 **지문을 어딘가 저장해야** 한다. qdrant
컬렉션에 메타 자리가 없어 sentinel point 같은 새 상태 축이 생기는데, 그것이
`.claude/wiki/plan_log/시맨틱-캐시.md` 1회차가 «색인 세대 번호» 를 기각한 바로 그 이유다. payload 에는
자리가 이미 있고, 낡은 항목이 **조회 시점에 스스로 걸러지는** 모양은 `_sources_still_valid()`
와 같다.

## 무엇을 싣나 — 기본은 싣고, 뺄 것만 명시한다

이 저장소의 salt 규칙은 *"출력을 가르는 것만 넣는다"* 이고 그 기준은 그대로다. 다만 **틀리는
방향이 비대칭이라** 손으로 고르지 않는다 — 빠뜨리면 **낡은 답을 조용히 서빙**하고, 넘치면
**미스가 한 번 더 날 뿐**이다. 그래서 각 설정 객체를 통째로 훑고 뺄 것만 이름으로 적는다.
설정 필드가 새로 생겨도 저절로 지문에 든다(손으로 세면 그때 빠뜨린다).

빼는 것은 셋뿐이다.

- **`*_api_key`** — 키를 바꿔도 출력이 안 갈린다. 그리고 payload 에 남길 값이 아니다
- **`generation_top_k`** — 유효 `top_k` 가 이미 그 값이다. 덮어썼으면 `top_k` 쪽이 진실이고,
  안 덮었으면 같은 수라 두 번 실을 이유가 없다. 두 번 실으면 **덮어쓴 호출에서만** 엉뚱하게
  미스가 난다
- **`*_cache_dir` · `*_cache_enabled`** — 리랭커·재작성기의 *호출 캐시* 설정이라 파이프라인이
  무엇인지를 안 바꾼다(같은 점수를 디스크에서 읽어오느냐 다시 부르느냐일 뿐이다).
  그리고 `cache_dir` 은 **절대 경로**라, 실으면 payload 에 머신의 파일 배치가 박히고
  같은 설정이 머신마다 다른 지문을 낸다 — *넘쳐도 미스 한 번* 이라는 위 비대칭이 여기서는
  성립하지 않는다(늘 미스다)

## 꺼진 단계는 `off` 한 낱말로 접는다

리랭커가 꺼져 있는데 `RERANK_DEVICE` 를 바꿨다고 캐시가 죽으면 안 된다 — 그 설정은 그때
아무것도 안 한다. 켜져 있을 때만 그 객체의 값이 지문에 든다.

## `rewrite_mode` 를 싣는 것은 `rewrite.py` 와 반대인데, 기준은 같다

`retrieval/rewrite.py` 의 캐시 salt 는 `rewrite_mode` 를 **안 싣는다** — 재작성 *호출* 은
`(질의, 1차 pool)` 만 보고 mode 는 그 **뒤에서** 갈리므로, 그 호출의 출력을 안 가른다.
반면 여기가 가리는 것은 *검색 결과와 답 본문* 이고 `fuse`/`replace` 는 그것을 실제로 가른다.
**같은 기준이 반대 답을 낸 자리라 «통일하자» 로 뭉개지기 쉽다** —
`tests/answer_cache/test_answer_cache.py` 가 둘을 나란히 박아 두었다.

## 앞머리 버전 번호(`v1`)

지문에 **무엇을 담는지가 바뀌면** 옛 항목의 지문은 더 이상 비교 대상이 아니다. 버전 번호를
올리면 전부 한 번에 미스가 되어 다시 채워진다 — 마이그레이션을 안 만드는 것이 캐시에서는
맞다(비어도 정상 동작하는 최적화라는 것이 `clear()` 를 고른 근거와 같다).
"""

from __future__ import annotations

from typing import Any

from rag_pipeline.config import IngestConfig

__all__ = ["pipeline_fingerprint"]

# 지문의 구성이 바뀔 때 올린다. 값이 아니라 **버전**이라 분포에서 고를 것이 없다 (모듈 docstring)
_VERSION = "v1"

# 설정 객체에서 뺄 필드. **뺄 것만 적는 것이 이 모듈의 방향이다** (모듈 docstring 참고)
_SKIP = frozenset({"generation_top_k"})

# 이름 규칙으로 빼는 것. 새 설정 객체가 같은 규칙을 따르면 저절로 빠진다.
#
# **`_timeout_sec` 는 `_cache_*` 와 같은 자리다** — 타임아웃은 *답이 무엇인가* 를 안 가른다.
# 가르는 것은 «답이 나오는가» 이고, 안 나온 회차는 애초에 캐시에 안 들어간다. 그래서 이 값을
# 바꿨다고 이미 저장된 답이 낡지 않는다.
#
# **뺄 이유가 하나 더 있고 그쪽이 더 급하다** — 지금 `exact` 캐시는 *"`source=="user"` 50줄에서
# 적중률 5% 미만이면 끈다"* 의 표본을 모으는 중이다(`evaluation/README.md` 8절). 실으면
# 타임아웃 한 줄을 고칠 때마다 전량 미스가 되어 **그 판정을 아래로 민다** — 아래 `max_retries`
# 조각이 꺼졌을 때 붙지 않는 것과 같은 이유다
_SKIP_SUFFIXES = ("_api_key", "_cache_dir", "_cache_enabled", "_timeout_sec")


# 이 필드를 지문에 싣나
def _carried(key: str) -> bool:
    return key not in _SKIP and not key.endswith(_SKIP_SUFFIXES)


# 설정 객체 하나를 `이름=값` 목록으로 편다. 키를 정렬하는 것은 pydantic 의 선언 순서가
# 바뀌어도 같은 설정이 같은 지문을 내게 하기 위해서다
def _fields(label: str, config: Any) -> str:
    items = sorted(
        (key, value) for key, value in config.model_dump().items() if _carried(key)
    )
    body = ",".join(f"{key}={value}" for key, value in items)
    return f"{label}({body})"


# 이 답을 만든 파이프라인을 한 줄로 적는다. **해시가 아니라 읽히는 문자열이다** —
# payload 를 열었을 때 *어느 손잡이가 바뀌어 미스가 났나* 가 값 자체로 보여야 한다
# (`rerank_cache_salt_for()` 와 같은 모양이다).
#
# `use_bm25`·`reranker_on`·`rewriter_on` 을 설정에서 파생하지 않고 인자로 받는 이유는
# **설정이 그 셋의 진실이 아니어서**다. BM25 는 인덱스를 넘겼는가로 갈리고(그마저 비면 안
# 돈다), 리랭커·재작성기는 부르는 쪽이 객체를 안 넘기면 설정이 차 있어도 안 돈다 —
# `evaluation/run_eval.py` 가 실제로 그렇게 부른다
def pipeline_fingerprint(
    config: IngestConfig,
    *,
    use_bm25: bool,
    reranker_on: bool,
    rewriter_on: bool,
    top_k: int,
    max_retries: int = 0,
) -> str:
    parts = [
        _VERSION,
        f"bm25={int(use_bm25)}",
        f"top_k={top_k}",
        _fields("retrieval", config.retrieval),
        _fields("rerank", config.rerank) if reranker_on else "rerank=off",
        _fields("rewrite", config.rewrite) if rewriter_on else "rewrite=off",
        _fields("generation", config.generation),
    ]
    # **재시도 루프는 켜졌을 때만 조각을 붙인다.** 위 셋(`rerank=off` 꼴)과 모양이 갈리는데,
    # 일부러다 — 꺼진 지문이 **이 인자가 생기기 전과 글자 그대로 같아야** `qa_cache_*` 의
    # 기존 항목이 살아남는다. 그것이 필요한 이유는 지금 `exact` 캐시가 켜진 채
    # *"`source=="user"` 50줄에서 적중률 5% 미만이면 끈다"* 의 표본을 모으는 중이라
    # (`evaluation/README.md` 8절), 전량 미스가 그 판정을 아래로 밀기 때문이다.
    #
    # **「기본은 싣고 뺄 것만 명시」와 어긋나지 않는다**(모듈 docstring) — 그 규칙이 막는 것은
    # *파이프라인이 다른데 지문이 같은* 경우인데, 루프가 꺼져 있으면 파이프라인이 **실제로**
    # 예전과 같다. 켜지면 검색이 여러 번 돌아 실제로 다르므로 그때 조각이 붙는다
    if max_retries > 0:
        parts.append(f"agent_retries={max_retries}")
    return "|".join(parts)
