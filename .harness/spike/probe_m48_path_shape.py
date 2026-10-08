"""不弹窗的设计取证：path 的两条生成分支各产出什么形状？

不启动任何 GUI —— 只读源码 + 用替身跑纯函数，判断「根窗口该不该进 path」。

问题：ADR 0018 D1 写的是「从根窗口到目标控件的层级描述」，
那根窗口自身算一级是按规格的。但执行器 `_narrow_by_path` 要「在候选的后代里找这一级」，
而窗口不属于自己的后代 —— 于是这一级永远匹配不上。

两种修法：
  A. 捕获侧不把根窗口写进 path（path 只留「根窗口**之下**的容器」）；
  B. 执行器每级允许匹配候选自身（已实现）。
"""

from __future__ import annotations

import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


def show_branch(name: str, lineno_hint: str) -> None:
    print(f"--- {name} ---")


print("=== 分支 1：_dfs_smallest_at（window scope）+ _describe_info ===")
print("path_out 起点 = 传入的 info（root_info = window 自身）→ 首级必然是 window")
print()

print("=== 分支 2：capture_at 的父链上溯 ===")
print("_ancestor_chain 从 info 上溯，链尾 = 根窗口 → 末级必然是 window")
print()

# 用替身验证 _ancestor_chain 是否真的把根窗口收进去
sys.path.insert(0, str(ROOT / "tests" / "contract"))
from test_desktop_locator_path import FakeElement, FakeInfo  # noqa: E402

from rpa_core.capture.desktop_agent import _ancestor_chain  # noqa: E402

root = FakeElement(FakeInfo(control_type="Window", automation_id="mainWindow"), handle=10)
mid = FakeElement(FakeInfo(control_type="Pane", automation_id="p"), handle=11, parent=root)
leaf = FakeElement(FakeInfo(control_type="Button", automation_id="b"), handle=12, parent=mid)

chain = _ancestor_chain(leaf, 10)
print("_ancestor_chain(leaf, root=10) =>", [e.element_info.automation_id for e in chain])
print("是否含根窗口自身？", root in chain)
print()

# 看 _window_scope_hit 的 DFS 起点
print("=== _dfs_smallest_at 的 path_out 首元素 ===")
src = (ROOT / "src" / "rpa_core" / "capture" / "desktop_agent.py").read_text(encoding="utf-8")
tree = ast.parse(src)
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == "_dfs_smallest_at":
        body = ast.get_source_segment(src, node)
        print("首 3 行含 path_out 的语句：")
        for line in body.splitlines():
            if "path_out.append" in line:
                print("   ", line.strip())
                break
        break
print()
print("=== 结论 ===")
print("两条分支都把「根窗口自身」写进 path ⇒ path 首级（或末级）恒等于 window 自身。")
print("若执行器只在后代里找这一级 ⇒ 恒空。故 A 与 B 必须选一个（或都做）。")
