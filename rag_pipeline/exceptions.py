"""파이프라인 전역 예외. backend가 이 타입들을 HTTP 상태코드로 매핑한다."""

from __future__ import annotations


# 파이프라인에서 발생하는 모든 예외의 최상위. user_message는 그대로 사용자에게 노출된다
class RagPipelineError(Exception):
    def __init__(self, message: str, *, user_message: str | None = None) -> None:
        super().__init__(message)
        self.user_message = user_message or message


# 파싱을 지원하지 않는 포맷 (HTTP 415)
class UnsupportedFormatError(RagPipelineError):
    def __init__(
        self, message: str, *, user_message: str | None = None, detected: str = ""
    ) -> None:
        super().__init__(message, user_message=user_message)
        self.detected = detected


# 파일이 손상됐거나 구조가 예상과 달라 파싱에 실패 (HTTP 422)
class CorruptedFileError(RagPipelineError):
    pass


# 업로드 크기 상한 초과 (HTTP 413)
class FileTooLargeError(RagPipelineError):
    pass


# ZIP 폭탄 등 압축 해제 크기가 상한을 넘음 (HTTP 413)
class DecompressionLimitError(RagPipelineError):
    pass


# 임베딩을 만들지 못했다 — 키 없음 / API 실패 / 응답 개수 불일치 / 토큰 상한 (HTTP 503)
#
# **왜 예외인가.** 다른 외부 API 핸들러는 빈 결과를 돌려주고 상위 폴백에 맡기지만
# (LlamaParse -> PyMuPDF, Vision -> OCR), 임베딩에는 내려갈 층이 없다. 빈 벡터를 돌려주면
# 그 청크는 영원히 검색되지 않는데 아무도 모른다 — 그 규약이 막으려던 상황이 규약을 지켜서
# 일어난다. 근거는 plan_log/embedding.md 의 "실패 계약" 절
class EmbeddingError(RagPipelineError):
    pass


# 색인에 실패했다 — 청크/벡터 개수 불일치 / 차원 불일치 / 저장 실패 / 잘못된 top_k (HTTP 503)
#
# **왜 예외인가.** EmbeddingError 와 같은 이유이고 한 칸 밀린 같은 상황이다. 여기서 부분
# 성공을 조용히 허용하면 저장 안 된 청크가 영원히 검색되지 않는데 아무도 모른다. 폴백 층이
# 있는 로더 쪽(LlamaParse -> PyMuPDF)과 달리 indexing 에는 내려갈 곳이 없다.
#
# 하위 타입으로 가르지 않는 것도 embedding 과 같은 판단이다 — _status_for() 가 isinstance 로
# 표를 훑으므로 종류를 늘리면 순서 문제만 는다. 무엇이 틀렸는지는 user_message 가 말한다
class IndexingError(RagPipelineError):
    pass
