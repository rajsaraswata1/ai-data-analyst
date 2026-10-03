"""Application errors. Users see `message`; details go to the log only."""
from __future__ import annotations


class AppError(Exception):
    def __init__(self, message: str, status: int = 400, code: str = "bad_request", detail: str = ""):
        super().__init__(message)
        self.message, self.status, self.code, self.detail = message, status, code, detail


class ValidationError(AppError):
    def __init__(self, message: str, detail: str = ""):
        super().__init__(message, 400, "validation_error", detail)


class UnsafeSQLError(AppError):
    def __init__(self, message: str, detail: str = ""):
        super().__init__(message, 400, "unsafe_sql", detail)


class NotFoundError(AppError):
    def __init__(self, message: str = "Not found"):
        super().__init__(message, 404, "not_found")


class UnsupportedQuestion(AppError):
    def __init__(self, message: str):
        super().__init__(message, 200, "unsupported_question")
