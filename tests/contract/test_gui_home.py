"""M27 S1 工作台骨架契约：流程列表（含最近运行状态）、打开/新建、空态引导。

测试在 offscreen Qt 平台运行；缺 PySide6 时整组跳过。
"""

from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")


@pytest.fixture(scope="module")
def qapp():
    from rpa_core.gui.app import build_application

    return build_application()


@pytest.fixture(scope="module")
def catalog(qapp):
    from rpa_core.catalog import load_catalog
    from rpa_core.cli import _commands_root

    return load_catalog(_commands_root())


def _store(tmp_path):
    from rpa_core.devserver.store import WorkflowDirStore

    return WorkflowDirStore(tmp_path / "workflows")


def _write_flow(store, name: str, flow_id: str | None = None) -> None:
    store.write(
        name,
        {
            "schema_version": "1.0",
            "id": flow_id or name,
            "name": name,
            "inputs": {},
            "root": {"type": "sequence", "id": "root", "children": []},
        },
    )


def _write_run(store, run_id: str, *, workflow_id: str, status: str = "succeeded") -> None:
    run_dir = store.root.parent / "run_artifacts" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "result.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "workflow_id": workflow_id,
                "status": status,
                "started_at": "2026-09-20T10:00:00+00:00",
                "ended_at": "2026-09-20T10:00:03+00:00",
                "error": None,
            }
        ),
        encoding="utf-8",
    )


def test_home_lists_flows_with_run_status(catalog, tmp_path):
    """流程列表：名称/最近运行状态/时间/元素数；按最近运行时间倒序。"""
    store = _store(tmp_path)
    _write_flow(store, "alpha", flow_id="flow-alpha")
    _write_flow(store, "beta", flow_id="flow-beta")
    (store.root / "alpha" / "elements").mkdir(parents=True, exist_ok=True)
    (store.root / "alpha" / "elements" / "e1.json").write_text("{}", encoding="utf-8")
    _write_run(store, "r1", workflow_id="flow-beta", status="failed")

    from rpa_core.gui.home import HomeWindow

    home = HomeWindow(store, catalog, open_editor=lambda name, run_id=None: None)
    names = [flow["name"] for flow in home._flows]
    assert set(names) == {"alpha", "beta"}
    # 有运行记录的排前面
    assert names[0] == "beta"
    beta = home._flows[0]
    assert beta["status"] == "failed"
    alpha = next(flow for flow in home._flows if flow["name"] == "alpha")
    assert alpha["elements"] == 1

    table = home.table
    assert table.rowCount() == 2
    assert table.item(0, 0).text() == "beta"
    assert table.item(0, 1).text() == "失败"  # 状态中文
    assert table.item(0, 2).text().startswith("2026-09-20")


def test_home_open_and_new_flow(catalog, tmp_path, monkeypatch):
    """打开选中流程 / 新建流程都经注入的 open_editor；新建写库后可被列出。"""
    store = _store(tmp_path)
    _write_flow(store, "alpha")
    opened: list[tuple[str, str | None]] = []
    from rpa_core.gui.home import HomeWindow

    home = HomeWindow(
        store, catalog,
        open_editor=lambda name, run_id=None: opened.append((name, run_id)),
    )
    home.table.setCurrentCell(0, 0)
    home._open_selected()
    assert opened == [("alpha", None)]

    # 新建：桩掉命名对话框，确认落库并打开
    import rpa_core.gui.home as home_module

    monkeypatch.setattr(
        home_module.QInputDialog, "getText", staticmethod(lambda *a, **k: ("gamma", True))
    )
    home._create_flow()
    assert "gamma" in store.list()
    assert opened[-1] == ("gamma", None)
    assert any(flow["name"] == "gamma" for flow in home._flows)


def test_home_new_flow_rejects_empty_and_invalid_name(catalog, tmp_path, monkeypatch):
    """空名/非法名：不落库、不打开（校验交给 store，界面给出提示）。"""
    store = _store(tmp_path)
    warnings: list[tuple] = []
    import rpa_core.gui.home as home_module
    from rpa_core.gui.home import HomeWindow

    monkeypatch.setattr(
        home_module.QMessageBox,
        "warning",
        staticmethod(lambda *a, **k: warnings.append(a)),
    )
    opened: list[tuple[str, str | None]] = []
    home = HomeWindow(
        store, catalog,
        open_editor=lambda name, run_id=None: opened.append((name, run_id)),
    )

    monkeypatch.setattr(
        home_module.QInputDialog, "getText", staticmethod(lambda *a, **k: ("   ", True))
    )
    home._create_flow()
    assert store.list() == []
    assert opened == []
    # 空名是知会型（无需决策）⇒ 页面内提示，不弹模态（M49 P1-3）
    assert "不能为空" in home.hint.text()
    assert warnings == []

    monkeypatch.setattr(
        home_module.QInputDialog,
        "getText",
        staticmethod(lambda *a, **k: ("bad/../escape", True)),
    )
    home._create_flow()
    assert store.list() == []
    assert opened == []
    # 非法名仍由 store 校验并把原因弹出来（属于「操作失败」，要看得见）
    assert warnings

    monkeypatch.setattr(
        home_module.QInputDialog,
        "getText",
        staticmethod(lambda *a, **k: ("bad/../escape", True)),
    )
    home._create_flow()
    assert store.list() == []
    assert opened == []


def test_home_empty_state_guides_user(catalog, tmp_path):
    """空库：给出引导文案，不抛异常。"""
    store = _store(tmp_path)
    from rpa_core.gui.home import HomeWindow

    home = HomeWindow(store, catalog, open_editor=lambda name, run_id=None: None)
    assert home.table.rowCount() == 0
    assert "空的" in home.hint.text() or "新建流程" in home.hint.text()


# ---- S2 运行历史页签 --------------------------------------------------------


def test_home_history_tab_lists_all_runs_and_filters_by_flow(catalog, tmp_path):
    """运行历史页签：全局列出所有流程的运行；筛选下拉可按流程收窄。"""
    store = _store(tmp_path)
    _write_flow(store, "alpha", flow_id="flow-alpha")
    _write_flow(store, "beta", flow_id="flow-beta")
    _write_run(store, "r-alpha", workflow_id="flow-alpha", status="succeeded")
    _write_run(store, "r-beta", workflow_id="flow-beta", status="failed")

    from rpa_core.gui.home import HomeWindow

    home = HomeWindow(store, catalog, open_editor=lambda name, run_id=None: None)
    assert home.history_table.rowCount() == 2  # 全部
    # 筛选到 alpha
    index = home.history_filter.findData("flow-alpha")
    home.history_filter.setCurrentIndex(index)
    assert home.history_table.rowCount() == 1
    assert home.history_table.item(0, 0).text().startswith("2026-09-20")
    assert home.history_table.item(0, 1).text() == "alpha"
    assert home.history_table.item(0, 2).text() == "成功"


def test_home_open_selected_run_opens_editor_with_timeline(catalog, tmp_path):
    """双击一条历史运行：打开对应流程的编辑器并带上该 run（载入时间线）。"""
    store = _store(tmp_path)
    _write_flow(store, "alpha", flow_id="flow-alpha")
    _write_run(store, "r1", workflow_id="flow-alpha")
    opened: list[tuple[str, str | None]] = []

    from rpa_core.gui.home import HomeWindow

    home = HomeWindow(
        store, catalog,
        open_editor=lambda name, run_id=None: opened.append((name, run_id)),
    )
    home.history_table.setCurrentCell(0, 0)
    home._open_selected_run()
    assert opened == [("alpha", "r1")]


def test_home_open_run_without_flow_reports_hint(catalog, tmp_path):
    """运行记录的流程已不在流程库：不打开编辑器，给出提示。"""
    store = _store(tmp_path)
    _write_flow(store, "alpha", flow_id="flow-alpha")
    _write_run(store, "ghost-run", workflow_id="flow-gone")
    opened: list[tuple[str, str | None]] = []

    from rpa_core.gui.home import HomeWindow

    home = HomeWindow(
        store, catalog,
        open_editor=lambda name, run_id=None: opened.append((name, run_id)),
    )
    for row, run in enumerate(home._visible_runs()):
        if run["runId"] == "ghost-run":
            home.history_table.setCurrentCell(row, 0)
    home._open_selected_run()
    assert opened == []
    assert "找不到" in home.history_hint.text()


# ---- 启动路径（M40）：产物扫描不在构造期同步跑 --------------------------------
def _counting_list_runs(monkeypatch):
    """把 home 模块里的 list_runs 换成计数版，返回计数字典。"""
    from rpa_core.gui import home as home_mod

    calls = {"count": 0}
    original = home_mod.list_runs

    def _counting(*args, **kwargs):
        calls["count"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(home_mod, "list_runs", _counting)
    return calls


def test_home_refresh_scans_artifacts_once(catalog, tmp_path, monkeypatch):
    """同步构造也只扫一次产物：两个页签读的是同一份运行记录。

    回归（M40）：此前 `refresh_flows()` 扫一遍、`refresh_history()` 又扫一遍，
    每次都要全量读 `run_artifacts` 下每个 `result.json`。
    """
    from rpa_core.gui.home import HomeWindow

    calls = _counting_list_runs(monkeypatch)
    store = _store(tmp_path)
    _write_run(store, "run-b", workflow_id="flow-b")

    home = HomeWindow(store, catalog)
    assert calls["count"] == 1, "构造期只该扫一次产物（流程列表与运行历史共用）"
    home.close()


def test_home_defer_refresh_skips_artifact_scan_at_construction(catalog, tmp_path, monkeypatch):
    """`defer_refresh=True`：构造期**不**扫产物，窗口可以先出现。

    回归（维护者 2026-09-28 报障「冷启动很慢，启动后内部切换标签也很慢」）：产物
    扫描是启动路径上唯一的重量级同步段，窗口要等它跑完才出现、期间点页签毫无响应。
    defer 之后由调用方（`run_gui`）在 `show()` 之后用零延时定时器触发。
    """
    from rpa_core.gui.home import HomeWindow

    calls = _counting_list_runs(monkeypatch)
    store = _store(tmp_path)
    _write_run(store, "run-a", workflow_id="flow-a")

    home = HomeWindow(store, catalog, defer_refresh=True)
    assert calls["count"] == 0, "defer_refresh=True 时构造期不该扫产物"
    assert "正在读取" in home.hint.text(), "延迟期间要有加载提示，不能让用户看到空表"

    home.refresh_flows()  # 调用方在 show() 之后触发的那一步
    assert calls["count"] == 1
    assert home.history_table.rowCount() == 1, "刷新后运行历史要真的填上（不是只算了账）"
    home.close()


# ---- 分片刷新（M40，2026-09-28 追加报障：第一次点页签/第一次开面板卡） ---------


def _pump_until(qapp, predicate, *, turns: int = 500) -> int:
    """反复派发事件直到条件成立，返回用掉的轮数。

    轮数是**判据的一部分**：分片刷新若退化成一轮跑完，下面的断言就该红。
    """
    for turn in range(turns):
        if predicate():
            return turn
        qapp.processEvents()
    raise AssertionError(f"条件在 {turns} 轮内未成立")


def _home(store, catalog, **kwargs):
    from rpa_core.gui.home import HomeWindow

    return HomeWindow(store, catalog, open_editor=lambda name, run_id=None: None, **kwargs)


def test_home_incremental_refresh_matches_sync_refresh(catalog, qapp, tmp_path):
    """分片刷新的结果必须与同步刷新**一致**，且确实分了多轮。

    用户报障「内部标签切换，第一次点击很明显」的根因不是切换本身，而是 `show()` 之后
    紧接着整段跑完的产物扫描：实测本该 show+60ms 受理的第一下点击被推迟 202ms。分片
    之后每轮只占一个预算，但**结果一字不能变**——否则就是拿正确性换响应性。
    """
    store = _store(tmp_path)
    _write_flow(store, "alpha", flow_id="flow-alpha")
    _write_flow(store, "beta", flow_id="flow-beta")
    for index in range(5):
        _write_run(store, f"r{index}", workflow_id="flow-beta", status="succeeded")

    sliced = _home(store, catalog, defer_refresh=True)
    sliced.scan_slice_seconds = 0.0  # 每轮只推进一条：把分片逼到最碎
    sliced.start_refresh()
    turns = _pump_until(qapp, lambda: not sliced._scanning)
    assert turns >= 6, f"5 条记录、每轮 1 条，必然要多轮才扫完；实测只用了 {turns} 轮"

    sync = _home(store, catalog)
    assert sliced._flows == sync._flows
    assert [run["runId"] for run in sliced._runs] == [run["runId"] for run in sync._runs]
    assert sliced.history_table.rowCount() == sync.history_table.rowCount() == 5
    assert sliced.table.item(0, 1).text() == sync.table.item(0, 1).text() == "成功"
    sliced.close()
    sync.close()


def test_home_scan_pending_does_not_claim_unrun(catalog, qapp, tmp_path):
    """扫描未完成期间：运行列显示 `…`，不显示「未运行」。

    「还没读到」与「确实没有运行记录」是两件事。分片把这段时间**拉长了**（本机约
    0.3s，冷盘更久），若沿用同步版的「未运行」文案，用户会看到一段时间的假结论。
    """
    store = _store(tmp_path)
    _write_flow(store, "alpha", flow_id="flow-alpha")
    _write_run(store, "r1", workflow_id="flow-alpha", status="succeeded")

    home = _home(store, catalog, defer_refresh=True)
    home.scan_slice_seconds = 0.0
    home.start_refresh()

    assert home._scanning is True
    assert home.table.rowCount() == 1, "流程列表要**立刻**填上（这一份是本地读取，很便宜）"
    assert home.table.item(0, 1).text() == "…", "还没读到就写「未运行」＝把未知当已知"
    assert "正在读取" in home.hint.text()
    assert "正在读取" in home.history_hint.text()

    _pump_until(qapp, lambda: not home._scanning)
    assert home.table.item(0, 1).text() == "成功"
    assert home.history_table.rowCount() == 1
    assert home.history_hint.text().startswith("共 1 条")
    home.close()


def test_home_sync_refresh_supersedes_pending_scan(catalog, qapp, tmp_path, monkeypatch):
    """分片扫描进行中的**同步刷新**必须取代它，不许后到覆盖新结果。

    两条同步入口都要管（`refresh_flows`＝流程库「刷新」，`refresh_history`＝历史页签「刷新」），
    否则用户看到的是「点了刷新、数字却变回旧的」——比慢更坏。

    判据是**中间态**：取代必须发生在新的同步读**开始之前**，而不是「函数返回时顺手已被取代」。
    `refresh_flows()` 结尾无条件调 `refresh_history(runs)`，后者也会 abort——只判后者的话，
    把 `refresh_flows` 自己的 abort 摘掉照样绿（负向验证注入 #12 第一次跑出来的假绿灯）。
    """
    import rpa_core.gui.home as home_module

    store = _store(tmp_path)
    _write_flow(store, "alpha", flow_id="flow-alpha")
    _write_run(store, "r1", workflow_id="flow-alpha")

    home = _home(store, catalog, defer_refresh=True)
    home.scan_slice_seconds = 0.0

    original_list_runs = home_module.list_runs
    reads: list[bool] = []

    def _spy_list_runs(root, **kwargs):
        # 记录「这一次同步读开始时，旧分片是不是已经死了」
        reads.append(not home._scanning and home._scan_iter is None)
        return original_list_runs(root, **kwargs)

    monkeypatch.setattr(home_module, "list_runs", _spy_list_runs)

    sync_entries = (("流程库刷新", home.refresh_flows), ("历史刷新", home.refresh_history))
    for entry, sync_call in sync_entries:
        home.start_refresh()
        qapp.processEvents()  # 推进一轮，制造「扫描进行中」
        assert home._scanning is True, entry

        mark = len(reads)
        sync_call()
        assert home._scanning is False, f"{entry} 后分片扫描仍在跑"
        assert home._scan_iter is None, entry
        assert home._scan_timer is not None and not home._scan_timer.isActive(), entry
        assert reads[mark:], f"{entry} 没触发同步读，判据打空了"
        assert all(reads[mark:]), (
            f"{entry}：旧分片要在新的同步读**开始之前**就被取代，"
            "靠函数返回时被别处兜掉不算（那是假绿灯）"
        )

    # 刷新后又多了一次运行：旧扫描若还在跑，收尾时会用 1 条覆盖掉 2 条
    _write_run(store, "r2", workflow_id="flow-alpha")
    home.refresh_flows()
    assert home.history_table.rowCount() == 2
    for _ in range(10):
        qapp.processEvents()
    assert home.history_table.rowCount() == 2, "被取代的旧扫描回来覆盖了新结果"
    home.close()


def test_run_gui_wires_sliced_refresh_on_workbench():
    """接线：工作台启动路径必须调分片版 `start_refresh`，否则分片白写。

    这条只看「run_gui 里写了哪一句」——行为在别的用例里量，这里防的是「实现了但没接上」。
    """
    from pathlib import Path

    app_py = Path(__file__).resolve().parents[2] / "src" / "rpa_core" / "gui" / "app.py"
    source = app_py.read_text(encoding="utf-8")
    start = source.index("def run_gui(")
    try:
        end = source.index("\ndef ", start + 1)  # run_gui 是文件最后一个函数
    except ValueError:
        end = len(source)
    body = source[start:end]
    assert "window.start_refresh" in body, "run_gui 的工作台分支没接分片刷新"
    assert "window.refresh_flows" not in body, "run_gui 不该再直接调同步版 refresh_flows"
