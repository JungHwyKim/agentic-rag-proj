"""문자 수준 정리 — 제어문자와 보이지 않는 서식문자를 지운다.

PDF 는 글머리 기호를 제어 코드포인트로 떨어뜨린다. 실측: 신뢰가능 AI 가이드 PDF 에서
U+0001 192개 / U+0007 47개 / U+0008 4개가 나왔고, `작성 \\x07김동현 수석` 처럼 본문
한가운데 박혀 있다. 임베딩·검색에 그대로 노이즈로 들어간다. 같은 문서의 HWPX 판은 0개라,
이 규칙이 필요한 쪽은 PDF 경로다.

지우는 기준은 특정 코드포인트 목록이 아니라 **유니코드 범주** 다. Cc(제어)와 Cf(서식)을
지운다. Cf 에는 U+200B(zero-width space), U+FEFF(BOM), U+00AD(soft hyphen) 처럼 눈에
안 보이면서 단어를 갈라놓는 문자가 들어간다 — 보이지 않으니 사람이 못 잡고, 토큰이 쪼개져
검색이 조용히 나빠진다.

**`\\n` 은 반드시 남긴다.** 실측 560개가 전부 표 요소에 있고 마크다운 표의 행 구분자다.
지우면 `| 구 분 | 추진내용 || --- |` 처럼 한 줄로 뭉개져 표가 통째로 깨진다. `\\t` 도 남긴다.
`\\r` 은 지우지 않고 `\\n` 으로 바꾼다 — 그냥 지우면 앞뒤 줄이 서로 붙어버린다.

알면서 감수하는 것: Cf 에는 ZWJ(U+200D)도 들어가므로 이모지 결합(가족 이모지 등)이 쪼개진다.
한국 공문서 코퍼스에서는 사실상 나오지 않아 예외로 두지 않았다. 이모지를 살려야 하는 코퍼스가
생기면 _KEEP 에 U+200D 를 추가하면 된다.
"""

from __future__ import annotations

import unicodedata

# 제어·서식 범주지만 의미가 있어 남기는 문자.
# \n 은 표 마크다운의 행 구분자라 지우면 표가 깨진다
_KEEP = frozenset("\n\t")

# 지우는 유니코드 범주. Cc=제어문자, Cf=보이지 않는 서식문자
_STRIP_CATEGORIES = frozenset({"Cc", "Cf"})


# 문자열 하나에서 제어·보이지 않는 문자를 걷어낸다. 바뀐 게 없으면 원본을 그대로 돌려준다
def strip_control_chars(text: str) -> str:
    # \r 을 먼저 \n 으로 바꾼다. 아래 필터로 지워버리면 줄이 서로 붙는다
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")

    cleaned = "".join(
        ch
        for ch in normalized
        if ch in _KEEP or unicodedata.category(ch) not in _STRIP_CATEGORIES
    )
    # 동일하면 원본 객체를 돌려줘 상위에서 '안 바뀜'을 값 비교로 판단할 수 있게 한다
    return text if cleaned == text else cleaned
