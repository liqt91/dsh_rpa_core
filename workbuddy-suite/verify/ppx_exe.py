"""发布态验证：用打包出来的 exe 起窗口，跑通「会话推文件 → 编辑器载入」。

跟 ppx_boot.py 的区别只有一个：那边跑 `ppx dev --skip-frontend`（开发态，依赖 vite），
这边跑 `studio.py gui --exe`（发布态，前端已打进 exe）。两个都过，才算「开发态和发布态都能用」。

跑法（要开窗，约 40~60 秒）：
    ./.venv/Scripts/python.exe verify/ppx_exe.py
前置：先 `ppx build`，产物在 editor/build/WorkBuddy Studio/。
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
import urllib.request
from ctypes import wintypes as wt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ppx_boot as boot  # noqa: E402 - 先加 sys.path 才能导入同目录模块

SUITE = boot.SUITE
EDITOR = boot.EDITOR
STUDIO = boot.STUDIO
PORT = boot.PORT
TITLE = boot.TITLE
check = boot.check
wait_for = boot.wait_for
user32 = boot.user32
WM_CLOSE = boot.WM_CLOSE

EXE = EDITOR / "build" / "WorkBuddy Studio" / "WorkBuddy Studio.exe"


def http_post(route: str, payload: dict) -> dict:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(
        f"http://127.0.0.1:{PORT}{route}",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
    )
    request.add_header("Content-Type", "application/json")
    with opener.open(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    if not EXE.is_file():
        print(f"找不到打包产物：{EXE}\n先跑 ppx build")
        return 2
    if not boot.port_free(PORT):
        print(f"端口 {PORT} 已被占用，先清理")
        return 2

    env = dict(os.environ)
    env["STUDIO_HOME"] = str(SUITE / ".tmp-test")
    env["PYTHONIOENCODING"] = "utf-8"
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

    # 走「一条命令」的发布态入口：会话服务 + exe 窗口
    gui = subprocess.Popen(
        [sys.executable, str(STUDIO), "gui", "--port", str(PORT), "--exe", str(EXE)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    window = None
    try:
        ready = wait_for(boot._healthy, 15)
        check("gui 子命令把会话服务拉起来了", bool(ready), f"port {PORT}")
        if not ready:
            return 1

        queued = http_post("/open", {"path": str(target), "reason": "请确认这版流程"})
        check("会话收下「打开文件」请求", bool(queued.get("ok")), queued.get("path", ""))

        deadline = time.time() + 120
        while time.time() < deadline:
            window = user32.FindWindowW(None, TITLE)
            if window:
                break
            time.sleep(0.5)
        check("打包后的 exe 起出窗口", bool(window), TITLE if window else "120s 内没等到")
        if not window:
            return 1

        user32.SetWindowPos(window, boot.HWND_TOPMOST, 0, 0, 1040, 660, boot.SWP_SHOWWINDOW)
        time.sleep(0.8)
        rect = wt.RECT()
        dwmapi = ctypes.WinDLL("dwmapi")
        DWMWA_EXTENDED_FRAME_BOUNDS = 9
        if dwmapi.DwmGetWindowAttribute(
            window, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect), ctypes.sizeof(rect)
        ) != 0:
            user32.GetWindowRect(window, ctypes.byref(rect))
        width, height = rect.right - rect.left, rect.bottom - rect.top
        check("窗口尺寸合理", width > 700 and height > 500, f"{width}x{height}")

        seen = wait_for(lambda: boot._editor_path(target), 30)
        check("exe 里的前端连上了 Python 侧", bool(seen), seen or "30s 内没有轮询记录")

        opened = wait_for(lambda: boot._opened_event(target), 30)
        check("文件真的载入编辑器（收到 opened 事件）", bool(opened),
              opened or "30s 内没有 opened 事件")

        # 截图先落到 _ 前缀（.gitignore 里忽略），确认没问题再决定要不要替换上架图
        flags = boot.SWP_NOMOVE | boot.SWP_NOSIZE | boot.SWP_SHOWWINDOW
        user32.SetWindowPos(window, boot.HWND_TOPMOST, 0, 0, 0, 0, flags)
        user32.SetForegroundWindow(window)
        time.sleep(1.8)
        shot = SUITE / "listing" / "screenshots" / "_exe-window.png"
        try:
            from PIL import ImageGrab

            box = (rect.left + 1, rect.top + 1, rect.right - 1, rect.bottom - 1)
            ImageGrab.grab(bbox=box).convert("RGB").save(shot)
            print(f"[SHOT] {shot}")
        except Exception as exc:  # noqa: BLE001 - 截图失败不该让整条验证断掉
            check("窗口截图", False, str(exc))

        user32.PostMessageW(window, WM_CLOSE, 0, 0)
        t0 = time.time()
        # FindWindowW 找不到时按 restype=HWND 返回的是 None，不是 0 —— 写 `== 0` 永远为假
        closed = wait_for(lambda: not user32.FindWindowW(None, TITLE), 60)
        # 打包版（PyInstaller one-dir + WebView2）拆窗口比开发态慢，实测要几十秒，
        # 所以这里既断言「窗口最终消失」，也把耗时打出来 —— 慢到离谱也要能看出来。
        check("关窗后窗口消失", bool(closed), f"{time.time() - t0:.1f}s")

        exited = wait_for(lambda: gui.poll() is not None, 20)
        check("关窗后 gui（含会话服务）退出", bool(exited), f"rc={gui.poll()}")
    finally:
        if gui.poll() is None:
            gui.terminate()
            try:
                gui.wait(timeout=8)
            except subprocess.TimeoutExpired:
                gui.kill()
        if gui.stdout:
            tail = gui.stdout.read()[-1500:]
            if "Traceback" in tail or "[失败]" in tail:
                print("--- gui 尾部输出 ---")
                print(tail)

    print(f"\n{len(boot.failures)} failed")
    return 0 if not boot.failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
