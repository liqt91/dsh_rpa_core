"""GUI 启动/切页成本探针（真机平台版，M40 诊断）。

与 ``probe_gui_startup_cost.py`` 的差别：**不用 offscreen**，走本机真实平台插件
（Windows 下真实窗口/真实绘制/真实字体），因此能反映用户体感。窗口会短暂显示，
测完立即关闭——只读探针，不改任何状态。

跑法：``uv run python .harness/spike/probe_gui_startup_real.py``
"""

from __future__ import annotations

import time
from pathlib import Path

_LAPS: list[tuple[str, float]] = []
T0 = time.perf_counter()


def lap(label: str) -> None:
    _LAPS.append((label, time.perf_counter() - T0))


lap("脚本首行（解释器 + site 已就绪）")

from rpa_core.cli import _commands_root  # noqa: E402
from rpa_core.catalog import load_catalog  # noqa: E402
from rpa_core.devserver.store import WorkflowDirStore  # noqa: E402
from rpa_core.gui import app as gui_app  # noqa: E402

lap("import gui.app(含 PySide6 + catalog + pydantic)")

app = gui_app.build_application([])
lap("QApplication + apply_theme(QSS 生成 + setStyleSheet)")

Store = WorkflowDirStore
store = Store(Path("workflows"))
lap("WorkflowDirStore")

from rpa_core.gui.home import HomeWindow  # noqa: E402

window = HomeWindow(store, load_catalog(_commands_root()))
lap("HomeWindow ctor")

window.show()
app.processEvents()
lap("HomeWindow.show + 首帧")

for index in (1, 2, 0, 1):
    start = time.perf_counter()
    window.tabs.setCurrentIndex(index)
    app.processEvents()
    print(
        f"[probe] 切到「{window.tabs.tabText(index)}」: "
        f"{(time.perf_counter() - start) * 1000:.1f}ms"
    )

from PySide6.QtCore import QTimer  # noqa: E402

QTimer.singleShot(300, app.quit)
app.exec()
lap("事件循环 300ms + 退出")

print("\n=== 真机分段耗时 ===")
for (prev_label, prev), (label, now) in zip(_LAPS, _LAPS[1:], strict=False):
    print(f"{(now - prev) * 1000:8.1f} ms   {prev_label} → {label}")
print(f"{_LAPS[-1][1] * 1000:8.1f} ms   TOTAL")
