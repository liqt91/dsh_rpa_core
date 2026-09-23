"""第三轮深挖：Select 是否真改了选中态（含 set_focus 对照）+ 执行器线程内 getText 的真实异常。"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "e2e"))

import desktop_fixture  # noqa: E402

os.environ.setdefault("RPA_DESKTOP_E2E", "1")


async def main() -> None:
    from pywinauto import Desktop

    base = Path(tempfile.mkdtemp(prefix="rpa-s23-round3-"))
    desktop_fixture.kill_demo_apps()
    exe = desktop_fixture.compile_demo_app(base)
    process = subprocess.Popen([str(exe)])
    try:
        desktop_fixture.wait_for_window(desktop_fixture.APP_TITLE)
        desktop_fixture.warmup_uia(desktop_fixture.APP_TITLE)

        def selected_of(lst) -> str:
            """读当前选中项（SelectionPattern.GetCurrentSelection，元素级）。"""
            import pywinauto.uia_defines as uia_defs

            raw = lst.element_info.element
            sel = uia_defs.get_elem_interface(raw, "Selection")
            items = sel.GetCurrentSelection()
            names = []
            for i in range(items.Length):
                names.append(items.GetElement(i).CurrentName or "")
            return ",".join(names) or "<空>"

        win = Desktop(backend="uia").window(title=desktop_fixture.APP_TITLE)

        def by_auto_id(automation_id: str):
            out = []
            for child in win.descendants():
                try:
                    if child.element_info.automation_id == automation_id:
                        out.append(child)
                except Exception:
                    continue
            assert len(out) == 1, f"{automation_id}: {len(out)} 个命中"
            return out[0]

        lst = by_auto_id("optionsList")
        items = lst.descendants(control_type="ListItem")
        print(f"初始选中：{selected_of(lst)}")

        print("== 对照 A：直接 Select()（不碰焦点）==")
        items[1].iface_selection_item.Select()
        print(f"Select 后选中：{selected_of(lst)}")

        print("== 对照 B：先 set_focus 再 Select() ==")
        try:
            lst.set_focus()
        except Exception as exc:
            print(f"set_focus 抛 {type(exc).__name__}: {exc}")
        items[2].iface_selection_item.Select()
        print(f"Select 后选中：{selected_of(lst)}")

        print("== 对照 C：用 LegacyIAccessible/鼠标点击 items[0]（click_input）==")
        try:
            items[0].click_input()
            print(f"click_input 后选中：{selected_of(lst)}")
        except Exception as exc:
            print(f"click_input 抛 {type(exc).__name__}: {exc}")

        # 读回显 Label，验证「选中态变了但回显没变」还是「根本没选中」
        status = by_auto_id("listStatus")
        print(f"listStatus 回显 = {status.window_text()!r}")

        print("== 执行器线程内 getText 的真实异常（猴子补丁）==")
        from rpa_core.executors import DesktopExecutor

        orig = DesktopExecutor._read_element_text

        def patched(element):
            try:
                v = str(element.iface_value.GetCurrentValue())
                print(f"  [dbg] iface_value OK -> {v!r} "
                      f"(friendly={element.friendly_class_name()!r})")
                return v
            except Exception as exc:
                print(f"  [dbg] iface_value 抛 {type(exc).__name__}: {exc} "
                      f"(friendly={element.friendly_class_name()!r})")
                return orig(element)

        DesktopExecutor._read_element_text = staticmethod(patched)

        executor = DesktopExecutor()
        try:
            r = await executor.execute(
                _invocation("desktop.attachWindow", {"title": desktop_fixture.APP_TITLE}),
                asyncio.Event(),
            )
            session = str(r.outputs["sessionId"])
            f = await executor.execute(
                _invocation(
                    "desktop.findElement",
                    {
                        "sessionId": session,
                        "locator": {"backend": "uia", "automationId": "readOnlyNote"},
                        "timeoutMs": 3000,
                    },
                ),
                asyncio.Event(),
            )
            g = await executor.execute(
                _invocation(
                    "desktop.getText",
                    {"sessionId": session, "elementId": f.outputs["elementId"],
                     "timeoutMs": 2000},
                ),
                asyncio.Event(),
            )
            print(f"executor getText readOnlyNote -> {g.outputs.get('value')!r}")
        finally:
            await executor.close()
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        desktop_fixture.kill_demo_apps()
        shutil.rmtree(base, ignore_errors=True)


def _invocation(command: str, inputs: dict):
    from rpa_core.model.command import CommandInvocation

    return CommandInvocation(
        command_id=command,
        command_version="1.0.0",
        run_id="s23-round3",
        step_id="step",
        inputs=inputs,
    )


if __name__ == "__main__":
    asyncio.run(main())
