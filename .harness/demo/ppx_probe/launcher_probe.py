"""启动器探针：`run_probe.py` 会不会骗人。

由来（本轮实测，被后台任务收尾的日志抓到）：窗口在后台跑了 **1 小时 8 分**，退出码 0，
脚本打印的却是
    `ppx dev 已启动 (pid 19648)` / `窗口已就绪`
而 `_ppx_run.log` 里只有一行
    `[失败] 端口 5173 已被占用。请修改 development.port …`
也就是说：**这次根本没有起前端**，窗口连的是上一个没收干净的 vite。脚本的三条判断
（进程起来了 / http 通 / 窗口出现了）每一条都「通过」，结论却完全错了。

三条承诺，各自对着一个真实故障：

1. **端口被占 ⇒ 拒绝启动，并报出占用者 PID。** 不静默附着到别人（或上一个）的前端上。
2. **真起来 ⇒ 日志里没有 `[失败]`；关窗后 5173 腾空。**
   后半句验的是**不变量**，不是我们的代码：`ppx dev` 关窗时自己会把 vite 收掉。
   负向验证一开始我以为要自己收，写了一段 `release_port` 兜底 —— 拿掉它再跑，
   关窗后 5173 照样腾空，说明那段是多余的，且它还有反作用：一旦第 1 条被绕过，
   它的「现在监听的只可能是我们起的」这个假设就失效，会把**别人的** vite 杀掉
   （注入实测：`reuse：完事后 vite 还活着` 变红）。所以删掉，只留这段判据。
   孤儿 vite 的真实来源是 `ppx dev` **被强杀**（比如 agent 的 shell 结束），那种情况
   任何 `finally` 都来不及跑 —— 兜底只能是第 1 条的预检。
3. **`--reuse` ⇒ 走 `--skip-frontend`，复用现有前端**，而且**不杀**它（那是别人的）。

跑法（要开窗，全量约 90s）：
    ./.venv/Scripts/python.exe launcher_probe.py
    ./.venv/Scripts/python.exe launcher_probe.py --group=1   # 只验「拒不起」
分组是为了负向验证：第 1 组注入后留下的现场会污染后两组，串着跑就分不清谁报的红。
"""

from __future__ import annotations

import ctypes
import http.server
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from ctypes import wintypes as wt
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE / "app"
PY = HERE / ".venv" / "Scripts" / "python.exe"
PORT = 5173
TITLE = "RPA Probe"

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.FindWindowW.restype = wt.HWND
user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
WM_CLOSE = 0x0010

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""), flush=True)
    if not ok:
        failures.append(label)


# --------------------------------------------------------------------------
# 端口 / 窗口 小工具（探针自己判，不 import run_probe 的实现 —— 别用被测代码验被测代码）
# --------------------------------------------------------------------------
def serving(port: int = PORT) -> bool:
    """两个地址都探：vite 默认只绑 `::1`（只探 127.0.0.1 会误判成「空着」）。"""
    for family, address in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.6)
                if probe.connect_ex((address, port)) == 0:
                    return True
        except OSError:
            continue
    return False


def wait_port(free: bool, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if serving() != free:
            return True
        time.sleep(0.25)
    return serving() != free


def window_handle() -> int:
    # 找不到时 ctypes 把 NULL 映射成 None（HWND 的 restype 是指针），不能直接 int()
    return int(user32.FindWindowW(None, TITLE) or 0)


def wait_window(timeout: float = 40.0) -> int:
    deadline = time.time() + timeout
    while time.time() < deadline:
        hwnd = window_handle()
        if hwnd:
            return hwnd
        time.sleep(0.4)
    return 0


def close_window(hwnd: int) -> None:
    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)


class Dummy:
    """占位：一个只会回 200 的 HTTP 服务，模拟「上次没退干净的前端」。"""

    def __init__(self, port: int = PORT) -> None:
        outer = self
        self.hits = 0

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - http.server 的命名
                outer.hits += 1
                body = b"<html><title>stale</title></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):  # 别刷屏
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> Dummy:
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self.server.server_close()


def kill_tree(pid: int) -> None:
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, timeout=20)


def serve_vite() -> subprocess.Popen:
    """起真 vite 当「别人的前端」：直接在 `app/gui` 里跑 vite，固定 5173。

    ★ 不能用 `app/` 那层的 `npm run dev` —— 那是 `ppx dev`（会自己再开一个窗口，
    而且 shell 里 `ppx` 不在 PATH 上，实测直接 `'ppx' 不是内部或外部命令`）。
    """
    vite = APP / "gui" / "node_modules" / ".bin" / "vite.CMD"
    if not vite.is_file():
        vite = APP / "gui" / "node_modules" / ".bin" / "vite"
    if not vite.is_file():
        raise RuntimeError(f"找不到 vite（{vite}），先在 app/gui 里 npm install")
    log = (HERE / "_launcher_vite.log").open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [str(vite), "--port", str(PORT), "--strictPort"],
        cwd=APP / "gui",
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://localhost:{PORT}/", timeout=1):
                return proc
        except Exception:
            if proc.poll() is not None:
                raise RuntimeError(
                    f"vite 退出了（{proc.returncode}），看 _launcher_vite.log"
                ) from None
            time.sleep(0.4)
    kill_tree(proc.pid)
    raise RuntimeError("vite 60s 没起来，看 _launcher_vite.log")


def launch(extra: list[str], timeout: float = 90.0) -> tuple[int, str, float]:
    """跑 run_probe.py，等到它自己退出，返回 (rc, 输出, 耗时秒)。

    耗时一起返回是必须的：「拒绝启动」和「起来卡住被超时杀掉」的 rc 都非零，
    只看 rc 分不开，而它们是完全不同的故障。
    """
    out = HERE / "_launcher_run.out"
    started = time.time()
    with out.open("w", encoding="utf-8") as handle:
        proc = subprocess.Popen(
            [str(PY), "-u", "run_probe.py", *extra],
            cwd=HERE,
            stdout=handle,
            stderr=subprocess.STDOUT,
        )
        try:
            rc = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_tree(proc.pid)
            rc = -1
    return rc, out.read_text(encoding="utf-8", errors="replace"), time.time() - started


def launch_and_close(extra: list[str], label: str, timeout: float = 90.0) -> tuple[int, str]:
    """同一个流程：起 run_probe → 等窗 → 发 WM_CLOSE → 等它自己收尾。

    必须让它**自己**走完 finally（`release_port` 在那儿），所以不能用 terminate 硬杀。
    """
    out = HERE / "_launcher_run.out"
    with out.open("w", encoding="utf-8") as handle:
        proc = subprocess.Popen(
            [str(PY), "-u", "run_probe.py", *extra],
            cwd=HERE,
            stdout=handle,
            stderr=subprocess.STDOUT,
        )
        try:
            hwnd = wait_window()
            check(f"{label}：窗口出现了", hwnd != 0, f"hwnd={hwnd}")
            if hwnd:
                time.sleep(1.5)  # 让它把 rescue 那次定位走完
                close_window(hwnd)
            rc = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_tree(proc.pid)
            rc = -1
        finally:
            if proc.poll() is None:
                kill_tree(proc.pid)
    return rc, out.read_text(encoding="utf-8", errors="replace")


def main() -> int:
    # `--group=1` / `--group=2,3`：只跑某几组。负向验证要单独打某一组时用得上 ——
    # 第一组注入后留下的现场会污染后面两组，串起来跑就分不清「谁报的红」。
    only: set[int] = set()
    for arg in sys.argv[1:]:
        if arg.startswith("--group="):
            only.update(int(part) for part in arg.split("=", 1)[1].split(",") if part.strip())

    def want(index: int) -> bool:
        return not only or index in only

    if serving():
        print(f"端口 {PORT} 上现在就有东西在听 —— 先把前端关掉再跑本探针")
        return 2
    if window_handle():
        print(f"已有一个「{TITLE}」窗口 —— 先关掉它（否则判据会认错窗口）")
        return 2

    # ---- 一、端口被占 ⇒ 拒绝启动 -----------------------------------------
    if want(1):
        with Dummy() as dummy:
            check("占位服务已在 5173 上监听", serving())
            rc, text, elapsed = launch([], timeout=8)
            check("端口被占时 run_probe 退出码非零", rc != 0, f"rc={rc}")
            # rc 非零分不开「拒绝」与「挂住被杀」—— 拒绝是立刻返回（实测 <1s）
            check("拒绝是立刻返回的（<5s），不是挂住等超时", elapsed < 5.0, f"{elapsed:.1f}s")
            found = re.search(r"PID (\d+)", text)
            check("报出「已被占用」并给出占用者 PID", "已被占用" in text and found is not None,
                  found.group(0) if found else (text.strip().splitlines()[:1] or ["（无输出）"])[0])
            # 「已拉起」是在 Popen 之后打的 ⇒ 它没出现就等价于「没走到开窗那一步」
            check("拒绝时**没有**打印「已拉起」（没起就别说起了）", "已拉起" not in text)
            check("拒绝后没留下窗口", window_handle() == 0)
            check("占位进程没被误杀（端口仍被它占着）", serving() and dummy.hits >= 0)

        check("占位撤掉后 5173 空出来", wait_port(free=True, timeout=15))

    # ---- 二、--reuse ⇒ 复用现有前端，且不动它 -----------------------------
    if want(2):
        vite = serve_vite()
        try:
            check("真 vite 已占住 5173（充当「别人的前端」）", serving())
            rc, text = launch_and_close(["--reuse"], "reuse")
            tail = text.strip().splitlines()[-1] if text.strip() else ""
            check("reuse：退出码 0", rc == 0, f"rc={rc} · {tail}")
            hits = [line for line in text.splitlines() if "复用" in line][:1]
            reuse_line = hits or ["（没这行）"]
            check(
                "reuse：说明白是在复用，且报出占用者 PID",
                "复用" in text and re.search(r"PID (\d+)", reuse_line[0]) is not None,
                reuse_line[0],
            )
            log = (HERE / "_ppx_run.log").read_text(encoding="utf-8", errors="replace")
            check("reuse：ppx dev 日志里没有 [失败]", "[失败]" not in log,
                  [line for line in log.splitlines() if "[失败]" in line][:1])
            check("reuse：完事后 vite 还活着（不是我们的，不该杀）", serving())
        finally:
            kill_tree(vite.pid)

        check("关掉 vite 后 5173 空出来", wait_port(free=True, timeout=20))

    # ---- 三、正常起 ⇒ 日志干净，关窗后端口腾空 ---------------------------
    if want(3):
        rc, text = launch_and_close([], "normal")
        check("normal：退出码 0", rc == 0, f"rc={rc}")
        log = (HERE / "_ppx_run.log").read_text(encoding="utf-8", errors="replace")
        check("normal：ppx dev 自己起了前端（日志无 [失败]）", "[失败]" not in log,
              [line for line in log.splitlines() if "[失败]" in line][:1])
        check("normal：报的是「已拉起」而不是「已启动」", "已拉起" in text)
        # 这条验的是**不变量**，不是我们的代码：ppx dev 关窗时自己会把 vite 收掉
        check("关窗后 5173 腾空（ppx dev 自己收掉了 vite）", wait_port(free=True, timeout=15))

    print()
    if failures:
        print(f"{len(failures)} 条不成立：")
        for item in failures:
            print("  -", item)
        return 1
    print("全部通过：启动器不再谎报，也不留僵尸 vite")
    return 0


if __name__ == "__main__":
    sys.exit(main())
