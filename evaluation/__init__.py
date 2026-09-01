"""검색 품질 평가 하네스.

`rag_pipeline/` 밖에 두는 이유: 이것은 **파이프라인이 아니라 파이프라인을 재는 자**다.
`regression/` 이 최상위에 있는 것과 같은 자리이고, 배포되는 코드에 평가 도구가 섞이지 않는다.

무엇을 재고 무엇을 못 재는지는 `README.md` 와 `.claude/wiki/plan_log/평가-세트.md` 에 있다.
"""

from __future__ import annotations
