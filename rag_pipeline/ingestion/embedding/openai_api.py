"""OpenAI 임베딩 구현체.

## 실패하면 예외를 던진다 — 다른 외부 API 핸들러와 반대다

`table_llamaparse` 와 `image_vision` 은 실패해도 빈 리스트를 돌려주고 라우터의 폴백에
맡긴다. **여기서는 그러지 않는다.** 그 규약은 내려갈 층이 있어서 성립하는 것이고
(LlamaParse -> PyMuPDF, Vision -> OCR) 임베딩에는 없다. 빈 벡터를 돌려주면 그 청크는
영원히 검색되지 않는데 아무도 모른다. 근거는 plan_log/embedding.md 의 "실패 계약" 절.

그래서 **`len(반환) == len(입력)` 이 이 모듈의 계약이고, 못 지키게 되는 모든 경우가
`EmbeddingError` 다.** 배치 응답이 요청보다 적게 와도 조용히 짧은 목록을 돌려주지 않는다 —
호출부가 `zip(chunks, vectors)` 로 자리를 맞추므로 한 칸만 밀려도 검색이 엉뚱한 문서를
가리키게 된다.

## 왜 상한이 호출 수가 아니라 토큰인가

`max_vision_calls_per_doc` 를 그대로 베끼면 아무것도 안 막는다. 임베딩은 배치라 청크
500개가 5회 호출로 끝난다. 돈은 호출이 아니라 토큰이라 `tiktoken` 으로 **보내기 전에** 세서
Vision 과 같은 사전 검사 모양을 유지한다.

## 모킹 이음새

`_make_client()` 는 **모듈 레벨 함수**여야 한다. 테스트가
`mocker.patch.object(openai_api, "_make_client", ...)` 로 갈아끼운다. 클라이언트를
`__init__` 에서 만들거나 이 함수를 메서드로 옮기면 외부 API 테스트가 전부 깨진다.

## 지금 안 하는 것

같은 텍스트가 한 입력 안에 두 번 나와도 합치지 않는다. cleaner 가 반복 머리말을 이미
지웠고 청크에는 제목 경로가 앞에 붙어 서로 달라지므로, 중복 제거로 아낄 것이 크지 않다.
"""

from __future__ import annotations

from typing import Any

import tiktoken

from rag_pipeline.config import EmbeddingConfig
from rag_pipeline.exceptions import EmbeddingError
from rag_pipeline.ingestion.embedding.base import EmbeddingUsage, cache_salt_for
from rag_pipeline.ingestion.loader.content.base import ResultCache, content_hash

# 모델별 인코딩을 못 찾을 때 쓸 기본값. text-embedding-3-* 가 실제로 쓰는 것이기도 하다
_FALLBACK_ENCODING = "cl100k_base"


# OpenAI 클라이언트를 만든다. 키가 없으면 None.
# **모듈 레벨이어야 한다** — 테스트의 모킹 이음새다 (모듈 docstring 참고)
def _make_client(api_key: str | None) -> Any | None:
    if not api_key:
        return None
    try:
        from openai import OpenAI
    except ImportError:
        return None
    return OpenAI(api_key=api_key)


# 모델에 맞는 토크나이저. 모르는 모델이면 cl100k_base 로 떨어진다
def _encoding_for(model: str) -> Any:
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        return tiktoken.get_encoding(_FALLBACK_ENCODING)


# 텍스트 묶음의 토큰 수. 호출 전 상한 검사에 쓴다
def _count_tokens(texts: list[str], model: str) -> int:
    encoding = _encoding_for(model)
    return sum(len(encoding.encode(text)) for text in texts)


# 응답 항목 하나에서 벡터를 꺼낸다. 모양이 틀렸을 때 AttributeError·TypeError 가 날것으로
# 새어 나가면 backend 가 매핑표에서 못 찾아 500 을 돌려준다 — EmbeddingError 로 바꿔 올린다
def _as_vector(item: Any, index: int) -> list[float]:
    try:
        return [float(value) for value in item.embedding]
    except (AttributeError, TypeError, ValueError) as exc:
        raise EmbeddingError(
            f"임베딩 응답 {index}번 항목에서 벡터를 읽지 못했습니다: {type(exc).__name__}",
            user_message="임베딩 응답이 손상되어 처리를 중단했습니다.",
        ) from exc


# 응답에서 벡터를 꺼내 요청 순서대로 되돌린다. 자리로만 믿지 않고 index 를 본다 —
# 한 칸만 밀려도 청크와 벡터가 어긋나는데 아무 에러도 안 난다
def _ordered_vectors(data: Any, expected: int) -> list[list[float]]:
    items = list(data or [])
    if len(items) != expected:
        raise EmbeddingError(
            f"임베딩 응답 개수가 요청과 다릅니다: 요청 {expected} / 응답 {len(items)}",
            user_message="임베딩 응답이 요청과 맞지 않아 처리를 중단했습니다.",
        )

    slots: list[list[float] | None] = [None] * expected
    for item in items:
        index = getattr(item, "index", None)
        if not isinstance(index, int) or not 0 <= index < expected:
            raise EmbeddingError(
                f"임베딩 응답의 index가 범위를 벗어났습니다: {index!r}",
                user_message="임베딩 응답이 손상되어 처리를 중단했습니다.",
            )
        if slots[index] is not None:
            raise EmbeddingError(
                f"임베딩 응답에 index {index}가 두 번 나왔습니다",
                user_message="임베딩 응답이 손상되어 처리를 중단했습니다.",
            )
        slots[index] = _as_vector(item, index)

    # 위 셋을 통과했으면 빈 칸이 남을 수 없지만, 타입을 좁히기 위해 확인한다
    if any(slot is None for slot in slots):
        raise EmbeddingError(
            "임베딩 응답에 빠진 자리가 있습니다",
            user_message="임베딩 응답이 손상되어 처리를 중단했습니다.",
        )
    return [slot for slot in slots if slot is not None]


# OpenAI 임베딩 API로 텍스트를 벡터로 만든다
class OpenAIEmbedder:
    # 클라이언트를 여기서 만들지 않는다. _make_client() 를 호출 시점에 부르는 것이
    # 테스트의 모킹 이음새다
    def __init__(self, config: EmbeddingConfig) -> None:
        self._config = config
        self._usage = EmbeddingUsage()
        self._cache = ResultCache(
            config.embedding_cache_dir, enabled=config.embedding_cache_enabled
        )

    # 지금까지 쌓인 비용. 문서당으로 읽으려면 호출부가 reset() 한다
    @property
    def usage(self) -> EmbeddingUsage:
        return self._usage

    # 캐시 키의 salt. **규칙은 구현체가 갖지 않는다** — base.cache_salt_for() 가 정하고
    # 두 번째 구현체도 같은 것을 부른다 (base.py 모듈 docstring 참고)
    @property
    def _salt(self) -> str:
        return cache_salt_for(self._config)

    # 문서 텍스트들을 벡터로. 반환 길이는 입력 길이와 반드시 같다
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed_all(list(texts))

    # 질의 하나를 벡터로. 문서와 같은 모델·같은 salt 를 쓴다
    def embed_query(self, text: str) -> list[float]:
        return self._embed_all([text])[0]

    # 캐시에 있는 것은 꺼내고 없는 것만 API로 보낸 뒤 원래 자리에 되돌린다
    def _embed_all(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        for index, text in enumerate(texts):
            if not text or not text.strip():
                raise EmbeddingError(
                    f"{index}번째 입력이 비어 있습니다",
                    user_message="빈 텍스트는 임베딩할 수 없습니다.",
                )

        slots: list[list[float] | None] = [None] * len(texts)
        missing: list[int] = []
        for index, text in enumerate(texts):
            cached = self._cache.get(content_hash(text.encode("utf-8"), salt=self._salt))
            vector = cached.get("vector") if isinstance(cached, dict) else None
            if isinstance(vector, list) and vector:
                slots[index] = [float(value) for value in vector]
                self._usage.cache_hits += 1
            else:
                missing.append(index)

        if missing:
            self._fill_missing(texts, slots, missing)

        # 계약 확인. 여기까지 왔으면 빈 칸이 없어야 한다
        if any(slot is None for slot in slots):
            raise EmbeddingError(
                "임베딩 결과에 빈 자리가 남았습니다",
                user_message="임베딩을 완료하지 못했습니다.",
            )
        return [slot for slot in slots if slot is not None]

    # 캐시에 없던 자리들을 배치로 채운다
    def _fill_missing(
        self, texts: list[str], slots: list[list[float] | None], missing: list[int]
    ) -> None:
        config = self._config
        pending = [texts[index] for index in missing]

        # 상한은 보내기 전에 본다. Vision 이 max_vision_calls_per_doc 를 검사하는 자리와 같다.
        # usage 는 누적되므로 여기서 말하는 "문서 하나"는 reset() 사이의 구간이다
        tokens = _count_tokens(pending, config.embedding_model)
        if self._usage.total_tokens + tokens > config.max_embedding_tokens_per_doc:
            raise EmbeddingError(
                f"임베딩 토큰 상한({config.max_embedding_tokens_per_doc})을 넘습니다: "
                f"누적 {self._usage.total_tokens} + 이번 {tokens}",
                user_message=(
                    "문서가 너무 커서 임베딩 비용 상한을 넘었습니다. "
                    "문서를 나눠 올리거나 상한을 조정하세요."
                ),
            )

        client = _make_client(config.openai_api_key)
        if client is None:
            raise EmbeddingError(
                "OPENAI_API_KEY가 없어 임베딩을 만들 수 없습니다",
                user_message="임베딩 API 키가 설정되지 않아 처리할 수 없습니다.",
            )

        size = max(config.embedding_batch_size, 1)
        for start in range(0, len(pending), size):
            batch = pending[start : start + size]
            vectors = self._call_api(client, batch)
            for offset, vector in enumerate(vectors):
                index = missing[start + offset]
                slots[index] = vector
                self._cache.set(
                    content_hash(texts[index].encode("utf-8"), salt=self._salt),
                    {"vector": vector},
                )

    # 배치 하나를 실제로 보낸다. 카운터는 **성공한 뒤에만** 올린다
    def _call_api(self, client: Any, batch: list[str]) -> list[list[float]]:
        config = self._config
        params: dict[str, Any] = {"model": config.embedding_model, "input": batch}
        if config.embedding_dimensions is not None:
            params["dimensions"] = config.embedding_dimensions

        try:
            response = client.embeddings.create(**params)
        except Exception as exc:  # noqa: BLE001 - 원인을 EmbeddingError로 바꿔 위로 올린다
            raise EmbeddingError(
                f"임베딩 API 호출에 실패했습니다: {type(exc).__name__}",
                user_message="임베딩 서비스를 사용할 수 없어 처리를 중단했습니다.",
            ) from exc

        vectors = _ordered_vectors(getattr(response, "data", None), len(batch))
        self._check_dimensions(vectors)

        self._usage.api_calls += 1
        usage = getattr(response, "usage", None)
        reported = getattr(usage, "total_tokens", None)
        self._usage.total_tokens += (
            reported
            if isinstance(reported, int)
            else _count_tokens(batch, config.embedding_model)
        )
        return vectors

    # 한 응답 안에서 차원이 흔들리거나 설정과 다르면 저장 단계가 아니라 여기서 잡는다
    def _check_dimensions(self, vectors: list[list[float]]) -> None:
        sizes = {len(vector) for vector in vectors}
        if len(sizes) > 1:
            raise EmbeddingError(
                f"한 응답 안에서 벡터 차원이 다릅니다: {sorted(sizes)}",
                user_message="임베딩 응답이 손상되어 처리를 중단했습니다.",
            )

        expected = self._config.embedding_dimensions
        if expected is not None and sizes and sizes != {expected}:
            raise EmbeddingError(
                f"요청한 차원({expected})과 응답 차원({sizes.pop()})이 다릅니다",
                user_message="임베딩 응답이 요청과 맞지 않아 처리를 중단했습니다.",
            )
