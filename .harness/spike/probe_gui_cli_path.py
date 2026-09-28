"""真实 CLI 路径的 GUI 启动计时（M40 诊断）。

走 `rpa_core.cli.main()` 的 gui 子命令（用户实际敲的命令），但把 `app.exec()`
替换成立即返回，于是只测「到窗口 show 为止」的成本，进程随后正常退出。

跑法：``uv run python .harness/spike/probe_gui_cli_path.py``
"""

from __future__ import annotations

import sys
import time

T0 = time.perf_counter()

from PySide6.QtWidgets import QApplication  # noqa: E402

_boot = time.perf_counter() - T0

QApplication.exec = lambda self: 0  # type: ignore[method-assign]  # 不进入事件循环

from rpa_core.cli import main  # noqa: E402

_imported = time.perf_counter() - T0

sys.argv = ["rpa-core", "gui"]
_start = time.perf_counter()
try:
    main()
except SystemExit:
    pass
_shown = time.perf_counter() - T0

print("\n=== 真实 CLI 路径 ===")
print(f"解释器+site+Qt 绑定就绪: {_boot * 1000:8.1f} ms")
print(f"import rpa_core.cli     : {(_imported - _boot) * 1000:8.1f} ms")
print(f"main() 到窗口 show      : {(_shown - _imported) * 1000:8.1f} ms")
print(f"TOTAL（进程内）         : {_shown * 1000:8.1f} ms")
