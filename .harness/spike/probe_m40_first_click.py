"""M40 追加诊断：第一次点页签 / 第一次开元素库的卡顿来源（真机平台版）。

用户报障（2026-09-28 追加）：

    内部标签切换，第一次点击的时候很明显。以及点击元素库时，第一次也会卡一下，
    关闭元素库重开就好多了。不过重新运行 uv run rpa-core gui，再测试就不卡了。

先把**界面事实**钉死（否则修错窗口）：

    * 全仓只有一个 `QTabWidget` → `HomeWindow`（工作台）三页签：流程库 / 运行历史 / 指令测试。
      所以「内部标签」= 工作台页签。
    * 「元素库」是**编辑器**（`MainWindow`）的 dock，入口是工具栏 action（切 G），
      `_elements_dock()` 懒创建并缓存（`self._elements_dock_widget`）——
      「关闭重开就好多了」正是这个缓存的直接后果。

再把候选机制分开（同一份数据里分不开就没法修对地方）：

    A. 惰性 import —— `_elements_dock()` 才 import `element_panel` / `rpa_core.capture`；
       若成本大，它会挂在**第一次点击**上而不是启动路径上。
    B. 惰性构造 / 首次 polish / 首次绘制 —— 隐藏页签被 `setCurrentIndex` 变可见时才布局绘制。
    C. 冷磁盘读 —— 重启进程不重建 OS 文件缓存。
    D. `show()` 之后的阻塞段 —— 扫描整段跑在窗口出现后的那一轮事件循环里，
       用户的第一下点击被排在它后面。**这一条要用三臂 A/B 量。**

§3 三臂（各起一个独立进程，避免文件缓存互相污染）：

    none  —— 什么都不做：量「窗口首帧」本身的地板价
    block —— 旧行为 `QTimer.singleShot(0, refresh_flows)`（整段扫描）
    slice —— 新行为 `QTimer.singleShot(0, start_refresh)`（分片扫描）

判据是「本该 T 触发的输入实际什么时候才轮到」——用户的体感口径，不是内部排队口径。

跑法：``uv run python .harness/spike/probe_m40_first_click.py``
只读探针：不改任何文件、不写状态；窗口短暂显示后立即关闭。
"""

from __future__ import annotations

import builtins
import io
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LAPS: list[tuple[str, float]] = []
T0 = time.perf_counter()


def lap(label: str) -> None:
    LAPS.append((label, time.perf_counter() - T0))


def ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000


def _indent(text: str) -> str:
    """把子进程的输出缩进一层，便于在报告里分辨三臂。"""
    return "\n".join(f"    {line}" for line in text.rstrip().splitlines())


@contextmanager
def io_trace():
    """统计一段代码里**文本读**的次数与 open() 自耗时（patch builtins 与 io 两个名字）。"""
    stats: dict[str, object] = {"calls": 0, "seconds": 0.0, "names": []}
    real_builtin_open = builtins.open
    real_io_open = io.open

    def traced(file, mode="r", *args, **kwargs):
        start = time.perf_counter()
        try:
            return real_io_open(file, mode, *args, **kwargs)
        finally:
            text_read = isinstance(file, (str, os.PathLike)) and "r" in str(mode) and "b" not in str(mode)
            if text_read:
                stats["calls"] = int(stats["calls"]) + 1  # type: ignore[arg-type]
                stats["seconds"] = float(stats["seconds"]) + (time.perf_counter() - start)  # type: ignore[arg-type]
                names = stats["names"]
                if isinstance(names, list) and len(names) < 8:
                    names.append(Path(str(file)).name)

    builtins.open = traced
    io.open = traced  # pyright: ignore[reportAttributeAccessIssue]
    try:
        yield stats
    finally:
        builtins.open = real_builtin_open
        io.open = real_io_open  # pyright: ignore[reportAttributeAccessIssue]


def _subprocess_ms(prefix: str, module: str) -> tuple[float, int]:
    """在独立子进程里跑 ``prefix`` 后冷 import 目标模块，返回 (毫秒, 新导入模块数)。"""
    code = (
        "import time, importlib, sys\n"
        f"{prefix}\n"
        "before = len(sys.modules)\n"
        "t = time.perf_counter()\n"
        f"importlib.import_module({module!r})\n"
        "print(f'{(time.perf_counter() - t) * 1000:.1f} {(len(sys.modules) - before)}')\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, cwd=ROOT, env=env, check=False,
    )
    if proc.returncode != 0:
        print(f"    [子进程失败] {proc.stderr.strip().splitlines()[-1:]}")
        return -1.0, -1
    elapsed, count = proc.stdout.strip().split()
    return float(elapsed), int(count)


def run_input_latency_arm(arm: str) -> dict[str, object]:
    """一臂：`none` / `block`（整段 `refresh_flows`）/ `slice`（分片 `start_refresh`）。

    三臂必须在**各自独立的进程**里跑：同进程里第二臂会吃到第一臂刚读热过的文件
    缓存，比较就偏了（所以下面用子进程驱动，而不是同进程连跑三次）。
    """
    from PySide6.QtCore import QTimer

    from rpa_core.catalog import load_catalog
    from rpa_core.cli import _commands_root
    from rpa_core.devserver.store import WorkflowDirStore
    from rpa_core.gui import app as gui_app
    from rpa_core.gui.home import HomeWindow

    local_app = gui_app.build_application([])
    local_window = HomeWindow(
        WorkflowDirStore(Path("workflows")), load_catalog(_commands_root()),
        defer_refresh=True,
    )
    local_window.show()
    local_app.processEvents()

    marks: list[tuple[int, float, float]] = []
    click: dict[str, float] = {"at_ms": -1.0, "switch_ms": -1.0}
    state: dict[str, object] = {"ready_ms": None}
    show_at = time.perf_counter()

    for expect_ms in (50, 150, 400):
        def _mark(expect=expect_ms) -> None:
            now = (time.perf_counter() - show_at) * 1000
            marks.append((expect, now, now - expect))
        QTimer.singleShot(expect_ms, _mark)

    def first_tab_click() -> None:
        """模拟用户的「第一下点页签」：60 ms 时点到运行历史页签（用户报障 ①）。"""
        click["at_ms"] = (time.perf_counter() - show_at) * 1000
        start = time.perf_counter()
        local_window.tabs.setCurrentIndex(1)
        local_app.processEvents()
        click["switch_ms"] = (time.perf_counter() - start) * 1000

    QTimer.singleShot(60, first_tab_click)

    if arm == "block":
        QTimer.singleShot(0, local_window.refresh_flows)
    elif arm == "slice":
        QTimer.singleShot(0, local_window.start_refresh)

    def check_ready() -> None:
        if state["ready_ms"] is not None:
            return
        if local_window.history_table.rowCount() > 0 and not local_window._scanning:
            state["ready_ms"] = (time.perf_counter() - show_at) * 1000

    poll = QTimer()
    poll.setInterval(5)
    poll.timeout.connect(check_ready)
    poll.start()

    QTimer.singleShot(900, local_app.quit)
    local_app.exec()
    local_window.close()
    return {"marks": marks, "click": click, "state": state}


def _run_arm_in_subprocess(arm: str) -> str:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--arm", arm],
        capture_output=True, text=True, cwd=ROOT, env=env, check=False,
    )
    return proc.stdout + proc.stderr


if len(sys.argv) >= 3 and sys.argv[1] == "--arm":
    _arm = sys.argv[2]
    _result = run_input_latency_arm(_arm)
    for _expect, _actual, _late in _result["marks"]:  # type: ignore[union-attr]
        print(f"  [{_arm:5s}] 本该 {_expect:4d} ms 触发的输入 → 实际 {_actual:7.1f} ms（推迟 {_late:7.1f} ms）")
    _click = _result["click"]
    print(
        f"  [{_arm:5s}] 第一下**点页签**：本该 60 ms 被受理 → 实际 {_click['at_ms']:7.1f} ms"
        f"（推迟 {_click['at_ms'] - 60:7.1f} ms），切换本身 {_click['switch_ms']:.1f} ms"
    )
    _ready = _result["state"]["ready_ms"]  # type: ignore[index]
    print(f"  [{_arm:5s}] 运行历史表填好：{'n/a（本臂不刷新）' if _ready is None else f'{_ready:.1f} ms'}")
    raise SystemExit(0)

print("=" * 78)
print("§1  惰性 import 的**边际**成本（子进程：先 import rpa_core.gui.app，再冷 import）")
print("=" * 78)
for module in ("rpa_core.gui.element_panel", "rpa_core.capture", "rpa_core.devserver.store",
               "rpa_core.gui.home"):
    elapsed, count = _subprocess_ms("import rpa_core.gui.app", module)
    print(f"  {elapsed:8.1f} ms   +{count:4d} modules   {module}")

print("\n" + "=" * 78)
print("§2  启动分段（真实平台插件；窗口短暂显示）")
print("=" * 78)

lap("解释器 + site 就绪")
from rpa_core.cli import _commands_root  # noqa: E402
from rpa_core.catalog import load_catalog  # noqa: E402
from rpa_core.devserver.store import WorkflowDirStore  # noqa: E402
from rpa_core.gui import app as gui_app  # noqa: E402

lap("import gui.app")

app = gui_app.build_application([])
lap("QApplication + apply_theme")

store = WorkflowDirStore(Path("workflows"))
catalog = load_catalog(_commands_root())
lap("WorkflowDirStore + load_catalog")

from PySide6.QtCore import QTimer  # noqa: E402

from rpa_core.gui.home import HomeWindow, list_runs  # noqa: E402

window = HomeWindow(store, catalog, defer_refresh=True)
lap("HomeWindow ctor(defer_refresh=True)")

print("\n--- C: list_runs（run_artifacts 全量扫）冷/热 ---")
artifacts = store.root.parent / "run_artifacts"
file_count = sum(1 for p in artifacts.rglob("*") if p.is_file()) if artifacts.exists() else 0
for label in ("第 1 次（进程内首读）", "第 2 次（进程内复用）"):
    start = time.perf_counter()
    with io_trace() as stats:
        runs = list_runs(artifacts, limit=0)
    print(
        f"  {label}: {ms(start):8.1f} ms | {len(runs):4d} 条 | "
        f"文本读 {stats['calls']:4d} 次 / open 自耗时 {float(stats['seconds']) * 1000:6.1f} ms | "
        f"样例 {','.join(str(n) for n in stats['names'][:3])}"
    )
print(f"  run_artifacts 文件总数: {file_count}")

# ---- §3 三臂 A/B：show 之后用户输入被推迟多久（D 段） --------------------
print("\n--- D: show 之后用户输入的排队延迟（本该 T 触发，实际晚了多少）---")
print("  [三臂各起一个独立进程：同进程连跑会吃到刚读热的文件缓存，比较就偏了]")
for _arm_name in ("none", "block", "slice"):
    print(_indent(_run_arm_in_subprocess(_arm_name)))

window.show()
app.processEvents()
lap("HomeWindow.show + 首帧")
window.refresh_flows()  # 主进程给 §4/§5 备数据（这里量的不是它）
QTimer.singleShot(200, app.quit)
app.exec()  # 让积压的布局/重绘落地，§4 量的才是「切换本身」而不是排队
lap("主进程：同步刷新 + 200ms 排空事件队列")

# ---- §4 页签首切 vs 二切（B 段） ----------------------------------------
print("\n--- B: 工作台页签切换（用户报障 ①：第一次很明显）---")


def switch(index: int) -> float:
    start = time.perf_counter()
    window.tabs.setCurrentIndex(index)
    app.processEvents()
    return ms(start)


print(f"  →运行历史  第 1 次: {switch(1):7.1f} ms")
print(f"  →流程库    第 1 次: {switch(0):7.1f} ms")
print(f"  →运行历史  第 2 次: {switch(1):7.1f} ms")
print(f"  →指令测试  第 1 次: {switch(2):7.1f} ms")
print(f"  →指令测试  第 2 次: {switch(2):7.1f} ms")
print(f"  →流程库           : {switch(0):7.1f} ms")
print(f"  运行历史表行数: {window.history_table.rowCount()} | 流程库表行数: {window.table.rowCount()}")

# ---- §5 编辑器：元素库首开 vs 二开（A 段） -------------------------------
print("\n--- A: 编辑器「元素库」dock（用户报障 ②：第一次卡，关掉重开就好多了）---")
print("     （忠实复现：最大化编辑器 + 打开流程，与用户操作同序）")
from rpa_core.gui.app import MainWindow  # noqa: E402

start = time.perf_counter()
editor = MainWindow(catalog, workflows_root=Path("workflows"))
print(f"  MainWindow ctor          : {ms(start):7.1f} ms")
editor.showMaximized()
app.processEvents()

_flow_name = next((n for n in store.list() if (store.root / n / "elements").is_dir()), None)
start = time.perf_counter()
editor._open_named_flow(_flow_name)
print(f"  _open_named_flow({_flow_name!r}): {ms(start):7.1f} ms")

print(f"  首开前 element_panel 已在 sys.modules: {'rpa_core.gui.element_panel' in sys.modules}")
print(f"  首开前 rpa_core.capture  已在 sys.modules: {'rpa_core.capture' in sys.modules}")

start = time.perf_counter()
editor._toggle_elements_dock()  # 首开完整路径：惰性 import + 构造 + 首次 show + 刷列表
print(f"  首次 _toggle_elements_dock()（完整首开路径）  : {ms(start):7.1f} ms")

editor._elements_dock_widget.hide()
start = time.perf_counter()
editor._toggle_elements_dock()
print(f"  二次 _toggle_elements_dock()（dock 已缓存）    : {ms(start):7.1f} ms")

element_store = editor._element_store()
print(f"  当前流程={editor._current_flow_name()!r} / 元素数={len(element_store.list()) if element_store else 'n/a'}")

editor.close()
window.close()
QTimer.singleShot(150, app.quit)
app.exec()

print("\n" + "=" * 78)
print("§6  主进程分段耗时汇总")
print("=" * 78)
for (prev_label, prev), (label, now) in zip(LAPS, LAPS[1:], strict=False):
    print(f"{(now - prev) * 1000:8.1f} ms   {prev_label} → {label}")
print(f"{LAPS[-1][1] * 1000:8.1f} ms   TOTAL（不含后续 exec）")
