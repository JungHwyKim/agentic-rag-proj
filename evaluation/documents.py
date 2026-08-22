"""평가에 쓰는 문서 그룹.

## 왜 파일명에서 그룹을 못 뽑나

`plan_log/평가-세트.md` 2회차는 *"`source_doc` 에 확장자를 안 붙여 한 질의가 두 포맷을 함께
잰다"* 로 정했다. **실제 파일명을 보니 그것만으로는 안 된다** — 학교기록물 짝은 두 포맷의
이름 자체가 다르다(`「2025년 학교기록물 관리 지침」 (1).pdf` vs
`2025년 학교기록물 관리 지침.hwpx`). 확장자만 떼면 서로 다른 그룹이 된다.

그래서 그룹을 **명시적으로 적는다.** 자동 추론(확장자 제거·정규화·유사도)은 이 한 건을
맞추려다 다른 문서에서 조용히 틀릴 여지를 남기고, 그룹이 넷뿐이라 얻는 것도 없다.

## 왜 키가 파일명이 아니라 짧은 라벨인가

`qa_set.jsonl` 의 `gold_page` 가 이 라벨을 키로 쓴다. 파일명을 그대로 키로 두면 한 줄이
200자를 넘어 **사람 검수의 diff 가 안 읽힌다** — 한 줄 한 레코드인 jsonl 을 고른 이유가
그것이라 자기모순이 된다. 확장자로 가르는 것도 안 된다: 시행령 그룹에 `.hwpx` 가 둘이다.

**라벨을 바꾸면 `qa_set.jsonl` 을 함께 고쳐야 한다.** 그것을 사람 기억에 맡기지 않으려고
`qa_set.validate_items()` 가 `gold_page` 의 키를 이 표와 대조한다.

## 왜 `_가로쪽` 판이 시행령 그룹에 있나

같은 내용을 가로 쪽으로 조판한 판이고, 요소·청크가 세로판과 완전히 같다는 실측이 있다
(CLAUDE.md "HWPX 가로 쪽"). 포맷 비교에 **세 번째 표본을 공짜로** 준다.

## 파일이 없으면 조용히 건너뛴다

`tests/fixtures/` 는 gitignore 다(바이너리를 커밋하지 않는 방침). 실파일 회귀 테스트 다섯이
쓰는 것과 같은 정책을 여기서도 쓴다 — 없으면 skip 이지 실패가 아니다.
"""

from __future__ import annotations

from pathlib import Path

__all__ = [
    "DOCUMENT_GROUPS",
    "FIXTURES_DIR",
    "available_files",
    "format_labels",
    "group_of",
]

# 저장소 루트/tests/fixtures. evaluation/documents.py -> 루트
FIXTURES_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures"

# 그룹 키 -> {포맷 라벨: 파일명}.
# **바깥 키가 `qa_set.jsonl` 의 `source_doc`, 안쪽 키가 `gold_page` 의 키다.**
# 둘 중 무엇을 바꿔도 세트를 함께 고쳐야 하고, 그것을 검증이 강제한다
DOCUMENT_GROUPS: dict[str, dict[str, str]] = {
    # **HWPX 판을 일부러 뺐다.** 한컴이 이 문서의 표를 레이아웃 상자로 그려서 표 청크가
    # **0개**인데 PDF 판은 10개다 — 같은 문서의 두 판이라기에는 내용 구성이 다르고,
    # 포맷 비교(`plan_log/검색-생성.md` 비교 5번)의 전제가 이 짝에서만 성립하지 않는다.
    # 쪽 수도 이 문서만 두 판이 −1~−56 으로 벌어진다(다른 셋은 0 또는 +1).
    # 근거: verify_log/2026-08-22-검색평가-세트.md
    "신뢰가능-AI-가이드": {
        "pdf": "[DNA+_2019-5]_공공기관_신뢰가능_AI_구현_실용가이드.pdf",
    },
    "학교기록물-관리지침": {
        "hwpx": "2025년 학교기록물 관리 지침.hwpx",
        "pdf": "「2025년 학교기록물 관리 지침」 (1).pdf",
    },
    "중등-기초학력-보장계획": {
        "hwpx": "2026+중등+기초학력+보장+주친+계획.hwpx",
        "pdf": "2026+중등+기초학력+보장+주친+계획.pdf",
    },
    "시행령-개정안-안내서": {
        "hwpx": "시행령 개정안 안내서(정보시스템의무사항알림).hwpx",
        "pdf": "시행령 개정안 안내서(정보시스템의무사항알림).pdf",
        "hwpx-가로": "시행령 개정안 안내서(정보시스템의무사항알림)_가로쪽.hwpx",
    },
}


# 그 그룹이 쓰는 포맷 라벨들. 파일이 디스크에 있든 없든 정의된 전부다 —
# `gold_page` 검증은 "정의된 라벨이 다 채워졌나"를 보므로 파일 유무에 흔들리면 안 된다
def format_labels(group: str) -> tuple[str, ...]:
    return tuple(DOCUMENT_GROUPS.get(group, {}))


# 그룹에서 실제로 디스크에 있는 것만. 없으면 빈 dict 이고 부르는 쪽이 skip 한다
def available_files(group: str) -> dict[str, Path]:
    return {
        label: FIXTURES_DIR / name
        for label, name in DOCUMENT_GROUPS.get(group, {}).items()
        if (FIXTURES_DIR / name).exists()
    }


# 파일명이 속한 (그룹, 라벨). 모르는 파일이면 None
def group_of(file_name: str) -> tuple[str, str] | None:
    for group, by_label in DOCUMENT_GROUPS.items():
        for label, name in by_label.items():
            if file_name == name:
                return group, label
    return None
