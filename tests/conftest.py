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


# 큰 제목 한 줄과 작은 본문 여러 줄이 든 .pdf. 제목 승격 검증용.
# 본문 크기는 최빈값으로 정해지므로 본문 줄이 제목보다 많아야 한다
@pytest.fixture
def pdf_with_heading_file(tmp_path: Path) -> Path:
    document = pymupdf.open()
    page = document.new_page()

    _write_korean(page, (72, 90), "1. 지원 대상", size=22)
    for index, line in enumerate(
        ["중소기업이 해당한다.", "스타트업도 해당한다.", "연구기관도 포함한다."]
    ):
        _write_korean(page, (72, 140 + index * 24), line, size=10)

    path = tmp_path / "heading.pdf"
    document.save(str(path))
    document.close()
    return path


# 글자 크기가 다른 문단들이 든 .hwpx. header.xml 의 charPr 를 실제 한컴 파일처럼 넣는다.
# '강조' 문단은 본문 크기와 큰 크기를 섞어, 큰 글씨가 제목이 아니라 강조인 경우를 만든다
@pytest.fixture
def hwpx_with_heading(tmp_path: Path) -> Path:
    header = """<?xml version="1.0" encoding="UTF-8"?>
<hh:head xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head">
  <hh:refList>
    <hh:charProperties>
      <hh:charPr id="0" height="1000"/>
      <hh:charPr id="1" height="1600"/>
    </hh:charProperties>
  </hh:refList>
</hh:head>"""

    def run(text: str, char_pr: str) -> str:
        return f'<hp:run charPrIDRef="{char_pr}"><hp:t>{text}</hp:t></hp:run>'

    section = f"""<?xml version="1.0" encoding="UTF-8"?>
<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section"
        xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">
  <hp:p>{run("□ 개요", "1")}</hp:p>
  <hp:p>{run("첫 번째 본문입니다.", "0")}</hp:p>
  <hp:p>{run("두 번째 본문입니다.", "0")}</hp:p>
  <hp:p>{run("세 번째 본문입니다.", "0")}</hp:p>
  <hp:p>{run("작성 ", "0")}{run("홍길동", "1")}{run(" 수석", "0")}</hp:p>
</hs:sec>"""

    path = tmp_path / "heading.hwpx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/hwp+zip")
        zf.writestr("Contents/content.hpf", "<package/>")
        zf.writestr("Contents/header.xml", header)
        zf.writestr("Contents/section0.xml", section)
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


# 쪽 기하와 배치 정보가 든 .hwpx. 쪽을 넘기는 표의 쪽 계산을 검증하기 위한 픽스처.
# 용지 84188 - 여백(4251+2834+2834+2834) = 본문 71435 이 쓸 수 있는 높이다.
# 표는 vertpos 40000 에서 시작하므로 남은 공간은 31435 인데 실제 높이가 100000 이라
# 다음 쪽까지 넘어간다. hp:sz height 는 실제 한컴 파일처럼 남은 공간으로 잘린 값을 넣었다
@pytest.fixture
def hwpx_paged(tmp_path: Path) -> Path:
    cell = (
        '<hp:tc><hp:cellSz height="25000"/>'
        "<hp:subList><hp:p><hp:run><hp:t>{text}</hp:t></hp:run></hp:p></hp:subList></hp:tc>"
    )
    rows = "".join(
        f"<hp:tr>{cell.format(text=f'행{i}열1')}{cell.format(text=f'행{i}열2')}</hp:tr>"
        for i in range(1, 5)
    )

    section = f"""<?xml version="1.0" encoding="UTF-8"?>
<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section"
        xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">
  <hp:p>
    <hp:run>
      <hp:secPr>
        <hp:pagePr width="59528" height="84188">
          <hp:margin header="2834" footer="2834" left="5669" right="5669"
                     top="4251" bottom="2834"/>
        </hp:pagePr>
      </hp:secPr>
      <hp:t>표 앞 문단입니다.</hp:t>
    </hp:run>
    <hp:linesegarray><hp:lineseg vertpos="0"/></hp:linesegarray>
  </hp:p>
  <hp:p>
    <hp:run>
      <hp:tbl rowCnt="4" colCnt="2"><hp:sz height="31435"/>{rows}</hp:tbl>
    </hp:run>
    <hp:linesegarray><hp:lineseg vertpos="40000"/></hp:linesegarray>
  </hp:p>
  <hp:p>
    <hp:run><hp:t>표 뒤 문단입니다.</hp:t></hp:run>
    <hp:linesegarray><hp:lineseg vertpos="0"/></hp:linesegarray>
  </hp:p>
</hs:sec>"""

    path = tmp_path / "paged.hwpx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/hwp+zip")
        zf.writestr("Contents/content.hpf", "<package/>")
        zf.writestr("Contents/section0.xml", section)
    return path


# hwpx_paged 와 모든 수치가 같고 landscape="NARROWLY"(가로) 만 붙은 .hwpx.
# 가로 쪽은 세로 여유를 width 에서 얻어야 한다 — 59528-12753 = 46775 이 참값이고,
# height 를 쓰면 71435 로 53% 과대평가된다. 같은 표(높이 100000)가 세로에서는 2쪽,
# 가로에서는 3쪽을 먹으므로 두 픽스처의 기대값이 갈린다
@pytest.fixture
def hwpx_paged_landscape(tmp_path: Path) -> Path:
    cell = (
        '<hp:tc><hp:cellSz height="25000"/>'
        "<hp:subList><hp:p><hp:run><hp:t>{text}</hp:t></hp:run></hp:p></hp:subList></hp:tc>"
    )
    rows = "".join(
        f"<hp:tr>{cell.format(text=f'행{i}열1')}{cell.format(text=f'행{i}열2')}</hp:tr>"
        for i in range(1, 5)
    )

    section = f"""<?xml version="1.0" encoding="UTF-8"?>
<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section"
        xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">
  <hp:p>
    <hp:run>
      <hp:secPr>
        <hp:pagePr landscape="NARROWLY" width="59528" height="84188">
          <hp:margin header="2834" footer="2834" left="5669" right="5669"
                     top="4251" bottom="2834"/>
        </hp:pagePr>
      </hp:secPr>
      <hp:t>표 앞 문단입니다.</hp:t>
    </hp:run>
    <hp:linesegarray><hp:lineseg vertpos="0"/></hp:linesegarray>
  </hp:p>
  <hp:p>
    <hp:run>
      <hp:tbl rowCnt="4" colCnt="2"><hp:sz height="31435"/>{rows}</hp:tbl>
    </hp:run>
    <hp:linesegarray><hp:lineseg vertpos="40000"/></hp:linesegarray>
  </hp:p>
  <hp:p>
    <hp:run><hp:t>표 뒤 문단입니다.</hp:t></hp:run>
    <hp:linesegarray><hp:lineseg vertpos="0"/></hp:linesegarray>
  </hp:p>
</hs:sec>"""

    path = tmp_path / "paged_landscape.hwpx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/hwp+zip")
        zf.writestr("Contents/content.hpf", "<package/>")
        zf.writestr("Contents/section0.xml", section)
    return path


# hwpx_paged 와 같은 기하인데 마지막 행(2쪽에 놓인다)의 칸에 그림이 든 .hwpx.
# 표 안 그림은 시작 쪽 번호를 받으므로, 쪽을 넘긴 표에서는 page_end 로 범위를 남기는지 본다
@pytest.fixture
def hwpx_paged_with_image(tmp_path: Path) -> Path:
    cell = (
        '<hp:tc><hp:cellSz height="25000"/>'
        "<hp:subList><hp:p><hp:run><hp:t>{text}</hp:t></hp:run></hp:p></hp:subList></hp:tc>"
    )
    picture_cell = (
        '<hp:tc><hp:cellSz height="25000"/><hp:subList><hp:p><hp:run>'
        '<hp:pic><hc:img binaryItemIDRef="image1"/></hp:pic>'
        "</hp:run></hp:p></hp:subList></hp:tc>"
    )
    rows = "".join(
        f"<hp:tr>{cell.format(text=f'행{i}열1')}{cell.format(text=f'행{i}열2')}</hp:tr>"
        for i in range(1, 4)
    )
    rows += f"<hp:tr>{cell.format(text='행4열1')}{picture_cell}</hp:tr>"

    section = f"""<?xml version="1.0" encoding="UTF-8"?>
<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section"
        xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph"
        xmlns:hc="http://www.hancom.co.kr/hwpml/2011/core">
  <hp:p>
    <hp:run>
      <hp:secPr>
        <hp:pagePr width="59528" height="84188">
          <hp:margin header="2834" footer="2834" left="5669" right="5669"
                     top="4251" bottom="2834"/>
        </hp:pagePr>
      </hp:secPr>
      <hp:t>표 앞 문단입니다.</hp:t>
    </hp:run>
    <hp:linesegarray><hp:lineseg vertpos="0"/></hp:linesegarray>
  </hp:p>
  <hp:p>
    <hp:run>
      <hp:tbl rowCnt="4" colCnt="2"><hp:sz height="31435"/>{rows}</hp:tbl>
    </hp:run>
    <hp:linesegarray><hp:lineseg vertpos="40000"/></hp:linesegarray>
  </hp:p>
</hs:sec>"""

    buffer = io.BytesIO()
    Image.new("RGB", (400, 300), "white").save(buffer, format="PNG")

    path = tmp_path / "paged_image.hwpx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/hwp+zip")
        zf.writestr("Contents/content.hpf", "<package/>")
        zf.writestr("Contents/section0.xml", section)
        zf.writestr("BinData/image1.png", buffer.getvalue())
    return path


# 한 쪽에 들어가는 표 뒤에 vertpos 가 표 높이보다 작은 문단이 오는 .hwpx.
# 떠 있는 표(pos treatAsChar=0)는 뒤 문단의 vertpos 에 자기 높이가 반영되지 않는데,
# 이때 표 끝 위치로 쪽 경계를 판단하면 같은 쪽을 새 쪽으로 오인한다
# (실측: 학교기록물 지침의 22행 표 — 표 16046~44126, 뒤 문단 17966 이 같은 쪽이었다)
@pytest.fixture
def hwpx_floating_table(tmp_path: Path) -> Path:
    cell = (
        '<hp:tc><hp:cellSz height="14040"/>'
        "<hp:subList><hp:p><hp:run><hp:t>{text}</hp:t></hp:run></hp:p></hp:subList></hp:tc>"
    )
    rows = "".join(
        "<hp:tr>"
        + "".join(cell.format(text=f"행{i}열{j}") for j in range(1, 4))
        + "</hp:tr>"
        for i in range(1, 3)
    )

    section = f"""<?xml version="1.0" encoding="UTF-8"?>
<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section"
        xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">
  <hp:p>
    <hp:run>
      <hp:secPr>
        <hp:pagePr width="59528" height="84188">
          <hp:margin header="2834" footer="2834" left="5669" right="5669"
                     top="4251" bottom="2834"/>
        </hp:pagePr>
      </hp:secPr>
      <hp:t>표 캡션입니다.</hp:t>
    </hp:run>
    <hp:linesegarray><hp:lineseg vertpos="14000"/></hp:linesegarray>
  </hp:p>
  <hp:p>
    <hp:run>
      <hp:tbl rowCnt="2" colCnt="3">
        <hp:sz height="28080"/>
        <hp:pos treatAsChar="0" vertRelTo="PARA" vertAlign="TOP"/>
        {rows}
      </hp:tbl>
    </hp:run>
    <hp:linesegarray><hp:lineseg vertpos="16046"/></hp:linesegarray>
  </hp:p>
  <hp:p>
    <hp:run><hp:t>표 바로 뒤 문단입니다.</hp:t></hp:run>
    <hp:linesegarray><hp:lineseg vertpos="17966"/></hp:linesegarray>
  </hp:p>
</hs:sec>"""

    path = tmp_path / "floating.hwpx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/hwp+zip")
        zf.writestr("Contents/content.hpf", "<package/>")
        zf.writestr("Contents/section0.xml", section)
    return path


# 한글이 제목을 그릴 때 쓰는 껍데기 표(1칸 상자, 붙임 머리표, 인용 상자)와
# 진짜 데이터 표가 함께 든 .hwpx. 강등 규칙 검증용
@pytest.fixture
def hwpx_with_layout_boxes(tmp_path: Path) -> Path:
    def cell(text: str) -> str:
        return (
            "<hp:tc><hp:subList><hp:p><hp:run>"
            f"<hp:t>{text}</hp:t>"
            "</hp:run></hp:p></hp:subList></hp:tc>"
        )

    # 3x3 인용 상자 — 테두리를 그리려고 가운데 칸만 채운다
    quote = (
        "<hp:tbl rowCnt=\"3\" colCnt=\"3\">"
        # 꺾쇠는 실제 한컴 파일과 마찬가지로 XML 이스케이프된 형태로 들어있다
        f"<hp:tr>{cell('')}{cell('&lt; 근거 법령 &gt;')}{cell('')}</hp:tr>"
        f"<hp:tr>{cell('')}{cell('')}{cell('')}</hp:tr>"
        "</hp:tbl>"
    )
    data = (
        "<hp:tbl rowCnt=\"2\" colCnt=\"2\">"
        f"<hp:tr>{cell('이름')}{cell('점수')}</hp:tr>"
        f"<hp:tr>{cell('홍길동')}{cell('95')}</hp:tr>"
        "</hp:tbl>"
    )

    section = f"""<?xml version="1.0" encoding="UTF-8"?>
<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section"
        xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">
  <hp:p><hp:run>
    <hp:tbl rowCnt="1" colCnt="1"><hp:tr>{cell("Ⅰ. 개 요")}</hp:tr></hp:tbl>
  </hp:run></hp:p>
  <hp:p><hp:run>
    <hp:tbl rowCnt="1" colCnt="2">
      <hp:tr>{cell("붙임1")}{cell("관련 법률·시행령")}</hp:tr>
    </hp:tbl>
  </hp:run></hp:p>
  <hp:p><hp:run>{quote}</hp:run></hp:p>
  <hp:p><hp:run>{data}</hp:run></hp:p>
</hs:sec>"""

    path = tmp_path / "layout_boxes.hwpx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/hwp+zip")
        zf.writestr("Contents/content.hpf", "<package/>")
        zf.writestr("Contents/section0.xml", section)
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
