"""BACKLOG 尾巴探针：win32 原生 select/getSelectedText 的可行性验证。

问题：pywinauto 的 ListBoxWrapper/ComboBoxWrapper（内部已封装 LB_*/CB_* 原生
消息与跨进程缓冲区）能否直接吃 WinForms 的 ListBox/ComboBox？以及这两个控件
在 win32 定位器下的稳定锚点是什么（title/class/controlId 各报什么）。

跑法（会开窗抢前台）：

    .venv/Scripts/python.exe .harness/spike/probe_win32_native_select.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


def main() -> None:
    import pywinauto
    from pywinauto.controls.win32_controls import ComboBoxWrapper, ListBoxWrapper

    tmp = Path(tempfile.mkdtemp(prefix="rpa-win32-probe-"))
    exe = tmp / "RpaCoreDesktopDemo.exe"
    csc = "C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    subprocess.run(
        [
            csc, "/nologo", "/target:winexe", f"/out:{exe}",
            "/r:System.Windows.Forms.dll", "/r:System.Drawing.dll",
            str(ROOT / "testapps" / "desktop" / "Program.cs"),
        ],
        check=True,
        capture_output=True,
    )
    proc = subprocess.Popen([str(exe)])
    try:
        app = pywinauto.Application(backend="win32")
        deadline = time.time() + 15
        while True:
            try:
                app.connect(title="RPA Core Desktop Demo", timeout=1)
                break
            except Exception:
                if time.time() > deadline:
                    raise
                time.sleep(0.3)
        win = app.window(title="RPA Core Desktop Demo")

        report = {}
        for ctrl in win.descendants():
            cls = ctrl.class_name()
            if "listbox" in cls.lower() or "combobox" in cls.lower():
                info = ctrl.element_info
                report[cls.split(".")[1]] = {
                    "class_name": cls,
                    "control_id": info.control_id,
                    "window_text": info.rich_text,
                    "handle": info.handle,
                }
        print(json.dumps(report, ensure_ascii=False, indent=2))

        # -- 原生 select 实测 -----------------------------------------------------
        for cls, wrapper_cls, pick in (
            ("LISTBOX", ListBoxWrapper, "beta"),
            ("COMBOBOX", ComboBoxWrapper, "two"),
        ):
            hits = [
                c for c in win.descendants()
                if f".{cls}." in c.class_name().upper()
            ]
            if not hits:
                print(f"# {cls}: NOT FOUND")
                continue
            handle = hits[0].element_info.handle
            wrapper = wrapper_cls(handle)
            try:
                items = wrapper.item_texts()
            except Exception as exc:  # noqa: BLE001
                items = f"<item_texts failed: {exc}>"
            out = {"items": items}
            try:
                wrapper.select(pick)
                time.sleep(0.2)
                if cls == "LISTBOX":
                    sel = wrapper.selected_indices()
                    out["selected_indices_after_select"] = sel
                    out["selected_text"] = (
                        wrapper.item_text(sel[0]) if sel else None
                    )
                else:
                    out["selected_index_after_select"] = wrapper.selected_index()
                    try:
                        out["selected_text"] = wrapper.selected_text()
                    except Exception as exc:  # noqa: BLE001
                        out["selected_text"] = f"<{type(exc).__name__}: {exc}>"
            except Exception as exc:  # noqa: BLE001
                import traceback
                out["select_error"] = f"{type(exc).__name__}: {exc}"
                out["select_traceback"] = traceback.format_exc().strip().splitlines()[-3:]
            try:
                wrapper.select(2)  # 按索引
                time.sleep(0.2)
                out["after_index_select"] = (
                    wrapper.selected_indices()
                    if cls == "LISTBOX"
                    else wrapper.selected_index()
                )
            except Exception as exc:  # noqa: BLE001
                out["index_select_error"] = f"{type(exc).__name__}: {exc}"
            print(f"# {cls}: " + json.dumps(out, ensure_ascii=False, default=str))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main()
