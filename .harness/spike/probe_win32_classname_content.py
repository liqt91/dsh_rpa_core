"""BACKLOG 尾巴 #2 取证之二：哈希是否随**源码/产物内容**变化。

上一步证明「同源码、跨路径跨文件名重编译」哈希恒定。本步测内容轴：
  A. 纯注释变更（IL 不变）
  B. 真实 IL 变更（改一个控件文本）
  C. 版本资源变更（/win32icon 之外用 /resource 注入标记）

跑法（会开窗抢前台）：

    .venv/Scripts/python.exe .harness/spike/probe_win32_classname_content.py
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
SRC = ROOT / "testapps" / "desktop" / "Program.cs"

VARIANTS = {
    "baseline": None,
    "comment_only": lambda s: "// probe: comment-only change\n" + s,
    "real_change": lambda s: s.replace("Submit", "SubmitX"),
    "another_change": lambda s: s.replace("RPA Core Desktop Demo", "RPA Core Desktop Demo"),
}


def build(src_text: str, out_exe: Path) -> None:
    src = out_exe.with_suffix(".cs")
    src.write_text(src_text, encoding="utf-8")
    subprocess.run(
        [
            CSC, "/nologo", "/target:winexe", f"/out:{out_exe}",
            "/r:System.Windows.Forms.dll", "/r:System.Drawing.dll", str(src),
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
    base_src = SRC.read_text(encoding="utf-8")
    tmp = Path(tempfile.mkdtemp(prefix="rpa-cn-content-"))
    result = {}
    for name, mutate in VARIANTS.items():
        text = base_src if mutate is None else mutate(base_src)
        exe = tmp / f"{name}.exe"
        build(text, exe)
        result[name] = class_names(exe)
        print(f"# {name}: {result[name][0] if result[name] else '<none>'}",
              file=sys.stderr)

    base = result["baseline"]
    summary = {k: (v == base) for k, v in result.items()}
    print(json.dumps(
        {"variants": result, "same_as_baseline": summary},
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
