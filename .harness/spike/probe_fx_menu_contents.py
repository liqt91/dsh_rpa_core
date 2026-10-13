"""只读取证：打印桌面端 fx 变量菜单当前实际列出的路径（按来源分类）。

不修改任何源文件；仅构造 MainWindow 并调用 ``_collect_reference_paths``。
用法：python probe_fx_menu_contents.py [workflow.json]
"""

from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, os.path.join(_ROOT, "src"))

from rpa_core.catalog import load_catalog  # noqa: E402
from rpa_core.cli import _commands_root  # noqa: E402


def main() -> int:
    from rpa_core.gui.app import MainWindow, build_application
    from rpa_core.gui.flow_model import ROLE_ARGS_RAW, iter_real_nodes

    path = sys.argv[1] if len(sys.argv) > 1 else None
    doc = None
    if path:
        with open(os.path.join(_ROOT, path), encoding="utf-8") as fh:
            doc = json.load(fh)

    app = build_application()  # noqa: F841
    catalog = load_catalog(_commands_root())
    win = MainWindow(catalog, workflow=doc, flow_path=path)

    groups = win._reference_groups()

    alias_set, setvar_set = set(), set()
    print("=== 节点 ===")
    for item in iter_real_nodes(win.flow_model):
        holder = item.data(ROLE_ARGS_RAW)
        raw = holder.raw if holder is not None else None
        if not raw:
            continue
        cmd = raw.get("command") or raw.get("type")
        print(
            f"  id={str(raw.get('id')):<10} cmd={str(cmd):<22} "
            f"aliases={raw.get('output_aliases')} varName={(raw.get('with') or {}).get('varName')} "
            f"item_var={raw.get('item_var')} error_var={raw.get('error_var')}"
        )
        for a in (raw.get("output_aliases") or {}).values():
            alias_set.add(a)
        if raw.get("command") == "data.setVar":
            v = (raw.get("with") or {}).get("varName")
            if isinstance(v, str) and v:
                setvar_set.add(v)

    print()
    total = sum(len(paths) for _label, paths in groups)
    print(f"=== _reference_groups() 共 {len(groups)} 组 / {total} 条 ===")
    for label, paths in groups:
        print(f"  [{label}]")
        for p in paths:
            print("    ", repr(p))
    print()
    print("=== 分类（应只含前几类）===")
    flat = [p for _l, paths in groups for p in paths]
    print("  inputs.*            :", [p for p in flat if p.startswith("inputs.")])
    print("  output_aliases 值   :", sorted(alias_set))
    print("  data.setVar varName :", sorted(setvar_set))
    print("  steps.*.outputs.*   :", [p for p in flat if p.startswith("steps.")])
    print("  内置作用域          :", [p for p in flat if p in ("loop.item", "error.code", "error.message")])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
