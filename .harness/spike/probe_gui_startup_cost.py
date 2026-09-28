"""GUI 启动 / 切页成本探针（M40 诊断）。

分段打点「新进程 → 工作台可见」的每一段，以及 QTabWidget 切页耗时。
跑法：``uv run python .harness/spike/probe_gui_startup_cost.py``

只读探针：不改任何状态。``QT_QPA_PLATFORM=offscreen`` 下 Qt 的布局/样式开销
与真机同量级（都是 CPU 侧首次 polish + 布局），可用于定位「慢在哪一段」。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_LAPS: list[tuple[str, float]] = []


def lap(label: str) -> None:
    _LAPS.append((label, time.perf_counter()))


def report() -> None:
    print("\n=== 分段耗时 ===")
    for (prev_label, prev), (label, now) in zip(_LAPS, _LAPS[1:], strict=False):
        print(f"{(now - prev) * 1000:8.1f} ms   {prev_label} → {label}")
    total = _LAPS[-1][1] - _LAPS[0][1]
    print(f"{total * 1000:8.1f} ms   TOTAL ({_LAPS[0][0]} → {_LAPS[-1][0]})")


lap("process start")
import rpa_core.cli  # noqa: E402,F401
lap("import rpa_core.cli")

import rpa_core.gui.app as gui_app  # noqa: E402
lap("import rpa_core.gui.app(含 PySide6)")

from rpa_core.cli import _commands_root  # noqa: E402
from rpa_core.catalog import load_catalog  # noqa: E402
from rpa_core.devserver.store import WorkflowDirStore  # noqa: E402

lap("import catalog/store")

app = gui_app.build_application([])
lap("QApplication + apply_theme(QDarkStyle QSS)")

root = Path("workflows")
store = WorkflowDirStore(root)
lap("WorkflowDirStore")

# --- 分段测 refresh_flows 的两笔账 ---------------------------------------
from rpa_core.run_history import list_runs  # noqa: E402

t = time.perf_counter()
runs = list_runs(store.root.parent / "run_artifacts", limit=0)
print(f"\n[probe] list_runs: {time.perf_counter() - t:.3f}s, {len(runs)} 条")
lap("list_runs ×1")

from rpa_core.gui.command_matrix import CommandMatrixPanel, scope_breakdown  # noqa: E402

t = time.perf_counter()
panel = CommandMatrixPanel()
print(f"[probe] CommandMatrixPanel(): {time.perf_counter() - t:.3f}s")
lap("CommandMatrixPanel ctor(含 scope_breakdown 三遍读用例表)")
panel.deleteLater()

from rpa_core.gui.debug_log import install_window_show_watch  # noqa: E402

install_window_show_watch(app)
lap("install_window_show_watch（默认是否装 watcher）")

from rpa_core.gui.home import HomeWindow  # noqa: E402

t = time.perf_counter()
window = HomeWindow(store, load_catalog(_commands_root()))
print(f"[probe] HomeWindow(): {time.perf_counter() - t:.3f}s")
lap("HomeWindow ctor(含 refresh_flows + refresh_history)")

window.show()
app.processEvents()
lap("HomeWindow.show + 首帧（含 tab0 首次布局/QSS polish）")

for index in (1, 2, 0):
    t = time.perf_counter()
    window.tabs.setCurrentIndex(index)
    app.processEvents()
    print(
        f"[probe] 首次显示「{window.tabs.tabText(index)}」: "
        f"{(time.perf_counter() - t) * 1000:.1f}ms"
    )

# --- 编辑器窗口（工作台点「打开」后的那条路） -------------------------------
catalog = load_catalog(_commands_root())
t = time.perf_counter()
editor = gui_app.MainWindow(catalog)
print(f"[probe] MainWindow(): {time.perf_counter() - t:.3f}s")
lap("MainWindow ctor(编辑器)")

editor.showMaximized()
app.processEvents()
lap("编辑器 showMaximized + 首帧")

report()
