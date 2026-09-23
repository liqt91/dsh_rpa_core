"""探针：实测靶子主窗口与控件的**真实类名**（尾巴 #2 classNameRe 的判据依据）。

结论直接影响用例表里的正则该怎么写——不实测就是猜。
"""
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from tests.e2e.desktop_fixture import (  # noqa: E402
    compile_demo_app,
    force_foreground,
    kill_demo_apps,
    wait_for_window,
)

TITLE = "RPA Core Desktop Demo"


def main() -> None:
    kill_demo_apps()
    tmp = Path(tempfile.mkdtemp(prefix="probe-cls-"))
    exe = compile_demo_app(tmp)
    proc = subprocess.Popen([str(exe)])
    try:
        wait_for_window(TITLE, timeout=20.0)
        force_foreground(TITLE)

        from pywinauto import Desktop

        print("=== 主窗口候选（按标题）===")
        for w in Desktop(backend="win32").windows():
            try:
                if (w.window_text() or "") == TITLE:
                    print(f"  class_name={w.class_name()!r} title={w.window_text()!r}")
            except Exception:
                pass

        print("=== 靶子窗口的全部子控件类名 ===")
        from pywinauto import Application

        app = Application(backend="win32").connect(title=TITLE)
        win = app.window(title=TITLE)
        seen: dict[str, int] = {}
        for child in win.descendants():
            try:
                cls = child.class_name() or ""
                txt = child.window_text() or ""
                seen[f"{cls}"] = seen.get(cls, 0) + 1
                print(f"  class={cls!r} text={txt!r}")
            except Exception:
                continue
        print("=== 类名去重计数 ===")
        for cls, n in sorted(seen.items()):
            print(f"  {n:>3}x  {cls!r}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        kill_demo_apps()
        time.sleep(0.5)
        print("cleaned up")


if __name__ == "__main__":
    main()
