"""torch 스레드 상한이 실제로 먹는가. **torch 를 올리지 않고 잰다.**

이 파일이 `sentence_transformers` 나 실제 `torch` 를 부르면 기본 pytest 가 느려지고,
가중치를 내려받는 경로가 열린다 — `local_st.py` 가 경고하는 바로 그 자리다. 그래서
가짜 torch 모듈을 `sys.modules` 에 꽂아 **호출됐는가**만 본다.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from rag_pipeline import runtime
from rag_pipeline.config import RuntimeConfig


# 각 테스트가 깨끗한 멱등 플래그에서 시작한다. 안 되돌리면 첫 테스트가 나머지를 통과시킨다
@pytest.fixture(autouse=True)
def _fresh():
    runtime.reset_for_testing()
    yield
    runtime.reset_for_testing()


# set_num_threads 호출을 기록하는 가짜 torch. 실제 torch 를 안 올리는 것이 요점이다
@pytest.fixture
def fake_torch(monkeypatch):
    calls: list[int] = []
    module = SimpleNamespace(set_num_threads=calls.append)
    monkeypatch.setitem(sys.modules, "torch", module)
    return calls


# 값을 주면 그 수로 torch 에 먹인다
def test_applies_configured_thread_count(fake_torch) -> None:
    applied = runtime.apply_torch_threads(RuntimeConfig(torch_num_threads=4))

    assert applied == 4
    assert fake_torch == [4]


# **None 이면 torch 를 아예 안 건드린다.** 기본값을 안 낮추기로 한 결정이 여기 걸려 있다 —
# 건드리면 verify_log 의 과거 벽시계가 조용히 비교 불가가 된다
def test_none_leaves_torch_alone(fake_torch) -> None:
    applied = runtime.apply_torch_threads(RuntimeConfig(torch_num_threads=None))

    assert applied is None
    assert fake_torch == []


# 0 이하는 값이 아니라 실수다. torch 에 넘기면 거기서 터지므로 여기서 무시한다
@pytest.mark.parametrize("threads", [0, -1])
def test_nonpositive_is_ignored(fake_torch, threads: int) -> None:
    assert runtime.apply_torch_threads(RuntimeConfig(torch_num_threads=threads)) is None
    assert fake_torch == []


# **두 번째 호출부터는 아무것도 안 한다.** 부르는 자리가 둘(임베더·리랭커)이라
# 멱등이 아니면 "나중에 부른 쪽이 이긴다"가 그대로 재현된다
def test_is_idempotent(fake_torch) -> None:
    config = RuntimeConfig(torch_num_threads=2)

    assert runtime.apply_torch_threads(config) == 2
    assert runtime.apply_torch_threads(config) is None
    assert runtime.apply_torch_threads(RuntimeConfig(torch_num_threads=6)) is None
    assert fake_torch == [2]


# torch 가 없는 환경에서 **죽지 않는다.** 여기서 터지면 `_make_model()` 의 안내 메시지와
# `_load_model()` 의 None 폴백이 둘 다 사라진다
def test_missing_torch_does_not_raise(monkeypatch) -> None:
    import builtins

    real_import = builtins.__import__

    def _no_torch(name, *args, **kwargs):
        if name == "torch":
            raise ImportError("no torch")
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "torch", raising=False)
    monkeypatch.setattr(builtins, "__import__", _no_torch)

    assert runtime.apply_torch_threads(RuntimeConfig(torch_num_threads=4)) is None


# 인자를 안 주면 .env 를 읽는다. 환경변수가 그 경로를 타는지 본다
def test_reads_env_when_no_config_given(fake_torch, monkeypatch) -> None:
    monkeypatch.setenv("TORCH_NUM_THREADS", "3")

    assert runtime.apply_torch_threads() == 3
    assert fake_torch == [3]
