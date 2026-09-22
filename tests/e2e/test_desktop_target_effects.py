"""补齐靶子之后：命令是否**真的作用到了靶子上**（M38 S2，真机）。

## 为什么这些断言不在 L1 用例表里

L1 矩阵一次只跑**一条**命令，它能断言的是命令的返回值面（outputs / effects / 错误码 /
磁盘）。而「菜单项真的被选中」「拖拽真的移动了控件」「列表选中项真的变了」这类命题要
**读回靶子的状态**——那是第二条命令的事，属于流程层，所以落在 `tests/e2e`。

## 判据为什么是状态回显 Label

`testapps/desktop/Program.cs` 补的四组控件（原生菜单栏 / ListBox / ComboBox / 可拖
Label）每个都把手上的操作写进一个状态回显 Label（menuStatus / listStatus /
comboStatus / dragStatus）。**命令返回 success 证明不了操作生效**——`desktop.select`
的实现把异常吞掉后照样返回 success（实测三个 selectBy 分支都如此）。状态回显才是那个
能区分「生效」与「静默成功」的读侧探针。

回显一律**用 uia 后端 + automationId 读**（Label 的 UIA Name 就是它的文本，实测可靠；
Edit/ListBox 不行——见下面第二条 xfail），与被测命令走哪个后端无关。

## 两条 xfail 钉的是**实现缺口**，不是靶子缺口

- `desktop.select`（uia）：三个 selectBy 分支实测全部 success 而选中项一步没动。
- `desktop.getText`（uia）对 Edit：UIA 的 Name 被 MSAA 的 labeled-by 规则回落到相邻
  Label 的文本（实测 queryInput → 'Name'、readOnlyNote → 'Drag'、optionsList → 'Ready'）。

写成非 strict 的 xfail：实现修好后会自动转成 XPASS，提示把它改成正向断言。
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import desktop_fixture
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

_UNAVAILABLE = desktop_fixture.fixture_unavailable_reason()

pytestmark = pytest.mark.skipif(
    os.environ.get("RPA_DESKTOP_E2E") != "1",
    reason="真实桌面 E2E 会弹窗抢焦点，缺省跳过；设 RPA_DESKTOP_E2E=1 启用",
)


@pytest.fixture(scope="module")
def demo_app():
    """编译并拉起靶子一次（本模块共用同一个进程）。"""
    if _UNAVAILABLE is not None:
        pytest.skip(_UNAVAILABLE)
    base = Path(tempfile.mkdtemp(prefix="rpa-desktop-effects-"))
    # 先清残留进程再编译：上一次会话留下的靶子会占住输出 exe，编译无法覆盖它。
    desktop_fixture.kill_demo_apps()
    exe = desktop_fixture.compile_demo_app(base)
    process = subprocess.Popen([str(exe)])
    try:
        desktop_fixture.wait_for_window(desktop_fixture.APP_TITLE)
        # 首次触达 UIA provider 的初始化能到 ~60s，会掐断命令外层的 15s 超时。
        desktop_fixture.warmup_uia(desktop_fixture.APP_TITLE)
        desktop_fixture.force_foreground(desktop_fixture.APP_TITLE)
        yield exe
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        desktop_fixture.kill_demo_apps()
        shutil.rmtree(base, ignore_errors=True)


def _invocation(command: str, inputs: dict):
    from rpa_core.model.command import CommandInvocation

    return CommandInvocation(
        command_id=command,
        command_version="1.0.0",
        run_id="desktop-target-effects",
        step_id="step",
        inputs=inputs,
    )


async def _attach(executor, prefix: str) -> str:
    result = await executor.execute(
        _invocation(
            f"{prefix}.attachWindow",
            {"title": desktop_fixture.APP_TITLE, "timeoutMs": 5000},
        ),
        asyncio.Event(),
    )
    assert result.status == "success", (
        f"{prefix}.attachWindow 失败：{getattr(result.error, 'code', None)} "
        f"{getattr(result.error, 'message', '')}"
    )
    return str(result.outputs["sessionId"])


async def _read_label(executor, session_id: str, automation_id: str) -> str:
    """读靶子的状态回显 Label（uia 后端 + automationId——Label 的 Name 就是文本）。"""
    found = await executor.execute(
        _invocation(
            "desktop.findElement",
            {
                "sessionId": session_id,
                "locator": {"backend": "uia", "automationId": automation_id},
                "timeoutMs": 2000,
            },
        ),
        asyncio.Event(),
    )
    assert found.status == "success", (
        f"状态回显 {automation_id} 没找到：{getattr(found.error, 'code', None)}"
    )
    got = await executor.execute(
        _invocation(
            "desktop.getText",
            {
                "sessionId": session_id,
                "elementId": found.outputs["elementId"],
                "timeoutMs": 2000,
            },
        ),
        asyncio.Event(),
    )
    assert got.status == "success", f"读 {automation_id} 失败：{got.status}"
    return str(got.outputs["value"])


def _menu_select_via_win32(path: list[str]) -> str:
    """用 win32 后端选一条菜单，返回 menuStatus 的读回值。"""
    from rpa_core.executors import DesktopExecutor, Win32DesktopExecutor

    async def scenario() -> str:
        reader, actor = DesktopExecutor(), Win32DesktopExecutor()
        try:
            reader_session = await _attach(reader, "desktop")
            actor_session = await _attach(actor, "desktop.win32")
            desktop_fixture.force_foreground(desktop_fixture.APP_TITLE)
            result = await actor.execute(
                _invocation(
                    "desktop.win32.menuSelect",
                    {"sessionId": actor_session, "menuPath": path},
                ),
                asyncio.Event(),
            )
            assert result.status == "success", (
                f"menuSelect {path} 失败：{getattr(result.error, 'code', None)} "
                f"{getattr(result.error, 'message', '')}"
            )
            return await _read_label(reader, reader_session, "menuStatus")
        finally:
            await reader.close()
            await actor.close()

    return asyncio.run(scenario())


def test_menu_select_fires_the_two_level_item(demo_app):
    """靶子补原生菜单栏之后，win32 侧这条命令才第一次有成功路径（实测已生效）。"""
    assert _menu_select_via_win32(["Actions", "Increment"]) == "menu:increment"


def test_menu_select_fires_the_nested_item(demo_app):
    """多级菜单按 '->' 拼接这一口径，靠嵌套三级路径钉住。"""
    assert _menu_select_via_win32(["Actions", "Nested", "Deep"]) == "menu:deep"


@pytest.mark.parametrize(
    "backend_name,locator",
    [
        ("uia", {"backend": "uia", "automationId": "dragHandle"}),
        ("win32", {"backend": "win32", "title": "Drag"}),
    ],
)
def test_drag_moves_the_target_control(demo_app, backend_name, locator):
    """拖拽真的作用到控件上：dragStatus 从 'none' 变成 'up:<位置>'。

    终点用 `desktop_fixture.client_center` 现算——绝对屏幕坐标写不死（靶子用
    CenterScreen，而「屏幕中心」随会话的虚拟屏布局漂移，实测本机窗口落在 y 为负的区域）。
    """
    from rpa_core.executors import DesktopExecutor, Win32DesktopExecutor

    executor_cls = DesktopExecutor if backend_name == "uia" else Win32DesktopExecutor
    prefix = "desktop" if backend_name == "uia" else "desktop.win32"
    center = desktop_fixture.client_center(desktop_fixture.APP_TITLE)
    assert center is not None, "靶子窗口不在，取不到客户区中心"

    async def scenario() -> str:
        reader, actor = DesktopExecutor(), executor_cls()
        try:
            reader_session = await _attach(reader, "desktop")
            actor_session = await _attach(actor, prefix)
            found = await actor.execute(
                _invocation(
                    f"{prefix}.findElement",
                    {"sessionId": actor_session, "locator": locator, "timeoutMs": 2000},
                ),
                asyncio.Event(),
            )
            assert found.status == "success", (
                f"findElement {locator} 失败：{getattr(found.error, 'code', None)}"
            )
            desktop_fixture.force_foreground(desktop_fixture.APP_TITLE)
            result = await actor.execute(
                _invocation(
                    f"{prefix}.drag",
                    {
                        "sessionId": actor_session,
                        "elementId": found.outputs["elementId"],
                        "targetX": center[0],
                        "targetY": center[1],
                    },
                ),
                asyncio.Event(),
            )
            assert result.status == "success", (
                f"drag 失败：{getattr(result.error, 'code', None)}"
            )
            return await _read_label(reader, reader_session, "dragStatus")
        finally:
            await reader.close()
            await actor.close()

    status = asyncio.run(scenario())
    assert status.startswith("up:"), (
        f"drag 返回成功，但靶子的 dragStatus 还是 {status!r}——"
        "鼠标没落到控件上（真实副作用面）"
    )


@pytest.mark.xfail(
    strict=False,
    reason=(
        "desktop.select 的实现用错 UIA pattern：ListBox 的 iface_selection 是 "
        "SelectionPattern（没有 Select 方法），label/value 分支又拿 GetCurrentSelection() "
        "当『全部选项』遍历；两处都被 except 吞掉 → 命令返回 success 但选中项不变"
    ),
)
def test_select_changes_the_list_selection(demo_app):
    """钉住缺口：选中项应当从 none 变成 list:1:beta（实测仍是 none）。"""
    from rpa_core.executors import DesktopExecutor

    async def scenario() -> str:
        reader, actor = DesktopExecutor(), DesktopExecutor()
        try:
            reader_session = await _attach(reader, "desktop")
            actor_session = await _attach(actor, "desktop")
            found = await actor.execute(
                _invocation(
                    "desktop.findElement",
                    {
                        "sessionId": actor_session,
                        "locator": {"backend": "uia", "automationId": "optionsList"},
                        "timeoutMs": 2000,
                    },
                ),
                asyncio.Event(),
            )
            assert found.status == "success"
            result = await actor.execute(
                _invocation(
                    "desktop.select",
                    {
                        "sessionId": actor_session,
                        "elementId": found.outputs["elementId"],
                        "value": "1",
                        "selectBy": "index",
                    },
                ),
                asyncio.Event(),
            )
            assert result.status == "success"
            return await _read_label(reader, reader_session, "listStatus")
        finally:
            await reader.close()
            await actor.close()

    assert asyncio.run(scenario()) == "list:1:beta"


@pytest.mark.xfail(
    strict=False,
    reason=(
        "uia 的 getText 用 UIA Name 当文本，而 WinForms 的 Edit/ListBox 没有 "
        "AccessibleName，UIA 按 MSAA 的 labeled-by 规则回落到相邻 Label 的文本"
        "（实测 readOnlyNote → 'Drag'、queryInput → 'Name'）"
    ),
)
def test_get_text_reads_the_edit_value(demo_app):
    """钉住缺口：只读 Edit 的内容是 'note-ready'，实测读回的是相邻 Label 的 'Drag'。"""
    from rpa_core.executors import DesktopExecutor

    async def scenario() -> str:
        reader = DesktopExecutor()
        try:
            session = await _attach(reader, "desktop")
            return await _read_label(reader, session, "readOnlyNote")
        finally:
            await reader.close()

    assert asyncio.run(scenario()) == "note-ready"
