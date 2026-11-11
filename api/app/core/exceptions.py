"""Domain exceptions.

Services raise these exceptions; the HTTP mapping is centralized
in the exception handlers registered in `main.py`, keeping routers thin and
preventing internal details (SQL, stack traces) from reaching the client.
"""


class AppError(Exception):
    status_code: int = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class UnauthorizedError(AppError):
    status_code = 401


class ForbiddenError(AppError):
    status_code = 403


class NotFoundError(AppError):
    status_code = 404


class ConflictError(AppError):
    status_code = 409


class PayloadTooLargeError(AppError):
    status_code = 413


class UnsupportedMediaTypeError(AppError):
    status_code = 415


class ServiceUnavailableError(AppError):
    """External service failure (e.g. Azure Blob). Details go to the log."""

    status_code = 503

    def __init__(self, message: str, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class TooManyRequestsError(AppError):
    status_code = 429

    def __init__(self, message: str, retry_after: int) -> None:
        super().__init__(message)
        self.retry_after = retry_after
