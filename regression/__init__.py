"""회귀 기준선.

`baseline.json` 은 **승인된 기대값 하나**다. 회차가 쌓이는 `.claude/wiki/verify_log/` 와도, 실행마다
값이 바뀌는 `evaluation/` 과도 성격이 다르다 — 저쪽 둘은 "얼마가 나왔나" 이고 여기는
"얼마가 나와야 하나" 다.

재고 대조하는 것은 `measure.py`, 판정 절차는 `regression-test` 스킬에 있다.
"""

from __future__ import annotations
