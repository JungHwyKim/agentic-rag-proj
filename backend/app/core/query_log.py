"""실사용 질의를 한 줄씩 JSONL 로 남긴다. **적중률의 분모를 만드는 것이 유일한 목적이다.**

## 왜 backend 계층인가

재려는 것이 *실사용* 반복률이다. `rag_pipeline/query.py:answer_question()` 에 붙이면
`evaluation/run_eval.py` 가 한 실행에 60~105건을 같은 파일에 쌓아 **분모가 오염된다** —
그 질의들은 사람이 두 번 묻지 않으므로 반복률을 구조적으로 0 쪽으로 끌어내린다.
미들웨어는 반대쪽 문제가 있다: `Answer` 를 못 봐 **캐시 히트 여부를 못 실어 분자가 사라진다.**

덤으로 진입점 셋이 HTTP 를 모르는 경계가 그대로 산다. 원본 보관이 backend 만의 일인 것과
같은 잣대다(`Settings.originals_dir` 주석).

## 정규화 키를 왜 캐시 것으로 쓰나

`cache_point_id_for()` 를 그대로 부른다. 여기가 이 모듈에서 제일 중요한 한 줄이다 —
**캐시를 안 켜도 반복률이 계산되고**, 나중에 켰을 때 로그의 `key` 와 캐시 point id 가
같은 자리를 가리킨다. 별도 정규화를 만들면 *로그가 재는 반복*과 *캐시가 실제로 잡는 반복*이
갈려, 재서 켠 판단이 켠 뒤에 틀어진다.

## 무엇이 안 남나 — 분모의 뜻

**성공한 질의만 남는다.** 예외로 끝난 질의(`GenerationError` → 503)는 한 줄도 안 만든다.
분모의 뜻을 *"답이 나온 질의"* 하나로 고정하기 위해서다 — 섞으면 같은 분모가 두 뜻을 갖고,
그때는 적중률이 무엇에 대한 비율인지 로그만 봐서는 알 수 없다. 빈 질문도 라우트가 먼저
돌려보내므로 안 남는다.

## 실패는 삼킨다

쓰기가 실패해도 질의는 그대로 답한다. 로그 때문에 질의가 죽으면 관측이 신뢰성을 깎는다 —
`ResultCache.set()` 과 `QdrantAnswerCache` 가 이미 같은 규약이고, 이쪽은 **최적화도 아니고
관측**이라 더 그렇다. 삼키는 것은 `OSError` 뿐이다: 그 밖의 예외(직렬화 불가 등)는 이 모듈의
버그라 조용히 넘기면 로그가 영원히 안 쌓이는 것을 아무도 모른다.

## 잠금과 상한

`ask()` 가 `run_in_threadpool` 을 쓰므로 여러 스레드가 동시에 붙는다. 모듈 레벨 `Lock` 으로
한 줄 쓰기를 직렬화한다. **프로세스가 여럿인 경우는 안 막는다** — 지금 실행 형태가
`uvicorn --reload` 단일 프로세스다(CLAUDE.md 의 명령어 절). 멀티워커로 가면 여기부터 본다.

**상한도 자동 정리도 없다.** `Settings.originals_dir` 이 같은 판단을 이미 했다 —
지금 값을 정하면 관측 없는 숫자가 된다. 대신 경로와 on/off 를 밖으로 연다.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path

from rag_pipeline.answer_cache import cache_point_id_for
from rag_pipeline.schemas.answer import Answer

__all__ = ["append_query_log"]

# 한 줄 쓰기를 직렬화한다. 프로세스가 하나라는 전제 위에 있다 (모듈 docstring 참고)
_LOCK = threading.Lock()


# 질의 하나를 로그에 한 줄 붙인다. **실패해도 아무것도 안 던진다** (모듈 docstring 참고).
#
# 라우트에 인라인하지 않은 이유는 테스트 이음새를 남기기 위해서다 — 외부 API 핸들러의
# `_make_client()` 가 모듈 레벨 함수인 것과 같은 잣대다.
def append_query_log(path: Path, query: str, answer: Answer, *, elapsed_ms: int) -> bool:
    record = {
        "at": datetime.now(UTC).isoformat(),
        "query": query,
        # 캐시와 **같은 파생**이다. 다른 정규화를 만들면 두 반복률이 갈린다
        "key": cache_point_id_for(query),
        # 화면 배지의 판정 근거와 같은 하나다 (`Answer.cached_from`)
        "cached": answer.cached_from is not None,
        "elapsed_ms": elapsed_ms,
        "sources": len(answer.sources),
    }
    line = json.dumps(record, ensure_ascii=False) + "\n"

    try:
        with _LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line)
    except OSError:
        return False
    return True
