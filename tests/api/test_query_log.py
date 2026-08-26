"""실사용 질의 로그. **적중률의 분모가 무엇인지를 여기서 못 박는다.**

라우트 테스트(`test_query_route.py`)와 갈라 두는 이유는 재는 것이 응답이 아니라 **부수 효과**
여서다. 저쪽은 HTML 조각을 보고 이쪽은 파일 한 줄을 본다.

경로는 `conftest.py` 의 autouse 픽스처가 `tmp_path` 로 돌린다 — 안 그러면 저장소의
`data/queries.jsonl` 에 테스트 질의가 쌓여, 나중에 캐시를 켤지 정하는 근거가 오염된다.
**실제로 한 번 샜다**(2026-08-26, 15줄 전부 `q`·`질문`·`또 질문`): 라우트를 배선한 순간
자동 테스트 훅이 `tests/api` 를 돌렸는데 그때 이 픽스처가 아직 없었다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import create_app
from rag_pipeline.answer_cache import cache_point_id_for
from rag_pipeline.config import EvaluatorConfig, GenerationConfig, IngestConfig
from rag_pipeline.exceptions import GenerationError
from rag_pipeline.schemas.answer import Answer, CacheOrigin, EvaluatorVerdict
from rag_pipeline.schemas.retrieval import RetrievedChunk


# 저장소는 안 쓰지만 첫 질의가 BM25 를 지으며 iter_chunks() 를 부른다 (test_query_route.py 와 같다)
class _Store:
    def iter_chunks(self):  # noqa: ANN201 - Iterator[StoredChunk]. 늘 비어 있다
        return iter(())

    def close(self) -> None:
        pass


# 앱을 만들고 가짜 상태를 꽂는다. lifespan 은 일부러 안 돌린다
def _client() -> TestClient:
    app = create_app()
    config = IngestConfig()
    object.__setattr__(config, "_generation", GenerationConfig(openai_api_key=None))
    # **판정도 못 박는다** — 라우트가 질의마다 `get_evaluator()` 를 부르므로, 안 박으면
    # 개발자 `.env` 의 `EVALUATOR_MODEL` 이 픽스처에 실린다 (`_generation` 핀과 같은 이유)
    object.__setattr__(
        config, "_evaluator", EvaluatorConfig(evaluator_model=None, openai_api_key=None)
    )
    app.state.ingest_config = config
    app.state.store = _Store()
    app.state.embedder = object()
    return TestClient(app)


def _chunk() -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id="계약서_v2.pdf:0007",
        document_id="계약서_v2.pdf",
        text="제12조 …",
        metadata={"source_name": "계약서_v2.pdf", "page": 4},
        dense_score=0.812,
    )


def _lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


# 질의 하나에 한 줄. **필드 일곱이 곧 계약이다** — 넷은 분자·분모를 만들고(`key`·`cached`),
# 둘은 켰을 때 얼마를 아끼는지를 만들고(`elapsed_ms`·`sources`), `verdict` 는 실사용 판정
# 분포를 만든다. **`verdict` 키는 판정이 꺼져 있어도 있다** — 없을 때 키를 빼면
# "판정이 꺼져 있었다"와 "옛 형식이라 칸이 없다"가 집계에서 안 갈린다
def test_one_query_writes_one_line(mocker, query_log_path: Path):
    mocker.patch(
        "backend.app.api.routes.query.answer_question",
        return_value=Answer(query="위약금은?", text="20% 입니다", sources=[_chunk()]),
    )

    assert _client().post("/api/v1/query", data={"query": "위약금은?"}).status_code == 200

    (row,) = _lines(query_log_path)
    assert set(row) == {"at", "query", "key", "cached", "elapsed_ms", "sources", "verdict"}
    # 판정이 꺼진 상태다. 칸은 있고 값이 None 이다
    assert row["verdict"] is None
    assert row["query"] == "위약금은?"
    assert row["cached"] is False
    assert row["sources"] == 1
    assert isinstance(row["elapsed_ms"], int)
    datetime.fromisoformat(row["at"])


# **`key` 가 캐시와 같은 파생이라는 것이 이 로그의 존재 이유다.** 다른 정규화를 쓰면
# 로그가 재는 반복률과 캐시가 실제로 잡는 반복이 갈려, 재서 켠 판단이 켠 뒤에 틀어진다
def test_key_is_the_cache_point_id(mocker, query_log_path: Path):
    mocker.patch(
        "backend.app.api.routes.query.answer_question",
        return_value=Answer(query="위약금은?", text="답", sources=[]),
    )

    _client().post("/api/v1/query", data={"query": "위약금은?"})

    assert _lines(query_log_path)[0]["key"] == cache_point_id_for("위약금은?")


# 표기가 달라도 같은 질문은 같은 `key` 다. **반복률이 성립하는 근거** — 사용자가 같은 질문을
# 두 번 칠 때 띄어쓰기까지 같으리라 기대할 수 없다
def test_same_question_written_differently_shares_the_key(mocker, query_log_path: Path):
    mocker.patch(
        "backend.app.api.routes.query.answer_question",
        return_value=Answer(query="q", text="답", sources=[]),
    )
    client = _client()

    client.post("/api/v1/query", data={"query": "유예기간이 언제까지인가요?"})
    client.post("/api/v1/query", data={"query": "  유예기간이   언제까지인가요?  "})

    first, second = _lines(query_log_path)
    assert first["key"] == second["key"]
    # 원문은 그대로 남는다 — 무엇이 반복되는지를 사람이 읽어야 해석이 된다
    assert first["query"] != second["query"]


# 캐시에서 온 답이면 `cached` 가 참이다. **적중률의 분자**이고, 판정 근거는 화면 배지와
# 같은 하나(`Answer.cached_from`)다
def test_cache_hit_is_recorded(mocker, query_log_path: Path):
    mocker.patch(
        "backend.app.api.routes.query.answer_question",
        return_value=Answer(
            query="유예기간 언제까지죠?",
            text="6개월입니다.",
            sources=[_chunk()],
            cached_from=CacheOrigin(
                query="유예기간이 언제까지인가요?",
                created_at=datetime.now(UTC) - timedelta(days=3),
                score=0.964,
            ),
        ),
    )

    _client().post("/api/v1/query", data={"query": "유예기간 언제까지죠?"})

    assert _lines(query_log_path)[0]["cached"] is True


# 판정을 돌렸으면 그 세 상태가 그대로 실린다. **재려는 것이 실사용 분포**라 세트 43건으로는
# 원리적으로 못 잰다 — 그 비율이 재시도(Rewriter)를 되살릴지 정하는 근거다
def test_verdict_is_recorded(mocker, query_log_path: Path):
    mocker.patch(
        "backend.app.api.routes.query.answer_question",
        return_value=Answer(
            query="q",
            text="답",
            sources=[_chunk()],
            verdict=EvaluatorVerdict(verdict="insufficient", reasoning="빈 서식이다"),
        ),
    )

    assert _client().post("/api/v1/query", data={"query": "q"}).status_code == 200

    (row,) = _lines(query_log_path)
    assert row["verdict"] == "insufficient"


# **사유는 안 싣는다.** 상한도 자동 정리도 없는 파일에 모델 문장이 질의마다 쌓인다 —
# 사유를 읽는 도구는 `evaluation/verdicts.py --wrong` 이 이미 있다
def test_reasoning_is_not_recorded(mocker, query_log_path: Path):
    mocker.patch(
        "backend.app.api.routes.query.answer_question",
        return_value=Answer(
            query="q",
            text="답",
            sources=[],
            verdict=EvaluatorVerdict(verdict="failed", reasoning="판정을 얻지 못했습니다"),
        ),
    )

    assert _client().post("/api/v1/query", data={"query": "q"}).status_code == 200

    (row,) = _lines(query_log_path)
    assert row["verdict"] == "failed"
    assert "판정을 얻지 못했습니다" not in json.dumps(row, ensure_ascii=False)


# **분모의 뜻은 "답이 나온 질의" 하나다.** 예외로 끝난 질의를 섞으면 같은 분모가 두 뜻을 갖고,
# 그때는 적중률이 무엇에 대한 비율인지 로그만 봐서는 알 수 없다
def test_failed_query_leaves_no_line(mocker, query_log_path: Path):
    mocker.patch(
        "backend.app.api.routes.query.answer_question",
        side_effect=GenerationError("모델이 응답하지 않았습니다"),
    )

    assert _client().post("/api/v1/query", data={"query": "질문"}).status_code == 503
    assert _lines(query_log_path) == []


# 빈 질문은 라우트가 먼저 돌려보내므로 한 줄도 안 만든다
def test_blank_query_leaves_no_line(query_log_path: Path):
    assert _client().post("/api/v1/query", data={"query": "   "}).status_code == 200
    assert _lines(query_log_path) == []


# **쓰기가 실패해도 질의는 그대로 답한다.** 로그 때문에 질의가 죽으면 관측이 신뢰성을 깎는다 —
# 부모 자리에 파일이 있어 mkdir 이 OSError 로 죽는 상황을 만든다
def test_write_failure_does_not_break_the_query(mocker, monkeypatch, tmp_path: Path):
    blocker = tmp_path / "blocked"
    blocker.write_text("나는 디렉터리가 아니다", encoding="utf-8")
    monkeypatch.setenv("QUERY_LOG_PATH", str(blocker / "queries.jsonl"))
    mocker.patch(
        "backend.app.api.routes.query.answer_question",
        return_value=Answer(query="q", text="답", sources=[]),
    )

    response = _client().post("/api/v1/query", data={"query": "q"})

    assert response.status_code == 200
    assert "답" in response.text


# 꺼면 파일 자체가 안 생긴다. **파일을 만들어 두고 안 쓰는 것과 다르다** — 안 만드는 쪽이라야
# "이 머신은 로그를 안 남긴다"가 파일 유무로 보인다
def test_disabled_writes_nothing(mocker, monkeypatch, query_log_path: Path):
    monkeypatch.setenv("QUERY_LOG_ENABLED", "false")
    mocker.patch(
        "backend.app.api.routes.query.answer_question",
        return_value=Answer(query="q", text="답", sources=[]),
    )

    assert _client().post("/api/v1/query", data={"query": "q"}).status_code == 200
    assert not query_log_path.exists()
