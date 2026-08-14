"""텍스트 계열 로더 (.txt/.md/.csv/.tsv).

한국어 텍스트 파일은 cp949로 저장된 경우가 흔해서 인코딩 자동 감지가 필수다.
UTF-8로 단정하고 읽으면 한글이 통째로 깨진다.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from charset_normalizer import from_bytes

from rag_pipeline.ingestion.loader.content.base import ContentRegion, ContentType, LoadContext
from rag_pipeline.ingestion.loader.document.base import BaseDocumentLoader
from rag_pipeline.schemas.document import ElementType, FileFormat

# 한국어 환경에서 실제로 자주 쓰이는 레거시 인코딩
_KOREAN_ENCODINGS = ("cp949", "euc-kr")


# 문자열에 한글이 들어있는지 확인. 레거시 인코딩 추측이 맞았는지 판단하는 근거로 쓴다
def _has_hangul(text: str) -> bool:
    return any("가" <= ch <= "힣" or "ㄱ" <= ch <= "ㆎ" for ch in text)


# 바이트에서 인코딩을 추정해 문자열로 디코드한다.
#
# UTF-8을 먼저 확정하는 이유: UTF-8은 구조가 엄격해 오탐이 사실상 없다.
# cp949를 charset-normalizer보다 먼저 보는 이유: 짧은 한국어 문자열(예: "한글" 4바이트)은
# 통계적 추정이 자주 틀린다. 다만 라틴 계열 텍스트를 cp949로 잘못 읽지 않도록,
# 디코드 결과에 실제로 한글이 들어있을 때만 채택한다.
def decode_text(data: bytes) -> tuple[str, str]:
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass

    for encoding in _KOREAN_ENCODINGS:
        try:
            decoded = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        if _has_hangul(decoded):
            return decoded, encoding

    best = from_bytes(data).best()
    if best is not None:
        return str(best), best.encoding or "unknown"

    return data.decode("utf-8", errors="replace"), "utf-8(replace)"


# 줄바꿈을 LF로 통일한다. Windows에서 저장한 파일은 CRLF라 문단 분리가 어긋난다
def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


# 구분자를 추정한다. 탭이 쉼표보다 많으면 TSV로 본다
def _sniff_delimiter(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",\t;|").delimiter
    except csv.Error:
        return "\t" if sample.count("\t") > sample.count(",") else ","


# .txt/.md/.csv/.tsv 를 읽는 로더
class TextLoader(BaseDocumentLoader):
    file_format = FileFormat.TEXT

    def __init__(self, config=None, *, file_format: FileFormat = FileFormat.TEXT) -> None:
        super().__init__(config)
        self.file_format = file_format

    # 파일 전체를 문단 또는 표 리전으로 만든다
    def detect_regions(self, path: Path, ctx: LoadContext) -> list[ContentRegion]:
        raw = path.read_bytes()
        text, encoding = decode_text(raw)
        text = normalize_newlines(text)

        if encoding.lower() not in ("utf_8", "utf-8", "ascii"):
            ctx.warn(f"{encoding} 인코딩으로 디코드했습니다.")

        if self.file_format is FileFormat.CSV:
            return self._csv_regions(text)
        return self._text_regions(text)

    # CSV/TSV를 표 리전 하나로 만든다
    def _csv_regions(self, text: str) -> list[ContentRegion]:
        delimiter = _sniff_delimiter(text[:4096])
        rows = [row for row in csv.reader(io.StringIO(text), delimiter=delimiter) if any(row)]
        if not rows:
            return []
        return [
            ContentRegion(
                content_type=ContentType.TABLE,
                order=0,
                source_format=self.file_format.value,
                rows=rows,
                metadata={"delimiter": delimiter},
            )
        ]

    # 빈 줄 기준으로 문단을 나눠 텍스트 리전 목록을 만든다
    def _text_regions(self, text: str) -> list[ContentRegion]:
        regions: list[ContentRegion] = []
        order = 0
        for block in text.split("\n\n"):
            stripped = block.strip()
            if not stripped:
                continue

            # 마크다운 헤딩은 제목 요소로 구분한다
            is_heading = self.file_format is FileFormat.MARKDOWN and stripped.startswith("#")
            element_type = ElementType.TITLE if is_heading else ElementType.PARAGRAPH

            regions.append(
                ContentRegion(
                    content_type=ContentType.TEXT,
                    order=order,
                    source_format=self.file_format.value,
                    text=stripped,
                    metadata={"element_type": element_type},
                )
            )
            order += 1
        return regions
