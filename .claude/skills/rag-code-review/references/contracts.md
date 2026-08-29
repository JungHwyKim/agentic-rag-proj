# 전 단계 공통 계약

어느 단계를 리뷰하든 적용되는 규약이다. 단계 고유 지식은 영역별 reference 에 있다.

여기 적힌 내용과 실제 코드가 다르면 **실제 코드가 맞다.** 그때는 코드를 지적하지 말고
"reference 와 구현이 어긋난다"를 리포트에 따로 적는다.

## 1. 파이프라인 형태

`rag_pipeline/ingestion/pipeline.py:ingest()` 가 유일한 진입점이고 **HTTP 를 모른다.**
스크립트·노트북에서 그대로 돌아야 하므로 어떤 단계도 FastAPI / Request / HTTPException 을
알면 안 된다.

모든 출력은 `ParsedDocument(metadata, elements)` 로 수렴한다
(`rag_pipeline/schemas/document.py`). 단계는 이 타입을 주고받으며 순차 합성된다.

## 2. 외부 API 규약 — 예외를 던지지 않는다

외부 API 를 부르는 코드는 **실패해도 예외를 밖으로 내보내지 않는다.** 빈 결과를 돌려주고
`ctx.warn()` 으로 알린다. 그래야 상위 폴백이 돈다.

참고 구현은 `loader/content/table_llamaparse.py` 다 — `except Exception:  # noqa: BLE001`
로 삼키고, `ctx.warn()` 으로 알린 뒤 `[]` 를 돌려준다.

**계약의 경계는 `handle()` 이지 API 호출 한 줄이 아니다.** `try` 로 호출만 감싸고 **응답을
해석하는 구간을 밖에 두면 규약이 깨진다** — 실제로 그 상태였다(2026-08-18). 호출은 성공하고
`result.markdown` 을 문자열로 알고 `.strip()` 을 부른 곳에서 `AttributeError` 가 나
`handle()` 밖으로 새었고, `table_native` 폴백이 발동할 기회 자체가 없어 `ingest()` 전체가
멈췄다. 응답에서 값을 꺼내는 코드는 **SDK 가 바뀌면 틀리는 코드**라 호출만큼 위험하다.

- 예외가 밖으로 나간다 → **Critical.** 키 없는 환경에서 적재 전체가 죽고 기본 테스트가 깨진다
- 응답 해석부(`getattr`·인덱싱·형변환)가 `try` 밖에 있다 → **Critical.** 같은 결과가 된다
- 실패 사유를 한 문구로 뭉갠다 → "호출 실패"와 "응답 해석 실패"는 대응이 다르다. 앞은
  장애·키·네트워크이고 뒤는 **SDK 변경이라 코드를 고쳐야 한다**
- `except Exception` 에 `# noqa: BLE001` 과 이유 주석이 없다
- 실패했는데 `ctx.warn()` 이 없다 → 사용자는 결과가 왜 엉성한지 알 길이 없다
- 폴백으로 내려가면서 이미 계산한 결과를 버리고 다시 계산한다

## 3. 비용 통제 4종

비용 가시성은 **경로가 둘이다**(2026-08-29 에 둘째가 생겼다). 하나는 적재 쪽 —
`LoadContext` 의 카운터가 `DocumentMetadata` 로 노출된다. 다른 하나는 **모든 유료 단계에
걸치는** `rag_pipeline/cost_log.py` 이고, 그쪽이 (d)다. 앞엣것은 *이 문서 하나가 얼마를
썼나* 를, 뒤엣것은 *이 실행이 얼마를 썼나* 를 답한다 — 겹치지만 분모가 다르다.

**(a) 카운터는 호출이 나간 뒤에 올린다** — `table_llamaparse.py` 의 `_parse_blob()`,
`table_vision.py` 와 `image_vision.py` 의 `_call_vision()`

**이 절이 지목하던 예시가 실제로는 규약을 어기고 있었다**(2026-08-22 감사에서 발견). 오래
`image_vision.py:_call_vision()` 을 (a)의 모범으로 이름까지 대며 인용했는데, 그 함수는 카운터를
**해석 뒤**에 올리고 해석부를 `try` 밖에 두고 있었다 — `choices=[]` 하나로 `IndexError` 가
`handle()` 밖까지 새고 과금 1인데 장부가 0이 됐다(실행으로 재현). 2026-08-18 에
`table_llamaparse` 를 고친 수정이 형제 파일로 안 퍼진 것이다. 지금은 셋 다 맞다.

**교훈은 예시를 바꾸는 것이 아니다** — 외부 API 핸들러가 여럿이면 **리뷰 때 나란히 대조한다.**
reference 가 예시로 든 함수라고 검사에서 빼지 않는다. 문서가 코드보다 늦게 낡는다.

가르는 기준은 **과금이 됐는가**이지 우리가 그 응답을 잘 썼는가가 아니다. 그래서
`_parse_blob()` 은 `parse()` 가 돌아온 직후, **응답을 해석하기 전에** 올린다. 해석이 실패해도
그 호출은 이미 과금됐다 — 뒤로 미루면 이번처럼 해석이 전부 실패할 때 **돈은 나갔는데 장부가
0** 이 된다(2026-08-18 실측). 되돌리지 말 것.

- 실제로 호출했는데 카운터를 안 올린다 → **Critical.** 비용이 보이지 않는다
- 호출 자체가 **실패**(예외)했는데 올린다 → 과금 안 된 걸 셌다
- 응답 해석 실패를 이유로 카운터를 안 올린다 → 위와 반대 방향의 같은 오류다

**(b) 상한 검사를 우회하지 않는다** — `image_vision.py` 의 `_call_vision()` 이
`ContentRoutingConfig.max_vision_calls_per_doc`(기본 30)을 검사한다.
새 외부 API 단계에도 같은 보호가 필요하다.

**(c) 캐시 키 salt 에 결과를 바꾸는 설정을 전부 넣는다.** 모델명·티어·버전이 빠지면 설정을
바꿔도 옛 결과가 나온다.

```python
content_hash(blob, salt=f"vision:{cfg.vision_model}")       # image_vision.py 의 _call_vision()
content_hash(blob, salt=f"llamaparse:{tier}:{version}")     # table_llamaparse.py 의 _parse_blob()
```

`ResultCache` 를 안 거치고 외부 API 를 부르면 재업로드마다 재과금된다.

**기계장치가 loader 안에 있다는 점을 본다.** `LoadContext` · `content_hash()` · `ResultCache`
는 셋 다 `loader/content/base.py` 에 있고, 따라서 loader 패키지 소속이다. 새 단계가 외부 API
비용을 쓴다면 이걸 **재사용·일반화**했는지 확인한다. 따로 복제했으면 비용 가시성이 두 곳으로
갈라지므로 지적한다.

**(d) 유료 호출 지점에는 `append_cost_log()` 한 줄이 카운터 **옆**에 있다.**
`rag_pipeline/cost_log.py`. 이음새는 열하나이고 전부 같은 모양이다 — `api_calls += 1` ·
`cache_hits += 1` · 폴백 자리 바로 옆이다. **카운터가 이미 그 자리에 있으므로 두 번째
진실이 안 생긴다**, 그 배치를 옮기면 생긴다.

- **새 유료 단계에 로그가 없다 → Critical.** `usage` 인스턴스를 만들어도 그것을 읽는
  코드가 없으면 비용이 0으로 사라진다. `generation/answer.py` 가 **함수라 인스턴스를 못
  갖고** 그래서 `verify_log/` 에 *"생성 LLM 2회"* 처럼 횟수만 남았던 자리다
- **세 상태를 다 남기는가** — `call`·`cache_hit`·`failed`. 성공만 남기면 「얼마 아꼈나」와
  「한도에 막혔나」를 못 낸다. 뒤엣것이 429 사고를 가른 신호다
- **로컬·무료 갈래는 안 부른다.** 부르면 문서 하나 색인에 수백 줄이 쌓인다
  (`local_ce.py`·`local_st.py` 는 계약만 맞추고 로그를 안 부른다 — 되돌리지 말 것)
- **금액을 줄에 싣지 않는다.** 단가가 바뀌면 쌓인 줄이 조용히 거짓이 된다. 금액은
  `cost/pricing.py` 를 읽는 `cost/report.py` 몫이다
- **`usage` 의 `total_tokens` 는 파생 프로퍼티다** — 다섯 클래스 전부. **생성자 인자로
  넘기면 깨진다**(`pipeline.py:_usage_since()` 가 실제로 그렇게 깨졌다). 읽는 쪽은 안 바뀌고
  **만드는 쪽만** 바뀐다는 것이 이 변경에서 제일 놓치기 쉬운 자리다
- **가짜 응답에 `prompt_tokens`·`completion_tokens` 가 있는가.** 합계만 흉내낸 가짜는
  입출력을 가르는 코드에 **0을 먹이고도 통과한다** — 실제로 그 상태로 잠긴 적이 없었다

`cost/` 는 **`rag_pipeline` 도 `evaluation` 도 import 하지 않는다**(플러그인으로 뽑을 수
있게 둔 경계이고 `tests/cost/test_cost_portable.py` 가 잠갔다). 거기에 저장소 상수를
들이면 지적한다.

## 4. 예외 → HTTP 매핑

`rag_pipeline/exceptions.py` 에 예외를 추가하면 `backend/app/main.py` 의
`_STATUS_BY_EXCEPTION` 에도 넣어야 한다. **빼먹으면 사용자가 500 을 본다** —
`_status_for()` 가 표에서 못 찾으면 500 을 기본값으로 돌려준다.

그 `_status_for()` 가 `isinstance` 로 표를 위에서부터 훑으므로 **하위 클래스를 상위 클래스보다
먼저** 넣어야 한다. 순서가 틀리면 조용히 엉뚱한 상태코드가 나간다.

모든 예외는 `RagPipelineError`(`exceptions.py`) 를 상속하고 한국어 `user_message` 를 갖는다.

## 5. 코드 컨벤션

- **모든 class/def 위에 한 줄 한국어 설명 주석.** 예외 없다
- 모듈 docstring 에는 *왜* 그렇게 했는지를 적는다. 실측 수치는 근거와 함께
- **각 단계 `__init__.py` docstring 끝에 "이 단계의 규약이 어디 있나" 색인 절이 있다.**
  CLAUDE.md 가 단계별 상세를 안 갖고 여기를 가리키므로(2026-08-22), **하위 모듈을 새로
  만들거나 규약을 옮겼는데 색인에 안 넣으면 포인터가 끊긴다.** 끊겨도 에러가 안 나므로
  리뷰에서 본다 — `cleaner`·`splitter`·`embedding`·`indexing` 넷 다 이 형태다
- 모든 모듈 상단에 `from __future__ import annotations`
- ruff: line-length 100, `E,F,I,UP,B,SIM` 선택, E501 무시

## 6. 테스트 계약

- **기본 실행(`pytest -q`)은 API 키 없이 전부 통과해야 한다.** 키가 필요한 테스트는
  `@pytest.mark.integration` 을 단다. 안 달면 CI 가 깨지고, 달았는데 실제 과금 호출이면
  그것도 리포트에 적는다
- **모킹 이음새는 각 API 핸들러의 모듈 레벨 `_make_client()`** 다
  (`mocker.patch.object(image_vision, "_make_client", return_value=fake)`).
  **인라인화하거나 클래스 메서드로 옮기면 외부 API 테스트가 전부 깨진다 → Critical**
- lazy 프로퍼티는 `object.__setattr__(config, "_content", ContentRoutingConfig(...))` 로
  덮어쓴다 — `tests/conftest.py` 의 `offline_config` 픽스처가 그 형태다
- **폴백 경로 테스트가 있는지 본다.** 성공 경로만 있으면 폴백이 죽어도 아무도 모른다.
  이 저장소에서 제일 자주 빠지는 테스트다
- 샘플 파일은 코드로 생성한다. 바이너리를 저장소에 커밋하지 않는 방침이므로 새 픽스처도 그래야 한다
- 새 설정을 추가했으면 `rag_pipeline/config.py` · `.env.example` · `offline_config` 3곳이
  맞는지 본다

## 7. 일반 정확성

추측으로 쓰지 않는다. "이런 입력이면 이렇게 깨진다"를 한 줄로 댈 수 있어야 리포트에 올린다.

- 정렬·순서 계약을 깬 변경
- 손상된 입력 · 빈 컬렉션 · 0바이트 데이터 처리 누락
- 인코딩: 한국어 파일명, `errors=` 지정 없는 `decode()`
- 리소스 누수: 핸들·이미지·임시파일을 안 닫는다
- 거대 입력 상한이 새 경로에 안 걸렸다
- 경로 처리: `Path` 대신 문자열 결합, Windows 경로 가정
