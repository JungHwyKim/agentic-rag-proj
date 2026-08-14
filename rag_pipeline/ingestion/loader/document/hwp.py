"""구형 바이너리 포맷 스텁 (.hwp / .doc / .ppt).

.hwp 는 OLE 바이너리, .hwpx 는 ZIP+XML 로 서로 완전히 다른 포맷이다. 파이썬만으로
.hwp -> .hwpx 변환은 불가능하고 한컴오피스 본체가 필요하다. 그래서 지금은 파싱하지
않고, 사용자가 무엇을 해야 하는지 알려주는 안내 메시지와 함께 거부한다.

추후 hwp -> hwpx 변환 기능은 HwpLoader.convert_to_hwpx() 자리에 붙인다.
"""

from __future__ import annotations

from pathlib import Path

from rag_pipeline.exceptions import UnsupportedFormatError
from rag_pipeline.ingestion.loader.content.base import ContentRegion, LoadContext
from rag_pipeline.ingestion.loader.document.base import BaseDocumentLoader
from rag_pipeline.schemas.document import FileFormat, ParsedDocument

# 포맷별 사용자 안내 메시지
_GUIDANCE: dict[FileFormat, str] = {
    FileFormat.HWP: (
        "구형 한글 문서(.hwp)는 아직 지원하지 않습니다. "
        "한글에서 '파일 > 다른 이름으로 저장하기'를 열고 "
        "파일 형식을 'HWPX 문서 (*.hwpx)'로 선택해 저장한 뒤 다시 업로드해 주세요."
    ),
    FileFormat.DOC: (
        "구형 워드 문서(.doc)는 아직 지원하지 않습니다. "
        "Word에서 '다른 이름으로 저장'을 열고 파일 형식을 "
        "'Word 문서 (*.docx)'로 선택해 저장한 뒤 다시 업로드해 주세요."
    ),
    FileFormat.PPT: (
        "구형 파워포인트 문서(.ppt)는 아직 지원하지 않습니다. "
        "PowerPoint에서 '다른 이름으로 저장'을 열고 파일 형식을 "
        "'PowerPoint 프레젠테이션 (*.pptx)'으로 선택해 저장한 뒤 다시 업로드해 주세요."
    ),
}


# 파싱을 지원하지 않는 구형 포맷을 안내 메시지와 함께 거부하는 로더
class UnsupportedLegacyLoader(BaseDocumentLoader):
    file_format = FileFormat.HWP

    def __init__(self, config=None, *, file_format: FileFormat = FileFormat.HWP) -> None:
        super().__init__(config)
        self.file_format = file_format

    # 리전 감지 단계까지 가지 않는다. load()에서 바로 예외를 던진다
    def detect_regions(self, path: Path, ctx: LoadContext) -> list[ContentRegion]:
        return []

    # 항상 UnsupportedFormatError를 던진다. backend가 이를 415로 매핑한다
    def load(self, path: Path, *, original_name: str | None = None) -> ParsedDocument:
        message = _GUIDANCE.get(
            self.file_format, f"{self.file_format.value} 포맷은 아직 지원하지 않습니다."
        )
        raise UnsupportedFormatError(
            f"unsupported legacy format: {self.file_format.value}",
            user_message=message,
            detected=self.file_format.value,
        )

    # 추후 hwp -> hwpx 변환 기능이 들어갈 자리. 구현되면 load()가 이걸 먼저 시도한다
    def convert_to_hwpx(self, path: Path) -> Path | None:
        return None
