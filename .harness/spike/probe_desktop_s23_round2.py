"""第二轮深挖：包装对象的 iface_value / iface_selection_item 与裸接口差在哪。"""

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

    base = Path(tempfile.mkdtemp(prefix="rpa-s23-round2-"))
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
            assert len(out) == 1, f"{automation_id}: {len(out)} 个命中"
            return out[0]

        note = by_auto_id("readOnlyNote")
        print("== Edit (readOnlyNote) ==")
        try:
            iface = note.iface_value
            print(f"iface_value = {iface}")
            print(f"CurrentValue = {iface.CurrentValue!r}")
        except Exception as exc:
            print(f"iface_value 抛 {type(exc).__name__}: {exc}")
        raw = note.element_info.element
        try:
            val = raw.GetCurrentPropertyValue(30006)
            print(f"raw 30006 = {val!r}")
        except Exception as exc:
            print(f"raw 30006 抛 {type(exc).__name__}: {exc}")

        lst = by_auto_id("optionsList")
        print("== ListBox (optionsList) ==")
        items = lst.descendants(control_type="ListItem")
        print(f"descendants(control_type='ListItem') -> {len(items)} 项")
        for idx, item in enumerate(items):
            raw_item = item.element_info.element
            try:
                v6 = raw_item.GetCurrentPropertyValue(30006)
            except Exception as exc:
                v6 = f"<{type(exc).__name__}>"
            print(f"  [{idx}] name={item.window_text()!r} raw30006={v6!r}")

        if items:
            target = items[1]
            print("== Select 测试（items[1] 应为 beta）==")
            print(f"  选中前 is_selected 尝试…")
            sel_iface = target.iface_selection_item
            print(f"  iface_selection_item = {sel_iface}")
            sel_iface.Select()
            print("  Select() 调用完成（无异常）")
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
