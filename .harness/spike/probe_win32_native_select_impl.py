"""尾巴 #1 实现的实测探针：把要写进用例表的期望值先量出来。

逐项实测（走真实命令路径）：
  1. select 的三个 selectBy（value/label/index）在 ListBox 与 ComboBox 上
     → 成功 + effects[].details.selectedItem
  2. select 无匹配项 → 错误码与 details 形状
  3. getSelectedText → outputs.text（ListBox / ComboBox）
  4. 非列表控件（Submit 按钮）→ 错误码与 details 形状
  5. 空选中（刚 attach 未选过）→ getSelectedText 是否空串（负索引陷阱的对照）

跑法：

    .venv/Scripts/python.exe .harness/spike/probe_win32_native_select_impl.py
"""

from __future__ import annotations

import asyncio
import json
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
        run_id="win32-select-impl-probe",
        step_id="step",
        inputs=inputs,
    )


def main() -> int:
    from pywinauto import Desktop as PD
    from pywinauto.controls.win32_controls import ComboBoxWrapper, ListBoxWrapper

    from rpa_core.executors import Win32DesktopExecutor

    base = Path(tempfile.mkdtemp(prefix="rpa-win32-select-impl-"))
    desktop_fixture.kill_demo_apps()
    exe = desktop_fixture.compile_demo_app(base)
    process = subprocess.Popen([str(exe)])
    out: dict = {}
    try:
        desktop_fixture.wait_for_window(desktop_fixture.APP_TITLE)
        desktop_fixture.force_foreground(desktop_fixture.APP_TITLE)

        window = PD(backend="win32").window(title=desktop_fixture.APP_TITLE)
        kids = list(window.descendants())
        list_cls = next(
            c.class_name() for c in kids if isinstance(c, ListBoxWrapper)
        )
        combo_cls = next(
            c.class_name() for c in kids if isinstance(c, ComboBoxWrapper)
        )
        out["placeholders"] = {"listBoxClass": list_cls, "comboBoxClass": combo_cls}

        async def scenario() -> None:
            ex = Win32DesktopExecutor()

            async def run(command: str, inputs: dict):
                return await ex.execute(_invocation(command, inputs), asyncio.Event())

            try:
                attached = await run(
                    "desktop.win32.attachWindow", {"title": desktop_fixture.APP_TITLE}
                )
                assert attached.status == "success", attached.error
                sid = str(attached.outputs["sessionId"])

                async def find(locator: dict) -> str:
                    r = await run("desktop.win32.findElement", {
                        "sessionId": sid, "locator": locator, "timeoutMs": 3000,
                    })
                    assert r.status == "success", r.error
                    return str(r.outputs["elementId"])

                async def shape(result) -> dict:
                    err = getattr(result, "error", None)
                    return {
                        "status": result.status,
                        "errorCode": str(getattr(err, "code", "")) if err else None,
                        "errorDetails": getattr(err, "details", None) if err else None,
                        "outputs": result.outputs or None,
                        "effects": [
                            {"kind": str(e.kind), "details": e.details}
                            for e in (result.effects or [])
                        ],
                    }

                # --- 0：**未选过**时的 getSelectedText（负索引陷阱的对照）-------
                # 演示程序两个列表初始都无选中（SelectedIndex = -1）。若实现误用
                # `selected_text()` / `selected_indices()[0]`，这里会静默回**最后一项**
                # （three / gamma）而不是空串。必须排在所有 select 之前量。
                for label, cls in (("listbox", list_cls), ("combobox", combo_cls)):
                    el0 = await find({"backend": "win32", "className": cls})
                    r0 = await run("desktop.win32.getSelectedText", {
                        "sessionId": sid, "elementId": el0,
                    })
                    out[f"unselected_getSelectedText_{label}"] = await shape(r0)

                # --- 1/2/3：ListBox 与 ComboBox ---------------------------------
                for label, cls, picks, miss in (
                    ("listbox", list_cls,
                     [("value", "beta"), ("label", "gamma"), ("index", "0")], "delta"),
                    ("combobox", combo_cls,
                     [("value", "two"), ("label", "three"), ("index", "0")], "four"),
                ):
                    el = await find({"backend": "win32", "className": cls})
                    ok_rows = []
                    for select_by, value in picks:
                        r = await run("desktop.win32.select", {
                            "sessionId": sid, "elementId": el,
                            "value": value, "selectBy": select_by,
                        })
                        ok_rows.append({"selectBy": select_by, "value": value,
                                        **(await shape(r))})
                    out[f"select_ok_{label}"] = ok_rows

                    r = await run("desktop.win32.select", {
                        "sessionId": sid, "elementId": el,
                        "value": miss, "selectBy": "label",
                    })
                    out[f"select_miss_{label}"] = await shape(r)

                    r = await run("desktop.win32.getSelectedText", {
                        "sessionId": sid, "elementId": el,
                    })
                    out[f"getSelectedText_{label}"] = await shape(r)

                # --- 4：非列表控件（Submit 按钮）-------------------------------
                submit = await find({"backend": "win32", "title": "Submit"})
                for command, inputs in (
                    ("desktop.win32.select",
                     {"elementId": submit, "value": "one", "selectBy": "label"}),
                    ("desktop.win32.getSelectedText", {"elementId": submit}),
                ):
                    r = await run(command, {"sessionId": sid, **inputs})
                    out[f"nonlist_{command.split('.')[-1]}"] = await shape(r)

                # --- 5：ComboBox 未选过时的 getSelectedText（负索引对照）---------
                # 演示程序 ComboBox 初始无选中（Text 为空、SelectedIndex=-1）。
                # 若实现误用 selected_text()，这里会回 "three"（最后一项）。
                combo_el = await find({"backend": "win32", "className": combo_cls})
                r = await run("desktop.win32.getSelectedText", {
                    "sessionId": sid, "elementId": combo_el,
                })
                out["combobox_initial_selection"] = await shape(r)
            finally:
                await ex.close()

        asyncio.run(scenario())
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        desktop_fixture.kill_demo_apps()
        shutil.rmtree(base, ignore_errors=True)

    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
