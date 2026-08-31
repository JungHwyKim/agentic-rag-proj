"""회귀 기준선을 재고 `baseline.json` 과 대조한다.

## 왜 저장소에 있나

이 코드는 원래 매번 스크래치패드에 새로 짜였다가 버려졌다. 그래서 **재는 비용이 갱신을
미루게 하는 구조**였다 — `.claude/wiki/task_log/2026-08-23-loader-content-남은셋.md` §5 가
*"기준선이 그 자리인데 이번엔 안 돌렸다"* 로 회귀 방지선 한 칸을 비운 채 끝났고,
`tests` 블록은 519 에서 655 까지 136건이 밀렸다.

## 왜 기준선을 쓰지 않나

`--save-baseline` 을 일부러 안 만들었다. `regression-test` 4단계가 *"의도한 변화로 판정한
항목만 값과 근거를 함께 고친다"* 라 **부분 갱신이 기본**인데, 전체 덮어쓰기 버튼은 그 절차를
무력화한다 — 수치가 달라졌다고 자동으로 덮으면 기준선이 회귀를 따라다니게 되고 장치 전체가
무의미해진다. `evaluation/run_eval.py` 가 그 플래그를 가진 것은 저쪽 값이 **한 덩어리**라
통째로 굳혀도 되기 때문이고, 이쪽은 문서 10건이 각각 사람이 쓴 `근거` 문장을 갖고 있다.

**판정(의도한 변화 / 회귀 / 확인 필요)도 하지 않는다.** 달라진 것을 보여줄 뿐이다.
어느 쪽이 맞는지는 실제 문서를 열어 봐야 알고, 그건 사람 몫이다.

## 왜 설정을 못 박나

`detect_regions()` 는 실제로 설정을 읽는다. 지금 닿는 것은 셋이다 —
`max_excel_rows` · `pdf_scan_text_threshold` · `pdf_render_dpi`. 그중 뒤의 둘은 스캔 PDF 를
통째 이미지 리전으로 만들지를 가르므로 **리전 수를 직접 바꾼다.**

`IngestConfig()` 를 그냥 만들면 개발자 `.env` 를 타 같은 명령이 머신마다 다른 값을 낸다.
`evaluation/config.py` 가 같은 판단을 이미 했고 `tests/ingestion/indexing/test_indexing_real.py`
도 그렇다. **다만 `evaluation/` 을 import 하지는 않는다** — `regression/` 이 거기 의존하면
평가 쪽 설정 변경이 회귀 기준선을 조용히 움직인다.

닿는 셋만 못 박지 않고 넓게 못 박은 것은, 나중에 누가 `detect_regions()` 에서 다른 필드를
읽기 시작해도 `.env` 가 새어 들어오지 않게 하기 위해서다. 안 넣은 것은 **머신마다 달라야
하는 것**뿐이다 — API 키와 `tesseract_cmd`.

## `tests` 블록이 수집 수인 이유

`tests/fixtures/` 가 gitignore 라 다른 클론에는 없고, 실파일 테스트가 거기서 조용히 skip
된다. **skip 은 수집 뒤에 일어나므로** 수집 수는 환경과 무관하다. `passed`/`skipped` 를
영역별로 적으면 픽스처 없는 클론에서 여러 영역이 어긋나 항상 빨간 기준선이 된다.

**이것을 논증으로 두지 않고 실측했다**(2026-08-23, 픽스처를 안 복사한 워크트리). 처음 재보니
틀렸다 — `tests/ingestion/loader/document` 가 146 에서 **121** 로 25건 줄었다.
`test_hwpx_real.py` 만 `FIXTURES.glob("*.hwpx")` 로 parametrize 해 **수집 시점에** 파일
시스템을 봤기 때문이다. 실파일 모듈 다섯 중 그 하나만 그랬고 나머지 넷은 처음부터 정적 목록 +
런타임 skip 이었다. **그래서 그 모듈을 나머지와 같은 모양으로 바꿨고**(`COMMON_HWPX`),
지금은 hwpx 파일이 0개든 6개든 7개든 146 이다.

즉 이 지표는 이제 실제로 환경 독립이다. 되돌리면 그 성질이 조용히 깨지므로,
`baseline.json` 의 `_meta.픽스처 없는 환경` 이 그 경위를 함께 들고 있다.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rag_pipeline.config import ContentRoutingConfig, IngestConfig
from rag_pipeline.ingestion.loader.content.base import ContentType, LoadContext
from rag_pipeline.ingestion.loader.detect import detect_format
from rag_pipeline.ingestion.loader.document.hwp import UnsupportedLegacyLoader
from rag_pipeline.ingestion.loader.registry import get_loader

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = Path(__file__).resolve().parent / "baseline.json"
FIXTURES_DIR = PROJECT_ROOT / "tests" / "fixtures"

# 측정이 고정하는 값 전부. **`baseline.json` 의 `_meta.설정` 이 이것을 그대로 싣는다** —
# 기준선 파일만 보고도 어떤 설정으로 잰 값인지 알 수 있어야 하고, 두 곳에 손으로 적으면
# 한쪽이 조용히 낡는다. 여기 없는 것(API 키·tesseract 경로)은 일부러 뺀 것이다
MEASURE_SETTINGS: dict[str, dict[str, Any]] = {
    "ingest": {
        "max_excel_rows": 5000,
        "detect_headings": True,
    },
    # detect_regions() 가 실제로 읽는 것은 pdf_scan_text_threshold·pdf_render_dpi 둘뿐이다.
    # 나머지는 나중에 누가 다른 필드를 읽어도 `.env` 가 안 새게 하려고 함께 못 박은 것이다
    "content": {
        "table_pdf_handler": "pymupdf",
        "image_handler": "ocr",
        "vision_model": "gpt-4.1-mini",
        "max_vision_calls_per_doc": 30,
        "min_image_width": 100,
        "min_image_height": 100,
        "min_image_display_pt": 20.0,
        "max_image_aspect_ratio": 8.0,
        "ocr_lang": "kor+eng",
        "ocr_psm": 6,
        "ocr_preserve_spaces": True,
        "ocr_min_chars": 10,
        "ocr_min_confidence": 60.0,
        "pdf_scan_text_threshold": 10,
        "pdf_render_dpi": 300,
        "cache_enabled": False,
    },
}

# 기준선 항목에서 대조하지 않는 키. 사람이 쓴 산문이라 값 비교 대상이 아니다
_PROSE_KEYS = frozenset({"근거", "지원"})

# 쪽 번호 집합을 적는 리전 종류. 텍스트는 거의 모든 쪽에 있어 집합이 무의미하다
_PAGED_TYPES = (ContentType.IMAGE, ContentType.TABLE)


# 대조 결과 한 줄. 도구는 판정을 안 하므로 판정 칸이 없다
@dataclass(frozen=True)
class DiffRow:
    item: str
    baseline: str
    current: str


# 측정용 IngestConfig. lazy 프로퍼티를 명시 값으로 덮어 `.env` 가 결과를 못 흔들게 한다
def measure_config() -> IngestConfig:
    config = IngestConfig(**MEASURE_SETTINGS["ingest"])
    # `object.__setattr__` 로 덮는 것은 이 저장소 관례다 — lazy 프로퍼티가 `_content` 를
    # 캐시하므로 먼저 채워 두면 `.env` 를 읽는 기본 생성이 아예 안 일어난다
    object.__setattr__(config, "_content", ContentRoutingConfig(**MEASURE_SETTINGS["content"]))
    return config


# 설정 dict 의 키가 실제 필드명과 맞는지 본다. 설정 클래스가 전부 extra="ignore" 라
# 이름을 틀리면 예외 없이 그 항목만 조용히 안 먹고, 덮었다고 믿는 값이 실은 `.env` 를 탄다
def unknown_setting_keys() -> dict[str, list[str]]:
    known = {
        "ingest": set(IngestConfig.model_fields),
        "content": set(ContentRoutingConfig.model_fields),
    }
    return {
        block: sorted(set(values) - known[block])
        for block, values in MEASURE_SETTINGS.items()
        if set(values) - known[block]
    }


# 리전을 종류별로 센다. 값이 0인 종류도 남긴다 — 빼면 '안 쟀다'로 읽힌다
def count_by_type(regions: Iterable[Any]) -> dict[str, int]:
    counts = Counter(region.content_type.value for region in regions)
    return {kind.value: counts.get(kind.value, 0) for kind in ContentType}


# 리전이 **시작하는** 쪽 번호의 집합. 쪽을 넘기는 요소의 끝 쪽은 metadata["page_end"] 다
def pages_by_type(regions: Iterable[Any]) -> dict[str, list[int]]:
    buckets: dict[str, set[int]] = {kind.value: set() for kind in _PAGED_TYPES}
    for region in regions:
        if region.content_type.value in buckets and region.page is not None:
            buckets[region.content_type.value].add(region.page)
    return {kind: sorted(pages) for kind, pages in buckets.items()}


# 파일 하나를 detect_regions() 단독으로 재서 기준선 항목 모양으로 만든다.
# **외부 API 를 안 탄다 — 과금이 없다.** route_all() 을 부르지 않는 것이 그 이유다
def measure_document(path: Path, config: IngestConfig | None = None) -> dict[str, Any]:
    path = Path(path)
    config = config or measure_config()
    file_format = detect_format(path)

    entry: dict[str, Any] = {
        "format": file_format.value,
        "size_bytes": path.stat().st_size,
    }

    loader = get_loader(file_format, config)
    # 레거시 포맷은 리전 감지까지 가지 않는다. 여기 있는 이유는 '감지는 되는가' 하나다
    if isinstance(loader, UnsupportedLegacyLoader):
        entry["지원"] = False
        return entry

    ctx = LoadContext(config=config.content)
    regions = loader.detect_regions(path, ctx)
    # page_count 는 detect_regions() 가 아니라 여기서 온다 (document/base.py:load 참고)
    extra = loader.extract_metadata(path)

    entry["page_count"] = extra.get("page_count")
    entry["regions_total"] = len(regions)
    entry["regions_by_type"] = count_by_type(regions)
    entry["pages_by_type"] = pages_by_type(regions)
    entry["warnings"] = list(ctx.warnings)
    return entry


# 기준선에 적힌 문서 전부를 잰다. **없는 파일은 조용히 skip 한다** — tests/fixtures/ 가
# gitignore 라 없는 환경이 있고, 없다고 해서 회귀가 아니다
def measure_documents(
    names: Iterable[str],
    fixtures_dir: Path | None = None,
    config: IngestConfig | None = None,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    fixtures_dir = fixtures_dir or FIXTURES_DIR
    config = config or measure_config()

    measured: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    for name in names:
        path = fixtures_dir / name
        if not path.is_file():
            missing.append(name)
            continue
        measured[name] = measure_document(path, config)
    return measured, missing


# 기준선에 없는데 픽스처 폴더에 있는 파일. 일부러 뺀 것일 수도 있어 diff 가 아니라 알림이다
def extra_fixtures(names: Iterable[str], fixtures_dir: Path | None = None) -> list[str]:
    fixtures_dir = fixtures_dir or FIXTURES_DIR
    if not fixtures_dir.is_dir():
        return []
    known = set(names)
    return sorted(p.name for p in fixtures_dir.iterdir() if p.is_file() and p.name not in known)


# pytest 노드 id 목록을 테스트가 든 디렉터리별로 접는다.
# **영역 목록을 어디에도 안 박는 것이 요점이다** — 고정 목록이면 새 영역이 생겨도 아무
# 말을 안 하고, 실제로 tests/hooks 와 tests/evaluation 이 근거 서사에 한 번도 안 나왔다
def fold_by_area(node_ids: Iterable[str]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for raw in node_ids:
        line = raw.strip().replace("\\", "/")
        if "::" not in line:
            continue
        file_part = line.split("::", 1)[0]
        if not file_part.endswith(".py") or "/" not in file_part:
            continue
        counts[file_part.rsplit("/", 1)[0]] += 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


# 수집만 하고 실행하지 않는다(1~2초). 여기서 재는 것이 수집 수인 이유는 모듈 docstring 에
def collect_tests(root: Path | None = None) -> dict[str, int]:
    root = root or PROJECT_ROOT
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--collect-only"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"pytest --collect-only 가 실패했다:\n{result.stdout}\n{result.stderr}")
    return fold_by_area(result.stdout.splitlines())


# 값 하나를 표에 넣을 문자열로. dict 는 키 순서를 고정해 같은 값이 다르게 안 보이게 한다
def render_value(value: Any) -> str:
    if value is None:
        return "(없음)"
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


# 기준선 항목과 측정 항목을 필드별로 대조한다. 사람이 쓴 산문 키는 뺀다
def diff_entry(name: str, expected: dict[str, Any], actual: dict[str, Any]) -> list[DiffRow]:
    rows: list[DiffRow] = []
    for key in sorted(set(expected) | set(actual)):
        if key in _PROSE_KEYS:
            continue
        want = expected.get(key)
        got = actual.get(key)
        if want == got:
            continue
        label = "(기준선에 없음)" if key not in expected else render_value(want)
        rows.append(DiffRow(f"{name} · {key}", label, render_value(got)))
    return rows


# documents 블록 전체 대조
def diff_documents(
    baseline: dict[str, dict[str, Any]], current: dict[str, dict[str, Any]]
) -> list[DiffRow]:
    rows: list[DiffRow] = []
    for name, actual in current.items():
        expected = baseline.get(name)
        if expected is None:
            rows.append(DiffRow(name, "(기준선에 없음)", f"리전 {actual.get('regions_total')}"))
            continue
        rows.extend(diff_entry(name, expected, actual))
    return rows


# tests 블록 대조. 영역이 새로 생기거나 사라진 것도 한 줄로 낸다
def diff_tests(baseline: dict[str, Any], current: dict[str, int]) -> list[DiffRow]:
    expected = baseline.get("by_area") or {}
    rows: list[DiffRow] = []
    for area in sorted(set(expected) | set(current)):
        want = expected.get(area)
        got = current.get(area)
        if want == got:
            continue
        rows.append(
            DiffRow(
                area,
                "(기준선에 없음)" if want is None else str(want),
                "(수집 안 됨)" if got is None else str(got),
            )
        )

    want_total = baseline.get("collected_total")
    got_total = sum(current.values())
    if want_total != got_total:
        rows.append(DiffRow("collected_total", render_value(want_total), str(got_total)))
    return rows


# 달라진 항목만 표로. 안 바뀐 것을 나열하면 바뀐 것이 묻힌다
def render(title: str, rows: list[DiffRow]) -> str:
    if not rows:
        return f"## {title}\n\n달라진 항목 없음.\n"

    head = f"## {title}\n\n| 항목 | 기준선 | 현재 |\n|---|---|---|\n"
    body = "".join(f"| {row.item} | {row.baseline} | {row.current} |\n" for row in rows)
    return head + body


# 기준선을 읽는다. 쓰지 않는다
def load_baseline(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or BASELINE_PATH).read_text(encoding="utf-8"))


# 명령행 진입점. 달라진 것이 있으면 1로 끝나지만 그것은 '달라졌다'이지 '회귀'가 아니다
def main() -> int:
    parser = argparse.ArgumentParser(
        description="회귀 기준선을 재서 baseline.json 과 대조한다. 기준선을 쓰지는 않는다."
    )
    parser.add_argument(
        "--json", action="store_true", help="대조 대신 잰 값만 JSON 으로 낸다(붙여넣기용)"
    )
    parser.add_argument(
        "--skip-tests", action="store_true", help="tests 블록을 안 잰다(문서만 볼 때)"
    )
    args = parser.parse_args()

    bad = unknown_setting_keys()
    if bad:
        print(f"MEASURE_SETTINGS 에 실재하지 않는 필드가 있다: {bad}", file=sys.stderr)
        return 2

    baseline = load_baseline()
    names = list(baseline.get("documents", {}))
    measured, missing = measure_documents(names)
    tests = {} if args.skip_tests else collect_tests()

    if args.json:
        payload: dict[str, Any] = {"_meta": {"설정": MEASURE_SETTINGS}, "documents": measured}
        if not args.skip_tests:
            payload["tests"] = {"by_area": tests, "collected_total": sum(tests.values())}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    rows = diff_documents(baseline.get("documents", {}), measured)
    print(render(f"documents ({len(measured)}건 잼)", rows))

    test_rows: list[DiffRow] = []
    if not args.skip_tests:
        test_rows = diff_tests(baseline.get("tests", {}), tests)
        print(render(f"tests ({sum(tests.values())}건 수집)", test_rows))

    if missing:
        print(f"\n픽스처가 없어 못 잰 문서 {len(missing)}건 (회귀가 아니다):")
        for name in missing:
            print(f"  - {name}")

    unknown = extra_fixtures(names)
    if unknown:
        print(f"\n기준선에 없는 픽스처 {len(unknown)}건 (일부러 뺀 것인지 _meta.갱신 이력 확인):")
        for name in unknown:
            print(f"  - {name}")

    changed = rows + test_rows
    if changed:
        print(f"\n달라진 항목 {len(changed)}개. **회귀인지 의도한 변화인지는 사람이 판정한다** —")
        print("절차는 `regression-test` 스킬 3단계에 있다. 이 도구는 기준선을 고치지 않는다.")
    return 1 if changed else 0


if __name__ == "__main__":
    raise SystemExit(main())
