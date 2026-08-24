"""답변 캐시의 qdrant 구현. **문서와 같은 클라이언트를 쓰되 컬렉션은 다르다.**

## 왜 클라이언트를 빌려 쓰나

qdrant 로컬 파일 모드가 저장 디렉터리에 배타 잠금을 건다. `get_store()` 를 한 번 더 부르면
같은 프로세스 안에서 자기 자신과 충돌한다 — backend 는 `lifespan` 에서 이미 하나 열어 앱
수명 내내 들고 있기 때문이다. 그래서 `QdrantStore.client` 를 빌린다. 한 클라이언트가 여러
컬렉션을 보는 것은 qdrant 의 정상 사용이고, 닫을 것은 여전히 store 하나뿐이다.

## 왜 컬렉션을 가르나

섞으면 `iter_chunks()`(BM25 가 전량 훑는다)와 `list_documents()`(사이드바)가 **캐시 항목을
문서 청크로 센다.** 필터로 가르는 길은 없다 — `search(filters=...)` 를 안 만들기로 한 것이
`plan_log/indexing.md` 의 확정이다.

## 왜 답과 질문이 한 point 인가

**답은 검색 대상이 아니라 질문에 딸린 값이다.** 컬렉션을 갈라 답을 따로 두면 qdrant 가
point 마다 벡터를 요구해 *아무도 검색하지 않는 답변에 임베딩 비용*이 붙고, 쓰기가 둘로
갈려 "질문만 저장된" 반쪽 상태가 생긴다 — 그 상태에서 히트하면 빈 답을 돌려준다.

## 실패는 예외가 아니라 미스다

로더의 *빈 결과 + 경고* 규약이 성립하는 조건은 **내려갈 층이 있는가**이고 여기는 있다
(정상 검색·생성이 그대로 돈다). embedding·indexing 이 예외를 던지는 것은 그 층이 없어서였다.
그래서 이 모듈은 **아무것도 밖으로 안 던진다.** 무슨 일이 있었는지는 `warnings` 로 나간다
(`Bm25Index.warnings` 와 같은 자리다).

캐시가 죽어서 질의가 죽으면, 최적화가 신뢰성을 깎은 것이 된다.

## 무효화를 왜 근거 point 로 하나

문서를 재색인·삭제하면 캐시된 답이 낡는다. `point_id_for()` 가 결정론적이라 **재색인해도
같은 청크는 같은 id** 이므로, 저장해 둔 근거 id 를 되짚으면 사라진 것이 잡힌다. 텍스트
해시까지 함께 보는 이유는 *삭제*와 *수정*이 다른 사건이어서다 — id 는 그대로인데 내용만
바뀐 경우를 id 만으로는 못 본다.

TTL(시간)을 안 쓰는 것은 **축이 어긋나서**다. 코퍼스가 안 바뀌면 한 달 전 답도 옳고,
바뀌면 1분 전 답도 틀리다. 시간은 그 둘을 못 가른다.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
import uuid
from datetime import UTC, datetime
from typing import Any

from qdrant_client import models

from rag_pipeline.config import AnswerCacheConfig, EmbeddingConfig, IndexingConfig
from rag_pipeline.ingestion.embedding.base import Embedder
from rag_pipeline.ingestion.indexing.qdrant_store import QdrantStore, collection_name_for
from rag_pipeline.schemas.answer import Answer, CacheOrigin
from rag_pipeline.schemas.retrieval import RetrievedChunk
from rag_pipeline.schemas.vector import point_id_for

__all__ = ["QdrantAnswerCache", "cache_point_id_for"]

# 질문 -> point UUID 파생에 쓰는 고정 네임스페이스. **청크용(schemas/vector.py)과 일부러
# 다르다** — 두 파생이 같은 네임스페이스를 쓸 이유가 없고, 갈라 두면 어느 쪽에서 나온
# id 인지가 값 자체로 구분된다. 바꾸면 기존 캐시가 전부 새 point 가 되어 한 번 비워진다
_NAMESPACE = uuid.UUID("c3a7f21b-58d4-5e19-9f0a-2b7d4e6c8a13")

# 질문 키를 만들 때 공백을 하나로 접는다. **지우지는 않는다** —
# evaluation/scoring.py:squash() 가 공백을 통째로 지우는 것은 PDF 추출이 낱말 사이 공백을
# 잃기 때문이고, 사용자가 친 질문에는 그 문제가 없다
_SPACES = re.compile(r"\s+")


# 질문에서 캐시 point id 를 만든다. 같은 질문은 늘 같은 자리라 재질의가 중복이 아니라 갱신이다
def cache_point_id_for(query: str) -> str:
    # NFC 정규화는 records.py 와 같은 이유다 — 눈에 같은 한글이 자모 분리 여부로 다른 키가
    # 되면, 막으려던 중복이 그대로 일어난다. 한국어 코퍼스라 이론적 위험이 아니다
    normalized = unicodedata.normalize("NFC", _SPACES.sub(" ", query).strip())
    return str(uuid.uuid5(_NAMESPACE, normalized))


# 청크 텍스트의 지문. 무효화가 *삭제*와 *수정*을 가르는 데 쓴다
def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# 답변 하나를 되살릴 payload 로 만든다. sources 를 통째로 싣는 이유는 인용을 잃지 않기
# 위해서다 — RetrievedChunk 의 metadata 에 page·heading_path 가 들어 있고 그것이
# 출처 표시의 유일한 근거다(schemas/answer.py)
#
# **`RetrievedChunk` 에는 point id 가 없다.** 저장소 신원을 들고 있는 것은 `StoredChunk.id`
# 인데 승격 과정에서 떨어진다. 그래서 저장할 때 `point_id_for(chunk_id)` 로 되만든다 —
# 그 파생이 결정론적이라 같은 청크는 늘 같은 자리이고, 무효화가 되짚을 열쇠가 된다
def _to_payload(answer: Answer, *, created_at: datetime) -> dict[str, Any]:
    return {
        "query": answer.query,
        "answer": answer.text,
        "sources": [source.model_dump(mode="json") for source in answer.sources],
        "source_point_ids": [point_id_for(source.chunk_id) for source in answer.sources],
        "source_hashes": [_text_hash(source.text) for source in answer.sources],
        "created_at": created_at.isoformat(),
    }


# 비슷한 질문에 이전 답변을 되돌려주는 캐시. 문서 저장소의 클라이언트를 빌려 쓴다
class QdrantAnswerCache:
    # store 를 참조만 하고 클라이언트를 여기서 당기지 않는다 — 게으름을 지키는 것이
    # backend lifespan 의 요구다(색인 전에 저장소를 헛되이 열면 안 된다)
    def __init__(
        self,
        store: QdrantStore,
        *,
        indexing: IndexingConfig,
        embedding: EmbeddingConfig,
        config: AnswerCacheConfig,
        threshold: float,
    ) -> None:
        self._store = store
        self._config = config
        self._threshold = threshold
        self._collection = collection_name_for(
            indexing, embedding, prefix=config.answer_cache_collection_name
        )
        self.warnings: list[str] = []

    # 이 캐시가 실제로 쓰는 컬렉션 이름. 문서 컬렉션과 반드시 다르다
    @property
    def collection(self) -> str:
        return self._collection

    # 같은 질문으로 볼 최소 유사도. 없으면 캐시 자체가 안 만들어진다(__init__.py)
    @property
    def threshold(self) -> float:
        return self._threshold

    # 비슷한 질문의 답을 찾는다. 없으면 None — **예외를 안 던진다**(모듈 docstring 참고)
    def lookup(self, query: str, *, embedder: Embedder) -> Answer | None:
        text = query.strip()
        if not text:
            return None

        try:
            vector = embedder.embed_query(text)
        except Exception as exc:  # noqa: BLE001 - 캐시 실패는 미스로 내린다. 질의는 계속 돈다
            self.warnings.append(f"캐시 조회용 임베딩에 실패해 건너뜁니다: {type(exc).__name__}")
            return None

        point = self._nearest(vector)
        if point is None:
            return None

        score = float(getattr(point, "score", 0.0) or 0.0)
        if score < self._threshold:
            return None

        payload = getattr(point, "payload", None) or {}
        if not self._sources_still_valid(payload, point_id=str(point.id)):
            return None

        return self._to_answer(payload, query=text, score=score)

    # 답 하나를 캐시에 넣는다. **근거가 없는 답은 저장하지 않는다** —
    # 무효화가 걸 축이 없어서, 문서를 새로 넣은 뒤에도 "찾지 못했습니다"를 되돌려준다
    def put(self, answer: Answer, *, embedder: Embedder) -> bool:
        if not answer.sources or not answer.text.strip():
            return False
        # 캐시에서 온 답을 다시 저장하지 않는다. 같은 point 를 원 질문이 아닌 말로 덮어써
        # 배지가 가리키는 "원래 질문"이 회를 거듭할수록 원본에서 멀어진다
        if answer.cached_from is not None:
            return False

        try:
            vector = embedder.embed_query(answer.query.strip())
        except Exception as exc:  # noqa: BLE001 - 저장 실패가 답변을 막으면 안 된다
            self.warnings.append(f"캐시 저장용 임베딩에 실패해 건너뜁니다: {type(exc).__name__}")
            return False

        if not vector:
            return False

        try:
            self._ensure_collection(len(vector))
            self._client.upsert(
                collection_name=self._collection,
                points=[
                    models.PointStruct(
                        id=cache_point_id_for(answer.query),
                        vector=vector,
                        payload=_to_payload(answer, created_at=datetime.now(UTC)),
                    )
                ],
            )
        except Exception as exc:  # noqa: BLE001 - 저장 실패가 답변을 막으면 안 된다
            self.warnings.append(f"답변을 캐시에 저장하지 못했습니다: {type(exc).__name__}")
            return False
        return True

    # 문서 저장소에서 빌린 클라이언트. 여기서 처음 당겨진다
    @property
    def _client(self) -> Any:
        return self._store.client

    # 컬렉션이 없으면 만든다. 거리 척도가 Dot 인 것은 문서 컬렉션과 같은 이유다 —
    # 벡터가 단위 길이라 Cosine 과 순위가 같고 더 싸다(plan_log/embedding.md 실측).
    # **그래서 임계값을 코사인 유사도로 읽어도 된다**
    def _ensure_collection(self, vector_size: int) -> None:
        client = self._client
        if client.collection_exists(self._collection):
            return
        client.create_collection(
            collection_name=self._collection,
            vectors_config=models.VectorParams(
                size=vector_size,
                distance=models.Distance.DOT,
            ),
        )

    # 제일 가까운 캐시 항목 하나. **1건만 본다** — 둘째 후보로 할 일이 없다.
    # 컬렉션이 없는 것은 오류가 아니라 "아직 캐시가 없다"이다 (QdrantStore.search() 와 갈린다:
    # 저쪽은 색인 자체가 없다는 뜻이라 사용자가 할 일이 있지만, 여기는 정상 첫 질의다)
    def _nearest(self, vector: list[float]) -> Any:
        try:
            client = self._client
            if not client.collection_exists(self._collection):
                return None
            response = client.query_points(
                collection_name=self._collection,
                query=list(vector),
                limit=1,
                with_payload=True,
            )
        except Exception as exc:  # noqa: BLE001 - 캐시 실패는 미스로 내린다
            self.warnings.append(f"캐시를 조회하지 못해 건너뜁니다: {type(exc).__name__}")
            return None

        points = list(getattr(response, "points", []) or [])
        return points[0] if points else None

    # 저장할 때의 근거 청크가 아직 그대로인지 본다. 아니면 그 캐시 항목을 지우고 미스로 내린다
    def _sources_still_valid(self, payload: dict[str, Any], *, point_id: str) -> bool:
        ids = [str(item) for item in (payload.get("source_point_ids") or [])]
        hashes = [str(item) for item in (payload.get("source_hashes") or [])]
        # 옛 형식이거나 짝이 안 맞는 항목은 판정할 수 없다. **믿지 않는 쪽으로 떨어뜨린다** —
        # 낡은 답을 주는 것이 이 기능의 유일한 위험이라 의심스러우면 다시 만드는 편이 싸다
        if not ids or len(ids) != len(hashes):
            self._drop(point_id)
            return False

        try:
            found = self._store.fetch_by_ids(ids)
        except Exception as exc:  # noqa: BLE001 - 판정 실패도 미스로 내린다
            self.warnings.append(f"캐시 무효화를 판정하지 못해 건너뜁니다: {type(exc).__name__}")
            return False

        current = {chunk.id: _text_hash(chunk.text) for chunk in found}
        for point, expected in zip(ids, hashes, strict=True):
            # 없으면 지워진 것이고, 다르면 고쳐진 것이다. 둘 다 이 답의 근거가 사라졌다는 뜻이다
            if current.get(point) != expected:
                self._drop(point_id)
                return False
        return True

    # 낡은 캐시 항목 하나를 지운다. 실패해도 미스로 돌아가면 되므로 조용히 넘어간다
    def _drop(self, point_id: str) -> None:
        try:
            self._client.delete(
                collection_name=self._collection,
                points_selector=models.PointIdsList(points=[point_id]),
            )
        except Exception as exc:  # noqa: BLE001 - 청소 실패가 질의를 막으면 안 된다
            self.warnings.append(f"낡은 캐시 항목을 지우지 못했습니다: {type(exc).__name__}")

    # payload 를 Answer 로 되살린다. **query 는 지금 질문이고 원 질문은 cached_from 에 남는다** —
    # 화면이 그 둘을 나란히 보여줘야 사용자가 뜻이 같은지 직접 판단할 수 있다
    def _to_answer(self, payload: dict[str, Any], *, query: str, score: float) -> Answer | None:
        sources: list[RetrievedChunk] = []
        for raw in payload.get("sources") or []:
            try:
                sources.append(RetrievedChunk.model_validate(raw))
            except Exception as exc:  # noqa: BLE001 - 스키마가 바뀐 옛 항목은 미스로 내린다
                self.warnings.append(f"캐시 항목을 되살리지 못해 건너뜁니다: {type(exc).__name__}")
                return None

        created_at = payload.get("created_at")
        try:
            when = datetime.fromisoformat(str(created_at))
        except (TypeError, ValueError):
            self.warnings.append("캐시 항목에 시각이 없어 건너뜁니다")
            return None

        return Answer(
            query=query,
            text=str(payload.get("answer") or ""),
            sources=sources,
            cached_from=CacheOrigin(
                query=str(payload.get("query") or ""),
                created_at=when,
                score=score,
            ),
        )
