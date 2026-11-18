"""M79 真机复验：DPI / 遮挡 / 多窗口 / 微信自绘（不依赖 csc 靶子）。

⚠ 会启动真实窗口并抢前台；只在 RPA_DESKTOP_E2E=1 时运行。
微信只取窗口矩形与一小块 UI 模板做技术命中，不读聊天文本。

跑法：RPA_DESKTOP_E2E=1 ./.venv/Scripts/python.exe \
      .harness/spike/probe_m79_real_verification.py
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULTS: list[dict] = []


def record(name: str, ok: bool | None, detail: str) -> None:
    state = "PASS" if ok is True else ("FAIL" if ok is False else "SKIP")
    RESULTS.append({"check": name, "state": state, "detail": detail})
    print(f"{state} | {name} — {detail}", flush=True)


def run_cli(argv: list[str]) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "rpa_core.cli", *argv],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False,
    )
    return proc.returncode, (proc.stdout or "").strip()


def write_element(workflows: Path, name: str, doc: dict) -> None:
    target = workflows / "demo" / "elements"
    target.mkdir(parents=True, exist_ok=True)
    (target / f"{name}.json").write_bytes(
        json.dumps(doc, ensure_ascii=False).replace("\r\n", "\n").encode("utf-8")
    )


def live_verify(workflows: Path, name: str, *, shot: bool = False) -> dict | None:
    argv = ["elements", "verify", "--flow", "demo", "--workflows", str(workflows),
            "--live", "--mode", "flash"]
    if shot:
        argv.append("--shot")
    argv.append(name)
    code, out = run_cli(argv)
    if code != 0:
        record(f"live verify {name}", False, f"rc={code} out={out[:200]}")
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        record(f"live verify {name}", False, f"non-JSON {out[:200]} ({exc})")
        return None


def _process_path(pid: int) -> str:
    """按 pid 取可执行文件全路径（识别微信 4.x 只能靠这个，类名不可靠）。"""
    k32 = ctypes.windll.kernel32
    handle = k32.OpenProcess(0x1000, False, pid)
    if not handle:
        return ""
    from ctypes import wintypes

    buffer = ctypes.create_unicode_buffer(4096)
    size = wintypes.DWORD(4096)
    ok = k32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size))
    k32.CloseHandle(handle)
    return buffer.value if ok else ""


def enum_windows() -> list[tuple[int, str, str]]:
    import win32gui

    rows: list[tuple[int, str, str]] = []

    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            rows.append((hwnd, win32gui.GetClassName(hwnd), win32gui.GetWindowText(hwnd)))
        return True

    win32gui.EnumWindows(cb, None)
    return rows


def wait_for_pid_window(pid: int, timeout: float = 12.0) -> int | None:
    import win32process

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for hwnd, _, _ in enum_windows():
            if win32process.GetWindowThreadProcessId(hwnd)[1] == pid:
                return hwnd
        time.sleep(0.2)
    return None


def pick_uia_child(hwnd: int):
    from pywinauto import Desktop

    window = Desktop(backend="uia").window(handle=hwnd)
    for item in window.descendants():
        info = item.element_info
        ctype = getattr(info, "control_type", None)
        if ctype not in ("Edit", "Document"):
            continue
        box = item.rectangle()
        if box.right <= box.left or box.bottom <= box.top:
            continue
        locator: dict = {"backend": "uia", "controlType": ctype}
        aid = getattr(info, "automation_id", None)
        name = getattr(info, "name", None)
        if aid:
            locator["automationId"] = aid
        elif name:
            locator["name"] = name
        return locator, (box.left + box.right) // 2, (box.top + box.bottom) // 2
    return None


def main() -> int:
    if os.environ.get("RPA_DESKTOP_E2E") != "1":
        print("SKIP: 真机会抢前台；设 RPA_DESKTOP_E2E=1 才运行")
        return 0
    import win32con
    import win32gui

    user32 = ctypes.windll.user32
    dpi = user32.GetDpiForSystem() if hasattr(user32, "GetDpiForSystem") else 96
    record("DPI 取证", True, f"系统 DPI={dpi}，缩放≈{round(dpi / 96 * 100)}%（未切系统档位）")

    work = Path("rpa-m79-real")
    if work.exists():
        import shutil
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)
    workflows = work / "workflows"

    note = subprocess.Popen(["notepad.exe"])
    try:
        hwnd = wait_for_pid_window(note.pid)
        if not hwnd:
            record("真窗口靶子", False, "没等到记事本窗口")
            return 1
        record("真窗口靶子", True, f"notepad hwnd={hwnd}")
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetWindowPos(hwnd, win32con.HWND_TOP, 80, 80, 700, 520,
                              win32con.SWP_SHOWWINDOW)
        time.sleep(0.4)

        picked = pick_uia_child(hwnd)
        if not picked:
            record("语义控件取证", False, "记事本 UIA 树里没找到 Edit/Document")
            return 1
        locator, cx, cy = picked
        record("语义控件取证", True, f"locator={locator} point=({cx},{cy})")

        semantic = {"kind": "desktop", "selector": {"locator": locator},
                    "verifyCount": 1, "metadata": {"windowHandle": hwnd}}
        write_element(workflows, "semantic", semantic)
        live = live_verify(workflows, "semantic", shot=True)
        if live:
            ok = live.get("count") == 1 and live.get("windowHandle") == hwnd \
                and bool(live.get("rect")) and bool(live.get("shot"))
            record("语义校验 + flash + shot", ok,
                   json.dumps(live, ensure_ascii=False)[:220])

        code, out = run_cli(["capture", "desktop", "--point", f"{cx},{cy}",
                             "--window-handle", str(hwnd), "--image"])
        if code != 0:
            record("按图捕获", False, f"rc={code} {out[:180]}")
        else:
            try:
                captured = json.loads(out)
                img = captured.get("selector", {}).get("locator", {})
                ok = img.get("driver") == "image" and bool(img.get("imageTemplate")) \
                    and bool(img.get("templateSize"))
                record("按图捕获", ok,
                       f"driver={img.get('driver')} size={img.get('templateSize')}")
                if ok:
                    write_element(workflows, "image", captured)
                    shot = live_verify(workflows, "image", shot=True)
                    if shot:
                        record("图像校验命中", shot.get("count", 0) >= 1,
                               json.dumps(shot, ensure_ascii=False)[:220])
            except json.JSONDecodeError as exc:
                record("按图捕获", False, f"non-JSON {out[:180]} ({exc})")

        # 遮挡 + 多窗口 Z 序：第二个记事本钉在第一个上面，再校验同一语义元素。
        cover = subprocess.Popen(["notepad.exe"])
        try:
            cover_hwnd = wait_for_pid_window(cover.pid)
            if cover_hwnd is None or cover_hwnd == hwnd:
                record("遮挡靶子", None, "没拿到第二个窗口")
            else:
                win32gui.SetWindowPos(
                    cover_hwnd, win32con.HWND_TOPMOST,
                    max(0, cx - 120), max(0, cy - 80), 380, 260,
                    win32con.SWP_SHOWWINDOW,
                )
                time.sleep(0.5)
                live2 = live_verify(workflows, "semantic")
                if live2:
                    ok = live2.get("count") == 1 and live2.get("windowHandle") == hwnd
                    record("遮挡下跨窗口命中", ok,
                           f"cover={cover_hwnd} {json.dumps(live2, ensure_ascii=False)[:180]}")
                fg = user32.GetForegroundWindow()
                record("flash 前置命中窗", fg == hwnd, f"foreground={fg} target={hwnd}")
        finally:
            cover.terminate()
            try:
                cover.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cover.kill()

        # ★ 微信 4.x 是 Qt 窗口（类名 Qt51514QWindowIcon），可执行文件叫 Weixin.exe。
        # 只按类名/标题判会漏掉它（M81 真机实锤：这样判出来是「没有可见窗口」）。
        import win32process

        wechat = []
        for h, cls, title in enum_windows():
            _, pid = win32process.GetWindowThreadProcessId(h)
            exe = _process_path(pid).lower()
            if any(tok in exe for tok in ("weixin.exe", "wechat.exe", "wxwork.exe")):
                wechat.append((h, cls, title))
            elif cls in ("WeChatMainWndForPC", "WeChat") or "微信" in title:
                wechat.append((h, cls, title))
        if not wechat:
            record("微信自绘区", None, "本机没有可见微信窗口，场景未覆盖")
        else:
            wh, _, title = wechat[0]
            if win32gui.GetWindowPlacement(wh)[1] == win32con.SW_SHOWMINIMIZED:
                win32gui.ShowWindow(wh, win32con.SW_RESTORE)
                time.sleep(0.3)
            wr = win32gui.GetWindowRect(wh)
            w, h = wr[2] - wr[0], wr[3] - wr[1]
            screen_w = user32.GetSystemMetrics(0)
            screen_h = user32.GetSystemMetrics(1)
            offscreen = wr[2] <= 0 or wr[0] >= screen_w or wr[3] <= 0 or wr[1] >= screen_h
            move_failed = False
            if offscreen:
                # 真机微信可能停在副屏关闭后的负坐标；只为捕获把它移回主屏，不改内容。
                try:
                    win32gui.SetWindowPos(
                        wh, win32con.HWND_TOP, 100, 100,
                        min(w, 900), min(h, 600), win32con.SWP_SHOWWINDOW,
                    )
                except Exception as exc:  # 提权窗口/跨会话窗口会拒绝移动，不能算实现失败
                    record("微信自绘区", None, f"窗口在屏外且无法移回主屏: {exc}")
                    move_failed = True
                else:
                    time.sleep(0.4)
                    wr = win32gui.GetWindowRect(wh)
                    w, h = wr[2] - wr[0], wr[3] - wr[1]
            if move_failed:
                pass
            elif w < 200 or h < 200:
                record("微信自绘区", None, f"窗口太小 {w}x{h}")
            else:
                px, py = wr[0] + min(80, w // 4), wr[1] + min(80, h // 4)
                code, out = run_cli(["capture", "desktop", "--point", f"{px},{py}",
                                     "--window-handle", str(wh), "--image"])
                if code != 0:
                    record("微信自绘区", False, f"capture rc={code} {out[:180]}")
                else:
                    doc = json.loads(out)
                    if doc.get("timeout") or doc.get("error") or doc.get("kind") != "desktop":
                        record("微信自绘区", False,
                               f"capture 无效产物 {json.dumps(doc, ensure_ascii=False)[:180]}")
                    else:
                        write_element(workflows, "wechat", doc)
                        result = live_verify(workflows, "wechat")
                        record("微信自绘区", bool(result and result.get("count", 0) >= 1),
                               f"title={title[:24]} point=({px},{py}) "
                               f"result={json.dumps(result, ensure_ascii=False)[:160]}")
    finally:
        note.terminate()
        try:
            note.wait(timeout=5)
        except subprocess.TimeoutExpired:
            note.kill()

    passed = sum(1 for r in RESULTS if r["state"] == "PASS")
    failed = sum(1 for r in RESULTS if r["state"] == "FAIL")
    skipped = sum(1 for r in RESULTS if r["state"] == "SKIP")
    print(f"M79 REAL CHECK: {passed} pass / {failed} fail / {skipped} skip", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
