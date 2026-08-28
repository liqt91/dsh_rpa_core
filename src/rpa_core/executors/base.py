import asyncio
from abc import ABC, abstractmethod

from rpa_core.model.command import CommandInvocation, CommandResult


class CommandExecutor(ABC):
    @abstractmethod
    async def execute(
        self, invocation: CommandInvocation, cancellation: asyncio.Event
    ) -> CommandResult:
        raise NotImplementedError

    async def close(self) -> None:
        return None
