"""第五轮：UIA Select() 改选中态之后，WinForms 的 SelectedIndexChanged 事件是否触发？"""

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
    import pywinauto.uia_defines as uia_defs
    from pywinauto import Desktop

    base = Path(tempfile.mkdtemp(prefix="rpa-s23-round5-"))
    desktop_fixture.kill_demo_apps()
    exe = desktop_fixture.compile_demo_app(base)
    process = subprocess.Popen([str(exe)])
    try:
        desktop_fixture.wait_for_window(desktop_fixture.APP_TITLE)
        desktop_fixture.warmup_uia(desktop_fixture.APP_TITLE)
        win = Desktop(backend="uia").window(title=desktop_fixture.APP_TITLE)

        def by_auto_id(automation_id: str):
            out = []
            for child in win.descendants():
                try:
                    if child.element_info.automation_id == automation_id:
                        out.append(child)
                except Exception:
                    continue
            assert len(out) == 1, f"{automation_id}: {len(out)}"
            return out[0]

        def selected_of(lst) -> str:
            sel = uia_defs.get_elem_interface(lst.element_info.element, "Selection")
            items = sel.GetCurrentSelection()
            return (
                ",".join(items.GetElement(i).CurrentName or "" for i in range(items.Length))
                or "<空>"
            )

        lst = by_auto_id("optionsList")
        status = by_auto_id("listStatus")
        items = lst.descendants(control_type="ListItem")
        print(f"初始：选中={selected_of(lst)  } listStatus={status.window_text()!r}")
        items[1].iface_selection_item.Select()
        print(f"Select(beta) 后：选中={selected_of(lst)} listStatus={status.window_text()!r}")
        items[2].iface_selection_item.Select()
        print(f"Select(gamma) 后：选中={selected_of(lst)} listStatus={status.window_text()!r}")
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
