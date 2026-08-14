"""매직바이트로 파일의 실제 포맷을 판별한다.

확장자를 믿으면 안 되는 이유:
  - docx/pptx/xlsx/hwpx 는 전부 ZIP(PK\\x03\\x04) 이라 겉모습이 같다
  - hwp/doc/ppt/xls 는 전부 OLE 복합문서(D0 CF 11 E0) 라 겉모습이 같다
확장자만 보면 이름을 .hwpx 로 바꾼 .hwp 가 들어왔을 때 파싱 도중 엉뚱한 에러가 난다.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import olefile

from rag_pipeline.schemas.document import FileFormat

# 매직바이트 시그니처
_ZIP_MAGIC = b"PK\x03\x04"
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_PDF_MAGIC = b"%PDF-"

# 이미지 포맷 시그니처 (offset, bytes) 목록
_IMAGE_MAGICS: list[tuple[int, bytes]] = [
    (0, b"\x89PNG\r\n\x1a\n"),  # PNG
    (0, b"\xff\xd8\xff"),  # JPEG
    (0, b"GIF87a"),
    (0, b"GIF89a"),
    (0, b"BM"),  # BMP
    (0, b"II*\x00"),  # TIFF little-endian
    (0, b"MM\x00*"),  # TIFF big-endian
]

# ZIP 내부에 이 경로가 있으면 해당 포맷으로 판정
_ZIP_MARKERS: list[tuple[str, FileFormat]] = [
    ("word/document.xml", FileFormat.DOCX),
    ("ppt/presentation.xml", FileFormat.PPTX),
    ("xl/workbook.xml", FileFormat.XLSX),
    # HWPX(OWPML)는 패키지 매니페스트가 Contents/content.hpf 에 있다
    ("Contents/content.hpf", FileFormat.HWPX),
]

# OLE 스트림 이름으로 구형 바이너리 포맷을 구분
_OLE_MARKERS: list[tuple[str, FileFormat]] = [
    ("WordDocument", FileFormat.DOC),
    ("PowerPoint Document", FileFormat.PPT),
    ("Workbook", FileFormat.XLS),
    ("Book", FileFormat.XLS),
]

# 확장자로만 갈리는 순수 텍스트 계열
_TEXT_EXTENSIONS: dict[str, FileFormat] = {
    ".txt": FileFormat.TEXT,
    ".md": FileFormat.MARKDOWN,
    ".markdown": FileFormat.MARKDOWN,
    ".csv": FileFormat.CSV,
    ".tsv": FileFormat.CSV,
}

# ZIP 압축 해제 총 크기 상한 (zip bomb 방어). 기본 500MB
MAX_UNCOMPRESSED_BYTES = 500 * 1024 * 1024


# 파일 앞부분을 읽어 매직바이트를 확인
def _read_head(path: Path, size: int = 16) -> bytes:
    with path.open("rb") as f:
        return f.read(size)


# ZIP 컨테이너 내부 구조를 보고 오피스 계열 포맷을 구분
def _detect_zip(path: Path) -> FileFormat:
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())

            # zip bomb 방어: 압축 해제 총 크기가 상한을 넘으면 파싱하지 않는다
            total = sum(info.file_size for info in zf.infolist())
            if total > MAX_UNCOMPRESSED_BYTES:
                return FileFormat.UNKNOWN

            for marker, fmt in _ZIP_MARKERS:
                if marker in names:
                    return fmt

            # HWPX는 구현체에 따라 content.hpf 위치가 다를 수 있어 mimetype으로 재확인
            if "mimetype" in names:
                mimetype = zf.read("mimetype").decode("ascii", errors="ignore").strip()
                if "hwp" in mimetype:
                    return FileFormat.HWPX

            # 경로 구분자나 대소문자가 다른 변종 대비
            lowered = {n.lower() for n in names}
            if any(n.startswith("contents/") and n.endswith(".hpf") for n in lowered):
                return FileFormat.HWPX
    except (zipfile.BadZipFile, KeyError, OSError):
        return FileFormat.UNKNOWN
    return FileFormat.UNKNOWN


# OLE 복합문서 내부 스트림 이름으로 구형 포맷을 구분
def _detect_ole(path: Path) -> FileFormat:
    try:
        with olefile.OleFileIO(path) as ole:
            streams = {"/".join(entry) for entry in ole.listdir()}

            # HWP 5.0: FileHeader 스트림 앞 32바이트에 "HWP Document File" 시그니처가 있다
            if "FileHeader" in streams:
                header = ole.openstream("FileHeader").read(32)
                if header.startswith(b"HWP Document File"):
                    return FileFormat.HWP

            for marker, fmt in _OLE_MARKERS:
                if marker in streams:
                    return fmt
    except (OSError, ValueError):
        return FileFormat.UNKNOWN
    return FileFormat.UNKNOWN


# 이미지 매직바이트 확인. WEBP는 RIFF....WEBP 형태라 별도 처리
def _is_image(head: bytes) -> bool:
    for offset, magic in _IMAGE_MAGICS:
        if head[offset : offset + len(magic)] == magic:
            return True
    return head[:4] == b"RIFF" and head[8:12] == b"WEBP"


# 바이트에 널문자가 없고 UTF-8/CP949로 디코드되면 텍스트로 간주
def _looks_like_text(path: Path) -> bool:
    sample = _read_head(path, 4096)
    if b"\x00" in sample:
        return False
    for encoding in ("utf-8", "cp949"):
        try:
            sample.decode(encoding)
            return True
        except UnicodeDecodeError:
            continue
    return False


# 업로드된 파일의 실제 포맷을 판별한다. 확장자는 텍스트 계열 구분에만 보조로 쓴다
def detect_format(path: Path, *, original_name: str | None = None) -> FileFormat:
    path = Path(path)
    if not path.is_file():
        return FileFormat.UNKNOWN

    head = _read_head(path)
    if not head:
        return FileFormat.UNKNOWN

    if head.startswith(_PDF_MAGIC):
        return FileFormat.PDF
    if head.startswith(_ZIP_MAGIC):
        return _detect_zip(path)
    if head.startswith(_OLE_MAGIC):
        return _detect_ole(path)
    if _is_image(head):
        return FileFormat.IMAGE

    # 여기까지 오면 바이너리 시그니처가 없다 -> 텍스트 계열인지 확인
    if _looks_like_text(path):
        suffix = Path(original_name or path.name).suffix.lower()
        return _TEXT_EXTENSIONS.get(suffix, FileFormat.TEXT)

    return FileFormat.UNKNOWN
