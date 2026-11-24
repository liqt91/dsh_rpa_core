"""真机探针：原生元素编辑框的 F10 全局热键是否真的接上了。

做法：真建一个编辑框（真窗口、真消息泵），把 ``_start_check`` 换成只记数的桩，
再用 ``keybd_event`` 打两次 F10（第二次长按 0.5s），要求：
- 提示行存在且文案正确（用户能看见「按 F10 也能校验」）；
- 两次按键 → 恰好两次触发（长按只算一次）；
- 关框后不再触发（watch 线程确实停了）。
"""

from __future__ import annotations

import ctypes
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from rpa_core.capture_kit import edit_dialog as edition  # noqa: E402

user32 = ctypes.windll.user32
user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
user32.GetDlgItem.restype = ctypes.c_void_p


def press(hold: float = 0.08) -> None:
    user32.keybd_event(0x79, 0, 0, 0)
    time.sleep(hold)
    user32.keybd_event(0x79, 0, 2, 0)


def main() -> int:
    dlg = edition.ElementEditDialog()
    if not dlg.available:
        print("SKIP: 非 Windows / 无桌面会话")
        return 0
    fired: list[float] = []
    dlg._start_check = lambda hwnd: fired.append(time.monotonic())  # type: ignore[method-assign]

    worker = threading.Thread(
        target=lambda: dlg.show(
            title="F10 探针", name="demo", selector="div.a > span.b", info="1 个",
        ),
        daemon=True,
    )
    worker.start()
    for _ in range(40):
        if dlg._hwnd:
            break
        time.sleep(0.05)
    hwnd = dlg._hwnd
    if not hwnd:
        print("FAIL: 编辑框没建起来")
        return 1
    time.sleep(0.4)

    hint = ctypes.create_unicode_buffer(64)
    ctl = user32.GetDlgItem(ctypes.c_void_p(hwnd), edition._ID_HOTKEY_HINT)
    if ctl:
        user32.GetWindowTextW(ctl, hint, 64)
    print(f"hint: {hint.value!r}")

    press()
    time.sleep(0.4)
    press(hold=0.5)  # 长按：上升沿只该算一次
    time.sleep(0.6)
    print(f"fired={len(fired)}")

    dlg.close()
    time.sleep(0.5)
    press()
    time.sleep(0.4)
    print(f"after close fired={len(fired)}")

    ok = hint.value == edition._HOTKEY_HINT_TEXT and len(fired) == 2
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
