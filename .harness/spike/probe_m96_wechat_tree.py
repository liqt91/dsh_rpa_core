"""M96 取证：微信窗口的 UIA 树到底有多深，MMUIRenderSubWindowHW 下面还有没有子节点。

只读探针：不改任何东西，只打印。用途是判「捕获落在渲染窗」是代码没下钻，
还是 UIA 客观就只暴露这一层。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))


def main() -> int:
    from pywinauto import Desktop

    desktop = Desktop(backend="uia")
    roots = []
    for win in desktop.windows():
        try:
            title = win.window_text()
            cls = win.class_name()
        except Exception:  # noqa: BLE001
            continue
        if "微信" in title or "WeChat" in cls or "Weixin" in cls:
            roots.append((win, title, cls))
    if not roots:
        print("没找到微信顶层窗口（微信没启动？或本进程看不到）")
        return 1

    for win, title, cls in roots:
        print(f"\n=== 顶层窗口 title=[{title}] class=[{cls}] handle={win.handle} ===")
        _dump(win, depth=0, max_depth=6)
    return 0


def _dump(node, *, depth: int, max_depth: int) -> None:
    pad = "  " * depth
    try:
        info = node.element_info
        ct = info.control_type
        name = info.name
        aid = info.automation_id
        cls = info.class_name
    except Exception as exc:  # noqa: BLE001
        print(f"{pad}<读属性失败 {type(exc).__name__}>")
        return
    try:
        rect = node.rectangle()
        box = f"{rect.left},{rect.top} {rect.width()}x{rect.height()}"
    except Exception:  # noqa: BLE001
        box = "?"
    print(f"{pad}- {ct} name=[{name}] aid=[{aid}] class=[{cls}] {box}")
    if depth >= max_depth:
        return
    try:
        kids = node.children()
    except Exception as exc:  # noqa: BLE001
        print(f"{pad}  <children 失败 {type(exc).__name__}>")
        return
    if not kids:
        print(f"{pad}  <无子节点>")
        return
    if len(kids) > 60:
        print(f"{pad}  <子节点 {len(kids)} 个，只列前 60>")
        kids = kids[:60]
    for kid in kids:
        _dump(kid, depth=depth + 1, max_depth=max_depth)


if __name__ == "__main__":
    raise SystemExit(main())
