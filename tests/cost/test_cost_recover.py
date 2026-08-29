"""과거 복구가 **못 가르는 것을 못 가른다고 말하는지** 본다.

이 도구에서 제일 쉽게 틀리는 자리는 `rerank` 다. 0으로 두면 안 쓴 것처럼 보이고, 전부
유료로 세면 로컬 실행이 비용으로 둔갑한다 — **두 방향 다 틀리는데 에러가 안 난다.**
그래서 「못 가름」이 값이어야 하고, 이 파일이 그것을 잠근다.
"""

from __future__ import annotations

import json
from pathlib import Path

from cost.recover import main, scan


def _cache(tmp_path: Path, layout: dict[str, list[dict | str]]) -> Path:
    root = tmp_path / ".cache"
    for name, entries in layout.items():
        folder = root / name
        folder.mkdir(parents=True, exist_ok=True)
        for index, entry in enumerate(entries):
            body = entry if isinstance(entry, str) else json.dumps(entry)
            (folder / f"{index:04d}.json").write_text(body, encoding="utf-8")
    return root


def _by_name(root: Path, **kwargs) -> dict:
    return {verdict.name: verdict for verdict in scan(root, **kwargs)}


def test_all_paid_folders_count_every_file(tmp_path: Path) -> None:
    root = _cache(tmp_path, {"evaluator": [{}, {}, {}], "rewrite": [{}], "answer_judge": [{}, {}]})
    verdicts = _by_name(root)

    assert verdicts["evaluator"].paid == 3
    assert verdicts["rewrite"].paid == 1
    assert verdicts["answer_judge"].paid == 2


# **벡터 길이가 가르는 축이다.** OpenAI 차원만 유료이고 로컬(KURE 1024)은 무료다
def test_embedding_is_split_by_vector_length(tmp_path: Path) -> None:
    root = _cache(
        tmp_path,
        {
            "embedding": [
                {"vector": [0.0] * 1536},
                {"vector": [0.0] * 3072},
                {"vector": [0.0] * 1024},
                {"vector": [0.0] * 1024},
            ]
        },
    )
    embedding = _by_name(root)["embedding"]
    assert (embedding.paid, embedding.free, embedding.unknown) == (2, 2, 0)


# 못 읽는 파일을 **무료로 단정하지 않는다** — 모르는 것은 「못 가름」이다
def test_unreadable_embedding_entries_are_unknown(tmp_path: Path) -> None:
    root = _cache(tmp_path, {"embedding": ["json 이 아니다", {"vector": "리스트가 아니다"}]})
    embedding = _by_name(root)["embedding"]
    assert (embedding.paid, embedding.free, embedding.unknown) == (0, 0, 2)


# **여기가 이 파일의 요점이다.** rerank 는 셋 다 한 폴더를 써서 못 가른다
def test_rerank_is_unknown_never_paid_or_free(tmp_path: Path) -> None:
    root = _cache(tmp_path, {"rerank": [{"scores": [0.5]}, {"scores": [0.9]}, {"scores": [2.3]}]})
    rerank = _by_name(root)["rerank"]
    assert (rerank.paid, rerank.free, rerank.unknown) == (0, 0, 3)


# 캐시가 없는 갈래는 **0이 아니라 「캐시 없음」이다** — 0으로 찍으면 «안 썼다» 로 읽힌다
def test_cacheless_stages_are_reported_as_such(tmp_path: Path) -> None:
    verdicts = _by_name(_cache(tmp_path, {}))
    for name in ("generate", "web_search"):
        assert verdicts[name].paid == 0
        assert "캐시 없음" in verdicts[name].note


def test_missing_cache_dir_is_not_an_error(tmp_path: Path) -> None:
    verdicts = _by_name(tmp_path / "없는폴더")
    assert all(verdict.paid == 0 for verdict in verdicts.values())


# **토큰·금액은 키를 늘 쓰되 값이 `null` 이다** — 빼면 «못 되살린다» 와 «0이었다» 가 섞인다
def test_json_output_keeps_null_token_and_cost_keys(tmp_path: Path, capsys) -> None:
    root = _cache(tmp_path, {"evaluator": [{}]})
    assert main(["--cache-dir", str(root), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    for entry in payload:
        assert entry["tokens"] is None
        assert entry["usd"] is None


def test_table_says_what_it_cannot_recover(tmp_path: Path, capsys) -> None:
    root = _cache(tmp_path, {"rerank": [{}], "evaluator": [{}]})
    main(["--cache-dir", str(root)])

    out = capsys.readouterr().out
    assert "못 가름" in out
    assert "토큰과 금액은 되살릴 수 없다" in out
