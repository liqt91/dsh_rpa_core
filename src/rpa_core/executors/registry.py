from types import MappingProxyType

from .base import CommandExecutor


class ExecutorRegistry:
    def __init__(self, executors: dict[str, CommandExecutor]):
        self._executors = MappingProxyType(dict(executors))

    def get(self, name: str) -> CommandExecutor:
        try:
            return self._executors[name]
        except KeyError as exc:
            raise KeyError(f"Executor is not registered: {name}") from exc

    async def close(self) -> None:
        for executor in self._executors.values():
            await executor.close()
