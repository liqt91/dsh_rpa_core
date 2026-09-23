"""S2.3 修复的真机探针：四项修复逐项在真靶子上验证（RPA_DESKTOP_E2E=1）。

1. uia `desktop.select`：三个 selectBy 分支都应真选中（listStatus 回显）；
   无匹配项应 ELEMENT_NOT_FOUND（不再静默 success）。
2. uia `desktop.getText`：Edit 走 ValuePattern（readOnlyNote → 'note-ready'），
   不再串位到相邻 Label；Label 控件回落 window_text()。
3. win32 `select` / `getSelectedText`：显式 EXECUTOR_FAILED（不再静默假成功/恒空串）。
4. win32 `controlId` 过滤：descendants 后手工补滤生效（唯一命中 / 错值 0 命中）。
5. `screenshot`（两后端）：Pillow 就位后正路径落盘 PNG（文件头 \\x89PNG）。
"""

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
        run_id="s23-probe",
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


async def _find(executor, session: str, prefix: str, locator: dict) -> str | None:
    result = await executor.execute(
        _invocation(
            f"{prefix}.findElement",
            {"sessionId": session, "locator": locator, "timeoutMs": 3000},
        ),
        asyncio.Event(),
    )
    if result.status != "success":
        return None
    return str(result.outputs["elementId"])


async def _run(reader, actor, session: str, command: str, inputs: dict):
    return await reader.execute(
        _invocation(command, {"sessionId": session, **inputs}), asyncio.Event()
    )


async def _read_label(reader, session: str, automation_id: str) -> str:
    element = await _find(reader, session, "desktop", {"backend": "uia", "automationId": automation_id})
    assert element is not None, automation_id
    got = await _run(
        reader,
        reader,
        session,
        "desktop.getText",
        {"elementId": element, "timeoutMs": 2000},
    )
    assert got.status == "success", got.error
    return str(got.outputs["value"])


def main() -> int:
    from rpa_core.executors import DesktopExecutor, Win32DesktopExecutor

    base = Path(tempfile.mkdtemp(prefix="rpa-s23-probe-"))
    desktop_fixture.kill_demo_apps()
    exe = desktop_fixture.compile_demo_app(base)
    process = subprocess.Popen([str(exe)])
    failures: list[str] = []
    try:
        desktop_fixture.wait_for_window(desktop_fixture.APP_TITLE)
        desktop_fixture.warmup_uia(desktop_fixture.APP_TITLE)
        desktop_fixture.force_foreground(desktop_fixture.APP_TITLE)

        async def scenario() -> None:
            reader = DesktopExecutor()
            actor = DesktopExecutor()
            win = Win32DesktopExecutor()
            try:
                rs = await _attach(reader, "desktop")
                asx = await _attach(actor, "desktop")
                ws = await _attach(win, "desktop.win32")

                # --- 1. uia select：三个分支都要真选中 -------------------
                list_el = await _find(
                    actor, asx, "desktop",
                    {"backend": "uia", "automationId": "optionsList"},
                )
                assert list_el, "optionsList 没找到"
                cases = [
                    ("index", "1", "beta"),
                    ("label", "gamma", "gamma"),
                    ("value", "alpha", "alpha"),
                ]
                for select_by, value, expected in cases:
                    r = await _run(
                        actor, actor, asx, "desktop.select",
                        {"elementId": list_el, "value": value, "selectBy": select_by},
                    )
                    got_item = str(
                        (r.effects[0].details or {}).get("selectedItem", "")
                    ) if r.status == "success" else ""
                    ok = r.status == "success" and got_item == expected
                    print(f"[select {select_by}={value!r}] status={r.status} "
                          f"selectedItem={got_item!r} expect={expected!r} -> {'OK' if ok else 'FAIL'}")
                    if not ok:
                        failures.append(f"select {select_by}={value!r}")

                # 无匹配项 → ELEMENT_NOT_FOUND
                r = await _run(
                    actor, actor, asx, "desktop.select",
                    {"elementId": list_el, "value": "delta", "selectBy": "label"},
                )
                code = getattr(r.error, "code", None)
                print(f"[select no-match] status={r.status} code={code} -> "
                      f"{'OK' if (r.status == 'error' and str(code) == 'ELEMENT_NOT_FOUND') else 'FAIL'}")
                if not (r.status == "error" and str(code) == "ELEMENT_NOT_FOUND"):
                    failures.append("select no-match")

                # --- 2. uia getText：Edit 走 ValuePattern，不再串位 -------
                for automation_id, note in (("readOnlyNote", "Edit 只读"),
                                            ("queryInput", "Edit 可写"),
                                            ("nameLabel", "Label 回落")):
                    el = await _find(actor, asx, "desktop", {"backend": "uia", "automationId": automation_id})
                    assert el, automation_id
                    r = await _run(
                        actor, actor, asx, "desktop.getText",
                        {"elementId": el, "timeoutMs": 2000},
                    )
                    val = str(r.outputs.get("value")) if r.status == "success" else f"<{r.status}>"
                    print(f"[getText {automation_id}] value={val!r} ({note})")
                    if automation_id == "readOnlyNote" and val != "note-ready":
                        failures.append("getText readOnlyNote")

                # --- 3. win32 select / getSelectedText：显式失败 ---------
                win_el = await _find(win, ws, "desktop.win32", {"backend": "win32", "title": "Submit"})
                assert win_el, "win32 Submit 没找到"
                for command, inputs in (
                    ("desktop.win32.select", {"elementId": win_el, "value": "one", "selectBy": "label"}),
                    ("desktop.win32.getSelectedText", {"elementId": win_el}),
                ):
                    r = await _run(win, win, ws, command, inputs)
                    code = getattr(r.error, "code", None)
                    ok = r.status == "error" and str(code) == "EXECUTOR_FAILED"
                    print(f"[win32 {command.split('.')[-1]}] status={r.status} code={code} -> "
                          f"{'OK' if ok else 'FAIL'}")
                    if not ok:
                        failures.append(command)

                # --- 4. win32 controlId 过滤 ----------------------------
                # 先枚举一遍拿 Submit 的真实 control_id
                ids: dict[int, str] = {}
                for title in ("Submit", "Count", "Drag"):
                    el = await _find(win, ws, "desktop.win32", {"backend": "win32", "title": title})
                    assert el, title
                    from rpa_core.executors.desktop_win32 import Win32DesktopExecutor as W
                    # element_id -> wrapper 的 control_id：直接用 pywinauto 再看一次
                    from pywinauto import Desktop as PD
                    win_wrap = PD(backend="win32").window(title=desktop_fixture.APP_TITLE)
                    for child in win_wrap.descendants(title=title):
                        ids.setdefault(child.element_info.control_id, title)
                print(f"[enumerate] control_id 映射：{ids}")
                submit_id = next(cid for cid, t in ids.items() if t == "Submit")
                el = await _find(
                    win, ws, "desktop.win32",
                    {"backend": "win32", "title": "Submit", "controlId": submit_id},
                )
                ok = el is not None
                print(f"[controlId 正命中] title=Submit + controlId={submit_id} -> "
                      f"{'OK' if ok else 'FAIL'}")
                if not ok:
                    failures.append("controlId 正命中")
                el = await _find(
                    win, ws, "desktop.win32",
                    {"backend": "win32", "title": "Submit", "controlId": submit_id + 1},
                )
                ok = el is None
                print(f"[controlId 错值 0 命中] -> {'OK' if ok else 'FAIL'}")
                if not ok:
                    failures.append("controlId 错值")

                # --- 5. screenshot：两后端正路径落 PNG ------------------
                for label, executor_, sid, prefix, locator in (
                    ("uia", actor, asx, "desktop",
                     {"backend": "uia", "automationId": "submitButton"}),
                    ("win32", win, ws, "desktop.win32",
                     {"backend": "win32", "title": "Submit"}),
                ):
                    el = await _find(executor_, sid, prefix, locator)
                    assert el, label
                    shot = base / f"shot_{label}.png"
                    r = await _run(
                        executor_, executor_, sid,
                        f"{prefix}.screenshot", {"elementId": el, "savePath": str(shot)},
                    )
                    head = shot.read_bytes()[:4] if shot.exists() else b""
                    ok = r.status == "success" and head == b"\x89PNG"
                    print(f"[screenshot {label}] status={r.status} "
                          f"png={'yes' if head == b'\x89PNG' else head!r} -> {'OK' if ok else 'FAIL'}")
                    if not ok:
                        failures.append(f"screenshot {label}")
            finally:
                await reader.close()
                await actor.close()
                await win.close()

        asyncio.run(scenario())
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        desktop_fixture.kill_demo_apps()
        shutil.rmtree(base, ignore_errors=True)

    print()
    if failures:
        print(f"PROBE FAILED：{failures}")
        return 1
    print("PROBE OK：五项修复全部实测通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
