"""비용을 재는 자[尺]. `evaluation/`(품질) · `regression/`(회귀)와 나란한 자리다.

`rag_pipeline` 이 아니라 **그것이 쓴 돈을 읽는** 쪽이라 최상위에 산다.

## 이 폴더의 제약 — 옮길 수 있게 둔다

**`rag_pipeline` 도 `evaluation` 도 import 하지 않는다.** 아는 것은 `data/cost.jsonl` 의
줄 모양과 `.cache/` 의 디렉터리 구조뿐이고, 경로는 전부 CLI 인자·환경변수로 받는다.
`PROJECT_ROOT` 같은 저장소 상수도 안 쓴다.

이유는 이 셋을 나중에 **플러그인으로 통째로 옮기기 위해서**다(`skills/<name>/scripts/`).
지금 옮기지 않는 것은 *안 돌려본 것을 패키징하면 남의 머신에서 처음 깨지기* 때문이고,
그때 깨질 자리가 바로 import 다 — **옮기는 날에야 안다.** 그래서
`tests/cost/test_portable.py` 가 지금부터 그 경계를 검사한다.

쓰는 쪽(`rag_pipeline/cost_log.py` 와 이음새 열하나)은 반대로 **옮길 수 없다.** 유료 호출과
같은 프로세스 안에 있어야 하기 때문이다.
"""

from __future__ import annotations

import sys

__all__ = ["use_utf8_stdout"]


# 표를 찍기 전에 부른다. **Windows 기본 stdout 이 cp949 라 `—`·`·` 하나에 도구가 죽는다** —
# 실제로 첫 실행이 `UnicodeEncodeError` 로 표 중간에서 멈췄고, 그때 이미 찍힌 줄은 한글이
# 통째로 깨져 있었다. CLAUDE.md 가 적어 둔 인코딩 함정의 **세 번째 얼굴(출력)** 이 이것이다.
#
# `PYTHONIOENCODING=utf-8` 을 부르는 쪽에 요구하지 않는 것이 결정이다 — 그 요구는 잊히고,
# 잊히면 *비용을 보려고 돌린 명령이 비용을 못 보여준다.* 도구가 자기 출력을 책임진다.
#
# `errors="replace"` 인 것은 어떤 터미널에서도 **죽지 않는 쪽**을 고른 것이다: 여기서 나오는
# 것은 사람이 읽는 표라, 글자 하나가 `?` 로 보이는 것보다 표가 통째로 안 나오는 것이 나쁘다
def use_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
