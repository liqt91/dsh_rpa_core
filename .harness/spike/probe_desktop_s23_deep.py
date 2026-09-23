"""深挖 uia select / getText 的元素面：ListBox 的子项长什么样、Edit 支持哪些 pattern。"""

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


def _invocation(command: str, inputs: dict):
    from rpa_core.model.command import CommandInvocation

    return CommandInvocation(
        command_id=command,
        command_version="1.0.0",
        run_id="s23-deep-probe",
        step_id="step",
        inputs=inputs,
    )


async def main() -> None:
    from pywinauto.uia_defines import get_elem_interface

    from rpa_core.executors import DesktopExecutor

    base = Path(tempfile.mkdtemp(prefix="rpa-s23-deep-"))
    desktop_fixture.kill_demo_apps()
    exe = desktop_fixture.compile_demo_app(base)
    process = subprocess.Popen([str(exe)])
    try:
        desktop_fixture.wait_for_window(desktop_fixture.APP_TITLE)
        desktop_fixture.warmup_uia(desktop_fixture.APP_TITLE)

        executor = DesktopExecutor()
        try:
            result = await executor.execute(
                _invocation("desktop.attachWindow", {"title": desktop_fixture.APP_TITLE}),
                asyncio.Event(),
            )
            session = str(result.outputs["sessionId"])

            async def find(locator: dict):
                r = await executor.execute(
                    _invocation(
                        "desktop.findElement",
                        {"sessionId": session, "locator": locator, "timeoutMs": 3000},
                    ),
                    asyncio.Event(),
                )
                assert r.status == "success", r.error
                return r.outputs["elementId"]

            # 拿 element_id 背后的 pywinauto wrapper：借 attachWindow 的会话表不可行，
            # 直接用 pywinauto 定位同一批元素（automationId 过滤同 uia _find 口径）。
            from pywinauto import Desktop

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
            print(f"optionsList: friendly={lst.friendly_class_name()!r} "
                  f"control_type={lst.element_info.control_type!r} "
                  f"children={lst.control_count()}")
            for depth1 in lst.descendants():
                info = depth1.element_info
                name = info.name
                try:
                    val = info.GetCurrentPropertyValue(30006)
                except Exception as exc:
                    val = f"<{type(exc).__name__}: {exc}>"
                try:
                    sel_item = get_elem_interface(depth1.element_info.element, "SelectionItem")
                    can_select = sel_item is not None
                except Exception as exc:
                    can_select = f"<{type(exc).__name__}: {exc}>"
                print(f"  子项: control_type={info.control_type!r} name={name!r} "
                      f"value={val!r} selection_item={can_select}")

            note = by_auto_id("readOnlyNote")
            print(f"readOnlyNote: friendly={note.friendly_class_name()!r} "
                  f"control_type={note.element_info.control_type!r}")
            try:
                iface = get_elem_interface(note.element_info.element, "Value")
                print(f"  Value pattern: {iface}")
                if iface is not None:
                    print(f"  CurrentValue={iface.CurrentValue!r}")
            except Exception as exc:
                print(f"  Value pattern 异常: {type(exc).__name__}: {exc}")
            print(f"  window_text={note.window_text()!r}")
            print(f"  rich_text={note.element_info.rich_text!r}")
            print(f"  Name 属性={note.element_info.name!r}")
            print(f"  LegacyIAccessible value=")
            try:
                legacy = get_elem_interface(note.element_info.element, "LegacyIAccessible")
                print(f"    {legacy.CurrentValue!r}" if legacy else "    None")
            except Exception as exc:
                print(f"    <{type(exc).__name__}: {exc}>")

            # 再看 value 属性（30006）在 Edit 上是什么
            try:
                print(f"  30006 (ValueProperty)={note.element_info.GetCurrentPropertyValue(30006)!r}")
            except Exception as exc:
                print(f"  30006 异常: {type(exc).__name__}: {exc}")
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


if __name__ == "__main__":
    asyncio.run(main())
