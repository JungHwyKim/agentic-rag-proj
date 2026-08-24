"""로컬 임베딩 구현체 — sentence-transformers.

`OpenAIEmbedder` 의 두 번째 짝이다. 계약(`len(반환) == len(입력)`, 실패는 `EmbeddingError`,
`ResultCache` + `cache_salt_for()`)은 **글자 그대로 같고**, 다른 것만 아래에 적는다.

## 왜 이 구현체가 생겼나

`plan_log/embedding.md` 의 *모델·차원* 미결이 *"낱말로 못 찾고 적재로도 못 고치는 자리가
남았다"* 까지 좁혀졌고(포맷 비교 · hybrid 측정 · 어휘 겹침 세 회차), 남은 후보가 **임베딩의
한국어 변별력** 하나였다. 그것을 재려면 **계열·토크나이저가 진짜 다른** 두 번째 모델이
있어야 한다 — 같은 계열(`text-embedding-3-large`)로 재면 *"안 움직였다"* 가 임베딩 축을
못 닫는다. 3회차가 `nlpai-lab/KURE-v1` 을 골랐다.

## 기본 pytest 는 여기로 안 온다 — 두 겹으로 막는다

**로컬 모델은 키가 없어도 돈다.** 그래서 지금까지의 방어(*"키를 지운다"*)가 여기서는 안
통하고, 기본 `pytest` 가 가중치 2.27GB 를 내려받는 경로가 **아무 에러 없이** 열릴 수 있다.

1. `tests/conftest.py` 의 `offline_config` 가 `embedding_provider="openai"` 를 못 박는다
2. `torch`·`sentence-transformers` 가 `requirements-local.txt` 에만 있어, 기본 설치에는
   애초에 그 패키지가 없다

## device 를 `cpu` 로 못 박는다 — 설정으로 안 연다

**재현성이다.** cu128 torch 를 깐 머신에서만 GPU 로 가면 같은 명령이 다른 벡터를 내고,
*"baseline 은 재현될 때만 기준선"* 이 무너진다 — `evaluation/config.py` 가 `.env` 를 막은
것과 같은 잣대다. 설정으로 열면 그 값이 `.env` 를 타서 머신마다 갈린다.

## 정규화를 명시적으로 켠다

`indexing/qdrant_store.py` 가 거리 척도를 **Dot 으로 고정**했고 그 근거가 *"벡터가 단위
길이"* 라는 실측이다. KURE-v1 이 `2_Normalize` 모듈을 갖고 있어 사실 켜져 있지만,
**체크포인트 구성에 기대면 안 된다** — 모듈이 없는 모델로 갈아끼우는 순간 검색이 에러 없이
나빠진다. 그래서 `normalize_embeddings=True` 를 넘기고, 실제 노름은 실측해 기록한다.

## 토큰을 모델 자신의 토크나이저로 센다

`OpenAIEmbedder` 는 `tiktoken`(cl100k)으로 세지만 KURE-v1 은 XLM-R sentencepiece 다.
`tiktoken` 으로 세면 **리포트에 실리는 수가 이 모델의 것이 아니다.**

`max_embedding_tokens_per_doc` 검사는 **그대로 적용한다.** 과금은 0이지만 그 상한의 두 번째
목적(*"400청크급 문서는 이 코퍼스에서 사고를 의심할 구간"*)은 provider 를 안 탄다.
다만 200,000 이라는 값 자체는 cl100k 기준으로 잡힌 것이라, XLM-R 기준 실측 뒤에 다시 본다.

## 모킹 이음새

`_make_model()` 은 **모듈 레벨 함수**여야 한다. `openai_api._make_client()` 와 같은 자리이고,
테스트가 `mocker.patch.object(local_st, "_make_model", ...)` 로 갈아끼운다. 인라인화하면
임베딩 테스트가 전부 실제 가중치를 내려받는다. `sentence_transformers` import 를 **이 함수
안에서** 하는 이유도 같은 축이다 — 모듈 상단에 두면 미설치 환경에서 `get_embedder()` 가
아니라 **import 자체가** 죽는다.
"""

from __future__ import annotations

from typing import Any

from rag_pipeline.config import EmbeddingConfig
from rag_pipeline.exceptions import EmbeddingError
from rag_pipeline.ingestion.embedding.base import EmbeddingUsage, cache_salt_for
from rag_pipeline.ingestion.loader.content.base import ResultCache, content_hash

# 벡터가 머신마다 갈리면 기준선이 성립하지 않는다 (모듈 docstring 참고)
_DEVICE = "cpu"


# sentence-transformers 모델을 만든다.
# **모듈 레벨이어야 한다** — 테스트의 모킹 이음새다 (모듈 docstring 참고)
def _make_model(model_name: str) -> Any:
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise EmbeddingError(
            f"sentence-transformers 가 설치되지 않았습니다: {exc}",
            user_message=(
                "로컬 임베딩을 쓰려면 requirements-local.txt 를 설치해야 합니다: "
                "uv pip install -r requirements-local.txt"
            ),
        ) from exc
    return SentenceTransformer(model_name, device=_DEVICE)


# 모델 자신의 토크나이저로 센다. tiktoken 으로 세면 이 모델의 수가 아니다
def _count_tokens(model: Any, texts: list[str]) -> int:
    tokenizer = getattr(model, "tokenizer", None)
    if tokenizer is None:
        return 0
    try:
        encoded = tokenizer(texts, add_special_tokens=True)["input_ids"]
    except Exception:  # noqa: BLE001 - 토큰 세기 실패로 임베딩을 막지 않는다. 상한만 못 본다
        return 0
    return sum(len(ids) for ids in encoded)


# 응답을 파이썬 float 목록으로 편다. numpy 배열이든 리스트든 같은 모양으로 받는다
def _as_vectors(raw: Any, expected: int) -> list[list[float]]:
    try:
        vectors = [[float(value) for value in row] for row in raw]
    except (TypeError, ValueError) as exc:
        raise EmbeddingError(
            f"임베딩 결과를 벡터로 읽을 수 없습니다: {type(exc).__name__}",
            user_message="임베딩 결과가 손상되어 처리를 중단했습니다.",
        ) from exc

    if len(vectors) != expected:
        raise EmbeddingError(
            f"임베딩 개수가 입력과 다릅니다: 입력 {expected} / 결과 {len(vectors)}",
            user_message="임베딩을 완료하지 못했습니다.",
        )
    return vectors


# 로컬 모델로 텍스트를 벡터로 만든다
class LocalEmbedder:
    # 모델을 여기서 만들지 않는다. _make_model() 을 호출 시점에 부르는 것이 모킹 이음새이고,
    # 게으르게 두면 **가중치를 실제로 쓸 때만** 내려받는다
    def __init__(self, config: EmbeddingConfig) -> None:
        # 차원 축소는 여기서 거절한다. bge-m3 계열은 MRL 미학습이라 앞 N 차원을 잘라
        # 재정규화하면 품질이 **조용히** 나빠진다 — 도는데 틀린 상태가 제일 늦게 발각된다
        if config.embedding_dimensions is not None:
            raise EmbeddingError(
                f"로컬 임베딩은 차원 축소를 지원하지 않습니다: "
                f"embedding_dimensions={config.embedding_dimensions}",
                user_message=(
                    "이 임베딩 모델은 차원을 줄일 수 없습니다. "
                    "EMBEDDING_DIMENSIONS 를 비워두세요."
                ),
            )
        self._config = config
        self._usage = EmbeddingUsage()
        self._model: Any | None = None
        self._cache = ResultCache(
            config.embedding_cache_dir, enabled=config.embedding_cache_enabled
        )

    # 지금까지 쌓인 비용. 과금은 0이지만 호출 수·토큰 수는 리포트가 읽는다
    @property
    def usage(self) -> EmbeddingUsage:
        return self._usage

    # 캐시 키의 salt. **규칙은 구현체가 갖지 않는다** — base.cache_salt_for() 가 정한다.
    # provider 는 안 들어간다: 모델 이름이 이미 두 갈래를 가른다 (base.py 참고)
    @property
    def _salt(self) -> str:
        return cache_salt_for(self._config)

    # 가중치는 한 번만 올린다. 캐시가 전량 적중하면 아예 안 올린다
    def _load(self) -> Any:
        if self._model is None:
            self._model = _make_model(self._config.embedding_model)
        return self._model

    # 문서 텍스트들을 벡터로. 반환 길이는 입력 길이와 반드시 같다
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed_all(list(texts))

    # 질의 하나를 벡터로. **KURE-v1 은 질의 접두사를 안 쓴다**
    # (config_sentence_transformers.json 이 prompts: {} · default_prompt_name: null).
    # 접두사를 요구하는 모델을 붙이는 날 여기와 cache_salt_for() 를 함께 고친다
    def embed_query(self, text: str) -> list[float]:
        return self._embed_all([text])[0]

    # 캐시에 있는 것은 꺼내고 없는 것만 모델에 태운 뒤 원래 자리에 되돌린다.
    # OpenAIEmbedder._embed_all() 과 같은 모양이다 — 자리 밀림을 막는 것이 요점이다
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
        model = self._load()

        # 상한은 태우기 전에 본다. 과금은 0이지만 "이 코퍼스에서 사고를 의심할 구간" 이라는
        # 두 번째 목적은 provider 를 안 탄다 (모듈 docstring 참고)
        tokens = _count_tokens(model, pending)
        if self._usage.total_tokens + tokens > config.max_embedding_tokens_per_doc:
            raise EmbeddingError(
                f"임베딩 토큰 상한({config.max_embedding_tokens_per_doc})을 넘습니다: "
                f"누적 {self._usage.total_tokens} + 이번 {tokens}",
                user_message=(
                    "문서가 너무 커서 임베딩 상한을 넘었습니다. "
                    "문서를 나눠 올리거나 상한을 조정하세요."
                ),
            )

        size = max(config.embedding_batch_size, 1)
        for start in range(0, len(pending), size):
            batch = pending[start : start + size]
            vectors = self._encode(model, batch)
            for offset, vector in enumerate(vectors):
                index = missing[start + offset]
                slots[index] = vector
                self._cache.set(
                    content_hash(texts[index].encode("utf-8"), salt=self._salt),
                    {"vector": vector},
                )

    # 배치 하나를 실제로 태운다. 카운터는 **성공한 뒤에만** 올린다
    def _encode(self, model: Any, batch: list[str]) -> list[list[float]]:
        try:
            raw = model.encode(
                batch,
                # indexing 이 Dot 을 쓰는 근거가 "벡터가 단위 길이" 라는 실측이다.
                # 체크포인트의 Normalize 모듈에 기대지 않고 여기서 못 박는다
                normalize_embeddings=True,
                batch_size=max(self._config.embedding_batch_size, 1),
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        except EmbeddingError:
            raise
        except Exception as exc:  # noqa: BLE001 - 원인을 EmbeddingError로 바꿔 위로 올린다
            raise EmbeddingError(
                f"로컬 임베딩에 실패했습니다: {type(exc).__name__}",
                user_message="임베딩 모델을 실행할 수 없어 처리를 중단했습니다.",
            ) from exc

        vectors = _as_vectors(raw, len(batch))
        self._check_dimensions(vectors)

        self._usage.api_calls += 1
        self._usage.total_tokens += _count_tokens(model, batch)
        return vectors

    # 한 배치 안에서 차원이 흔들리면 저장 단계가 아니라 여기서 잡는다.
    # OpenAI 쪽과 달리 "요청한 차원" 검사는 없다 — __init__ 이 이미 거절했다
    def _check_dimensions(self, vectors: list[list[float]]) -> None:
        sizes = {len(vector) for vector in vectors}
        if len(sizes) > 1:
            raise EmbeddingError(
                f"한 배치 안에서 벡터 차원이 다릅니다: {sorted(sizes)}",
                user_message="임베딩 결과가 손상되어 처리를 중단했습니다.",
            )
        if sizes == {0}:
            raise EmbeddingError(
                "임베딩 결과가 빈 벡터입니다",
                user_message="임베딩 결과가 손상되어 처리를 중단했습니다.",
            )
