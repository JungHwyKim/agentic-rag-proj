"""이미지 파일 로더 (.png/.jpg/.bmp/.tiff/.webp 등).

파일 전체를 이미지 리전 하나로 만들어 라우터에 넘긴다. OCR로 갈지 Vision으로 갈지는
여기서 정하지 않는다 — classify가 이미지 내용을 보고 판단한다.
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from rag_pipeline.ingestion.loader.content.base import ContentRegion, ContentType, LoadContext
from rag_pipeline.ingestion.loader.document.base import BaseDocumentLoader
from rag_pipeline.schemas.document import FileFormat


# 임의 포맷 이미지를 PNG 바이트로 통일한다. 핸들러들이 PNG만 다루면 되게 하기 위함
def to_png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    # 투명도가 있는 모드는 그대로, 나머지는 RGB로 변환해 저장 실패를 막는다
    if image.mode not in ("RGB", "RGBA", "L"):
        image = image.convert("RGB")
    image.save(buffer, format="PNG")
    return buffer.getvalue()


# 이미지 파일을 읽는 로더
class ImageLoader(BaseDocumentLoader):
    file_format = FileFormat.IMAGE

    # 이미지 파일 하나를 IMAGE 리전으로 만든다
    def detect_regions(self, path: Path, ctx: LoadContext) -> list[ContentRegion]:
        try:
            with Image.open(path) as image:
                image.load()
                blob = to_png_bytes(image)
                size = (image.width, image.height)
        except OSError:
            ctx.warn("이미지를 열 수 없어 건너뛰었습니다.")
            return []

        return [
            ContentRegion(
                content_type=ContentType.IMAGE,
                order=0,
                source_format=self.file_format.value,
                blob=blob,
                blob_kind="png",
                metadata={"width": size[0], "height": size[1]},
            )
        ]
