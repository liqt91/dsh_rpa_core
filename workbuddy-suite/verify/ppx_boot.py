"""PPX 窗口的真机启动验证。

这个脚本回答三个问题，缺一个都不算「能跑」：
  1. 窗口真的起来了？（按标题找窗口，量尺寸）
  2. 前端真的连上了 Python 侧？（会话服务看到编辑窗口轮询、并报出正在编辑的文件）
  3. 会话真的把「打开这个文件」的请求递进去了？（先 queue 一个 open，再看到 editor.path 变成目标）

跑法（要开窗，约 60~90 秒）：
    ./.venv/Scripts/python.exe verify/ppx_boot.py
前置：vite 已经在 5273 上跑（`cd editor/gui && pnpm run dev -- --port 5273 --strictPort`）。
"""

from __future__ import annotations

import ctypes
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from ctypes import wintypes as wt
from pathlib import Path

SUITE = Path(__file__).resolve().parents[1]
REPO = SUITE.parent
PPX = REPO / ".harness" / "demo" / "ppx_probe" / ".venv" / "Scripts" / "ppx.exe"
EDITOR = SUITE / "editor"
STUDIO = SUITE / "skills" / "workflow-studio" / "scripts" / "studio.py"
PORT = 7793
TITLE = "WorkBuddy Studio"

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.FindWindowW.restype = wt.HWND
user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.SetWindowPos.argtypes = [wt.HWND, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_uint]
user32.SetForegroundWindow.argtypes = [wt.HWND]
HWND_TOPMOST = ctypes.c_void_p(-1)
SWP_NOMOVE, SWP_NOSIZE, SWP_SHOWWINDOW = 0x0002, 0x0001, 0x0040
WM_CLOSE = 0x0010
WS_EX_TOPMOST = 0x00000008

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""), flush=True)
    if not ok:
        failures.append(label)


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        return probe.connect_ex(("127.0.0.1", port)) != 0


def wait_for(fn, timeout: float, interval: float = 0.4):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(interval)
    return None


def http_json(route: str) -> dict:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f"http://127.0.0.1:{PORT}{route}", timeout=3) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    if not PPX.is_file():
        print(f"找不到 ppx：{PPX}")
        return 2
    if not port_free(PORT):
        print(f"端口 {PORT} 已被占用，先清理")
        return 2
    if port_free(5273):
        print("5273 上没有前端服务：先在 editor/gui 里起 pnpm dev --port 5273")
        return 2

    env = dict(os.environ)
    env["STUDIO_HOME"] = str(SUITE / ".tmp-test")
    env["PYTHONIOENCODING"] = "utf-8"
    env["STUDIO_VERBOSE"] = "1"
    target = SUITE / ".tmp-test" / "weekly-report.json"
    target.write_text(
        json.dumps(
            {
                "name": "weekly-report",
                "version": 1,
                "root": {
                    "type": "sequence",
                    "id": "n1",
                    "children": [{"type": "command", "id": "n2", "command": "browser.launch"}],
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    server = subprocess.Popen(
        [sys.executable, str(STUDIO), "serve", "--port", str(PORT)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
    )
    window = None
    ppx = None
    try:
        ready = wait_for(lambda: _healthy(), 12)
        check("会话服务启动", bool(ready), f"port {PORT}")
        if not ready:
            return 1

        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        request = urllib.request.Request(
            f"http://127.0.0.1:{PORT}/open",
            data=json.dumps({"path": str(target), "reason": "请确认这版流程"}).encode("utf-8"),
            method="POST",
        )
        request.add_header("Content-Type", "application/json")
        with opener.open(request, timeout=4) as response:
            queued = json.loads(response.read().decode("utf-8"))
        check("会话收下「打开文件」请求", bool(queued.get("ok")), queued.get("path", ""))

        ppx = subprocess.Popen(
            [str(PPX), "dev", "--skip-frontend"],
            cwd=str(EDITOR),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        deadline = time.time() + 90
        while time.time() < deadline:
            window = user32.FindWindowW(None, TITLE)
            if window:
                break
            time.sleep(0.5)
        check("PPX 窗口出现", bool(window), TITLE if window else "90s 内没等到")

        if window:
            # 把窗口收进屏幕内再截图：屏幕比窗口小的时候，ImageGrab 会抓到背后的桌面
            user32.SetWindowPos(window, HWND_TOPMOST, 0, 0, 1040, 660, SWP_SHOWWINDOW)
            time.sleep(0.8)
            # 用 DWM 的扩展边框：GetWindowRect 含阴影，抓进来会留一圈桌面色
            rect = wt.RECT()
            dwmapi = ctypes.WinDLL("dwmapi")
            DWMWA_EXTENDED_FRAME_BOUNDS = 9
            if dwmapi.DwmGetWindowAttribute(
                window, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect), ctypes.sizeof(rect)
            ) != 0:
                user32.GetWindowRect(window, ctypes.byref(rect))
            width, height = rect.right - rect.left, rect.bottom - rect.top
            check("窗口尺寸合理", width > 700 and height > 500, f"{width}x{height}")

            seen = wait_for(lambda: _editor_path(target), 25)
            check("编辑窗口已连上 Python 侧", bool(seen), seen or "25s 内没有轮询记录")

            # 轮询只是「请求发到了」，这里要的是「文件真的进了编辑器」：
            # api 在 openFile 成功后才会 POST 一条 opened 事件。
            opened = wait_for(lambda: _opened_event(target), 25)
            check("文件真的载入编辑器（收到 opened 事件）", bool(opened),
                  opened or "25s 内没有 opened 事件")

            # 置顶 + 等一帧：ImageGrab 抓的是屏幕区域，窗口没画完会抓到背后的内容
            flags = SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW
            user32.SetWindowPos(window, HWND_TOPMOST, 0, 0, 0, 0, flags)
            user32.SetForegroundWindow(window)
            time.sleep(1.8)
            shot = SUITE / "listing" / "screenshots" / "editor-window.png"
            shot.parent.mkdir(parents=True, exist_ok=True)
            try:
                from PIL import ImageGrab

                # 内缩 1px：窗口圆角外的像素属于桌面，抓进来会留下脏边
                box = (rect.left + 1, rect.top + 1, rect.right - 1, rect.bottom - 1)
                ImageGrab.grab(bbox=box).convert("RGB").save(shot)
                print(f"[SHOT] {shot}")
            except Exception as exc:  # noqa: BLE001 - 截图失败不该让整条验证断掉
                check("窗口截图", False, str(exc))

            user32.PostMessageW(window, WM_CLOSE, 0, 0)
            t0 = time.time()
            # FindWindowW 找不到时按 restype=HWND 返回的是 None，不是 0 —— 写 `== 0` 永远为假，
            # 这条断言就会变成「靠 or 的另一半过」的假判据。
            closed = wait_for(lambda: not user32.FindWindowW(None, TITLE), 25)
            check("关窗后窗口消失", bool(closed), f"{time.time() - t0:.1f}s")
            # 窗口消失到进程真正退出之间还有几十毫秒（pywebview 收尾），不能一关窗就问。
            exited = wait_for(lambda: ppx.poll() is not None, 20)
            check("关窗后进程退出", bool(exited), f"rc={ppx.poll()}")
    finally:
        if ppx and ppx.poll() is None:
            ppx.terminate()
            try:
                ppx.wait(timeout=8)
            except subprocess.TimeoutExpired:
                ppx.kill()
        if ppx and ppx.stdout:
            tail = ppx.stdout.read()[-1500:]
            if "[失败]" in tail or "Error" in tail:
                print("--- ppx dev 尾部输出 ---")
                print(tail)
        try:
            server.terminate()
            server.wait(timeout=5)
        except Exception:  # noqa: BLE001
            server.kill()
        if server.stdout:
            server.stdout.close()

    print(f"\n{len(failures)} failed")
    return 0 if not failures else 1


def _healthy() -> bool:
    try:
        return bool(http_json("/health").get("ok"))
    except Exception:  # noqa: BLE001
        return False


def _opened_event(target: Path) -> str | None:
    try:
        payload = http_json("/events?since=0&kinds=opened&timeout=0.2")
    except Exception:  # noqa: BLE001
        return None
    for event in payload.get("events") or []:
        if Path(event.get("path", "")).resolve() == target.resolve():
            return f"#{event['id']} opened {event['path']}"
    return None


def _editor_path(target: Path) -> str | None:
    try:
        editor = http_json("/health").get("editor") or {}
    except Exception:  # noqa: BLE001
        return None
    if editor.get("online") and editor.get("path"):
        same = Path(editor["path"]).resolve() == target.resolve()
        return f"{editor['path']}（匹配={same}）" if same else None
    return None


if __name__ == "__main__":
    raise SystemExit(main())
