"""离屏渲染画布卡片，用于目视核对「灰色参数摘要」的字号/颜色可读性。

用法（须用 .venv 的 python）：
    ./.venv/Scripts/python.exe .harness/spike/probe_canvas_summary_readability.py [输出.png]

产出：把画布卡片截成 PNG，旁边打印每行摘要与其字体像素大小、颜色与对比度。
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402

from rpa_core.gui import fonts  # noqa: E402
from rpa_core.gui.app import apply_theme, build_application  # noqa: E402
from rpa_core.gui.canvas import build_canvas  # noqa: E402
from rpa_core.gui.flow_model import (  # noqa: E402
    ROLE_ARGS_SUMMARY,
    build_model_from_workflow,
)
from rpa_core.gui.theme import SURFACE, TEXT_SECONDARY  # noqa: E402


def _lin(c: float) -> float:
    c = c / 255.0
    return ((c + 0.055) / 1.055) ** 2.4 if c > 0.04045 else c / 12.92


def _luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast(fg: str, bg: str) -> float:
    a, b = _luminance(fg), _luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


WORKFLOW = {
    "root": {
        "type": "sequence",
        "id": "root",
        "children": [
            {"type": "action", "id": "n1", "command": "browser.navigate",
             "with": {"url": "https://example.com", "browserType": "msedge"}},
            {"type": "action", "id": "n2", "command": "browser.input",
             "with": {"selector": "#kw", "text": "hello", "clear": True,
                      "delay": 0, "mode": "type"}},
            {"type": "action", "id": "n3", "command": "data.setVar",
             "with": {"varName": "总数", "value": "1"}},
        ],
    }
}


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO_ROOT / ".harness/spike/_canvas_summary.png"
    app = build_application()
    apply_theme(app)

    model = build_model_from_workflow(WORKFLOW)
    view = build_canvas(model)
    view.resize(760, 46 * 3 + 20)
    view.show()
    app.processEvents()

    print(f"基准字号 BASE_PX={fonts.BASE_PX}  SUB_PX={fonts.SUB_PX}  "
          f"MONO_PX={fonts.MONO_PX}  MIN_PX={fonts.MIN_PX}")
    print(f"卡片底色 SURFACE={SURFACE}  摘要色 TEXT_SECONDARY={TEXT_SECONDARY}")
    print(f"摘要对比度 = {contrast(TEXT_SECONDARY, SURFACE):.2f}:1 "
          f"(WCAG AA 正文阈值 4.5:1)")
    print()
    root = model.invisibleRootItem()
    for row in range(root.rowCount()):
        item = root.child(row)
        title = item.data(Qt.ItemDataRole.DisplayRole)
        summary = item.data(ROLE_ARGS_SUMMARY) or ""
        print(f"  [{fonts.BASE_PX}px 粗体] {title}")
        print(f"  [{fonts.SUB_PX}px 灰色] {summary}")

    view.grab().save(str(out))
    print(f"\n已写出：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
