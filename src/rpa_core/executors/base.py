import asyncio
from abc import ABC, abstractmethod
from typing import Any

from rpa_core.model.command import CommandInvocation, CommandResult


def resolve_session_id(
    requested: Any,
    sessions: dict[str, Any],
    last_active: str | None = None,
) -> str:
    """解析会话 id：显式指定 > 最近激活 > 唯一会话。

    会话类命令（除 attach/close 等生命周期命令外）允许省略 sessionId，
    此时默认作用于最近激活的会话；只在存在多个候选且无激活记录时返回空串，
    由调用方报 SESSION_NOT_FOUND，避免歧义命中。
    """
    explicit = str(requested or "").strip()
    if explicit:
        return explicit
    if last_active and last_active in sessions:
        return last_active
    if len(sessions) == 1:
        return next(iter(sessions))
    return ""


class CommandExecutor(ABC):
    @abstractmethod
    async def execute(
        self, invocation: CommandInvocation, cancellation: asyncio.Event
    ) -> CommandResult:
        raise NotImplementedError

    async def close(self) -> None:
        return None
