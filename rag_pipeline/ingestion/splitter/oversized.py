"""상한을 넘는 텍스트를 쪼갠다.

**이 모듈은 이 코퍼스에서 거의 발화하지 않는다.** 실측으로 텍스트 요소의 최대 길이가
PDF 123자 / HWPX 167자 / 시행령 554자였고 1200자를 넘는 텍스트 요소는 세 문서 전부 0개였다.
그래도 두는 이유는 두 가지다 — 표에서 `rows` 를 못 얻었을 때 여기로 내려오고, 다른 코퍼스
(회의록·논문처럼 문단이 긴 문서)에서는 실제로 걸리기 때문이다.

겹침(overlap)은 **여기서만** 준다. 섹션 경계로 나뉜 청크 사이에 겹침을 주면 애써 지킨 의미
단위를 다시 흐린다. 여기는 이미 의미 단위를 어쩔 수 없이 자른 자리라서 앞뒤 문맥을 조금
물려주는 편이 낫다.

겹침을 붙인 뒤에도 상한을 지키기 위해, 자를 때 예산을 `max - overlap` 으로 잡고 나서
앞 조각의 꼬리를 붙인다. 예산을 그대로 두고 꼬리를 붙이면 결과가 상한을 넘는다.
"""

from __future__ import annotations

# 굵은 경계부터 시도한다. 문단 → 줄 → 낱말 순이고, 다 없으면 글자 수로 끊는다
_SEPARATORS = ("\n\n", "\n", " ")


# 겹침 없이 max_chars 이하 조각들로 자른다
def _pieces(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]

    for separator in _SEPARATORS:
        if separator not in text:
            continue

        packed: list[str] = []
        buffer = ""
        for part in text.split(separator):
            candidate = part if not buffer else buffer + separator + part
            if len(candidate) <= max_chars:
                buffer = candidate
                continue
            if buffer:
                packed.append(buffer)
            buffer = part
        if buffer:
            packed.append(buffer)

        # 조각 하나가 아직 크면 더 가는 경계로 내려간다. 그 조각에는 이번 구분자가
        # 없으므로(구분자 사이를 자른 것이다) 같은 자리를 다시 돌지 않는다
        result: list[str] = []
        for piece in packed:
            result.extend(_pieces(piece, max_chars) if len(piece) > max_chars else [piece])
        return result

    # 구분자가 하나도 없다. 표 마크다운 한 줄처럼 통짜인 경우다
    return [text[index : index + max_chars] for index in range(0, len(text), max_chars)]


# 텍스트를 상한 이하 조각들로 자르고, 조각 사이에 겹침을 준다
def split_text(text: str, *, max_chars: int, overlap: int = 0) -> list[str]:
    if max_chars <= 0 or len(text) <= max_chars:
        return [text]

    # 겹침이 상한만큼 크면 조각이 앞으로 나아가지 못한다. 그때는 겹침을 포기한다
    usable = max_chars - overlap if 0 < overlap < max_chars else max_chars
    pieces = _pieces(text, usable)
    if usable == max_chars or len(pieces) < 2:
        return pieces

    joined = [pieces[0]]
    for previous, current in zip(pieces, pieces[1:], strict=False):
        joined.append(previous[-overlap:] + current)
    return joined
