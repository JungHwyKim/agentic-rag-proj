"""실제 OpenAI 임베딩 API 로 **응답 스키마 가정**을 확인한다.

**이 파일은 실제 과금을 일으킨다.** `@pytest.mark.integration` 이 달려 있어 기본 실행
(`pyproject.toml` 의 `addopts = "-m 'not integration'"`)에서 빠지고, `.claude/hooks/
paid_test_check.py` 가 `-m integration` 자체를 막는다. 사용자가 `ALLOW_PAID_TESTS=1` 을
**자기 환경에** 설정해야 돈다 — 모델이 명령줄에서 설정해도 훅에는 닿지 않는다.

## 왜 필요한가

나머지 임베딩 테스트는 `_make_client()` 를 가짜로 갈아끼운다. 그래서 **가짜가 흉내내는
모양이 실제와 같은지는 검증되지 않는다.** 2026-08-18 첫 실행에서 아래 셋이 확인됐다.

- `response.data[i].index` 가 존재하고 int 다 (이걸 믿고 재정렬한다)
- `response.data[i].embedding` 이 실수 목록이고 1536차원이다
- `response.usage.total_tokens` 가 존재하고 int 다 (비용 카운터가 이걸 쓴다)

## 같은 텍스트라도 호출마다 벡터가 다르다 — 1e-6 비교를 쓰지 않는다

첫 실행에서 **`abs(a - b) < 1e-6` 로 두 호출을 비교한 테스트 2개가 실패했다.** 원인은
제품이 아니라 그 단언이었다. OpenAI 임베딩은 호출 간 비트 재현성을 보장하지 않는다 —
배치 구성이 달라지면 부동소수점 결과가 흔들린다.

그래서 **원소별 비교를 쓰지 않는다.** 되돌리면 다시 실패하고, 더 나쁘게는 **"자리가
밀린 것"과 "부동소수점 잡음"을 구분하지 못한다**(둘 다 `assert False` 로만 보인다).
순서 보존은 **자기 짝과의 유사도가 남과의 유사도보다 높은가**로 판정한다 — 잡음에
영향받지 않으면서 자리 밀림은 확실히 잡는다.

## 비용

짧은 한국어 문자열만 보낸다(8회 호출 · 186토큰 · $0.0000037). **실파일을 태우지 않는다** —
5문서 전체는 139,438토큰이고 스키마 확인에는 필요 없다.

캐시는 끈다. 켜면 두 번째 실행이 API 를 안 불러 아무것도 검증하지 못한 채 통과한다.
측정값을 보려면 `-s` 를 붙인다.
"""

from __future__ import annotations

import math

import pytest

from rag_pipeline.config import EmbeddingConfig
from rag_pipeline.ingestion.embedding import get_embedder

pytestmark = pytest.mark.integration

# 서로 뜻이 다른 문장을 쓴다. 순서 판정이 유사도 차이에 기대므로 비슷하면 판별력이 떨어진다
TEXTS = ["연차휴가는 며칠인가?", "정보시스템 의무사항 안내", "학교기록물 관리 지침"]

# text-embedding-3-small 의 기본 차원
DEFAULT_DIM = 1536

# 같은 텍스트를 두 번 임베딩했을 때 기대하는 최소 유사도.
# 비트 재현성은 없지만 의미는 같으므로 1 에 아주 가까워야 한다
SAME_TEXT_MIN_COSINE = 0.999


# 코사인 유사도. 원소별 비교 대신 이것으로 판정한다 (모듈 docstring 참고)
def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    return dot / (norm_a * norm_b)


# 벡터의 길이. 1 이면 저장소가 코사인 대신 내적을 쓸 수 있다 — indexing 단계의 입력이다
def _norm(vector: list[float]) -> float:
    return math.sqrt(sum(value * value for value in vector))


# 실제 키를 읽는 설정. 캐시를 꺼서 반드시 API 를 타게 한다
@pytest.fixture
def live_config() -> EmbeddingConfig:
    config = EmbeddingConfig(embedding_cache_enabled=False)
    if not config.openai_api_key:
        pytest.skip("OPENAI_API_KEY 가 없어 건너뜁니다.")
    return config


# 응답 스키마 가정을 한 번의 호출로 전부 확인한다. 호출을 나누면 그만큼 과금된다
def test_live_response_schema(live_config: EmbeddingConfig) -> None:
    embedder = get_embedder(live_config)

    vectors = embedder.embed_documents(TEXTS)

    # 계약: 길이가 같다
    assert len(vectors) == len(TEXTS)
    # data[i].embedding 이 실수 목록이라는 가정
    assert all(isinstance(value, float) for value in vectors[0])
    # 차원이 문서화된 값과 같고 항목마다 흔들리지 않는다
    assert {len(vector) for vector in vectors} == {DEFAULT_DIM}
    # usage.total_tokens 가정 — 없으면 비용 카운터가 로컬 추정치로 조용히 떨어진다
    assert embedder.usage.api_calls == 1
    assert embedder.usage.total_tokens > 0

    # 뜻이 다른 문장이면 벡터도 달라야 한다. 전부 같으면 API 가 잘못 돌고 있는 것이다
    assert _cosine(vectors[0], vectors[1]) < 0.95

    norms = [_norm(vector) for vector in vectors]
    print(f"\n[schema] tokens={embedder.usage.total_tokens} dim={len(vectors[0])}")
    print(f"[schema] norms={[round(n, 6) for n in norms]}")
    print(f"[schema] cos(0,1)={_cosine(vectors[0], vectors[1]):.6f}")


# 기본 차원의 벡터가 단위 길이인가. **indexing 이 거리 함수를 고르는 근거**라 따로 잰다.
# 단위 길이면 qdrant 에서 Cosine 대신 더 싼 Dot 을 쓸 수 있다
def test_live_vectors_are_unit_norm(live_config: EmbeddingConfig) -> None:
    vector = get_embedder(live_config).embed_query(TEXTS[0])

    norm = _norm(vector)
    print(f"\n[norm] default-dim norm={norm:.9f}")
    assert abs(norm - 1.0) < 1e-3


# dimensions 파라미터가 실제로 먹는지. "차원은 설정값"이라는 전제가 여기 걸려 있다.
# 축소 벡터의 길이도 함께 잰다 — 정규화가 풀리면 indexing 쪽에서 다시 정규화해야 한다
def test_live_dimensions_parameter(live_config: EmbeddingConfig) -> None:
    config = live_config.model_copy(update={"embedding_dimensions": 256})

    vector = get_embedder(config).embed_query(TEXTS[0])

    assert len(vector) == 256
    norm = _norm(vector)
    print(f"\n[dims] reduced-dim(256) norm={norm:.9f}")


# 질의와 문서가 같은 임베딩 공간인지. **원소별 비교가 아니라 유사도로 본다** —
# 호출 간 비트 재현성이 없다는 것을 첫 실행에서 확인했다(모듈 docstring)
def test_live_query_matches_document(live_config: EmbeddingConfig) -> None:
    embedder = get_embedder(live_config)

    query_vector = embedder.embed_query(TEXTS[0])
    document_vector = embedder.embed_documents([TEXTS[0]])[0]

    assert len(query_vector) == len(document_vector)
    similarity = _cosine(query_vector, document_vector)
    max_delta = max(abs(a - b) for a, b in zip(query_vector, document_vector, strict=True))
    print(f"\n[query] cos={similarity:.9f} max_elem_delta={max_delta:.3e}")
    assert similarity > SAME_TEXT_MIN_COSINE


# 배치를 나눠 보내도 자리가 안 밀리는지.
# **각 벡터가 자기 짝과 제일 비슷한가**로 본다 — 부동소수점 잡음에는 흔들리지 않고
# 자리 밀림은 확실히 잡는다(밀리면 남과 더 비슷해진다)
def test_live_batching_preserves_order(live_config: EmbeddingConfig) -> None:
    config = live_config.model_copy(update={"embedding_batch_size": 1})
    embedder = get_embedder(config)

    batched = embedder.embed_documents(TEXTS)
    assert embedder.usage.api_calls == len(TEXTS)

    single = get_embedder(live_config).embed_documents(TEXTS)

    for index, one in enumerate(single):
        similarities = [_cosine(one, many) for many in batched]
        best = max(range(len(similarities)), key=lambda i: similarities[i])
        print(f"\n[order] {index} -> best={best} sims={[round(s, 6) for s in similarities]}")
        # 자기 짝이 가장 비슷해야 한다
        assert best == index
        # 그리고 같은 텍스트끼리는 1 에 아주 가까워야 한다
        assert similarities[index] > SAME_TEXT_MIN_COSINE
