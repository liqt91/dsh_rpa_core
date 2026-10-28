"""负向验证：把启动器的修复各自改回原样，确认 launcher_probe.py 真会红。

绿的探针可能什么都没查。这里往 `run_probe.py` 里注入合法语法的破坏，期望**具体哪几条**
判据变红，再逐字节还原复核 md5。

注入一（拆掉「端口预检 + 日志失败」整对）：
    `if someone_serving():` → `if False:`，且 `failed = log_failure()` → `failed = ""`
    两条互为兜底（一个防「起之前就有人占着」，一个防「起之后才发现起不来」），
    **必须一次删整对** —— 只删一条另一条还能拦住，看不出哪条真在起作用。期望红：
      - 报出「已被占用」并给出占用者 PID
      - 拒绝时**没有**打印「已拉起」
      - 拒绝是立刻返回的（<5s），不是挂住等超时
    第三条是补的：注入后 run_probe 起不来也不退，被探针的 8s 超时杀掉，`rc` 照样非零 ——
    所以「rc 非零」这条判据根本分不清「拒绝」与「挂住」，得靠**耗时**和**文案**。

注入二（端口探测只探 IPv4）：双栈循环换回 `connect_ex(("127.0.0.1", port))`
    `vite` 不指定 host 时绑 `::1` ⇒ 判成「端口空着」，于是又起一个。期望只红一条：
      - reuse：说明白是在复用，且报出占用者 PID

注入三（就绪探测只打 IPv4）：`urlopen(f"http://localhost:{PORT}/")` → `127.0.0.1`
    前端绑在 ::1 上时永远「没就绪」，窗口却已经弹出来。期望红：
      - reuse：退出码 0

★ 删掉的一条（记录一下，免得下次又写回来）：
    原本还有一处「退出时用 taskkill 把端口腾空」（`release_port`）。把它整段拿掉后再跑，
    关窗路径**照样腾空** —— `ppx dev` 自己会收 vite。那段代码是多余的，而且有害：
    它假设「现在监听的只可能是我们起的」，一旦端口预检被绕过，它就会去杀**别人的** vite
    （实测里 `reuse：完事后 vite 还活着` 因此变红）。所以代码删了，判据
    「关窗后 5173 腾空」保留 —— 它验的是不变量，不是我们的实现。
    孤儿 vite 的真实来源是 `ppx dev` **被强杀**，那种情况任何 finally 都跑不到。

用法：
    ./.venv/Scripts/python.exe ../../spike/probe_launcher_negative.py
退出码 0 = 三条注入都被拦住、源码逐字节还原、复跑复绿。

★ 本脚本带开窗副作用（探针本身要开窗），全量约 6 分钟；每次跑完都收掉残留前端。
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = next(p for p in HERE.parents if (p / "src" / "rpa_core").is_dir())
PROBE_DIR = REPO / ".harness" / "demo" / "ppx_probe"
LAUNCHER = PROBE_DIR / "run_probe.py"
PROBE = PROBE_DIR / "launcher_probe.py"
PYTHON = PROBE_DIR / ".venv" / "Scripts" / "python.exe"
OUT = PROBE_DIR / "_negative_launcher.out"
PORT = 5173

SENTINEL = "负向验证注入点"

INJECTIONS: list[dict] = [
    {
        "name": "拆掉端口预检 + 日志失败（整对）",
        "groups": "1",
        "edits": [
            ("    if someone_serving():\n", f"    if False:  # {SENTINEL}\n"),
            ("        failed = log_failure()\n", f'        failed = ""  # {SENTINEL}\n'),
        ],
        "expect_fail": {
            "报出「已被占用」并给出占用者 PID",
            "拒绝时**没有**打印「已拉起」（没起就别说起了）",
            "拒绝是立刻返回的（<5s），不是挂住等超时",
        },
    },
    {
        "name": "端口探测只探 IPv4",
        "groups": "2",
        "edits": [
            (
                '    for family, address in ((socket.AF_INET, "127.0.0.1"), '
                '(socket.AF_INET6, "::1")):\n'
                "        try:\n"
                "            with socket.socket(family, socket.SOCK_STREAM) as probe:\n"
                "                probe.settimeout(timeout)\n"
                "                if probe.connect_ex((address, port)) == 0:\n"
                "                    return True\n"
                "        except OSError:\n"
                "            continue\n",
                f'    with socket.socket() as probe:  # {SENTINEL}\n'
                "        probe.settimeout(timeout)\n"
                '        return probe.connect_ex(("127.0.0.1", port)) == 0\n',
            )
        ],
        "expect_fail": {"reuse：说明白是在复用，且报出占用者 PID"},
    },
    {
        "name": "就绪探测只打 IPv4",
        "groups": "2",
        "edits": [
            (
                '            with urllib.request.urlopen('
                'f"http://localhost:{PORT}/", timeout=2):\n',
                "            with urllib.request.urlopen("
                'f"http://127.0.0.1:{PORT}/", timeout=2):  # ' + SENTINEL + "\n",
            )
        ],
        "expect_fail": {"reuse：退出码 0"},
    },
]


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def kill_port(port: int = PORT) -> None:
    """把还占着端口的进程收掉，别把上一次注入的现场留给下一次。"""
    try:
        done = subprocess.run(
            ["netstat", "-ano"], capture_output=True, encoding="mbcs", errors="replace", timeout=15
        )
    except Exception:
        return
    pids = set()
    for line in (done.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[3].upper() == "LISTENING" and parts[1].endswith(f":{port}"):
            if parts[4].isdigit() and parts[4] != "0":
                pids.add(parts[4])
    for pid in pids:
        subprocess.run(["taskkill", "/F", "/T", "/PID", pid], capture_output=True, timeout=20)
    if pids:
        print(f"      [清理] 收掉占着 {port} 的 PID {sorted(pids)}")
        time.sleep(0.6)


def run_probe(groups: str = "") -> tuple[int, list[str], str]:
    """跑探针（可只跑某几组），返回 (rc, 变红的判据文案, 原始输出)。输出落文件，不接管道。"""
    argv = [str(PYTHON), "-u", str(PROBE)]
    if groups:
        argv.append(f"--group={groups}")
    with OUT.open("w", encoding="utf-8") as handle:
        proc = subprocess.run(
            argv, cwd=str(PROBE_DIR), stdout=handle, stderr=subprocess.STDOUT, text=True
        )
    text = OUT.read_text(encoding="utf-8", errors="replace")
    failed = [
        line[len("[FAIL] ") :].split("  ")[0].strip()  # 探针用两个空格分隔判据与明细
        for line in text.splitlines()
        if line.startswith("[FAIL]")
    ]
    return proc.returncode, failed, text


def main() -> int:
    if SENTINEL in LAUNCHER.read_text(encoding="utf-8"):
        print(f"源码里已有哨兵「{SENTINEL}」，拒绝运行：上次注入没还原")
        return 2

    original = LAUNCHER.read_bytes()
    baseline = md5(LAUNCHER)
    print(f"基线 md5 {baseline} · {len(original)} B")

    rc, failed, _ = run_probe()
    if rc != 0 or failed:
        print(f"基线就不绿（rc={rc}, 红={failed}），先修探针再谈负向验证")
        kill_port()
        return 2
    print("基线：全绿\n")

    problems: list[str] = []
    try:
        for item in INJECTIONS:
            text = LAUNCHER.read_text(encoding="utf-8")
            if any(text.count(old) != 1 for old, _new in item["edits"]):
                counts = [(old.splitlines()[0][:46], text.count(old)) for old, _ in item["edits"]]
                problems.append(f"{item['name']}：锚点不唯一/不存在 {counts}")
                print(f"[SKIP] {item['name']}：锚点 {counts}")
                continue
            for old, new in item["edits"]:
                text = text.replace(old, new)
            LAUNCHER.write_bytes(text.encode("utf-8"))
            assert SENTINEL in LAUNCHER.read_text(encoding="utf-8"), "注入没落盘"

            rc, failed, _text = run_probe(item["groups"])
            got = set(failed)
            expected = item["expect_fail"]
            ok = rc != 0 and got == expected
            print(
                f"[{'PASS' if ok else 'FAIL'}] 注入「{item['name']}」（只跑第 {item['groups']} 组）"
                f"→ rc={rc}，红 {len(got)} 条：{sorted(got)}"
            )
            if not ok:
                print(f"        期望红：{sorted(expected)}")
                print(f"        实际红：{sorted(got)}")
                problems.append(f"{item['name']}：期望 {sorted(expected)}，实际 {sorted(got)}")

            LAUNCHER.write_bytes(original)
            restored = md5(LAUNCHER)
            ok_restore = restored == baseline
            print(f"[{'PASS' if ok_restore else 'FAIL'}] 逐字节还原（md5 {restored[:8]}）")
            if not ok_restore:
                problems.append(f"{item['name']}：还原后 md5 不等于基线")
            kill_port()
    finally:
        if md5(LAUNCHER) != baseline:
            LAUNCHER.write_bytes(original)
            print(f"[兜底] 已还原到基线 md5 {md5(LAUNCHER)[:8]}")
        kill_port()

    rc, failed, _ = run_probe()
    print(f"\n[{'PASS' if rc == 0 and not failed else 'FAIL'}] 复跑复绿（rc={rc}, 红={failed}）")
    if rc != 0 or failed:
        problems.append("还原后复跑不是全绿")
    kill_port()

    if problems:
        print("\n未通过：")
        for item in problems:
            print(f"  - {item}")
        return 1
    print(f"\n{len(INJECTIONS)} 条注入都被拦住，源码逐字节还原，复跑复绿")
    return 0


if __name__ == "__main__":
    sys.exit(main())
