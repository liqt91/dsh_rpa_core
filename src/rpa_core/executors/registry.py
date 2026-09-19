from types import MappingProxyType
from typing import Any

from .base import CommandExecutor


class ExecutorRegistry:
    def __init__(self, executors: dict[str, CommandExecutor]):
        self._executors = MappingProxyType(dict(executors))

    def get(self, name: str) -> CommandExecutor:
        try:
            return self._executors[name]
        except KeyError as exc:
            raise KeyError(f"Executor is not registered: {name}") from exc

    def restore_from_scopes(self, scopes: dict[str, Any]) -> None:
        """恢复钩子：让执行器从 checkpoint 快照重建跨进程可重建的资源绑定（M21）。

        执行器可选实现 `restore_from_scopes(scopes)`；未实现的跳过。钩子必须是
        纯内存、无副作用、失败即静默——恢复期的任何异常都不该让 resume 直接失败。
        """
        for executor in self._executors.values():
            restore = getattr(executor, "restore_from_scopes", None)
            if restore is None:
                continue
            try:
                restore(scopes)
            except Exception:  # noqa: BLE001 - 恢复是尽力而为，绝不阻断 resume
                continue

    async def close(self) -> None:
        for executor in self._executors.values():
            await executor.close()
