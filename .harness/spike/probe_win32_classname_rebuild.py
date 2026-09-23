"""BACKLOG 尾巴 #2 取证：WinForms 的 `WindowsForms10.*.app.0.<哈希>` 里那段哈希
到底跟什么绑定——跨重编译变不变？跨输出路径/文件名变不变？

跑法（会开窗抢前台）：

    .venv/Scripts/python.exe .harness/spike/probe_win32_classname_rebuild.py
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

CSC = "C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe"
SRC = str(ROOT / "testapps" / "desktop" / "Program.cs")


def build(out_exe: Path) -> None:
    subprocess.run(
        [
            CSC, "/nologo", "/target:winexe", f"/out:{out_exe}",
            "/r:System.Windows.Forms.dll", "/r:System.Drawing.dll", SRC,
        ],
        check=True,
        capture_output=True,
    )


def class_names(exe: Path) -> list[str]:
    import pywinauto

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
        return sorted({c.class_name() for c in win.descendants()})
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        time.sleep(0.5)


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="rpa-cn-rebuild-"))
    a = tmp / "dirA" / "DemoA.exe"
    a.parent.mkdir(parents=True)
    b = tmp / "dirB" / "DemoB.exe"
    b.parent.mkdir(parents=True)

    build(a)
    cn_a1 = class_names(a)
    build(a)                      # 二次编译，同路径同名
    cn_a2 = class_names(a)
    build(b)                      # 不同路径 + 不同文件名
    cn_b = class_names(b)

    print(json.dumps(
        {
            "compile1_same_path": cn_a1,
            "compile2_same_path": cn_a2,
            "compile3_other_path_name": cn_b,
            "hash_stable_across_rebuild_same_path": cn_a1 == cn_a2,
            "hash_stable_across_path_and_name": cn_a1 == cn_b,
        },
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
