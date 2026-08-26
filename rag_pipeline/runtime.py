"""프로세스 전역 런타임 설정을 실제로 먹이는 자리. 지금은 torch 스레드 상한 하나다.

## 왜 별도 모듈인가

값이 **프로세스 전역**이라 `EmbeddingConfig`·`RerankConfig` 어느 쪽에도 못 매단다.
이유는 `config.py` 의 `RuntimeConfig` 에 있다 — 요약하면 OpenMP 풀이 하나뿐이라 둘로
가르는 순간 *"설정한 값과 다른 수로 도는"* 상태가 에러 없이 생긴다.

## `torch` 를 모듈 상단에서 import 하지 않는다

이 저장소의 기존 규약이다. `local_st._make_model()` 과 `local_ce._load_model()` 이 각각
**함수 안에서** import 하고, 근거가 *"모듈을 불러오는 것만으로 torch 가 올라오면
`retrieve()` 를 부르는 모든 테스트가 느려진다"* 이다. 여기서 어기면 그 방어가 무의미해진다 —
이 모듈을 import 하는 것이 곧 torch 를 올리는 것이 되기 때문이다.

## `set_num_interop_threads()` 는 **일부러** 안 부른다

빠뜨린 것이 아니다. 이 함수는 **병렬 작업이 한 번이라도 시작된 뒤** 부르면
`RuntimeError` 를 던지는데, 호출부가 그 시점을 알 방법이 없다(라이브러리 어디서든 시작될
수 있다). 그리고 발열을 만드는 것은 matmul 의 **intra-op** 쪽이다 — 실측으로 임베딩
200청크가 CPU-초 625.73 을 태우는데 그것이 intra-op 8스레드다. inter-op 는 대부분 논다.
그래서 `set_num_threads()` 하나로 충분하고, 대신 **못 잡는 것이 무엇인지**를 여기 적어 둔다.

## 멱등이다

두 번째 호출부터는 아무것도 안 한다. 부르는 자리가 둘(임베더·리랭커의 모델 적재)이고
어느 쪽이 먼저 올지 모르는데, 매번 `set_num_threads()` 를 부르면 **나중에 부른 쪽이 이기는**
바로 그 모양이 된다 — 지금은 값이 하나뿐이라 결과가 같지만, 값이 갈리는 날 조용히 깨진다.
"""

from __future__ import annotations

from rag_pipeline.config import RuntimeConfig

__all__ = ["apply_torch_threads", "reset_for_testing"]

# 이미 적용했는가. 멱등을 만드는 것이 이 플래그 하나다 (모듈 docstring 참고)
_applied = False


# torch 의 intra-op 스레드 상한을 적용한다. **돌려주는 것은 실제로 적용한 값**이고,
# 안 건드렸으면 None 이다 — 부르는 쪽이 "먹었는지"를 확인할 수 있어야 한다.
#
# **torch 가 없어도 죽지 않는다.** 이 함수는 모델 적재 직전에 불리는데, torch 미설치는
# 바로 다음 줄에서 안내와 함께 잡히는 것이 이미 정해진 동작이다(`local_st._make_model()` 은
# `EmbeddingError`, `local_ce._load_model()` 은 `None` 폴백). 여기서 먼저 터지면 그 둘이 사라진다
def apply_torch_threads(config: RuntimeConfig | None = None) -> int | None:
    global _applied
    if _applied:
        return None

    threads = (config or RuntimeConfig()).torch_num_threads
    if threads is None or threads < 1:
        _applied = True
        return None

    try:
        import torch
    except ImportError:
        return None

    torch.set_num_threads(threads)
    _applied = True
    return threads


# 테스트가 멱등 플래그를 되돌린다. **제품 코드는 부르지 않는다** —
# 프로세스 수명 동안 한 번이라는 것이 계약이라, 되돌릴 수 있는 것은 테스트뿐이다
def reset_for_testing() -> None:
    global _applied
    _applied = False
