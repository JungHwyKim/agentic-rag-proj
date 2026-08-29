"""`cost/` 가 **옮길 수 있는 상태**로 남아 있는지 본다.

읽는 쪽(리포트·복구·가격표)은 언젠가 플러그인의 `skills/<name>/scripts/` 로 통째로 옮긴다
(`cost/__init__.py` docstring). 그때 깨질 자리는 **import** 인데, 옮기기 전에는 그 깨짐이
안 보인다 — 저장소 안에서는 `rag_pipeline` 이 늘 import 되기 때문이다.

그래서 지금부터 검사한다. **필드 이름이 아니라 «저장소에 매였는가»를 재므로 앞으로 생길
파일까지 잡는다** — `pipeline_fingerprint()` 의 절대 경로 검사가 같은 모양이다.

쓰는 쪽(`rag_pipeline/cost_log.py` 와 이음새 열하나)은 여기 대상이 아니다. 그쪽은 유료
호출과 같은 프로세스 안에 있어야 해서 **원리적으로 못 옮긴다.**
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_COST_DIR = Path(__file__).resolve().parents[2] / "cost"

# 이 패키지들에 기대면 옮기는 순간 import 에서 죽는다
_FORBIDDEN_ROOTS = ("rag_pipeline", "evaluation", "regression", "backend", "tests")


def _modules() -> list[Path]:
    return sorted(_COST_DIR.glob("*.py"))


def test_there_is_something_to_check() -> None:
    # 폴더 이름이 바뀌었는데 검사가 조용히 0개를 훑는 것을 막는다
    assert _modules(), f"{_COST_DIR} 에 모듈이 없습니다"


@pytest.mark.parametrize("path", _modules(), ids=lambda path: path.name)
def test_no_repo_imports(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])

    leaked = imported & set(_FORBIDDEN_ROOTS)
    assert not leaked, (
        f"{path.name} 이 {sorted(leaked)} 에 기댑니다 — 이 폴더는 통째로 옮길 수 있어야 "
        "합니다 (cost/__init__.py). 필요한 값은 CLI 인자나 환경변수로 받으세요"
    )


# **저장소 배치를 값으로 박으면 옮긴 뒤 엉뚱한 곳을 본다.** 절대 경로가 payload 로 샜던
# `pipeline_fingerprint()` 사고와 같은 종류다 — 단위 테스트가 원리적으로 못 잡던 그 자리를
# 여기서는 소스로 잡는다.
#
# **글자로 찾으면 안 된다.** 처음에 `"PROJECT_ROOT" not in source` 로 썼더니 *"PROJECT_ROOT
# 를 안 쓴다"* 라고 적어 둔 **주석 자체**가 걸렸다 — 규칙을 설명한 문장이 그 규칙을 어긴
# 것으로 읽힌다. AST 로 보면 코드에 실제로 쓰인 것만 남는다
@pytest.mark.parametrize("path", _modules(), ids=lambda path: path.name)
def test_no_repo_root_constants(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))

    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert "PROJECT_ROOT" not in names, (
        f"{path.name} 이 저장소 루트 상수에 매여 있습니다 — 경로는 CLI 인자나 환경변수로 받으세요"
    )

    # `Path(__file__).parents[N]` 로 저장소 위로 거슬러 올라가는 것도 같은 매임이다.
    # `parents[0]`(자기 폴더)까지 막으면 헛경보라 1 이상만 본다
    for node in ast.walk(tree):
        if not isinstance(node, ast.Subscript) or not isinstance(node.value, ast.Attribute):
            continue
        if node.value.attr != "parents" or not isinstance(node.slice, ast.Constant):
            continue
        assert not (isinstance(node.slice.value, int) and node.slice.value >= 1), (
            f"{path.name} 이 `parents[{node.slice.value}]` 로 저장소 배치를 거슬러 올라갑니다 — "
            "옮기면 엉뚱한 폴더를 봅니다"
        )
