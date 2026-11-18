"""M98 S2：微信窗口「重开之后」的校验诊断——**逐段隔离**能力差异。

背景（用户真机）：微信搜索框保存后当场校验命中；关掉微信窗口再打开就校验不到。
候选根因只有一个可测分叉：
  A. 树休眠——校验路径从不唤醒无障碍（捕获路径有 ``_maybe_wake_accessibility``）；
  B. 结构漂移——重开后 path 哪一级对不上（类名/automationId 变了）；
  C. 窗口不可见——校验只枚举可见顶层窗口，隐藏窗口根本不在候选链里。

本探针把三段分别打出来，不猜：
  [W] 窗口可见性 + 校验候选链里有没有它
  [R] 未唤醒状态：逐级 path 收窄（B 的直接证据）
  [A] 唤醒后：同一套收窄 + _find（A 的直接证据）
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

ELEMENT = ROOT / "examples" / "api-usage" / "elements" / "微信搜索框.json"


def wx_windows():
    import win32gui

    out = []

    def cb(hwnd, _):
        cls = win32gui.GetClassName(hwnd)
        title = win32gui.GetWindowText(hwnd)
        if cls.startswith("Qt5") or "微信" in title or "Weixin" in title:
            out.append((hwnd, cls, title, bool(win32gui.IsWindowVisible(hwnd)),
                        win32gui.GetWindowRect(hwnd)))
        return True

    win32gui.EnumWindows(cb, None)
    return out


def walk(window, locator, label):
    from rpa_core.executors.desktop import DesktopExecutor

    current = [window]
    for depth, step in enumerate(locator.path or [], 1):
        nxt = []
        boom = None
        for container in current:
            try:
                descendants = container.descendants()
            except Exception as exc:  # noqa: BLE001
                boom = f"{type(exc).__name__}: {exc}"
                continue
            for item in descendants:
                if DesktopExecutor._step_matches(step, item):
                    nxt.append(item)
        note = f"  [{label}] L{depth} {step} -> {len(nxt)}（容器 {len(current)}）"
        if boom:
            note += f"  抛:{boom}"
        print(note)
        if not nxt:
            print(f"    ↳ [{label}] 断点。上一级容器后代分布：")
            seen = {}
            for container in current:
                try:
                    for item in container.descendants():
                        info = item.element_info
                        key = f"{info.control_type}|{info.class_name}|{info.automation_id}"
                        seen[key] = seen.get(key, 0) + 1
                except Exception:  # noqa: BLE001
                    pass
            for key, count in sorted(seen.items(), key=lambda kv: -kv[1])[:12]:
                print(f"      {count:3d}  {key}")
            if not seen:
                print("      （空：descendants() 一个都没有）")
            return False
        current = nxt
    matched = []
    for scope in current:
        matched.extend(DesktopExecutor._find_in(scope, locator))
    print(f"  [{label}] 收窄完成 {len(current)} 个 scope，_find 命中 {len(matched)}")
    return bool(matched)


def main() -> int:
    from rpa_core.capture import accessibility
    from rpa_core.capture.foreground_window import visible_top_windows
    from rpa_core.executors.desktop import DesktopExecutor
    from rpa_core.model.desktop import DesktopLocator

    doc = json.loads(ELEMENT.read_text(encoding="utf-8"))
    locator = DesktopLocator.model_validate(doc["selector"]["locator"])

    print("[W] 窗口")
    wins = wx_windows()
    for hwnd, cls, title, visible, rect in wins:
        print(f"  hwnd={hwnd} visible={visible} class={cls!r} title={title!r} rect={rect}")
    cand = [w.hwnd for w in visible_top_windows(include_desktop_shell=True)
            if "微信" in (w.title or "") or "Weixin" in (w.title or "")]
    print("  校验候选链里的微信窗口:", cand)

    main_hwnd = next((h for h, _c, t, v, _r in wins if t == "微信" and v), 0)
    if not main_hwnd:
        main_hwnd = next((h for h, _c, t, _v, _r in wins if t == "微信"), 0)
    if not main_hwnd:
        print("没有微信主窗口，退出")
        return 0
    print(f"  用主窗口 hwnd={main_hwnd}，类名={next(c for h, c, t, _v, _r in wins if h == main_hwnd)!r}")

    window = DesktopExecutor._window_by_handle(main_hwnd)
    if window is None:
        print("_window_by_handle 返回 None（窗口不可构造）")
        return 0

    print("\n[R] 未唤醒")
    before = walk(window, locator, "cold")

    print("\n[A] 唤醒无障碍后")
    try:
        result = accessibility.activate(main_hwnd, poke=True)
        print("  activate:", result)
    except Exception as exc:  # noqa: BLE001
        print("  activate 抛:", type(exc).__name__, exc)
    window2 = DesktopExecutor._window_by_handle(main_hwnd)
    after = walk(window2, locator, "warm") if window2 is not None else False

    print("\n结论线索：cold=", before, " warm=", after)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
