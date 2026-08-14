"""테스트용 샘플 파일을 코드로 생성한다.

바이너리를 저장소에 커밋하지 않아 깔끔하고, 기대값을 정확히 알 수 있다.
(.hwpx 만은 실제 한컴 산출물과 구조가 다를 수 있어 합성 파일임을 감안해야 한다.)
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import openpyxl
import pymupdf
import pytest
from docx import Document
from PIL import Image, ImageDraw, ImageFont
from pptx import Presentation
from pptx.util import Inches

from rag_pipeline.config import ContentRoutingConfig, IngestConfig

# 한글 렌더링용 폰트 후보. 없으면 OCR 관련 테스트는 건너뛴다
_KOREAN_FONTS = [
    Path(r"C:\Windows\Fonts\malgun.ttf"),
    Path(r"C:\Windows\Fonts\gulim.ttc"),
    Path(r"C:\Windows\Fonts\batang.ttc"),
]


# 한글을 그릴 수 있는 폰트를 찾는다. 없으면 None
def _korean_font(size: int = 48) -> ImageFont.FreeTypeFont | None:
    for path in _KOREAN_FONTS:
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), size)
            except OSError:
                continue
    return None


# 외부 API를 절대 호출하지 않는 설정. 단위 테스트 기본값
@pytest.fixture
def offline_config() -> IngestConfig:
    config = IngestConfig()
    object.__setattr__(
        config,
        "_content",
        ContentRoutingConfig(
            openai_api_key=None,
            llama_cloud_api_key=None,
            table_pdf_handler="pymupdf",
            image_handler="ocr",
            cache_enabled=False,
        ),
    )
    return config


# 제목 -> 문단 -> 표 -> 문단 순서로 들어간 .docx. 순서 보존 검증용
@pytest.fixture
def docx_file(tmp_path: Path) -> Path:
    document = Document()
    document.add_heading("연간 보고서", level=1)
    document.add_paragraph("첫 번째 문단입니다.")

    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "이름"
    table.cell(0, 1).text = "점수"
    table.cell(1, 0).text = "홍길동"
    table.cell(1, 1).text = "95"

    document.add_paragraph("표 다음에 오는 문단입니다.")

    path = tmp_path / "sample.docx"
    document.save(str(path))
    return path


# 제목·본문·표·발표자 노트가 있는 .pptx
@pytest.fixture
def pptx_file(tmp_path: Path) -> Path:
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[5])
    slide.shapes.title.text = "분기 실적"

    box = slide.shapes.add_textbox(Inches(1), Inches(2), Inches(4), Inches(1))
    box.text_frame.text = "매출이 증가했습니다."

    shape = slide.shapes.add_table(2, 2, Inches(1), Inches(3), Inches(4), Inches(1))
    cells = shape.table
    cells.cell(0, 0).text = "분기"
    cells.cell(0, 1).text = "매출"
    cells.cell(1, 0).text = "1분기"
    cells.cell(1, 1).text = "100억"

    slide.notes_slide.notes_text_frame.text = "발표자 노트입니다."

    path = tmp_path / "sample.pptx"
    presentation.save(str(path))
    return path


# 시트 2개짜리 .xlsx
@pytest.fixture
def xlsx_file(tmp_path: Path) -> Path:
    workbook = openpyxl.Workbook()
    first = workbook.active
    first.title = "매출"
    first.append(["월", "금액"])
    first.append(["1월", 1000])
    first.append(["2월", 2000])

    second = workbook.create_sheet("비용")
    second.append(["항목", "금액"])
    second.append(["임대료", 500])

    path = tmp_path / "sample.xlsx"
    workbook.save(str(path))
    return path


# PDF 페이지에 한글을 쓴다. TTF를 임베드해야 나중에 텍스트로 다시 추출된다.
# 내장 CJK 폰트(china-s)로 쓰면 글자는 보이지만 추출이 안 돼 스캔본으로 오인된다
def _write_korean(page, position: tuple[float, float], text: str, size: int = 14) -> None:
    font_path = next((p for p in _KOREAN_FONTS if p.is_file()), None)
    if font_path is None:
        pytest.skip("한글 폰트를 찾지 못해 PDF 테스트를 건너뜁니다.")
    page.insert_font(fontname="kfont", fontfile=str(font_path))
    page.insert_text(position, text, fontname="kfont", fontsize=size)


# 한글 텍스트가 들어간 2쪽짜리 .pdf
@pytest.fixture
def pdf_file(tmp_path: Path) -> Path:
    document = pymupdf.open()
    lines = [
        ["첫째 쪽 내용입니다.", "이 문서는 로더 테스트를 위해 만들었습니다."],
        ["둘째 쪽 내용입니다.", "두 번째 쪽에도 본문이 이어집니다."],
    ]
    for page_lines in lines:
        page = document.new_page()
        for offset, text in enumerate(page_lines):
            _write_korean(page, (72, 100 + offset * 30), text)

    path = tmp_path / "sample.pdf"
    document.save(str(path))
    document.close()
    return path


# 본문과 괘선 표가 함께 있는 .pdf. PDF 표 라우팅 검증용
@pytest.fixture
def pdf_with_table_file(tmp_path: Path) -> Path:
    document = pymupdf.open()
    page = document.new_page()

    _write_korean(page, (72, 80), "아래는 성적표입니다.")

    left, top, col_w, row_h = 72.0, 120.0, 120.0, 28.0
    cols, rows = 2, 3
    # 괘선을 그려야 find_tables 가 표로 인식한다
    for r in range(rows + 1):
        y = top + r * row_h
        page.draw_line((left, y), (left + cols * col_w, y))
    for c in range(cols + 1):
        x = left + c * col_w
        page.draw_line((x, top), (x, top + rows * row_h))

    cells = [["이름", "점수"], ["홍길동", "95"], ["김철수", "88"]]
    for r, row in enumerate(cells):
        for c, value in enumerate(row):
            _write_korean(page, (left + c * col_w + 8, top + r * row_h + 19), value, size=12)

    path = tmp_path / "table.pdf"
    document.save(str(path))
    document.close()
    return path


# cp949로 저장한 텍스트 파일. 한국어 인코딩 자동 감지 검증용
@pytest.fixture
def cp949_text_file(tmp_path: Path) -> Path:
    path = tmp_path / "sample.txt"
    content = "첫 번째 문단입니다.\n\n두 번째 문단입니다."
    path.write_bytes(content.encode("cp949"))
    return path


# 쉼표 구분 CSV
@pytest.fixture
def csv_file(tmp_path: Path) -> Path:
    path = tmp_path / "sample.csv"
    path.write_text("이름,점수\n홍길동,95\n김철수,88\n", encoding="utf-8")
    return path


# 한글 글자가 크게 그려진 PNG. OCR 경로 검증용
@pytest.fixture
def korean_image_file(tmp_path: Path) -> Path:
    font = _korean_font(48)
    if font is None:
        pytest.skip("한글 폰트를 찾지 못해 OCR 테스트를 건너뜁니다.")

    image = Image.new("RGB", (900, 320), "white")
    draw = ImageDraw.Draw(image)
    draw.text((40, 60), "대한민국 서울특별시입니다", fill="black", font=font)
    draw.text((40, 160), "문서 스캔본 예시 문장", fill="black", font=font)

    path = tmp_path / "sample.png"
    image.save(str(path))
    return path


# 글자가 거의 없는 그림 이미지. classify가 Vision 쪽으로 보내야 하는 케이스
@pytest.fixture
def chart_like_image_file(tmp_path: Path) -> Path:
    image = Image.new("RGB", (600, 400), "white")
    draw = ImageDraw.Draw(image)
    for index, height in enumerate([120, 200, 280, 160]):
        x = 60 + index * 120
        draw.rectangle([x, 400 - height, x + 70, 380], fill=(60, 120, 200))
    path = tmp_path / "chart.png"
    image.save(str(path))
    return path


# 로고처럼 아주 작은 이미지. min_image_size 필터 검증용
@pytest.fixture
def tiny_image_file(tmp_path: Path) -> Path:
    image = Image.new("RGB", (40, 40), "red")
    path = tmp_path / "logo.png"
    image.save(str(path))
    return path


# 문단 2개와 표 1개가 든 합성 .hwpx
@pytest.fixture
def hwpx_file(tmp_path: Path) -> Path:
    section = """<?xml version="1.0" encoding="UTF-8"?>
<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section"
        xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">
  <hp:p><hp:run><hp:t>첫 번째 문단입니다.</hp:t></hp:run></hp:p>
  <hp:p><hp:run><hp:t>두 번째 문단입니다.</hp:t></hp:run></hp:p>
  <hp:p><hp:run>
    <hp:tbl>
      <hp:tr>
        <hp:tc><hp:subList><hp:p><hp:run><hp:t>이름</hp:t></hp:run></hp:p></hp:subList></hp:tc>
        <hp:tc><hp:subList><hp:p><hp:run><hp:t>점수</hp:t></hp:run></hp:p></hp:subList></hp:tc>
      </hp:tr>
      <hp:tr>
        <hp:tc><hp:subList><hp:p><hp:run><hp:t>홍길동</hp:t></hp:run></hp:p></hp:subList></hp:tc>
        <hp:tc><hp:subList><hp:p><hp:run><hp:t>95</hp:t></hp:run></hp:p></hp:subList></hp:tc>
      </hp:tr>
    </hp:tbl>
  </hp:run></hp:p>
</hs:sec>"""

    path = tmp_path / "sample.hwpx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/hwp+zip")
        zf.writestr("Contents/content.hpf", "<package/>")
        zf.writestr("Contents/section0.xml", section)
    return path


# 표 셀 안에 그림이 든 .hwpx. 셀 안 그림이 누락되지 않는지 확인하는 회귀 픽스처
@pytest.fixture
def hwpx_with_image_in_table(tmp_path: Path) -> Path:
    section = """<?xml version="1.0" encoding="UTF-8"?>
<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section"
        xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph"
        xmlns:hc="http://www.hancom.co.kr/hwpml/2011/core">
  <hp:p><hp:run><hp:t>표 앞 문단입니다.</hp:t></hp:run></hp:p>
  <hp:p><hp:run>
    <hp:tbl>
      <hp:tr>
        <hp:tc><hp:subList><hp:p><hp:run><hp:t>사진</hp:t></hp:run></hp:p></hp:subList></hp:tc>
        <hp:tc><hp:subList><hp:p><hp:run>
          <hp:pic><hc:img binaryItemIDRef="image1"/></hp:pic>
        </hp:run></hp:p></hp:subList></hp:tc>
      </hp:tr>
    </hp:tbl>
  </hp:run></hp:p>
</hs:sec>"""

    buffer = io.BytesIO()
    Image.new("RGB", (400, 300), "white").save(buffer, format="PNG")

    path = tmp_path / "table_image.hwpx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/hwp+zip")
        zf.writestr("Contents/content.hpf", "<package/>")
        zf.writestr("Contents/section0.xml", section)
        zf.writestr("BinData/image1.png", buffer.getvalue())
    return path


# 어떤 포맷 시그니처와도 맞지 않는 바이너리. UNKNOWN 판정 검증용
@pytest.fixture
def unknown_binary_file(tmp_path: Path) -> Path:
    path = tmp_path / "mystery.pdf"
    path.write_bytes(bytes(range(8)) * 64)
    return path


# 이미지 바이트를 만들어주는 헬퍼. 핸들러 단위 테스트에서 쓴다
@pytest.fixture
def png_bytes():
    def _make(width: int = 400, height: int = 200, color: str = "white") -> bytes:
        buffer = io.BytesIO()
        Image.new("RGB", (width, height), color).save(buffer, format="PNG")
        return buffer.getvalue()

    return _make
