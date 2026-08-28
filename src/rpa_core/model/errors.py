from enum import StrEnum


class ErrorCode(StrEnum):
    INVALID_INPUT = "INVALID_INPUT"
    COMMAND_NOT_FOUND = "COMMAND_NOT_FOUND"
    CAPABILITY_DENIED = "CAPABILITY_DENIED"
    INVALID_REFERENCE = "INVALID_REFERENCE"
    INVALID_OUTPUT = "INVALID_OUTPUT"
    ELEMENT_NOT_FOUND = "ELEMENT_NOT_FOUND"
    ELEMENT_AMBIGUOUS = "ELEMENT_AMBIGUOUS"
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    SESSION_LOST = "SESSION_LOST"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    EXECUTOR_FAILED = "EXECUTOR_FAILED"
    SCRIPT_FAILED = "SCRIPT_FAILED"
    PERSISTENCE_FAILED = "PERSISTENCE_FAILED"
    PLATFORM_UNSUPPORTED = "PLATFORM_UNSUPPORTED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class RpaError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        retryable: bool = False,
        details: dict | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}
