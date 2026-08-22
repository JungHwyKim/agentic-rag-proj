"""평가용 코퍼스 — 실파일을 파싱해 포맷별 텍스트를 만든다.

## 왜 요소 텍스트와 청크 텍스트를 **둘 다** 들고 있나

정답 span 을 검증하는 데 서로 다른 두 질문이 있고, 하나로는 답이 안 나온다.

0. **"이 문장이 몇 쪽에 있는가"** — 요소의 `page` 로 답한다. **청크의 `page` 로 답하면
   틀린다** — 그것은 청크의 *시작* 쪽이라, 쪽을 넘긴 청크의 뒷부분에 있는 span 은 한 쪽 앞으로
   보고된다(실측: `sch-13` 청크 8쪽 / 실제 9쪽 · `sih-11` 청크 5쪽 / 실제 6쪽 · `sch-14` 13 / 14).
   사람 검수가 그 쪽을 열었을 때 없으면 세트 전체를 못 믿게 된다
1. **"이 문장이 이 문서에 있는가"** — 요소 텍스트로 답한다. 청크 텍스트로 물으면 틀린다:
   `include_heading_in_text` 가 청크 앞에 `[제목 > 경로]` 를 붙이는데 그 경로가 포맷마다
   다르다(실측: 같은 대목을 HWPX 는 `[Ⅱ.세부실행방안>□업무역할별실행사항]`, PDF 는
   `[행정정보데이터세트시행령개정관련상세안내>ⅲ.추진사항>…]` 로 잡는다). 머리가 섞인 채로
   비교하면 **본문이 같은데 다르다고 나온다**
2. **"그 문장을 검색으로 찾을 수 있는가"** — 청크 텍스트로 답한다. span 이 청크 경계를
   가로지르면 어떤 청크에도 통째로 안 들어 있어 **영원히 못 맞힌다.** 채점이 청크 단위라
   이 검사가 없으면 세트에 못 맞히는 질의가 조용히 섞인다

## 공백을 지우고 비교한다

PDF 경로가 한국어 낱말 사이 공백을 자주 잃는다(실측: `템에서기록관의기록관리시 스템(rms)으로
이관하여관리하나` — 없어야 할 자리에 공백이 있고 있어야 할 자리에 없다). `scoring.normalize`
의 공백 압축만으로는 두 판이 안 맞는다. **채점과 검증이 반드시 같은 규칙을 써야 하므로**
`scoring.squash()` 하나만 쓰고 여기서 따로 만들지 않는다.

## 캐시

파싱이 느리다(문서 9개에 수 분). `.cache/evaluation/` 에 넣는다 — `.gitignore` 에 `.cache/`
가 이미 있다. 로더를 고치면 캐시가 낡으므로 `--refresh` 로 지운다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from evaluation.documents import DOCUMENT_GROUPS, FIXTURES_DIR
from evaluation.scoring import squash

__all__ = ["FormatCorpus", "build_corpus"]

# 저장소 루트/.cache/evaluation
CACHE_DIR = Path(__file__).resolve().parents[1] / ".cache" / "evaluation"

# 두 요소에 걸친 span 의 시작 조각을 찾을 때 쓰는 길이. `validate_items()` 가 span 하한을
# 6자로 막으므로 그보다 짧게 잡으면 우연한 일치가 는다
_HEAD_CHARS = 12

# 한 파일(= 한 포맷)의 텍스트. 두 질문에 각각 답할 재료를 함께 든다
@dataclass(frozen=True)
class FormatCorpus:
    file_name: str
    # 요소 텍스트 전체를 이어 붙여 공백을 지운 것. "이 문장이 문서에 있는가"
    body: str
    # 청크별 공백 없는 텍스트. "그 문장을 청크 하나 안에서 찾을 수 있는가"
    # **자리(index)가 곧 `Chunk.chunk_index` 다** — splitter 가 0부터 빈틈 없이 매기므로
    # 인접 판정(`chunk_indices_for`)이 이 자리 번호를 그대로 쓸 수 있다
    chunks: list[str]
    # 청크별 **시작** 쪽. `page_end` 까지 걸칠 수 있어 `gold_page` 의 근거로는 못 쓴다
    pages: list[int | None]
    # 요소별 공백 없는 텍스트와 그 쪽. **`gold_page` 는 이쪽에서 나온다**
    elements: list[str]
    element_pages: list[int | None]
    # 요소의 `order` 값. **자리 번호와 다르다** — cleaner 가 지운 자리와 핸들러가 0개를
    # 만든 자리 때문에 `order` 에는 빈틈이 있다 (schemas/chunk.py)
    element_orders: list[int]
    # 청크가 삼킨 요소들의 `order` 목록. 걸친 span 의 쪽을 그 청크 안에서만 찾게 해준다
    chunk_element_orders: list[list[int]]

    # span 을 담은 청크 자리 번호들. 오름차순이다
    def chunk_indices_for(self, needle: str) -> list[int]:
        return [i for i, text in enumerate(self.chunks) if needle in text]

    # span 이 처음 나오는 쪽. **요소에서 얻는다** — 청크의 `page` 는 시작 쪽이라,
    # 쪽을 넘긴 청크의 뒷부분에 있는 span 은 **한 쪽 앞으로 잘못 보고된다**
    # (실측: `sch-13` 이 청크 기준 8쪽 / 실제 9쪽, `sih-11` 이 5쪽 / 실제 6쪽).
    # 사람이 원문을 열었을 때 그 쪽에 없으면 세트를 못 믿게 되므로 요소로 좁힌다
    def first_page_for(self, needle: str) -> int | None:
        for text, page in zip(self.elements, self.element_pages, strict=False):
            if needle in text and page is not None:
                return page

        # 요소 하나에 통째로 안 들어가면 **두 요소에 걸친** 것이다. PDF 는 PyMuPDF 블록
        # 단위라 한 문장이 갈리는 일이 흔하다(실측: `sch-14` 의 span 이 요소 322 에서
        # `…비공개 사유가 소` 로 끊긴다). 그때는 **시작 조각이 있는 요소**의 쪽을 쓰되,
        # **그 span 을 담은 청크 안에서만 찾는다.**
        #
        # 문서 전체에서 앞 조각을 찾으면 틀린다 — 실측으로 `ai-15` 의 앞 12자
        # `신뢰가능ai시스템구현을` 이 요약절에도 있어 19쪽 대신 5쪽이 나왔다. span 자체는
        # 유일해도 그 **앞부분은 유일하지 않다.**
        by_order = {order: i for i, order in enumerate(self.element_orders)}
        head = needle[:_HEAD_CHARS]
        for index in self.chunk_indices_for(needle):
            for order in self.chunk_element_orders[index]:
                i = by_order.get(order)
                if i is None:
                    continue
                if head in self.elements[i] and self.element_pages[i] is not None:
                    return self.element_pages[i]

        # 마지막 수단. 청크의 **시작** 쪽이라 한 쪽 앞으로 틀릴 수 있다
        for index in self.chunk_indices_for(needle):
            if self.pages[index] is not None:
                return self.pages[index]
        return None


# 파일 하나를 파싱해 코퍼스로. 캐시가 있으면 그것을 쓴다
def _load_one(path: Path, *, refresh: bool = False) -> FormatCorpus:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached = CACHE_DIR / (path.name + ".json")

    if cached.exists() and not refresh:
        raw = json.loads(cached.read_text(encoding="utf-8"))
    else:
        # 지연 import — 코퍼스를 안 쓰는 경로(형식 검사만)가 파이프라인을 안 끌어오게 한다
        from rag_pipeline.config import IngestConfig
        from rag_pipeline.ingestion.pipeline import ingest
        from rag_pipeline.ingestion.splitter import split

        config = IngestConfig()
        document = ingest(path, original_name=path.name, config=config)
        chunks = split(document, config.splitter, document_key=path.name)
        raw = {
            "elements": [element.text for element in document.elements],
            "chunks": [chunk.text for chunk in chunks],
            "pages": [chunk.page for chunk in chunks],
            "element_pages": [element.page for element in document.elements],
            "element_orders": [element.order for element in document.elements],
            "chunk_element_orders": [list(chunk.element_orders) for chunk in chunks],
        }
        cached.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    return FormatCorpus(
        file_name=path.name,
        body=squash(" ".join(raw["elements"])),
        chunks=[squash(text) for text in raw["chunks"]],
        # 옛 캐시에는 `pages` 가 없다. 그때는 쪽을 모르는 것으로 두고 `--refresh` 를 알린다
        pages=list(raw.get("pages") or [None] * len(raw["chunks"])),
        elements=[squash(text) for text in raw["elements"]],
        element_pages=list(raw.get("element_pages") or [None] * len(raw["elements"])),
        element_orders=list(raw.get("element_orders") or range(len(raw["elements"]))),
        chunk_element_orders=list(raw.get("chunk_element_orders") or [[]] * len(raw["chunks"])),
    )


# 그룹별·**포맷 라벨별** 코퍼스. **없는 파일은 조용히 건너뛴다** (fixtures 는 gitignore 다).
# 키가 파일명이 아니라 라벨인 이유는 `documents.py` 모듈 docstring 에 있다
def build_corpus(
    groups: list[str] | None = None,
    *,
    refresh: bool = False,
) -> dict[str, dict[str, FormatCorpus]]:
    out: dict[str, dict[str, FormatCorpus]] = {}
    for group in groups or list(DOCUMENT_GROUPS):
        by_label: dict[str, FormatCorpus] = {}
        for label, name in DOCUMENT_GROUPS.get(group, {}).items():
            path = FIXTURES_DIR / name
            if path.exists():
                by_label[label] = _load_one(path, refresh=refresh)
        if by_label:
            out[group] = by_label
    return out
