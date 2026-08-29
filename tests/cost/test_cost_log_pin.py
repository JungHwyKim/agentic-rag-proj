"""루트 `tests/conftest.py` 의 **autouse 핀**이 살아 있는지 본다.

`CostLogConfig` 는 기본이 **켜짐**이고 기본 경로가 `PROJECT_ROOT/data/cost.jsonl` 이다.
핀이 없으면 유료 이음새를 지나는 **모든** 테스트가 개발자의 실제 비용 로그에 줄을 쌓는다 —
남는 것이 파일이 아니라 «이번 실측이 얼마짜리였나» 의 **근거 오염**이라, 나중에 그 로그로
계산한 금액이 조용히 부풀어 있다.

## 이 테스트는 `append_cost_log` 를 모킹하면 안 된다

모킹하면 **핀이 없어도 통과한다.** `tests/api/test_warmup.py` 가 워밍업 핀에서 같은 함정을
적어 뒀다 — 검사가 지키려는 것이 «실제로 어디에 쓰이나» 인데 그 쓰기를 가짜로 바꾸면
검사가 자기 자신을 재게 된다. 그래서 여기서는 **진짜로 쓰고 그 파일이 어디 생겼는지**를 본다.

## 핀은 끄지 않고 옮긴다

`COST_LOG_ENABLED=false` 로 막으면 쓰는 코드가 테스트에서 한 번도 안 돌아, 이음새를
빠뜨려도 아무도 모른다. 그래서 핀은 **경로만** 옮기고, 아래 첫 테스트가 그 «켜져 있음»을
함께 지킨다.
"""

from __future__ import annotations

from pathlib import Path

from rag_pipeline.config import CostLogConfig
from rag_pipeline.cost_log import append_cost_log

# 저장소 루트. `PROJECT_ROOT` 를 import 하지 않고 파일 위치에서 얻는다 —
# 그 상수를 읽으면 상수가 틀렸을 때 검사도 같이 틀린다
_REPO_ROOT = Path(__file__).resolve().parents[2]


# **경로가 저장소 밖으로 옮겨져 있어야 한다.** 픽스처가 있다는 것과 먹는다는 것은 다르다
def test_the_pin_moves_the_log_out_of_the_repo() -> None:
    path = CostLogConfig().cost_log_path.resolve()
    assert _REPO_ROOT not in path.parents, (
        f"비용 로그가 저장소 안({path})을 가리킵니다 — "
        "tests/conftest.py 의 autouse 핀(cost_log_path)이 죽었습니다"
    )


# **켜진 채로 옮겨져 있어야 한다.** 끄면 이음새를 빠뜨려도 테스트가 통과한다
def test_the_pin_keeps_logging_on() -> None:
    assert CostLogConfig().cost_log_enabled is True


# 실제로 써 보고 **그 파일이 어디 생겼는지** 확인한다. 모킹하지 않는 것이 요점이다
def test_a_real_write_lands_outside_the_repo(cost_log_path: Path) -> None:
    assert append_cost_log(
        stage="generate", provider="openai", model="gpt-4.1-mini", outcome="call"
    )
    assert cost_log_path.is_file()
    assert _REPO_ROOT not in cost_log_path.resolve().parents
