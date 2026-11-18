"""M98 S4：**等微信窗口真正重建**后再判定校验路径的行为。

用户真机：搜索框保存后当场校验命中；关掉微信窗口再打开就校验不到。
现有事实：
  - 微信窗口关闭后，UIA 树被销毁（RawViewWalker 只剩 1 个节点）；
  - 捕获路径带 ``--wake-accessibility``，会写 gate（trace: before=0→after=1）；
  - 校验路径（``DesktopElementVerifier``）**从不唤醒**。

本探针轮询到“树真的活了”（FindAll 后代 > 5）再做三段判定，避免在壳窗口上得出
假结论：
  [A] 树活但未唤醒：``DesktopExecutor._find`` 命中数（= 用户点校验的真实结果）
  [B] 唤醒 gate 后重建窗口再 ``_find``
  [C] 若仍不命中，逐级 path 收窄定位断点

用法：先跑起来（后台），用户把微信主窗口打开，脚本自己等。
"""

from __future__ import annotations

import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

ELEMENT = ROOT / "examples" / "api-usage" / "elements" / "微信搜索框.json"
DEADLINE = 240.0


def find_hwnd() -> int:
    import win32gui

    hit = 0

    def cb(h, _):
        nonlocal hit
        if win32gui.GetWindowText(h) == "微信" and win32gui.IsWindowVisible(h):
            hit = h
            return False
        return True

    win32gui.EnumWindows(cb, None)
    return hit


def tree_size(hwnd: int) -> int:
    from pywinauto.uia_defines import IUIA

    try:
        iuia = IUIA().iuia
        root = iuia.ElementFromHandle(hwnd)
        return int(root.FindAll(1, iuia.CreateTrueCondition()).Length)
    except Exception:  # noqa: BLE001
        return -1


def find_hits(hwnd: int, locator) -> tuple[int, str]:
    from rpa_core.executors.desktop import DesktopExecutor

    window = DesktopExecutor._window_by_handle(hwnd)
    if window is None:
        return -1, "_window_by_handle None"
    try:
        return len(DesktopExecutor._find(window, locator)), "ok"
    except Exception as exc:  # noqa: BLE001
        return -1, f"{type(exc).__name__}: {exc}"


def path_walk(hwnd: int, locator) -> None:
    from rpa_core.executors.desktop import DesktopExecutor

    window = DesktopExecutor._window_by_handle(hwnd)
    if window is None:
        print("  path_walk: window None")
        return
    current = [window]
    for depth, step in enumerate(locator.path or [], 1):
        nxt = []
        for container in current:
            try:
                for item in container.descendants():
                    if DesktopExecutor._step_matches(step, item):
                        nxt.append(item)
            except Exception:  # noqa: BLE001
                pass
        print(f"  L{depth} {step} -> {len(nxt)}（上 {len(current)}）")
        if not nxt:
            seen: dict[str, int] = {}
            for container in current:
                try:
                    for item in container.descendants():
                        info = item.element_info
                        key = f"{info.control_type}|{info.class_name}|{info.automation_id}"
                        seen[key] = seen.get(key, 0) + 1
                except Exception:  # noqa: BLE001
                    pass
            print(f"    ↳ 断点，上一级后代 {sum(seen.values())} 个，前 12 类：")
            for key, count in sorted(seen.items(), key=lambda kv: -kv[1])[:12]:
                print(f"      {count:3d}  {key}")
            return
        current = nxt
    print(f"  收窄完成 {len(current)} 个 scope")


def main() -> int:
    from rpa_core.capture import accessibility
    from rpa_core.model.desktop import DesktopLocator

    locator = DesktopLocator.model_validate(
        json.loads(ELEMENT.read_text(encoding="utf-8"))["selector"]["locator"]
    )

    started = time.time()
    hwnd = 0
    last = None
    while time.time() - started < DEADLINE:
        h = find_hwnd()
        if h:
            hwnd = h
            size = tree_size(h)
            if size != last:
                print(f"[{time.time() - started:5.1f}s] hwnd={h} 树后代={size}", flush=True)
                last = size
            if size > 5:
                break
        else:
            if last != "no-window":
                print(f"[{time.time() - started:5.1f}s] 没有可见的微信主窗口", flush=True)
                last = "no-window"
        time.sleep(1.0)
    else:
        print("等待超时，微信窗口始终没有重建树", flush=True)
        return 2

    print("\n[A] 树已活，未唤醒 —— 这就是用户点「校验」时走的路径")
    hits, note = find_hits(hwnd, locator)
    print(f"  _find 命中 {hits}  ({note})")

    print("\n[B] 唤醒 gate 后")
    try:
        print("  activate:", accessibility.activate(hwnd, poke=True))
    except Exception as exc:  # noqa: BLE001
        print("  activate 抛:", type(exc).__name__, exc)
    time.sleep(1.0)
    hits2, note2 = find_hits(hwnd, locator)
    print(f"  _find 命中 {hits2}  ({note2})")

    if hits <= 0:
        print("\n[C] path 逐级收窄")
        path_walk(hwnd, locator)

    print(f"\n结论：cold={hits} warm={hits2} 树={tree_size(hwnd)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
