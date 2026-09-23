"""BACKLOG 尾巴 #1 的执行器级探针：win32 原生 select/getSelectedText 能否落地。

要回答四个问题：
  Q1 `_find` 拿到的元素是不是自动包装好的 ListBoxWrapper / ComboBoxWrapper
     （pywinauto 的 `windowclasses` 正则决定 `descendants()` 的包装类型）？
  Q2 `element.select(<index>)` / `element.select(<文本>)` 能否生效？
  Q3 读回面从哪来（ListBox: selected_indices()+item_texts()；ComboBox:
     selected_index()+selected_text()）？
  Q4 原生消息会不会触发 WinForms 的 SelectedIndexChanged（状态回显 Label）——
     决定读侧只能靠 effect details 还是可以靠页面回显。

顺带证实尾巴 #2 的现实约束：ListBox/ComboBox 无窗口文本，win32 侧只能按
className / controlId 定位，而 controlId 跨运行漂移。

跑法（会开窗抢前台、编译演示程序）：

    .venv/Scripts/python.exe .harness/spike/probe_win32_native_select_e2e.py
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
        run_id="win32-native-probe",
        step_id="step",
        inputs=inputs,
    )


async def _attach(executor, prefix: str) -> str:
    result = await executor.execute(
        _invocation(f"{prefix}.attachWindow", {"title": desktop_fixture.APP_TITLE}),
        asyncio.Event(),
    )
    assert result.status == "success", result.error
    return str(result.outputs["sessionId"])


def main() -> int:
    from pywinauto import Desktop as PD
    from pywinauto.controls.win32_controls import ComboBoxWrapper, ListBoxWrapper

    from rpa_core.executors.desktop_win32 import Win32DesktopExecutor
    from rpa_core.model.desktop import DesktopLocator

    base = Path(tempfile.mkdtemp(prefix="rpa-win32-native-"))
    desktop_fixture.kill_demo_apps()
    exe = desktop_fixture.compile_demo_app(base)
    process = subprocess.Popen([str(exe)])
    findings: dict = {}
    try:
        desktop_fixture.wait_for_window(desktop_fixture.APP_TITLE)
        desktop_fixture.force_foreground(desktop_fixture.APP_TITLE)

        window = PD(backend="win32").window(title=desktop_fixture.APP_TITLE)

        # 先枚举：拿到 ListBox / ComboBox 的类名（className 跨运行稳定）
        raw = []
        for child in window.descendants():
            cls = child.class_name()
            if "LISTBOX" in cls.upper() or "COMBOBOX" in cls.upper():
                raw.append(child)
        findings["raw_children"] = [
            {
                "wrapper_type": type(c).__name__,
                "class_name": c.class_name(),
                "control_id": c.element_info.control_id,
                "window_text": c.window_text(),
            }
            for c in raw
        ]
        list_cls = next(
            c.class_name() for c in raw if isinstance(c, ListBoxWrapper)
        )
        combo_cls = next(
            c.class_name() for c in raw if isinstance(c, ComboBoxWrapper)
        )

        # --- Q1/Q2/Q3：走真实 `_find` + 原生 select + 读回 -------------------
        for label, cls, picks in (
            ("listbox", list_cls, ["beta", 2, "alpha"]),
            ("combobox", combo_cls, ["two", 2, "one"]),
        ):
            locator = DesktopLocator(backend="win32", className=cls)
            matches = Win32DesktopExecutor._find(window, locator)
            element = (
                Win32DesktopExecutor._pick(matches, locator.found_index)
                if matches else None
            )
            entry = {
                "matched_count": len(matches),
                "element_type": type(element).__name__ if element else None,
                "picks": [],
            }
            if element is not None:
                for pick in picks:
                    try:
                        element.select(pick)
                    except Exception as exc:  # noqa: BLE001
                        entry["picks"].append(
                            {"pick": pick, "error": f"{type(exc).__name__}: {exc}"}
                        )
                        continue
                    if isinstance(element, ListBoxWrapper):
                        idxs = element.selected_indices()
                        items = element.item_texts()
                        entry["picks"].append({
                            "pick": pick,
                            "selected_indices": idxs,
                            "read_back_text": items[idxs[0]] if idxs else "",
                            "item_texts": items,
                        })
                    else:
                        idx = element.selected_index()
                        entry["picks"].append({
                            "pick": pick,
                            "selected_index": idx,
                            "read_back_text": element.selected_text(),
                            "item_texts": element.item_texts(),
                        })
            findings[f"native_select_{label}"] = entry

        # --- Q4：原生消息是否触发 SelectedIndexChanged ----------------------
        from rpa_core.executors import DesktopExecutor

        async def read_list_status() -> str:
            uia = DesktopExecutor()
            try:
                us = await _attach(uia, "desktop")
                el = await uia.execute(
                    _invocation("desktop.findElement", {
                        "sessionId": us,
                        "locator": {"backend": "uia", "automationId": "listStatus"},
                        "timeoutMs": 3000,
                    }),
                    asyncio.Event(),
                )
                if el.status != "success":
                    return f"<find {el.status}>"
                got = await uia.execute(
                    _invocation("desktop.getText", {
                        "sessionId": us,
                        "elementId": str(el.outputs["elementId"]),
                        "timeoutMs": 2000,
                    }),
                    asyncio.Event(),
                )
                return str(got.outputs.get("value")) if got.status == "success" \
                    else f"<{got.status}>"
            finally:
                await uia.close()

        findings["listStatus_after_native_select"] = asyncio.run(read_list_status())
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        desktop_fixture.kill_demo_apps()
        shutil.rmtree(base, ignore_errors=True)

    print(json.dumps(findings, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
