"""枚举探针：win32 后端在演示程序上到底看到哪些控件（类名/文本/control id）。"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


def main() -> None:
    import pywinauto

    tmp = Path(tempfile.mkdtemp(prefix="rpa-win32-enum-"))
    exe = tmp / "RpaCoreDesktopDemo.exe"
    subprocess.run(
        [
            "C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe", "/nologo",
            "/target:winexe", f"/out:{exe}", "/r:System.Windows.Forms.dll",
            "/r:System.Drawing.dll", str(ROOT / "testapps" / "desktop" / "Program.cs"),
        ],
        check=True,
        capture_output=True,
    )
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
        desc = win.descendants()
        print(f"descendants: {len(desc)}")
        for c in desc:
            print(
                f"  cls={c.class_name()!r} text={c.window_text()!r} "
                f"cid={c.element_info.control_id}"
            )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main()
