"""서버 없이 화면을 보는 단독 HTML 을 만든다. 개발용 도구이고 앱은 이것을 import 하지 않는다.

    .\\.venv\\Scripts\\python.exe -m backend.preview

**왜 필요한가.** 템플릿을 브라우저로 직접 열면 CSS 가 `file:///static/app.css` 로 풀려 404 이고,
htmx 가 채우는 자리(문서 목록·대화)는 빈 채로 남고, Jinja 주석이 글자로 찍힌다. 그 화면은
"스타일이 깨진 앱" 처럼 보여서 원인을 짐작할 수 없다 — 실제로 두 번 그렇게 헤맸다.
그렇다고 모양 하나 보자고 매번 서버를 띄우는 것도 무겁다.

**모듈로 실행한다**(`-m backend.preview`). `regression/measure.py` 를 파일 경로로 직접 돌렸다가
`ModuleNotFoundError: No module named 'rag_pipeline'` 로 죽은 선례가 있다.

## 샘플이 예시화면이 아니라 실데이터인 이유

화면 구성안(스크린샷)은 문서 2개·소스 2개에 파일명이 짧다. 실제는 문서 5개·소스 5개이고
이름이 `국가아동권리보장원+권리지원부_제3차+아동정책기본계획+아동용+안내서.pdf` 처럼 길다.
**예시 값으로 채우면 확인하고 싶은 것을 정확히 못 본다** — 태그 줄바꿈, 이름 잘림, 사이드바
폭이 전부 긴 이름에서만 드러난다. 그래서 실제 질의에서 나온 값(점수 0.661~0.691, `p.13–14`)을
그대로 쓴다.

## 산출물은 커밋하지 않는다

`preview.html` 은 `.gitignore` 에 있다. 생성기는 커밋하고 산출은 안 한다 — 템플릿을 고치면
곧바로 낡는 파일이라, 저장소에 두면 어느 시점의 화면인지 아무도 모르게 된다.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from rag_pipeline.schemas.answer import Answer, RewriteTrace
from rag_pipeline.schemas.retrieval import RetrievedChunk

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = PROJECT_ROOT / "backend" / "app" / "templates"
CSS_PATH = PROJECT_ROOT / "backend" / "app" / "static" / "app.css"
OUTPUT_PATH = PROJECT_ROOT / "preview.html"

# 실제로 색인돼 있는 문서 다섯. 긴 한국어 파일명이 사이드바를 밀어내지 않는지 보는 것이 요점이다
SAMPLE_DOCUMENTS: list[dict[str, object]] = [
    {"source_name": "시행령 개정안 안내서(정보시스템의무사항알림).pdf", "chunk_count": 118},
    {"source_name": "「2025년 학교기록물 관리 지침」 (1).pdf", "chunk_count": 122},
    {"source_name": "2026+중등+기초학력+보장+주친+계획.pdf", "chunk_count": 103},
    {"source_name": "[DNA+_2019-5]_공공기관_신뢰가능_AI_구현_실용가이드.pdf", "chunk_count": 71},
    {
        "source_name": "국가아동권리보장원+권리지원부_제3차+아동정책기본계획+아동용+안내서.pdf",
        "chunk_count": 41,
    },
]
for _doc in SAMPLE_DOCUMENTS:
    _doc["document_id"] = _doc["source_name"]

_SOURCE_NAME = "시행령 개정안 안내서(정보시스템의무사항알림).pdf"
# (page, page_end, dense_score) — 2026-08-23 실제 질의 결과 그대로. p.13–14 는 쪽 넘김 렌더 확인용
_SAMPLE_HITS = [(3, None, 0.691), (13, 14, 0.675), (12, None, 0.673), (1, None, 0.664), (11, None, 0.661)]


# 화면에 그릴 샘플 답변 하나를 만든다
def sample_answer() -> Answer:
    query = "행정정보 데이터세트를 관리할 때 업무정보로 무엇을 기록해야 하나요?"
    return Answer(
        query=query,
        text=(
            "행정정보 데이터세트를 관리할 때 업무정보로는 첨부파일 형태의 자료와 종류, 뷰어, "
            "저장방식 및 경로 등을 파악하여 작성해야 합니다[3]."
        ),
        sources=[
            RetrievedChunk(
                chunk_id=f"{_SOURCE_NAME}:{index:04d}",
                document_id=_SOURCE_NAME,
                text=(
                    "주요 업무수행 증거인 행정정보 데이터세트도 중요 기록물로 관리해야 하며, "
                    "첨부파일 형태의 자료는 종류·뷰어·저장방식 및 경로를 파악하여 작성한다."
                ),
                metadata={
                    "source_name": _SOURCE_NAME,
                    "page": page,
                    "page_end": page_end,
                    "heading_path": [
                        "행정정보 데이터세트 시행령 개정 관련 상세 안내",
                        "Ⅰ. 개 요",
                        "□ 배경 및 필요성",
                    ],
                },
                dense_score=score,
            )
            for index, (page, page_end, score) in enumerate(_SAMPLE_HITS)
        ],
        # 재작성 줄을 그리게 한다. **일부러 길다** — 이 줄에서 사람이 볼 것이 말줄임이고,
        # 짧은 값을 넣으면 잘림이 안 나와 미리보기가 그 자리를 확인해 주지 못한다.
        # `fuse` 인 것도 의미가 있다: 꼬리말이 모드마다 갈리는데 저장소 기본값이 이쪽이다
        rewrite=RewriteTrace(
            query=(
                "행정정보 데이터세트 관리 시 기록해야 하는 업무정보의 종류와 "
                "첨부파일 뷰어·저장방식·경로 작성 기준"
            ),
            mode="fuse",
        ),
    )


# 여는 태그와 닫는 태그 사이를 통째로 갈아끼운다. htmx 가 평소 하는 일을 대신하는 자리다
def _fill(html: str, opening: str, content: str) -> str:
    return re.sub(
        rf"({re.escape(opening)}).*?(</div>)",
        lambda m: m.group(1) + content + m.group(2),
        html,
        count=1,
        flags=re.S,
    )


# 템플릿 셋을 렌더해 단독 HTML 하나로 합친다
def build() -> Path:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    answer = sample_answer()

    page = env.get_template("chat.html").render()
    documents = env.get_template("_documents.html").render(documents=SAMPLE_DOCUMENTS)
    answer_html = env.get_template("_answer.html").render(answer=answer, query=answer.query)

    # 목록 조각은 out-of-band 문단을 함께 실어 온다. 여기서는 htmx 가 없으니 손으로 가른다
    doc_list = documents.split('<p class="sidebar-foot"')[0]
    page = _fill(page, '<div id="documents"', doc_list)
    page = _fill(page, '<div id="messages" class="messages">', answer_html)
    page = page.replace(
        '<p class="sidebar-foot muted small" id="doc-count"></p>',
        '<p class="sidebar-foot muted small" id="doc-count">'
        f"{len(SAMPLE_DOCUMENTS)}개 문서 · 인덱싱 완료</p>",
    )

    # CSS 를 인라인한다. 이것이 file:// 에서 404 가 안 나게 하는 유일한 방법이다
    page = page.replace(
        '<link rel="stylesheet" href="/static/app.css">',
        f"<style>\n{CSS_PATH.read_text(encoding='utf-8')}\n</style>",
    )
    # htmx 는 없어도 되고(모든 자리를 이미 채웠다) 있으면 404 만 낸다
    page = re.sub(r'\s*<script src="/static/htmx\.min\.js" defer></script>', "", page)
    # **안내 배너를 지운다.** 이 파일도 file:// 로 열리므로 안 지우면 "직접 열지 말라"는
    # 배너가 정작 직접 열라고 만든 파일에 뜬다. id 가 앵커다 (chat.html 주석 참고)
    page = re.sub(r'\s*<script id="file-warning">.*?</script>', "", page, flags=re.S)

    OUTPUT_PATH.write_text(page, encoding="utf-8")
    return OUTPUT_PATH


# 만들고 어디에 놓였는지 알려준다
def main() -> int:
    path = build()
    print(f"만들었습니다: {path}")
    print(f"크기: {path.stat().st_size:,} bytes")
    print("브라우저로 이 파일을 열면 됩니다 (서버 불필요).")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
