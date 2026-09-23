"""补齐靶子之后：命令是否**真的作用到了靶子上**（M38 S2，真机）。

## 为什么这些断言不在 L1 用例表里

L1 矩阵一次只跑**一条**命令，它能断言的是命令的返回值面（outputs / effects / 错误码 /
磁盘）。而「菜单项真的被选中」「拖拽真的移动了控件」「列表选中项真的变了」这类命题要
**读回靶子的状态**——那是第二条命令的事，属于流程层，所以落在 `tests/e2e`。

## 判据为什么是状态回显 Label

`testapps/desktop/Program.cs` 补的四组控件（原生菜单栏 / ListBox / ComboBox / 可拖
Label）每个都把手上的操作写进一个状态回显 Label（menuStatus / listStatus /
comboStatus / dragStatus）。**命令返回 success 证明不了操作生效**——S2.3 之前的
`desktop.select` 把异常吞掉后照样返回 success（实测三个 selectBy 分支都如此）。
状态回显是那个能区分「生效」与「静默成功」的读侧探针——但对 select 例外，见下。

## 读侧探针的口径

回显一律**用 uia 后端 + automationId 读**（Label 的 UIA Name 就是它的文本，实测可靠；
Edit/ListBox 不行——但 S2.3 修好了 getText 的 ValuePattern 路径，Edit 现在也能读），
与被测命令走哪个后端无关。

**select 的读侧例外**：UIA 的 SelectionItemPattern.Select() 改的是 ListBox 的选中项，
但**不触发** WinForms 的 SelectedIndexChanged（实测 round5：选中=beta 而状态回显仍是
'none'）——所以 listStatus 对 UIA Select 不是有效读侧；这条用例的读侧是
① 执行器 effect details 里的 `selectedItem` 读回 + ② 直接用 UIA SelectionPattern
读靶子的当前选中项（独立于执行器）。

**但 win32 原生消息那条相反（尾巴 #1 转正时实测）**：pywinauto 的
`ListBoxWrapper/ComboBoxWrapper.select()` 在原生消息之后会
`notify_parent(LBN_SELCHANGE / CBN_SELCHANGE)`（post 一个 WM_COMMAND），所以
SelectedIndexChanged **会**触发、listStatus 会跟着变——`test_select_via_win32_native_messages`
因此可以把状态回显当独立读侧。两条用例合起来钉住「哪个后端的选中动作会让 UI 反应」。
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


def test_select_changes_the_list_selection(demo_app):
    """select（uia）真的改变了靶子的选中项（S2.3 修实现后转正）。

    双读侧：执行器 effect details 的 selectedItem 读回 + 独立于执行器的 UIA
    SelectionPattern 读靶子当前选中项——后者防「执行器自己写自己读」的自证。
    listStatus 回显不参与判据（UIA Select 不触发 SelectedIndexChanged，见模块 docstring）。
    """
    import pywinauto.uia_defines as uia_defs
    from pywinauto import Desktop

    from rpa_core.executors import DesktopExecutor

    async def scenario() -> tuple[str, str]:
        reader, actor = DesktopExecutor(), DesktopExecutor()
        try:
            await _attach(reader, "desktop")
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
            assert result.status == "success", getattr(result.error, "message", "")
            reported = str(result.effects[0].details["selectedItem"])
            # 独立读侧：直接用 UIA 读靶子的当前选中项
            win = Desktop(backend="uia").window(title=desktop_fixture.APP_TITLE)
            list_el = next(
                c
                for c in win.descendants(control_type="List")
                if c.element_info.automation_id == "optionsList"
            )
            sel = uia_defs.get_elem_interface(list_el.element_info.element, "Selection")
            arr = sel.GetCurrentSelection()
            independent = arr.GetElement(0).CurrentName if arr.Length else ""
            return reported, str(independent or "")
        finally:
            await reader.close()
            await actor.close()

    reported, independent = asyncio.run(scenario())
    assert reported == "beta", f"执行器读回 {reported!r}，应为 beta"
    assert independent == "beta", (
        f"独立 UIA 读回 {independent!r}，应为 beta——选中态没有真的落到靶子上"
    )


def test_select_via_win32_native_messages(demo_app):
    """select（win32 原生消息）真的改变了靶子选中项，且**靶子自己会反应**。

    BACKLOG 尾巴 #1 转正——此前 win32 侧 `select`/`getSelectedText` 是显式
    `EXECUTOR_FAILED`（S2.3 的止血），现走 LB_SETCURSEL / CB_SETCURSEL。

    两个读侧，第二个是本条独有的价值：
    ① 执行器 effect details 的 `selectedItem` 读回；
    ② **状态回显 listStatus**——原生消息经 pywinauto 的
       `notify_parent(LBN_SELCHANGE)`（post 一个 WM_COMMAND）会触发 WinForms 的
       `SelectedIndexChanged`，所以这里 listStatus 是**有效**读侧。这与 uia 侧刚好
       相反（`test_select_changes_the_list_selection` 的注释解释了 uia 那条为什么不
       能用 listStatus）——两条用例合起来把「哪个后端会让 UI 反应」钉死。
    最后再走一次 `getSelectedText` 按值读回：先选再读是两步流程，正是 uia 侧 notes
    里指明「属于流程层、放 tests/e2e」的那条。
    """
    from pywinauto import Desktop
    from pywinauto.controls.win32_controls import ListBoxWrapper

    from rpa_core.executors import DesktopExecutor, Win32DesktopExecutor

    # ListBox 没有窗口文本，win32 定位器只能按 className 认它；类名含机器级哈希段，
    # 所以运行期现读（与 L1 驱动的 `{listBoxClass}` 同一口径）。
    list_cls = next(
        child.class_name()
        for child in Desktop(backend="win32")
        .window(title=desktop_fixture.APP_TITLE)
        .descendants()
        if isinstance(child, ListBoxWrapper)
    )

    async def scenario() -> tuple[str, str, str]:
        reader, actor = DesktopExecutor(), Win32DesktopExecutor()
        try:
            reader_session = await _attach(reader, "desktop")
            actor_session = await _attach(actor, "desktop.win32")
            desktop_fixture.force_foreground(desktop_fixture.APP_TITLE)
            found = await actor.execute(
                _invocation(
                    "desktop.win32.findElement",
                    {
                        "sessionId": actor_session,
                        "locator": {"backend": "win32", "className": list_cls},
                        "timeoutMs": 2000,
                    },
                ),
                asyncio.Event(),
            )
            assert found.status == "success", (
                f"ListBox 没找到（className={list_cls!r}）："
                f"{getattr(found.error, 'code', None)}"
            )
            element_id = str(found.outputs["elementId"])
            selected = await actor.execute(
                _invocation(
                    "desktop.win32.select",
                    {
                        "sessionId": actor_session,
                        "elementId": element_id,
                        "value": "2",
                        "selectBy": "index",
                    },
                ),
                asyncio.Event(),
            )
            assert selected.status == "success", (
                f"win32 select 失败：{getattr(selected.error, 'code', None)} "
                f"{getattr(selected.error, 'message', '')}"
            )
            reported = str(selected.effects[0].details["selectedItem"])
            echoed = await _read_label(reader, reader_session, "listStatus")
            read_back = await actor.execute(
                _invocation(
                    "desktop.win32.getSelectedText",
                    {"sessionId": actor_session, "elementId": element_id},
                ),
                asyncio.Event(),
            )
            assert read_back.status == "success", getattr(read_back.error, "message", "")
            return reported, echoed, str(read_back.outputs["text"])
        finally:
            await reader.close()
            await actor.close()

    reported, echoed, read_back = asyncio.run(scenario())
    assert reported == "gamma", f"执行器读回 {reported!r}，应为 gamma"
    assert echoed == "list:2:gamma", (
        f"靶子回显 {echoed!r}，应为 'list:2:gamma'——"
        "原生消息没有触发 SelectedIndexChanged（UI 没反应）"
    )
    assert read_back == "gamma", f"getSelectedText 读回 {read_back!r}，应为 gamma"


def test_get_text_reads_the_edit_value(demo_app):
    """getText（uia）对 Edit 走 ValuePattern 读真实文本（S2.3 修实现后转正）。"""
    from rpa_core.executors import DesktopExecutor

    async def scenario() -> str:
        reader = DesktopExecutor()
        try:
            session = await _attach(reader, "desktop")
            return await _read_label(reader, session, "readOnlyNote")
        finally:
            await reader.close()

    assert asyncio.run(scenario()) == "note-ready"
