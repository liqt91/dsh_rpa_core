"""M41 探针：GUI 启动「初始化」分段耗时（决定要不要加载提示 / 提示覆盖哪一段）。

维护者 2026-09-28 报（第二轮之后）：

    启动前如果要初始化，可以有个加载提示

「如果要初始化」说明他自己也不确定启动期有没有值得一提的等待。本探针把启动
路径**按用户感知的顺序**切成段，逐段计时（offscreen，避免窗口抢前台）：

§1 `load_catalog(commands_root)`——命令目录 86 份 manifest 的读入 + 校验
§2 `build_application()`——QApplication 构造 + QDarkStyle 主题
§3 工作台 `HomeWindow(...)` 构造（`defer_refresh=True`）
§4 `<window>.show()` 首帧
§5 编辑器 `MainWindow(...)` 构造（`--workflow` 直开路径）
§6 汇总：**窗口出现之前**用户要干等多久（= 加载提示的覆盖率上限）

判据：若 §1+§2+§3（窗口出现前）已 > 300 ms，则加载提示有意义；否则提示本身
的显示/销毁开销会让启动「闪一下」，反而更差。

跑法：``uv run python .harness/spike/probe_m41_startup.py``
"""

from __future__ import annotations

import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPEAT = int(os.environ.get("M41_STARTUP_REPEAT", "3"))


def _timed(label: str, fn):
    """跑 fn，返回 (耗时中位数ms, 采样, 返回值)。"""
    samples = []
    value = None
    for _ in range(REPEAT):
        start = time.perf_counter()
        value = fn()
        samples.append((time.perf_counter() - start) * 1000)
    median = statistics.median(samples)
    print(
        f"    {label:<44} 中位 {median:>8.1f} ms   采样 "
        f"{' / '.join(f'{s:.1f}' for s in samples)}",
        flush=True,
    )
    return median, samples, value


def _splash_shot() -> int:
    """§4 专用：**真机平台**（非 offscreen）显示一次加载提示并全屏截屏。

    offscreen 下卡片画不到屏幕上、``ImageGrab`` 抓不到，所以单开一条路径：
    ``M41_STARTUP_ONLY=splash QT_QPA_PLATFORM=windows uv run python <本文件>``
    """
    from PIL import ImageGrab

    from rpa_core.gui.app import build_application
    from rpa_core.gui.splash import close_startup_splash, open_startup_splash

    app = build_application()
    splash = open_startup_splash(app)
    time.sleep(0.4)
    app.processEvents()
    shot = Path(__file__).with_name("_m41_splash_shot.png")
    ImageGrab.grab().save(shot)
    print(f"[M41] 显示中 visible={splash.isVisible()} → 全屏截屏 {shot}")
    close_startup_splash(splash)
    app.processEvents()
    print(f"[M41] 关闭后 visible={splash.isVisible()}")
    return 0


def main() -> int:
    if os.environ.get("M41_STARTUP_ONLY") == "splash":
        return _splash_shot()

    print(f"[M41] 启动分段（REPEAT={REPEAT}, QT_QPA_PLATFORM={os.environ['QT_QPA_PLATFORM']}）")
    commands_root = ROOT / "commands"
    workflows_root = ROOT / "workflows"

    print("\n§1 窗口出现之前")
    from rpa_core.catalog import load_catalog

    t_catalog, _, catalog = _timed(
        f"load_catalog({commands_root.name}/)", lambda: load_catalog(commands_root)
    )

    from rpa_core.gui.app import build_application

    app_holder: dict = {}

    def _build():
        if "app" not in app_holder:
            app_holder["app"] = build_application()
        return app_holder["app"]

    t_app, _, app = _timed("build_application()（QApplication + 主题）", _build)

    from rpa_core.devserver.store import WorkflowDirStore
    from rpa_core.gui.home import HomeWindow

    def _make_home():
        return HomeWindow(
            WorkflowDirStore(workflows_root), catalog, open_editor=lambda *a, **k: None,
            defer_refresh=True,
        )

    t_home, _, home = _timed("HomeWindow(...) 构造（工作台）", _make_home)

    def _show_home():
        home.show()
        app.processEvents()
        return None

    t_show, _, _ = _timed("home.show() + 首轮事件处理", _show_home)

    print("\n§2 --workflow 直开（编辑器路径）")
    from rpa_core.model.workflow import Workflow
    from rpa_core.gui.app import MainWindow

    flow = next(
        (p for p in sorted(workflows_root.glob("*/workflow.json"))), None
    )
    if flow is None:
        print("    （workflows/ 下没有 workflow.json，跳过）")
        t_main = float("nan")
    else:
        workflow = Workflow.model_validate_json(flow.read_text(encoding="utf-8"))

        def _make_main():
            return MainWindow(
                catalog, workflow=workflow, flow_path=flow, workflows_root=workflows_root
            )

        t_main, _, main = _timed(f"MainWindow(...) 构造（{flow.parent.name}）", _make_main)

        def _show_main():
            main.show()
            app.processEvents()
            return None

        t_show_main, _, _ = _timed("main.show() + 首轮事件处理", _show_main)

    print("\n§3 汇总（用户从敲命令到看见界面）")
    before_window = t_catalog + t_app + t_home
    print(f"    工作台：load_catalog + build_application + 构造 = {before_window:.1f} ms")
    print(f"    工作台：再 + show 首帧 = {before_window + t_show:.1f} ms")
    if t_main == t_main:  # not NaN
        editor = t_catalog + t_app + t_main
        print(f"    编辑器：load_catalog + build_application + 构造 = {editor:.1f} ms")
        print(f"    编辑器：再 + show 首帧 = {editor + t_show_main:.1f} ms")
    print(
        f"    判据：窗口出现前 {before_window:.1f} ms "
        f"→ 加载提示{'有意义' if before_window > 300 else '意义有限（会闪）'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
