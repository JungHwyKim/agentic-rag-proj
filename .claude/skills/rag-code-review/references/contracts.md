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

- 예외가 밖으로 나간다 → **Critical.** 키 없는 환경에서 적재 전체가 죽고 기본 테스트가 깨진다
- `except Exception` 에 `# noqa: BLE001` 과 이유 주석이 없다
- 실패했는데 `ctx.warn()` 이 없다 → 사용자는 결과가 왜 엉성한지 알 길이 없다
- 폴백으로 내려가면서 이미 계산한 결과를 버리고 다시 계산한다

## 3. 비용 통제 3종

비용 가시성은 `LoadContext` 의 카운터가 `DocumentMetadata` 로 노출되는 경로 하나뿐이다.

**(a) 카운터는 호출이 성공한 뒤에 올린다** — `image_vision.py` 의 `_call_vision()`,
`table_llamaparse.py` 의 `_parse_blob()`

- 실제로 호출했는데 카운터를 안 올린다 → **Critical.** 비용이 보이지 않는다
- 호출이 **실패**했는데 올린다 → 과금 안 된 걸 셌다

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
