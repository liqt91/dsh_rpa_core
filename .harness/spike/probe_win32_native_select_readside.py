"""尾巴 #1 的读侧验证：原生 select 后 UI 到底反不反应（逐步隔离）。

结论候选：pywinauto 的 ListBoxWrapper/ComboBoxWrapper.select() 在原生消息之后
**显式补发** LBN_SELCHANGE / CBN_SELCHANGE（`notify_parent` → post WM_COMMAND），
因此 WinForms 的 SelectedIndexChanged 会触发、状态回显 Label 会变。
本探针逐步 select 并在每步之间读 Label，若每步都跟着变则可定论。

跑法：

    .venv/Scripts/python.exe .harness/spike/probe_win32_native_select_readside.py
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
        run_id="win32-readside-probe",
        step_id="step",
        inputs=inputs,
    )


def _read_label(automation_id: str) -> str:
    from rpa_core.executors import DesktopExecutor

    async def go() -> str:
        uia = DesktopExecutor()
        try:
            attached = await uia.execute(
                _invocation("desktop.attachWindow", {"title": desktop_fixture.APP_TITLE}),
                asyncio.Event(),
            )
            assert attached.status == "success", attached.error
            sid = str(attached.outputs["sessionId"])
            el = await uia.execute(
                _invocation("desktop.findElement", {
                    "sessionId": sid,
                    "locator": {"backend": "uia", "automationId": automation_id},
                    "timeoutMs": 3000,
                }),
                asyncio.Event(),
            )
            if el.status != "success":
                return f"<find {el.status}>"
            got = await uia.execute(
                _invocation("desktop.getText", {
                    "sessionId": sid,
                    "elementId": str(el.outputs["elementId"]),
                    "timeoutMs": 2000,
                }),
                asyncio.Event(),
            )
            return str(got.outputs.get("value")) if got.status == "success" \
                else f"<{got.status}>"
        finally:
            await uia.close()

    return asyncio.run(go())


def main() -> int:
    from pywinauto import Desktop as PD
    from pywinauto.controls.win32_controls import ListBoxWrapper

    from rpa_core.executors.desktop_win32 import Win32DesktopExecutor
    from rpa_core.model.desktop import DesktopLocator

    base = Path(tempfile.mkdtemp(prefix="rpa-win32-readside-"))
    desktop_fixture.kill_demo_apps()
    exe = desktop_fixture.compile_demo_app(base)
    process = subprocess.Popen([str(exe)])
    steps: list[dict] = []
    try:
        desktop_fixture.wait_for_window(desktop_fixture.APP_TITLE)
        desktop_fixture.warmup_uia(desktop_fixture.APP_TITLE)
        desktop_fixture.force_foreground(desktop_fixture.APP_TITLE)

        window = PD(backend="win32").window(title=desktop_fixture.APP_TITLE)
        list_cls = next(
            c.class_name() for c in window.descendants()
            if isinstance(c, ListBoxWrapper)
        )
        locator = DesktopLocator(backend="win32", className=list_cls)
        element = Win32DesktopExecutor._pick(
            Win32DesktopExecutor._find(window, locator), None
        )
        assert isinstance(element, ListBoxWrapper), type(element)

        steps.append({"step": "before", "label": _read_label("listStatus")})
        for pick in ("beta", "gamma", "alpha"):
            element.select(pick)
            steps.append({"step": f"native select {pick!r}",
                          "label": _read_label("listStatus")})
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        desktop_fixture.kill_demo_apps()
        shutil.rmtree(base, ignore_errors=True)

    print(json.dumps(steps, ensure_ascii=False, indent=2))
    # 判定：每一步 label 都应是 list:<idx>:<item> 且随 pick 变化
    ok = (
        len(steps) == 4
        and all(s["label"].startswith("list:") for s in steps[1:])
        and len({s["label"] for s in steps[1:]}) == 3
    )
    print(f"\n# 读侧{'生效（每步都变）' if ok else '未生效/不随步变化'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
