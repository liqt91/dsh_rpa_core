"""M98：复现「微信搜索框保存后能校验，关掉窗口重开就校验不到」。

不写死任何窗口/元素事实：窗口按类名+标题现场找，元素从流程目录现场读。
输出四段：
  [W] 当前微信顶层窗口（含不可见）
  [E] 元素 selector 结构（locator 顶层 / path 级数 / 末项）
  [V] 生产校验（DesktopElementVerifier.verify）的真实返回
  [P] 逐级 path 收窄诊断：断在哪一级、该级候选里有谁
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

ELEMENT = ROOT / "examples" / "api-usage" / "elements" / "微信搜索框.json"


def wx_windows() -> list[tuple[int, str, str, bool, tuple[int, int, int, int]]]:
    import win32gui

    rows: list[tuple[int, str, str, bool, tuple[int, int, int, int]]] = []

    def cb(hwnd, _):
        cls = win32gui.GetClassName(hwnd)
        title = win32gui.GetWindowText(hwnd)
        if cls.startswith("Qt5") or "微信" in title or "Weixin" in title:
            rows.append(
                (hwnd, cls, title, bool(win32gui.IsWindowVisible(hwnd)),
                 win32gui.GetWindowRect(hwnd))
            )
        return True

    win32gui.EnumWindows(cb, None)
    return rows


def main() -> int:
    from rpa_core.capture import desktop_verification
    from rpa_core.capture.foreground_window import visible_top_windows
    from rpa_core.executors.desktop import DesktopExecutor
    from rpa_core.model.desktop import DesktopLocator

    doc = json.loads(ELEMENT.read_text(encoding="utf-8"))
    locator_doc = doc["selector"]["locator"]
    locator = DesktopLocator.model_validate(locator_doc)

    print("[W] 微信顶层窗口")
    wins = wx_windows()
    for hwnd, cls, title, visible, rect in wins:
        print(f"  hwnd={hwnd} visible={visible} class={cls!r} title={title!r} rect={rect}")
    if not wins:
        print("  （没有微信窗口）")
    print("  visible_top_windows 里的微信：",
          [w.hwnd for w in visible_top_windows(include_desktop_shell=True)
           if "微信" in (w.title or "") or "Weixin" in (w.title or "")])

    print("\n[E] 元素结构")
    print("  locator 顶层:", {k: v for k, v in locator_doc.items() if k != "path"})
    print("  path 级数:", len(locator_doc.get("path") or []))
    print("  path 末项:", (locator_doc.get("path") or [None])[-1])
    print("  metadata.windowHandle:", (doc.get("metadata") or {}).get("windowHandle"))
    print("  model path 级数:", len(locator.path or []))

    print("\n[V] 生产校验")
    verifier = desktop_verification.DesktopElementVerifier(
        expected_hwnd=(doc.get("metadata") or {}).get("windowHandle"),
    )
    print("  verify ->", verifier.verify(locator))
    print("  preview ->", verifier.preview(locator))

    main_hwnd = next((h for h, c, t, _v, _r in wins if t == "微信"), 0)
    if not main_hwnd:
        print("\n[P] 没有主窗口，跳过 path 诊断")
        return 0

    print(f"\n[P] 对 hwnd={main_hwnd} 逐级收窄")
    window = DesktopExecutor._window_by_handle(main_hwnd)
    if window is None:
        print("  _window_by_handle 返回 None")
        return 0
    current = [window]
    for depth, step in enumerate(locator.path or [], 1):
        nxt: list = []
        for container in current:
            try:
                descendants = container.descendants()
            except Exception as exc:  # noqa: BLE001
                print(f"  L{depth} container.descendants() 抛 {type(exc).__name__}: {exc}")
                continue
            for item in descendants:
                if DesktopExecutor._step_matches(step, item):
                    nxt.append(item)
        print(f"  L{depth} {step} -> {len(nxt)} 个（上一级容器 {len(current)}）")
        if not nxt:
            # 断点：把这一级在「上一级容器」全后代里的属性分布打出来
            print("    ↳ 断在这里。上一级容器的后代类名分布：")
            seen: dict[str, int] = {}
            for container in current:
                try:
                    for item in container.descendants():
                        info = item.element_info
                        key = f"{info.control_type}|{info.class_name}|{info.automation_id}"
                        seen[key] = seen.get(key, 0) + 1
                except Exception:  # noqa: BLE001
                    pass
            for key, count in sorted(seen.items(), key=lambda kv: -kv[1])[:15]:
                print(f"      {count:3d}  {key}")
            return 0
        current = nxt
    print(f"  收窄完成：{len(current)} 个 scope")
    matches = []
    for scope in current:
        matches.extend(DesktopExecutor._find_in(scope, locator))
    print("  _find 命中：", len(matches))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
