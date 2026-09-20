from enum import StrEnum


class ErrorCode(StrEnum):
    INVALID_INPUT = "INVALID_INPUT"
    COMMAND_NOT_FOUND = "COMMAND_NOT_FOUND"
    CAPABILITY_DENIED = "CAPABILITY_DENIED"
    INVALID_REFERENCE = "INVALID_REFERENCE"
    INVALID_OUTPUT = "INVALID_OUTPUT"
    ELEMENT_NOT_FOUND = "ELEMENT_NOT_FOUND"
    ELEMENT_AMBIGUOUS = "ELEMENT_AMBIGUOUS"
    # M28 S2 执行前预检的稳定分类：命中元素但不可安全操作时必须**显式失败**，
    # 绝不静默点到遮罩层/禁用控件上（否则「点错地方」会伪装成成功）。
    ELEMENT_COVERED = "ELEMENT_COVERED"
    ELEMENT_DISABLED = "ELEMENT_DISABLED"
    ELEMENT_NOT_VISIBLE = "ELEMENT_NOT_VISIBLE"
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
