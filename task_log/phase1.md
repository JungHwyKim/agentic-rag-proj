1단계 완료했습니다.

환경설정

- uv + Python 3.11.9 가상환경, requirements.txt / requirements.lock
- 53개 패키지 (초안의 98개에서 축소 — 아래 설명)
- Tesseract 5.5.0 + 한국어 연동 확인

검증 결과

ruff:    All checks passed
pytest:  106 passed (API 키 없이 전부 통과)
E2E:     docx/xlsx/png 업로드 정상, .exe·미확인 바이너리 415 거부

작업 중 발견해서 고친 것

1. llama-parse가 이중으로 deprecated — llama-parse → llama-cloud-services(2026-05-01 유지보수 종료, 이미 지남) → llama-cloud>=1.0. 죽은 패키지 대신 현행 SDK로 교체했고, 덕분에 불필요하게 딸려오던 llama-index-core·sqlalchemy·nltk·numpy·networkx 등 45개가 빠졌습니다.

2. OCR이 한글을 세로로 오인식 — 기본 PSM 3에서 "대한민국 서울특별시"가 [ 완 ro 소 mo로 나왔습니다. PSM 6으로 바꾸니 신뢰도 93%로 정확해졌습니다.

3. 전처리가 인식을 파괴 — 900→1000px 미세 업스케일이 보간 흐림을 만들어 원본 7토큰 → 전처리 후 0토큰이 됐습니다. 600px 미만일 때만 확대하도록 수정했습니다.

4. 한글 음절 분해 — 주식회사가 주 식 회 사로 나와 임베딩·검색이 망가지는 문제입니다. preserve_interword_spaces=1로 해결했는데, 여기서 하나 더 있었습니다: image_to_data는 이 옵션을 무시하고 여전히 음절로 쪼갭니다. 상자 간격으로 재조립하려 실측해봤더니 한글 음절 내부 간격(0.26~0.56)과 영문 단어 경계(0.48~0.59)가 겹쳐서 임계값 하나로 분리가 불가능했습니다. 그래서 용도를 분리했습니다 — 신뢰도는 image_to_data, 최종 텍스트는 image_to_string.

5. PDF 스캔 판정 임계값이 과함 — 20자 미만을 스캔으로 보면 제목만 있는 표지·간지가 전부 OCR로 넘어가 비용을 낭비합니다. 10자로 낮췄습니다.

6. .env.example에 실제 API 키 — 커밋되는 템플릿 파일이라 키가 저장소에 올라갑니다. .env(gitignore 대상)로 옮기고 템플릿은 비웠습니다.

남은 것 두 가지

.hwpx 실제 샘플이 필요합니다. 지금 테스트는 코드로 만든 합성 파일이라 실제 한컴 산출물과 구조가 다를 수 있습니다. 표와 문단이 있는 파일 하나를 tests/fixtures/에 넣어주시면 검증하겠습니다.

유료 API 실호출은 안 했습니다. .env에 실제 키가 있어서 표 있는 PDF나 차트 이미지를 올리면 과금됩니다. 허락 없이 크레딧을 쓰지 않으려고 외부 호출이 없는 경로로만 확인했습니다. 직접 확인하시려면:
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload
# http://127.0.0.1:8000/docs 에서 표 있는 PDF 업로드

검증 끝나면 다음은 cleaner/입니다.

claude --resume 6a7e4432-c1c7-4524-b91a-4d98205e09e5
