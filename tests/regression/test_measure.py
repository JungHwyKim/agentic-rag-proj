"""회귀 기준선 측정 도구의 단위 테스트.

**실파일이 필요 없는 것만 검사한다.** `tests/fixtures/` 는 gitignore 라 없는 환경이 있고,
그 환경에서도 이 테스트는 다 돌아야 한다 — 도구가 "픽스처가 없으면 조용히 skip 한다"를
지키는지 자체가 여기서 검사하는 항목이기 때문이다.

실제 문서를 재는 것이 맞는지는 `regression/baseline.json` 이 지킨다. 그쪽은 값이고
여기는 그 값을 만들어내는 규칙이다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from rag_pipeline.ingestion.loader.content.base import ContentType
from regression import measure
from regression.measure import DiffRow


# 리전 대역. count_by_type·pages_by_type 이 보는 두 필드만 있으면 된다
@dataclass
class FakeRegion:
    content_type: ContentType
    page: int | None = None


# MEASURE_SETTINGS 의 키가 실재하는 필드여야 한다. 설정 클래스가 전부 extra="ignore" 라
# 이름을 틀리면 예외 없이 그 항목만 조용히 안 먹고, 덮었다고 믿는 값이 실은 .env 를 탄다
def test_settings_names_are_real_fields():
    assert measure.unknown_setting_keys() == {}


# 고정 설정이 .env 를 이긴다. 안 그러면 같은 명령이 머신마다 다른 기준선을 낸다
def test_env_cannot_move_pinned_settings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TABLE_PDF_HANDLER", "llamaparse")
    monkeypatch.setenv("PDF_SCAN_TEXT_THRESHOLD", "999")

    config = measure.measure_config()

    assert config.content.table_pdf_handler == "pymupdf"
    assert config.content.pdf_scan_text_threshold == 10
    assert config.max_excel_rows == 5000


# detect_regions() 가 실제로 읽는 세 필드는 반드시 고정돼 있어야 한다.
# 이 셋이 빠지면 리전 수가 머신마다 달라지는데 대조에서는 '회귀' 로 보인다
@pytest.mark.parametrize(
    ("block", "field"),
    [
        ("ingest", "max_excel_rows"),
        ("content", "pdf_scan_text_threshold"),
        ("content", "pdf_render_dpi"),
    ],
)
def test_fields_that_reach_detect_regions_are_pinned(block: str, field: str):
    assert field in measure.MEASURE_SETTINGS[block]


# 노드 id 를 테스트가 든 디렉터리별로 접는다
def test_fold_by_area_groups_by_directory():
    node_ids = [
        "tests/ingestion/loader/document/test_pdf.py::test_a",
        "tests/ingestion/loader/document/test_pdf.py::test_b",
        "tests/ingestion/loader/document/test_hwpx.py::test_c",
        "tests/hooks/test_wiring.py::test_d",
    ]

    assert measure.fold_by_area(node_ids) == {
        "tests/ingestion/loader/document": 3,
        "tests/hooks": 1,
    }


# pytest 출력에는 노드 id 가 아닌 줄이 섞인다. 요약·빈 줄·경고를 세면 총계가 부풀어 오른다
def test_fold_by_area_ignores_non_node_lines():
    node_ids = [
        "tests/api/test_documents.py::test_upload",
        "",
        "655/662 tests collected (7 deselected) in 1.40s",
        "  warnings summary  ",
        "tests/api/test_documents.py",
    ]

    assert measure.fold_by_area(node_ids) == {"tests/api": 1}


# 파라미터 대괄호 안에 :: 가 들어와도 파일 경로는 첫 :: 앞이다
def test_fold_by_area_splits_on_first_separator():
    node_ids = ["tests/ingestion/splitter/test_split.py::test_x[a::b]"]

    assert measure.fold_by_area(node_ids) == {"tests/ingestion/splitter": 1}


# 윈도우 경로 구분자로 나와도 같은 영역이어야 한다
def test_fold_by_area_normalizes_backslashes():
    node_ids = [
        "tests\\hooks\\test_a.py::test_x",
        "tests/hooks/test_b.py::test_y",
    ]

    assert measure.fold_by_area(node_ids) == {"tests/hooks": 2}


# 값이 0인 종류도 남긴다. 빼면 '안 쟀다'로 읽힌다 — 표 0개인 변환본이 실제로 그 자리다
def test_count_by_type_keeps_zero_kinds():
    regions = [FakeRegion(ContentType.TEXT), FakeRegion(ContentType.TEXT)]

    assert measure.count_by_type(regions) == {"text": 2, "table": 0, "image": 0}


# 쪽 집합은 image·table 만, 정렬·중복 제거해서 낸다. 텍스트는 거의 모든 쪽에 있어 뺀다
def test_pages_by_type_sorts_and_dedups():
    regions = [
        FakeRegion(ContentType.TABLE, page=4),
        FakeRegion(ContentType.TABLE, page=1),
        FakeRegion(ContentType.TABLE, page=4),
        FakeRegion(ContentType.IMAGE, page=2),
        FakeRegion(ContentType.TEXT, page=9),
    ]

    assert measure.pages_by_type(regions) == {"image": [2], "table": [1, 4]}


# 쪽을 모르는 리전은 집합에서 뺀다. None 이 들어가면 정렬이 터진다
def test_pages_by_type_drops_unknown_page():
    regions = [FakeRegion(ContentType.TABLE, page=None), FakeRegion(ContentType.TABLE, page=3)]

    assert measure.pages_by_type(regions) == {"image": [], "table": [3]}


# 픽스처가 없으면 조용히 skip 한다. **없다고 해서 회귀가 아니다**
def test_missing_fixtures_are_skipped_not_raised(tmp_path):
    measured, missing = measure.measure_documents(["없는파일.pdf"], fixtures_dir=tmp_path)

    assert measured == {}
    assert missing == ["없는파일.pdf"]


# 픽스처 폴더 자체가 없어도 터지지 않는다 — 다른 클론의 기본 상태다
def test_absent_fixtures_dir_is_not_an_error(tmp_path):
    measured, missing = measure.measure_documents(["a.pdf"], fixtures_dir=tmp_path / "없음")

    assert measured == {}
    assert missing == ["a.pdf"]
    assert measure.extra_fixtures(["a.pdf"], fixtures_dir=tmp_path / "없음") == []


# 기준선에 없는 픽스처는 알려준다. 일부러 뺀 것일 수 있어 diff 가 아니라 알림이다
def test_extra_fixtures_lists_unknown_files(tmp_path):
    (tmp_path / "아는것.pdf").write_bytes(b"x")
    (tmp_path / "모르는것.pdf").write_bytes(b"x")

    assert measure.extra_fixtures(["아는것.pdf"], fixtures_dir=tmp_path) == ["모르는것.pdf"]


# 사람이 쓴 산문 키는 대조하지 않는다. 근거가 바뀌었다고 회귀가 아니다
def test_diff_entry_ignores_prose_keys():
    expected = {"regions_total": 10, "근거": "옛 설명"}
    actual = {"regions_total": 10, "근거": "새 설명"}

    assert measure.diff_entry("문서.pdf", expected, actual) == []


# 값이 달라지면 항목 · 필드 이름으로 한 줄
def test_diff_entry_reports_changed_value():
    rows = measure.diff_entry("문서.pdf", {"regions_total": 10}, {"regions_total": 12})

    assert rows == [DiffRow("문서.pdf · regions_total", "10", "12")]


# 기준선에 없던 필드는 '(기준선에 없음)' 으로 띄운다 — 0 이나 빈칸으로 뭉개면 안 된다
def test_diff_entry_marks_new_field():
    rows = measure.diff_entry("문서.pdf", {}, {"page_count": 3})

    assert rows == [DiffRow("문서.pdf · page_count", "(기준선에 없음)", "3")]


# dict 는 키 순서를 고정해 같은 값이 다르게 안 보이게 한다
def test_render_value_sorts_dict_keys():
    assert measure.render_value({"b": 1, "a": 2}) == json.dumps(
        {"a": 2, "b": 1}, ensure_ascii=False, sort_keys=True
    )


# 기준선에 아예 없는 문서는 한 줄로 띄운다
def test_diff_documents_flags_unknown_document():
    rows = measure.diff_documents({}, {"새문서.pdf": {"regions_total": 5}})

    assert rows == [DiffRow("새문서.pdf", "(기준선에 없음)", "리전 5")]


# 새 영역이 생기면 저절로 뜬다. 이것이 영역 목록을 안 박은 이유다
def test_diff_tests_flags_new_area():
    baseline = {"by_area": {"tests/hooks": 5}, "collected_total": 5}
    rows = measure.diff_tests(baseline, {"tests/hooks": 5, "tests/새영역": 3})

    assert DiffRow("tests/새영역", "(기준선에 없음)", "3") in rows
    assert DiffRow("collected_total", "5", "8") in rows


# 영역이 통째로 사라지면 '수집 안 됨' 이다. 0 으로 적으면 '테스트가 0개' 와 구분이 안 된다
def test_diff_tests_flags_vanished_area():
    baseline = {"by_area": {"tests/hooks": 5}, "collected_total": 5}
    rows = measure.diff_tests(baseline, {})

    assert DiffRow("tests/hooks", "5", "(수집 안 됨)") in rows


# 안 바뀌었으면 아무 줄도 안 낸다
def test_diff_tests_is_silent_when_unchanged():
    baseline = {"by_area": {"tests/hooks": 5}, "collected_total": 5}

    assert measure.diff_tests(baseline, {"tests/hooks": 5}) == []


# 달라진 것이 없으면 표 대신 한 줄. 안 바뀐 것을 나열하면 바뀐 것이 묻힌다
def test_render_says_nothing_changed():
    assert "달라진 항목 없음" in measure.render("documents", [])


# 달라진 것은 마크다운 표로
def test_render_builds_markdown_table():
    out = measure.render("tests", [DiffRow("tests/hooks", "141", "150")])

    assert "| 항목 | 기준선 | 현재 |" in out
    assert "| tests/hooks | 141 | 150 |" in out


# 이 도구는 기준선을 쓰지 않는다. 쓰기 진입점이 생기면 regression-test 4단계
# ("의도한 변화로 판정한 항목만 근거와 함께 고친다")가 통째로 무력화된다.
# **소스에 쓰기 호출이 없는지를 본다** — 이름만 검사하면 다른 이름으로 되살아난다
def test_tool_never_writes():
    source = Path(measure.__file__).read_text(encoding="utf-8")

    for forbidden in ("write_text", "write_bytes", "json.dump(", "save_baseline", "open("):
        assert forbidden not in source, f"measure.py 에 쓰기 경로가 생겼다: {forbidden}"


# 기준선 파일은 읽히기만 한다. 실제 파일로 한 번 확인한다
def test_load_baseline_reads_the_real_file():
    data = measure.load_baseline()

    assert data["tests"]["collected_total"] == sum(data["tests"]["by_area"].values())
    assert data["documents"], "기준선에 문서가 하나도 없다"
