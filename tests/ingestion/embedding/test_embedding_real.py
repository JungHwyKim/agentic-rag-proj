"""실제 문서로 토큰 수를 재는 회귀 테스트. **API 를 부르지 않는다** — tiktoken 만 쓴다.

합성 문서로는 "한국어 청크가 몇 토큰인가"를 보여줄 수 없다. 그런데 그 값이 세 가지를
결정한다 — 배치 크기, 문서당 토큰 상한, 그리고 `max_chunk_chars` 를 **문자로 세도 되는가**.

실측(2026-08-17)에서 나온 것:

- **문자/토큰 비율이 1.03~1.09 (중앙값)이고 최소가 0.90 이다.** 한국어 청크는 사실상 1:1 이라
  문자 상한과 토큰 상한이 거의 같은 뜻이 된다. 청크에 공백·숫자·영문·마크다운이 섞여 있어서다
- **청크 최대가 1,201토큰**으로 모델 입력 상한(8191)의 15% 다. 최악의 비율 0.90 을 써도
  1200자 청크는 1,333토큰이라 상한에 닿지 않는다
- **문서당 최대 51,341토큰**(학교기록물, 청크 102개)

tests/fixtures/ 에 실제 파일이 있을 때만 돌고 없으면 조용히 건너뛴다.
설정은 전부 명시해 넘긴다 — 개발자의 .env 가 상한이나 승격 여부를 덮으면 기대값이 흔들린다.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import tiktoken

from rag_pipeline.config import ContentRoutingConfig, IngestConfig, SplitterConfig
from rag_pipeline.ingestion.pipeline import ingest
from rag_pipeline.ingestion.splitter import split

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"

MODEL = "text-embedding-3-small"
# text-embedding-3-* 의 입력 상한
MODEL_TOKEN_LIMIT = 8191
CAP = 1200

DOCUMENTS = [
    "[DNA+_2019-5]_공공기관_신뢰가능_AI_구현_실용가이드.pdf",
# AI가이드 HWPX 는 2026-08-20 에 실데이터에서 뺐다 — 깨진 도형·표가 많다고 사용자가
# 판단했고 변환본을 다시 올리기로 했다. 그때 되돌린다
    "시행령 개정안 안내서(정보시스템의무사항알림).hwpx",
    "2025년 학교기록물 관리 지침.hwpx",
]

# 사람이 확인한 실측값. 청크 최대 토큰과 문서 전체 토큰을 함께 잠근다 —
# 앞쪽은 모델 상한 대비 여유를, 뒤쪽은 문서당 비용 상한의 근거를 지킨다
EXPECTED = {
    # 2026-08-19 에 19,346→19,789(+2.3%) 로 늘었다. PDF 병합 셀을 되풀이로 채운 만큼이고,
    # HWPX 쪽이 같은 이유로 늘어난 것과 짝을 이룬다. 중간에 19,807 이었던 적이 있는데, 그때는
    # 채움 판정이 "위 칸에 값이 있으면" 이라 위 칸이 실제로 여기까지 내려오지 않는 자리
    # 13칸까지 채우고 있었다 — 기하 판정으로 바꾸며 그만큼 빠졌다
    # 2026-08-19 에 한 번 더 19,789→19,982(+1.0%) 로 늘었다. 병합된 헤더 이름을 하위 열에
    # 채운 만큼이다 — 늘어난 토큰은 조각마다 열 이름이 살아 있게 된 대가다
    "[DNA+_2019-5]_공공기관_신뢰가능_AI_구현_실용가이드.pdf": {"max": 1137, "total": 19982},
    # 아래 둘은 2026-08-18 에 늘었다. HWPX 세로 병합 셀을 격자로 펴면서 가려져 있던 칸이
    # 값과 함께 살아났기 때문이다(25,333→27,256 / 51,341→52,592). 늘어난 토큰은 각 행이
    # 자기 단계·구분 값을 들고 있게 된 대가이고, 그게 없으면 행 하나만 검색됐을 때 어느
    # 단계인지 알 수 없다. 상한(200,000)에는 여전히 한참 못 미친다
    # 이 둘도 2026-08-19 에 병합 헤더 채움으로 한 번 더 늘었다(27,256→27,363 / 52,592→52,759)
    "시행령 개정안 안내서(정보시스템의무사항알림).hwpx": {"max": 1144, "total": 27363},
    "2025년 학교기록물 관리 지침.hwpx": {"max": 1201, "total": 52759},
}


# 그림을 건너뛰고 표도 로컬로만 파싱하는 설정. 외부 API 를 절대 부르지 않는다
@pytest.fixture
def offline_ingest_config() -> IngestConfig:
    config = IngestConfig(detect_headings=True)
    object.__setattr__(
        config,
        "_content",
        ContentRoutingConfig(
            openai_api_key=None,
            llama_cloud_api_key=None,
            table_pdf_handler="pymupdf",
            image_handler="skip",
            cache_enabled=False,
        ),
    )
    return config


# 기대값을 잰 그대로의 청킹 설정
@pytest.fixture
def splitter_config() -> SplitterConfig:
    return SplitterConfig(
        max_chunk_chars=CAP, chunk_overlap_chars=150, include_heading_in_text=True
    )


@pytest.fixture(scope="module")
def encoding():
    return tiktoken.encoding_for_model(MODEL)


# 파일이 있으면 청크의 토큰 수를 재서 돌려주고, 없으면 건너뛴다
def _token_counts(name, ingest_config, splitter_config, encoding) -> list[int]:
    path = FIXTURES / name
    if not path.is_file():
        pytest.skip(f"{name} 이(가) 없어 건너뜁니다.")
    chunks = split(ingest(path, config=ingest_config), splitter_config)
    if not chunks:
        pytest.skip(f"{name} 에서 청크가 나오지 않았습니다.")
    return [len(encoding.encode(chunk.text)) for chunk in chunks]


# 청크 하나가 모델 입력 상한을 넘으면 임베딩 자체가 실패한다. 문자로 세는 상한이
# 토큰 상한을 실제로 지켜주는지가 여기서 확인된다
@pytest.mark.parametrize("name", DOCUMENTS)
def test_no_chunk_exceeds_model_limit(
    name, offline_ingest_config, splitter_config, encoding
) -> None:
    counts = _token_counts(name, offline_ingest_config, splitter_config, encoding)

    assert max(counts) <= MODEL_TOKEN_LIMIT
    assert max(counts) == EXPECTED[name]["max"]


# 한국어 청크의 문자/토큰 비율. **이 값이 1 근처라는 것이 문자 기반 상한의 근거다** —
# 크게 떨어지면 1200자 청크가 토큰 상한을 넘기 시작한다
@pytest.mark.parametrize("name", DOCUMENTS)
def test_chars_per_token_stays_near_one(
    name, offline_ingest_config, splitter_config, encoding
) -> None:
    path = FIXTURES / name
    if not path.is_file():
        pytest.skip(f"{name} 이(가) 없어 건너뜁니다.")
    chunks = split(ingest(path, config=offline_ingest_config), splitter_config)
    ratios = [
        len(chunk.text) / len(encoding.encode(chunk.text))
        for chunk in chunks
        if encoding.encode(chunk.text)
    ]

    # 실측: 중앙값 1.03~1.09 / 최소 0.90. 최소가 0.5 밑으로 가면 문자 상한이 토큰 상한을
    # 더 이상 보장하지 못하므로 그 지점을 잠근다
    assert min(ratios) > 0.5
    assert 0.9 < sum(ratios) / len(ratios) < 1.5


# 문서당 토큰 합계. max_embedding_tokens_per_doc 기본값(200,000)의 근거다
@pytest.mark.parametrize("name", DOCUMENTS)
def test_document_total_tokens(
    name, offline_ingest_config, splitter_config, encoding
) -> None:
    counts = _token_counts(name, offline_ingest_config, splitter_config, encoding)

    assert sum(counts) == EXPECTED[name]["total"]
    assert sum(counts) < IngestConfig().embedding.max_embedding_tokens_per_doc
