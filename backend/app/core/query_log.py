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

## 판정(verdict)은 왜 여기 있나

`cached` 와 같은 급이다 — `Answer` 를 봐야만 알 수 있고, 재려는 것이 *실사용* 분포다.
`evaluation/qa_set_human.jsonl` 43건은 **고정된 세트**라 실사용에서 *불충분* 이 얼마나
나오는지를 원리적으로 못 잰다. 그 비율이 곧 재시도(Rewriter)를 되살릴지 정하는 근거다.

**키를 늘 쓴다.** 판정을 안 돌렸으면 `null` 이지 키를 빼지 않는다 — 빼면 *"판정이 꺼져
있었다"* 와 *"옛 형식이라 칸이 없다"* 가 집계에서 안 갈린다.

`reasoning` 은 **안 싣는다.** 상한도 자동 정리도 없는 파일에 모델 문장이 질의마다 쌓이고,
사유를 읽는 도구는 `evaluation/verdicts.py --wrong` 이 이미 있다.

## 재작성(rewrite)은 왜 **전문**을 싣나 — `reasoning` 은 뺐는데

재려는 것이 다르다. `verdict.reasoning` 은 **판정을 읽는 도구가 이미 있어서**
(`evaluation/verdicts.py --wrong`) 여기 쌓을 이유가 없었다. 재작성은 그런 도구가 없고,
무엇보다 **여기가 유일한 기록이다** — `.cache/rewrite` 는 내용 해시가 키라 *어떤 질의가
무엇으로 바뀌었나* 를 되짚을 수 없고, 재작성 캐시는 원 질의를 아예 안 담는다.

길이 걱정도 축이 다르다. `reasoning` 은 모델이 마음대로 쓰는 산문이고, 재작성 질의는
**질문 한 문장**에 `rewrite_max_tokens`(256) 상한이 걸려 있다 — 바로 위 `query` 칸이 이미
같은 급의 문자열을 싣고 있다.

**`rewrite_mode` 를 함께 싣는다.** 없으면 `fuse` 였는지 `replace` 였는지를 로그만 봐서 못
가르는데, 그 둘은 **원 질의가 함께 돌았는지**가 달라 같은 줄이 두 뜻을 갖는다. `.env` 한 줄로
바뀌는 값이라 실행 사이에 조용히 갈린다.

**두 칸 다 키를 늘 쓴다** — `verdict` 와 같은 규칙이고 이유도 같다.

## 출처(`source`)는 왜 헤더에서 오나 — 그리고 왜 화이트리스트가 없나

**이 로그의 갈래가 넷인데 셋이 오염이다**(2026-08-28 실측, 53줄) — uuid 꼬리 프로브 28 ·
cp949 깨짐 4 · 서버 E2E 4 이고 **실사용은 17줄뿐**이다. 오염의 방향이 서로 반대라
(앞 둘은 서로 다른 문자열이라 반복률을 아래로, E2E 는 같은 질의를 되풀이해 위로) **합계
하나만 보면 못 읽고, 한 갈래만 빼면 오히려 더 틀린다.**

구멍은 계층에 있다. `run_eval` 은 이 로그가 backend 에 있다는 것이, `tests/api` 는 autouse
픽스처가 막는데 **사람이 HTTP 로 넣는 측정 트래픽은 실사용과 같은 문으로 들어와 아무것도
안 막는다.** 그래서 요청이 스스로 밝히게 한다.

**헤더인 이유는 미들웨어를 기각한 이유의 거울상이다** — 저쪽은 `Answer` 를 못 봐 분자가
사라지지만, 라우트는 `Request` 를 이미 들고 있어 새 축이 안 생긴다.

**합법값을 화이트리스트로 좁히지 않는다.** 모르는 값을 거부해 `user` 로 떨어뜨리면 오타 하나가
측정 트래픽을 **실사용으로 세게** 만드는데, 그것이 이 칸이 막으려던 바로 그 일이다. 값이
무엇이든 *"실사용이 아니다"* 는 확실하고, 새 측정 갈래가 생길 때마다 코드를 고칠 일도 없다.
대신 길이는 자른다 — 헤더는 외부 입력이라 상한이 없으면 임의 길이 문자열이 줄마다 박힌다.

**기본값이 `user` 인 것은 안전한 쪽이 아니라 정직한 쪽이다.** 헤더를 안 붙인 측정은 다시
실사용으로 세어진다 — 이 칸은 그 사고를 **막는** 것이 아니라 갈래를 **데이터에 남기는**
것이다. 옛 줄에는 키가 아예 없어 *"옛 형식"* 과 *"이번 형식의 실사용"* 이 집계에서 갈린다
(`verdict` 가 `null` 로 갈리는 것과 같은 모양).

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

__all__ = ["QUERY_SOURCE_HEADER", "append_query_log", "normalize_source"]

# 한 줄 쓰기를 직렬화한다. 프로세스가 하나라는 전제 위에 있다 (모듈 docstring 참고)
_LOCK = threading.Lock()

# 요청이 자기 출처를 밝히는 헤더. **이름이 여기 사는 이유는 뜻이 여기 있어서다** —
# 라우트가 자기 문자열을 따로 들면 두 철자가 생기고, 어긋나도 아무 에러가 안 난다
QUERY_SOURCE_HEADER = "X-Query-Source"

# 출처 문자열의 길이 상한. **고른 값이 아니라 상한일 뿐이다** (모듈 docstring 참고)
_MAX_SOURCE_CHARS = 32

# 안 밝힌 요청의 출처. 화면에서 온 질의가 이것이다 (모듈 docstring 참고)
_DEFAULT_SOURCE = "user"


# 출처 문자열을 다듬는다. **거르지 않고 자르기만 한다** — 모르는 값을 실사용으로 떨어뜨리면
# 오타 하나가 측정 트래픽을 실사용으로 세게 만든다 (모듈 docstring 참고)
def normalize_source(raw: str | None) -> str:
    text = (raw or "").strip()
    return text[:_MAX_SOURCE_CHARS] if text else _DEFAULT_SOURCE


# 질의 하나를 로그에 한 줄 붙인다. **실패해도 아무것도 안 던진다** (모듈 docstring 참고).
#
# 라우트에 인라인하지 않은 이유는 테스트 이음새를 남기기 위해서다 — 외부 API 핸들러의
# `_make_client()` 가 모듈 레벨 함수인 것과 같은 잣대다.
def append_query_log(
    path: Path, query: str, answer: Answer, *, elapsed_ms: int, source: str | None
) -> bool:
    record = {
        "at": datetime.now(UTC).isoformat(),
        "query": query,
        # 캐시와 **같은 파생**이다. 다른 정규화를 만들면 두 반복률이 갈린다
        "key": cache_point_id_for(query),
        # 누가 넣은 질의인가. 안 밝히면 `user` 다 — **키를 늘 쓴다** (모듈 docstring 참고).
        # `source=` 에 기본값을 안 둔 것은 새 호출부가 빠뜨렸을 때 조용히 실사용으로 세는
        # 대신 `TypeError` 로 터지게 하기 위해서다
        "source": normalize_source(source),
        # 화면 배지의 판정 근거와 같은 하나다 (`Answer.cached_from`)
        "cached": answer.cached_from is not None,
        "elapsed_ms": elapsed_ms,
        "sources": len(answer.sources),
        # 판정을 돌렸으면 세 상태 중 하나, 안 돌렸으면 None (꺼짐 또는 캐시 히트).
        # **키를 늘 쓴다** (모듈 docstring 참고)
        "verdict": answer.verdict.verdict if answer.verdict is not None else None,
        # 무엇으로 검색했나. 재작성이 안 걸렸으면(꺼짐·폴백·캐시 히트) 둘 다 None 이다.
        # **전문을 싣는 이유와 mode 를 함께 싣는 이유는 모듈 docstring 에 있다**
        "rewritten": answer.rewrite.query if answer.rewrite is not None else None,
        "rewrite_mode": answer.rewrite.mode if answer.rewrite is not None else None,
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
