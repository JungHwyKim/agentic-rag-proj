"""진 질의 해부 도구의 순수 함수들.

전 구간(`inspect_misses`)은 색인된 qdrant 컬렉션이 있어야 도는데 그것은
`tests/fixtures/` + 유료 실행에 걸린다. 여기서 보는 것은 **그 밖에서 판정이 갈리는 세 곳**이다.

- 낡은 저장소를 조용히 읽지 않는가 (`require_fresh_store`)
- 같은 정답 청크를 가리키는 질의를 하나로 묶는가 (`group_misses`)
- 제목 머리 비율을 본문에서 긁지 않는가 (`heading_ratio`)
"""

from __future__ import annotations

import pytest

from evaluation.inspect import MissGroup, group_misses, heading_ratio, require_fresh_store


# 개수가 같으면 통과한다. 이 경로가 막히면 아무것도 못 읽는다
def test_fresh_store_passes() -> None:
    require_fresh_store(414, 414, label="dense-baseline", fmt="pdf")


# **개수가 다르면 멈춘다.** 조용히 읽으면 여기서 나온 순위가 그 리포트의 순위가 아니다
@pytest.mark.parametrize("actual", [0, 413, 415, 828])
def test_stale_store_stops_with_instructions(actual: int) -> None:
    with pytest.raises(SystemExit) as caught:
        require_fresh_store(actual, 414, label="dense-baseline", fmt="pdf")

    message = str(caught.value)
    assert str(actual) in message and "414" in message
    # 멈추기만 하면 다음 사람이 무엇을 해야 할지 모른다
    assert "--dense" in message and "--format pdf" in message


def _group(chunk_id: str, rank: int | None) -> MissGroup:
    return MissGroup(
        chunk_id=chunk_id, rank=rank, score=0.5, page=1, heading_path=[], text="본문"
    )


# 같은 청크를 가리키는 질의 넷이 덩어리 하나가 된다 — 실패 하나를 네 번 세지 않기 위해서다
def test_queries_sharing_a_gold_chunk_collapse() -> None:
    rows = [(f"sch-0{i}", _group("지침.pdf:0007", 61)) for i in range(2, 6)]
    rows.append(("bas-02", _group("계획.pdf:0013", 25)))

    groups = group_misses(rows)

    assert len(groups) == 2
    shared = next(g for g in groups if g.chunk_id == "지침.pdf:0007")
    assert shared.query_ids == ["sch-02", "sch-03", "sch-04", "sch-05"]


# 순위가 얕은 것부터 읽는다. 못 찾은 것(None)은 맨 뒤 — 읽을 것이 제일 적다
def test_groups_are_ordered_by_rank_with_unknown_last() -> None:
    rows = [
        ("a", _group("c:0001", 300)),
        ("b", _group("c:0002", None)),
        ("c", _group("c:0003", 22)),
    ]
    assert [g.chunk_id for g in group_misses(rows)] == ["c:0003", "c:0001", "c:0002"]


# 머리는 `[제목 > 경로]\n` 이고 그 길이는 heading_path 로만 센다
def test_heading_ratio_counts_the_prefix_splitter_actually_writes() -> None:
    path = ["Ⅱ. 세부실행방안", "□ 업무역할별 실행사항"]
    prefix = f"[{' > '.join(path)}]\n"
    text = prefix + "가" * 100

    assert heading_ratio(text, path) == pytest.approx(len(prefix) / len(text))


# **본문에서 정규식으로 긁으면 안 된다** — 대괄호로 시작하는 본문을 머리로 오인한다
def test_body_starting_with_a_bracket_is_not_mistaken_for_a_heading() -> None:
    text = "[붙임1] 행정정보 데이터세트 관리기준표"

    assert heading_ratio(text, None) == 0.0
    assert heading_ratio(text, []) == 0.0


# 빈 청크에서 0 으로 나누지 않는다
def test_empty_text_is_zero() -> None:
    assert heading_ratio("", ["제목"]) == 0.0
