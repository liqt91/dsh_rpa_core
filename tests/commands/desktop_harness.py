"""桌面通道 L1 矩阵的共享装配：真靶子、会话与元素预置、占位符物化。

## 为什么这里是真的桌面靶子，而不是打桩 pywinauto / Win32 绑定层

维护者定案（2026-09-22，M38 S2）：桌面通道的 L1 走**真桌面 fixture**——理由是
「绑定层桩掉之后只剩『桩被怎么调用』，而桌面侧要的证据本来就在真窗口上」：错误码、
会话表、元素解析都要有真窗口才成立。这与浏览器通道刻意不同（那边打桩在最底层
`_exchange`，因为它的可测面就是通道协议，见 `test_browser_matrix.py`）。

代价是本通道要真实开窗并抢前台，所以**缺省跳过**（沿用 `tests/e2e` 的同款约定：
`RPA_DESKTOP_E2E=1`）。

## 变体怎么拿到会话与元素

单命令表里的变体大多需要「已经 attach 好的会话」和「已经找到的元素」，否则只能测
负路径。装配按变体的 `setup` **现算**，再把结果注入 `inputs` 里的占位符：

    {"setup": {"find": {"input": {...locator...}}},
     "inputs": {"sessionId": "{session}", "elementId": "{element:input}"}}

- `setup.session`：`shared`（默认，本变体自建一个会话）/ `none`（不建——
  `attachWindow` / `getWindowList` 自己就是建会话或不需要会话的命令）。
- `setup.attach`：覆盖 attach 的入参（默认按靶子标题 exact 匹配）。
- `setup.find`：`名字 → locator`，按顺序 findElement，id 注入成 `{element:名字}`。
- `{appTitle}` / `{pid}`：靶子窗口标题与进程 id。

**每个变体自建会话、跑完即弃**（不是整个矩阵共用一个长命会话）。多花一次 attach
的时间，换来的是变体之间**没有状态污染**——`countLabel` 被点过、窗口被最小化或隐藏、
会话被 `closeSession` 关掉，这些都会让「共用一个会话」的方案后面成片假红，而排查
成本远高于那点时间。

**每个变体开始前都重新抢一次前台**：`setWindowState=minimized` 这类变体真的会把窗口
弄没，不重置的话后面所有变体连带失败。

## 与本仓库既有的记事本切片无关

`tests/e2e/test_windows_desktop.py` 那条切片在整目录运行时抖（记事本标题/焦点时序竞态，
已按 BACKLOG 先例登记）。本通道不复用它：靶子是自己编译并拉起的 fixture 进程，
生命周期由本模块掌握。
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

# `tests/e2e` 不是包（没有 `__init__.py`），pytest 只在收集到那个目录的用例时才把它
# 塞进 `sys.path`。本模块要复用那边已经写好的编译/启动/抢前台/清理，所以显式接入——
# 否则「tests/commands 先被收集」时 import 会失败，而报错是一句 ImportError，
# 离真正的原因（收集顺序）很远。`noqa: E402` 是必要的：这行必须在 sys.path 之后。
_E2E_DIR = Path(__file__).resolve().parents[1] / "e2e"
if str(_E2E_DIR) not in sys.path:
    sys.path.insert(0, str(_E2E_DIR))

import desktop_fixture  # noqa: E402

from rpa_core.model.command import CommandInvocation  # noqa: E402

# 与 tests/e2e 同款的开关名：真开窗 + 抢前台。
DESKTOP_ENV = "RPA_DESKTOP_E2E"
# 元素查找的缺省等待预算（毫秒）。靶子窗口已经 warm-up 过，5s 足够。
DEFAULT_FIND_TIMEOUT_MS = 5000
# 会话建立的缺省等待预算（毫秒）。
DEFAULT_ATTACH_TIMEOUT_MS = 5000


@dataclass
class DemoApp:
    """跑起来的靶子进程（`testapps/desktop/Program.cs` 现场编译出来的那个）。"""

    title: str
    exe: Path
    process: subprocess.Popen

    @property
    def pid(self) -> int:
        return int(self.process.pid)


def fixture_unavailable_reason() -> str | None:
    """两件事一起查：开关开没开、靶子能不能编译。返回原因（可用则 `None`）。"""
    if os.environ.get(DESKTOP_ENV) != "1":
        return (
            f"桌面 L1 矩阵会真实开窗并抢前台，缺省跳过；设 {DESKTOP_ENV}=1 启用"
            "（收集本目录还需 RPA_COMMAND_MATRIX=1）"
        )
    return desktop_fixture.fixture_unavailable_reason()


_UNAVAILABLE = fixture_unavailable_reason()
requires_desktop = pytest.mark.skipif(_UNAVAILABLE is not None, reason=_UNAVAILABLE or "")


@pytest.fixture(scope="session")
def demo_app(matrix_tmp_dir) -> Any:
    """编译并拉起靶子一次（整个矩阵共用同一个进程）；会话结束清理。

    用 `matrix_tmp_dir`（会话级临时目录）而不是 `tmp_path_factory`：后者会建 pytest 的
    basetemp，会话结束时在 atexit 里批量删除，在受限执行环境会被删除守卫拦下并以
    `SystemExit(1)` 收场（见 `tests/commands/conftest.py` §3 的既有记录）。
    """
    if _UNAVAILABLE is not None:
        pytest.skip(_UNAVAILABLE)
    app_dir = matrix_tmp_dir / "demo-app"
    app_dir.mkdir(parents=True, exist_ok=True)
    exe = desktop_fixture.compile_demo_app(app_dir)
    desktop_fixture.kill_demo_apps()
    process = subprocess.Popen([str(exe)])
    try:
        desktop_fixture.wait_for_window(desktop_fixture.APP_TITLE)
        # 先 warm-up UIA：首次触达 provider 的初始化能到 ~60s，会掐断 15s 的命令超时。
        desktop_fixture.warmup_uia(desktop_fixture.APP_TITLE)
        desktop_fixture.force_foreground(desktop_fixture.APP_TITLE)
        yield DemoApp(title=desktop_fixture.APP_TITLE, exe=exe, process=process)
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        desktop_fixture.kill_demo_apps()


@dataclass(frozen=True)
class DesktopBackend:
    """一个后端（uia / win32）在矩阵里的全部差异。"""

    name: str
    #: 命令 id 前缀：`desktop` / `desktop.win32`
    prefix: str
    #: 造一个执行器实例（每个变体一个，见模块 docstring）
    factory: Callable[[], Any]

    def command(self, name: str) -> str:
        return f"{self.prefix}.{name}"


def uia_backend() -> DesktopBackend:
    from rpa_core.executors import DesktopExecutor

    return DesktopBackend(name="uia", prefix="desktop", factory=DesktopExecutor)


def win32_backend() -> DesktopBackend:
    from rpa_core.executors import Win32DesktopExecutor

    def factory() -> Any:
        executor = Win32DesktopExecutor()
        if executor is None:
            pytest.skip("Win32DesktopExecutor 在非 win32 上是占位 None")
        return executor

    return DesktopBackend(name="win32", prefix="desktop.win32", factory=factory)


def _invocation(command: str, inputs: dict[str, Any]) -> CommandInvocation:
    return CommandInvocation(
        command_id=command,
        command_version="1.0.0",
        run_id="matrix",
        step_id="step",
        inputs=inputs,
    )


def _window_handle(title: str) -> str:
    """靶子主窗口的原生句柄（十进制字符串）。

    `desktop.win32.attachWindow` 有一条 `handle` 直连分支（不枚举、不按标题匹配），
    要覆盖它就得有个真句柄。`FindWindowW` 的类名参数传 `None` 表示不限定类名——
    与 `attachWindow` 的 exact 快路径用的是同一个 API。
    """
    try:
        import ctypes

        handle = ctypes.windll.user32.FindWindowW(None, title)
    except Exception:
        return "0"
    return str(int(handle or 0))


async def _prepare(
    executor: Any,
    setup: dict[str, Any],
    app: DemoApp,
    tmp_dir: Path,
    *,
    backend: DesktopBackend,
) -> dict[str, str]:
    """按变体的 `setup` 现算占位符表（`{session}` / `{element:x}` / `{appTitle}` / `{pid}`）。

    装配失败直接抛 `AssertionError`：这是**用例表/靶子**的问题，不该伪装成被测命令的
    失败（否则一句话「命令报错了」会把人引到实现上去查）。
    """
    from tests.commands.matrix import materialize_inputs

    extra: dict[str, str] = {
        "appTitle": app.title,
        "pid": str(app.pid),
        "handle": _window_handle(app.title),
    }
    if setup.get("session", "shared") == "none":
        return extra

    attach_inputs = setup.get("attach") or {
        "title": app.title,
        "timeoutMs": DEFAULT_ATTACH_TIMEOUT_MS,
    }
    outcome = await executor.execute(
        _invocation(
            backend.command("attachWindow"),
            materialize_inputs(attach_inputs, tmp_dir, extra),
        ),
        asyncio.Event(),
    )
    if outcome.status != "success":
        raise AssertionError(
            f"装配失败：{backend.command('attachWindow')} → {outcome.status} "
            f"{getattr(outcome.error, 'code', None)} {getattr(outcome.error, 'message', '')}"
        )
    session_id = str(outcome.outputs["sessionId"])
    extra["session"] = session_id

    for name, locator in (setup.get("find") or {}).items():
        found = await executor.execute(
            _invocation(
                backend.command("findElement"),
                {
                    "sessionId": session_id,
                    "locator": materialize_inputs(locator, tmp_dir, extra),
                    "timeoutMs": int(setup.get("findTimeoutMs") or DEFAULT_FIND_TIMEOUT_MS),
                },
            ),
            asyncio.Event(),
        )
        if found.status != "success":
            raise AssertionError(
                f"装配失败：{backend.command('findElement')}({name}) → {found.status} "
                f"{getattr(found.error, 'code', None)} {getattr(found.error, 'message', '')}"
            )
        extra[f"element:{name}"] = str(found.outputs["elementId"])
    return extra


async def _execute_variant(
    backend: DesktopBackend,
    app: DemoApp,
    tmp_dir: Path,
    command: str,
    variant: dict[str, Any],
):
    from tests.commands.matrix import materialize_inputs

    executor = backend.factory()
    try:
        extra = await _prepare(
            executor, variant.get("setup") or {}, app, tmp_dir, backend=backend
        )
        inputs = materialize_inputs(variant.get("inputs", {}), tmp_dir, extra)
        started = time.perf_counter()
        result = await executor.execute(_invocation(command, inputs), asyncio.Event())
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return result, elapsed_ms
    finally:
        await executor.close()


def run_variant(
    backend: DesktopBackend,
    app: DemoApp,
    tmp_dir: Path,
    command: str,
    variant: dict[str, Any],
) -> list[str]:
    """执行一个变体，返回 `expect` 的违规说明（空 = 通过）。

    桌面通道**没有通道调用记录**（它是真机，不存在「下发了什么 op」这一层），
    所以 `expect` 里不能出现 `onlyCall` / `calls` / `noCalls`——`check_expect` 收到
    `calls=None` 时会把这些声明直接判成违规，而不是静默跳过（「断言写了但没人执行」
    正是假绿灯的成因）。桌面侧的证据面是结果的 `outputs` / `effects` / 错误码 / 磁盘。
    """
    from tests.commands.matrix import check_expect

    # 上一个变体可能把窗口最小化/隐藏了：每个变体开始前把前台焦点与可见性要回来。
    desktop_fixture.force_foreground(app.title)
    result, elapsed_ms = asyncio.run(
        _execute_variant(backend, app, tmp_dir, command, variant)
    )
    return check_expect(
        variant["expect"], result=result, tmp_dir=tmp_dir, elapsed_ms=elapsed_ms
    )


def variant_tmp_dir(matrix_tmp_dir: Path, node_name: str) -> Path:
    """每个变体一个干净目录（截图这类会落盘的变体之间必须互不干扰）。

    名字取 pytest 的参数化 id；其中的 `::` 与 `<>` 等在 Windows 上不是合法文件名字符，
    先净化。
    """
    invalid = '<>:"/\\|?*'
    raw = node_name.split("[", 1)[-1].rstrip("]")
    safe = "".join("-" if char in invalid else char for char in raw)
    path = matrix_tmp_dir / safe
    path.mkdir(parents=True, exist_ok=True)
    return path
