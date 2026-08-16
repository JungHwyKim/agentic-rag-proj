"""공백 정규화 — 연속된 공백·탭을 한 칸으로 줄이고 줄 앞뒤 공백을 없앤다.

실측: 시행령 안내서 HWPX 14요소 · AI 가이드 HWPX 89요소 · 같은 문서 PDF 13요소에서 연속 공백이
나왔고, PDF 는 앞뒤 공백이 붙은 요소가 15개였다. `'...명시하고,  추가적 필요 사항은...'` 처럼
문장 한가운데 두 칸이 들어간다. BPE 토크나이저는 " " 와 "  " 를 다른 토큰으로 쪼개므로 같은
문장이 다른 벡터가 된다.

지금 픽스처만 보면 값어치가 작아 보이지만 **픽스처가 OCR 경로를 안 태운다는 점을 감안해야 한다.**
스캔 PDF 는 Tesseract 를 타고, 그 출력은 공백이 훨씬 불규칙하다.

`\\n` 은 보존한다 — 표 마크다운의 행 구분자라 지우면 표가 한 줄로 뭉개진다(characters.py 와
같은 이유). 그래서 줄 단위로 나눠 처리한 뒤 다시 개행으로 잇는다.

표의 빈 셀 마커 `|  |` 가 `| |` 로 바뀌지만 둘 다 유효한 빈 셀이라 표는 안 깨진다.
"""

from __future__ import annotations

import re

# 줄 안의 연속된 공백·탭. 개행은 여기 걸리지 않게 문자 클래스를 좁게 잡는다
_RUNS = re.compile(r"[ \t]+")


# 문자열의 공백을 정규화한다. 바뀐 게 없으면 원본을 그대로 돌려준다
def normalize_whitespace(text: str) -> str:
    lines = [_RUNS.sub(" ", line).strip() for line in text.split("\n")]
    cleaned = "\n".join(lines).strip()
    # 동일하면 원본 객체를 돌려줘 상위에서 '안 바뀜'을 is 비교로 판단할 수 있게 한다
    return text if cleaned == text else cleaned
