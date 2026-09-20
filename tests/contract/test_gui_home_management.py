"""M27 S3 流程管理动作契约：复制/重命名/删除（连带运行历史与资产）/导入/导出。

- store 层：动作语义（重名拒绝、整目录 purge、导入导出形状）；
- GUI 层：删除先列连带范围且默认取消、正在编辑时拒绝；
- 运行历史清理：`purge_runs` 只删属于该流程的目录。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rpa_core.devserver.store import (
    WorkflowDirStore,
    WorkflowNameError,
    WorkflowStoreError,
)
from rpa_core.run_history import list_runs, purge_runs


def _store(tmp_path) -> WorkflowDirStore:
    return WorkflowDirStore(tmp_path / "workflows")


def _write_flow(store: WorkflowDirStore, name: str, flow_id: str | None = None) -> None:
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


def _write_run(store: WorkflowDirStore, run_id: str, workflow_id: str) -> Path:
    run_dir = store.root.parent / "run_artifacts" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "result.json").write_text(
        json.dumps({"run_id": run_id, "workflow_id": workflow_id, "status": "succeeded"}),
        encoding="utf-8",
    )
    return run_dir


# ---- store 层 --------------------------------------------------------------


def test_copy_flow_copies_assets_and_rejects_existing(tmp_path):
    store = _store(tmp_path)
    _write_flow(store, "alpha")
    (store.root / "alpha" / "elements").mkdir(parents=True)
    (store.root / "alpha" / "elements" / "e1.json").write_text("{}", encoding="utf-8")

    store.copy_flow("alpha", "alpha2")
    assert (store.root / "alpha2" / "workflow.json").is_file()
    assert (store.root / "alpha2" / "elements" / "e1.json").is_file()
    with pytest.raises(WorkflowStoreError):
        store.copy_flow("alpha", "alpha2")
    with pytest.raises(WorkflowNameError):
        store.copy_flow("alpha", "bad/../escape")


def test_rename_flow_moves_directory(tmp_path):
    store = _store(tmp_path)
    _write_flow(store, "alpha")
    store.rename_flow("alpha", "beta")
    assert store.list() == ["beta"]
    assert not (store.root / "alpha").exists()
    with pytest.raises(WorkflowStoreError):
        store.rename_flow("beta", "beta")


def test_delete_flow_purge_removes_assets(tmp_path):
    """purge=True：整目录删除（elements/、data/ 一并走）。"""
    store = _store(tmp_path)
    _write_flow(store, "alpha")
    (store.root / "alpha" / "elements").mkdir(parents=True)
    (store.root / "alpha" / "data").mkdir(parents=True)
    store.delete_flow("alpha", purge=True)
    assert store.list() == []
    assert not (store.root / "alpha").exists()


def test_export_and_import_flow_roundtrip(tmp_path):
    store = _store(tmp_path)
    _write_flow(store, "alpha", flow_id="flow-alpha")
    (store.root / "alpha" / "elements").mkdir(parents=True)
    (store.root / "alpha" / "elements" / "e1.json").write_text("{}", encoding="utf-8")

    target = tmp_path / "exported"
    store.export_flow("alpha", target)
    assert (target / "workflow.json").is_file()
    assert (target / "elements" / "e1.json").is_file()

    store.import_flow("alpha-imported", target)
    assert (store.root / "alpha-imported" / "workflow.json").is_file()
    with pytest.raises(WorkflowStoreError):
        store.import_flow("alpha-imported", target)  # 已存在


def test_purge_runs_only_removes_matching_workflow(tmp_path):
    store = _store(tmp_path)
    _write_flow(store, "alpha", flow_id="flow-alpha")
    _write_flow(store, "beta", flow_id="flow-beta")
    _write_run(store, "r-alpha", "flow-alpha")
    _write_run(store, "r-beta", "flow-beta")

    removed = purge_runs(store.root.parent / "run_artifacts", "flow-alpha")
    assert removed == 1
    remaining = {run["runId"] for run in list_runs(store.root.parent / "run_artifacts")}
    assert remaining == {"r-beta"}


# ---- GUI 层 ----------------------------------------------------------------


@pytest.fixture(scope="module")
def qapp():
    os = pytest.importorskip("os")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from rpa_core.gui.app import build_application

    return build_application()


@pytest.fixture(scope="module")
def catalog(qapp):
    from rpa_core.catalog import load_catalog
    from rpa_core.cli import _commands_root

    return load_catalog(_commands_root())


def test_home_delete_asks_with_scope_and_cancels_by_default(catalog, tmp_path, monkeypatch):
    """删除：确认框列出连带范围；选「取消」（默认）时什么都不删。"""
    store = _store(tmp_path)
    _write_flow(store, "alpha", flow_id="flow-alpha")
    (store.root / "alpha" / "elements").mkdir(parents=True)
    (store.root / "alpha" / "elements" / "e1.json").write_text("{}", encoding="utf-8")
    (store.root / "alpha" / "data").mkdir(parents=True)
    _write_run(store, "r-alpha", "flow-alpha")

    import rpa_core.gui.home as home_module
    from rpa_core.gui.home import HomeWindow

    seen: dict = {}

    def _question(parent, title, text, *args, **kwargs):
        seen["text"] = text
        seen["default"] = args[1] if len(args) > 1 else None
        return home_module.QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(home_module.QMessageBox, "question", staticmethod(_question))
    home = HomeWindow(store, catalog, open_editor=lambda name, run_id=None: None)
    home.table.setCurrentCell(0, 0)
    home._delete_flow()

    assert "元素资产：1 个" in seen["text"]
    assert "数据表格：有" in seen["text"]
    assert "运行历史：1 条" in seen["text"]
    assert seen["default"] == home_module.QMessageBox.StandardButton.Cancel
    # 取消 → 全部保留
    assert store.list() == ["alpha"]
    assert (store.root / "alpha" / "elements" / "e1.json").is_file()
    assert len(list_runs(store.root.parent / "run_artifacts")) == 1
    assert "已取消" in home.hint.text()


def test_home_delete_confirmed_removes_flow_assets_and_history(catalog, tmp_path, monkeypatch):
    """确认删除：流程目录（含资产）与该流程运行历史一并删除；其它流程不受影响。"""
    store = _store(tmp_path)
    _write_flow(store, "alpha", flow_id="flow-alpha")
    _write_flow(store, "beta", flow_id="flow-beta")
    (store.root / "alpha" / "elements").mkdir(parents=True)
    _write_run(store, "r-alpha", "flow-alpha")
    _write_run(store, "r-beta", "flow-beta")

    import rpa_core.gui.home as home_module
    from rpa_core.gui.home import HomeWindow

    monkeypatch.setattr(
        home_module.QMessageBox, "question",
        staticmethod(lambda *a, **k: home_module.QMessageBox.StandardButton.Yes),
    )
    home = HomeWindow(store, catalog, open_editor=lambda name, run_id=None: None)
    for row, flow in enumerate(home._flows):
        if flow["name"] == "alpha":
            home.table.setCurrentCell(row, 0)
    home._delete_flow()

    assert store.list() == ["beta"]
    assert not (store.root / "alpha").exists()
    remaining = {run["runId"] for run in list_runs(store.root.parent / "run_artifacts")}
    assert remaining == {"r-beta"}
    assert "已删除" in home.hint.text()


def test_home_rename_and_copy_actions(catalog, tmp_path, monkeypatch):
    """重命名/复制：经命名对话框落库并刷新列表。"""
    store = _store(tmp_path)
    _write_flow(store, "alpha")
    import rpa_core.gui.home as home_module
    from rpa_core.gui.home import HomeWindow

    monkeypatch.setattr(
        home_module.QInputDialog, "getText",
        staticmethod(lambda *a, **k: ("renamed", True)),
    )
    home = HomeWindow(store, catalog, open_editor=lambda name, run_id=None: None)
    home.table.setCurrentCell(0, 0)
    home._rename_flow()
    assert store.list() == ["renamed"]

    monkeypatch.setattr(
        home_module.QInputDialog, "getText",
        staticmethod(lambda *a, **k: ("copied", True)),
    )
    home._copy_flow()
    assert set(store.list()) == {"renamed", "copied"}


def test_home_refuses_management_while_editing(catalog, tmp_path, monkeypatch):
    """流程正在编辑器里打开：重命名/删除被拒绝并给出原因。"""
    store = _store(tmp_path)
    _write_flow(store, "alpha")
    import rpa_core.gui.home as home_module
    from rpa_core.gui import app as app_module
    from rpa_core.gui.home import HomeWindow

    warnings: list = []
    monkeypatch.setattr(
        home_module.QMessageBox, "warning",
        staticmethod(lambda *a, **k: warnings.append(a)),
    )
    home = HomeWindow(store, catalog, open_editor=lambda name, run_id=None: None)
    home.table.setCurrentCell(0, 0)

    # 伪造「编辑器正打开 alpha」
    monkeypatch.setattr(
        app_module, "_EDITOR_WINDOW",
        type("W", (), {"flow_path": store.root / "alpha" / "workflow.json"})(),
        raising=False,
    )
    home._rename_flow()
    home._delete_flow()
    assert store.list() == ["alpha"]  # 未被动过
    assert len(warnings) == 2
    assert "正在编辑器里打开" in str(warnings[0][2])


# ---- S4 运行入口 -----------------------------------------------------------


class _FakeRunManager:
    def __init__(self, states: list[dict]):
        self._states = list(states)
        self.calls: list[tuple] = []
        self.cancelled: list[str] = []
        self.closed = False

    def start(self, workflow_name, inputs=None, breakpoints=None):
        self.calls.append(("start", workflow_name, inputs, breakpoints))
        return {"runId": "run-1", "pid": 1}

    def status(self, run_id):
        if len(self._states) > 1:
            return self._states.pop(0)
        return self._states[0]

    def cancel(self, run_id):
        self.cancelled.append(run_id)
        return {'runId': run_id}

    def events(self, run_id):
        return {'events': []}

    def close(self):
        self.closed = True


def test_home_run_entry_starts_and_polls_to_terminal(catalog, tmp_path):
    """运行入口：发起运行 → 运行中禁用按钮 → 终态后刷新状态列并恢复按钮。"""
    store = _store(tmp_path)
    _write_flow(store, "alpha", flow_id="flow-alpha")
    from rpa_core.gui.home import HomeWindow

    home = HomeWindow(store, catalog, open_editor=lambda name, run_id=None: None)
    home.table.setCurrentCell(0, 0)
    fake = _FakeRunManager([
        {"running": True, "result": None},
        {"running": False, "result": {"status": "succeeded"}},
    ])
    home._run_manager = fake

    home._run_selected()
    assert fake.calls[0] == ("start", "alpha", None, None)
    assert home.run_button.isEnabled() is False
    assert "运行中" in home.run_status.text()

    home._poll_run()  # 仍在运行：按钮保持禁用
    assert home.run_button.isEnabled() is False

    home._poll_run()  # 终态：恢复并提示结果
    assert home.run_button.isEnabled() is True
    assert "succeeded" not in home.run_status.text()  # 用中文状态
    assert "成功" in home.run_status.text()


def test_home_run_entry_rejects_second_run_while_running(catalog, tmp_path):
    """已有运行在进行中：不再发起第二次（避免工作台叠加多个 run）。"""
    store = _store(tmp_path)
    _write_flow(store, "alpha")
    from rpa_core.gui.home import HomeWindow

    home = HomeWindow(store, catalog, open_editor=lambda name, run_id=None: None)
    home.table.setCurrentCell(0, 0)
    fake = _FakeRunManager([{"running": True, "result": None}])
    home._run_manager = fake

    home._run_selected()
    home._run_selected()
    assert len(fake.calls) == 1
    assert "已有运行" in home.run_status.text()


def test_home_shutdown_releases_run_manager(catalog, tmp_path):
    """关闭工作台：停轮询并 close RunManager（释放子进程句柄）。"""
    store = _store(tmp_path)
    from rpa_core.gui.home import HomeWindow

    home = HomeWindow(store, catalog, open_editor=lambda name, run_id=None: None)
    fake = _FakeRunManager([{"running": False, "result": None}])
    home._run_manager = fake
    home._start_run_polling()
    home._shutdown_run_manager()
    assert fake.closed is True
    assert home._run_manager is None


# ---- 影刀式行为：运行收起首页 + 浮窗；打开流程收起首页 + 编辑器最大化 ----------


def test_home_run_hides_home_and_shows_float(catalog, tmp_path):
    """首页运行：收起首页并弹出右下角浮窗；浮窗不提供暂停/继续/单步（控制权在编辑器）。"""
    store = _store(tmp_path)
    _write_flow(store, "alpha")
    from rpa_core.gui.home import HomeWindow

    home = HomeWindow(store, catalog, open_editor=lambda name, run_id=None: None)
    home.table.setCurrentCell(0, 0)
    fake = _FakeRunManager([{"running": True, "result": None}])
    home._run_manager = fake

    home._run_selected()
    assert home.isVisible() is False            # 首页收起（影刀式）
    assert home._run_float is not None
    assert home._run_float.pause_button.isVisible() is False
    assert home._run_float.continue_button.isVisible() is False
    assert home._run_float.step_button.isVisible() is False
    assert "运行中" in home._run_float.title_label.text() or "运行中" in (
        home._run_float.step_label.text()
    )

    # 取消：请求取消运行（不直接改状态）
    home._cancel_run()
    assert fake.cancelled == ["run-1"]


def test_home_restore_brings_back_home_and_closes_float(catalog, tmp_path):
    """还原：关闭浮窗并重新显示首页。"""
    store = _store(tmp_path)
    _write_flow(store, "alpha")
    from rpa_core.gui.home import HomeWindow

    home = HomeWindow(store, catalog, open_editor=lambda name, run_id=None: None)
    home.table.setCurrentCell(0, 0)
    fake = _FakeRunManager([{"running": True, "result": None}])
    home._run_manager = fake
    home._run_selected()
    home._restore_home()
    assert home._run_float is None


def test_home_open_flow_hides_home_and_editor_maximized(catalog, tmp_path, monkeypatch):
    """打开流程：首页收起；编辑器走 showMaximized（影刀式最大化）。"""
    store = _store(tmp_path)
    _write_flow(store, "alpha")
    from rpa_core.gui import app as app_module
    from rpa_core.gui.home import HomeWindow

    # 不注入 hook：走真实 app.open_editor_window（其内部用被替换的 _EDITOR_WINDOW）
    home = HomeWindow(store, catalog)
    calls: list[str] = []

    class _FakeEditor:
        def __init__(self):
            self.flow_path = None

        def showMaximized(self):
            calls.append("showMaximized")

        def raise_(self):
            pass

        def _open_named_flow(self, name):
            calls.append(f"open:{name}")

    fake_editor = _FakeEditor()
    monkeypatch.setattr(app_module, "_EDITOR_WINDOW", fake_editor, raising=False)
    home.open_flow("alpha")
    assert home.isVisible() is False
    assert calls == ["showMaximized", "open:alpha"]


def test_editor_close_returns_to_home(catalog, tmp_path, monkeypatch):
    """编辑器关闭 → 工作台（首页）重新显示（两段式往返）。"""
    store = _store(tmp_path)
    _write_flow(store, "alpha")
    from rpa_core.gui import app as app_module
    from rpa_core.gui.home import HomeWindow

    home = HomeWindow(store, catalog, open_editor=lambda name, run_id=None: None)
    home.hide()
    monkeypatch.setattr(app_module, "_HOME_WINDOW", home, raising=False)

    editor = app_module.MainWindow(catalog, workflows_root=store.root)
    editor._dirty = False
    editor._shutdown_run_manager()  # closeEvent 的收尾路径
    assert home.isVisible() is True


def test_rename_allowed_after_editor_closed(catalog, tmp_path, monkeypatch):
    """回归（维护者报障）：编辑器**已关闭**后重命名应放行——单例仍持有 flow_path，
    必须按「窗口是否可见」判断是否正在编辑。"""
    store = _store(tmp_path)
    _write_flow(store, "alpha")
    import rpa_core.gui.home as home_module
    from rpa_core.gui import app as app_module
    from rpa_core.gui.home import HomeWindow

    class _ClosedEditor:
        flow_path = store.root / "alpha" / "workflow.json"

        def isVisible(self):
            return False

    monkeypatch.setattr(app_module, "_EDITOR_WINDOW", _ClosedEditor(), raising=False)
    monkeypatch.setattr(
        home_module.QInputDialog, "getText",
        staticmethod(lambda *a, **k: ("alpha2", True)),
    )
    home = HomeWindow(store, catalog, open_editor=lambda name, run_id=None: None)
    home.table.setCurrentCell(0, 0)
    home._rename_flow()
    assert store.list() == ["alpha2"]  # 已重命名（未被误拒）


def test_rename_refused_while_editor_visible(catalog, tmp_path, monkeypatch):
    """编辑器**可见**（真正打开）时仍拒绝重命名。"""
    store = _store(tmp_path)
    _write_flow(store, "alpha")
    import rpa_core.gui.home as home_module
    from rpa_core.gui import app as app_module
    from rpa_core.gui.home import HomeWindow

    class _OpenEditor:
        flow_path = store.root / "alpha" / "workflow.json"

        def isVisible(self):
            return True

    monkeypatch.setattr(app_module, "_EDITOR_WINDOW", _OpenEditor(), raising=False)
    monkeypatch.setattr(
        home_module.QMessageBox, "warning", staticmethod(lambda *a, **k: None)
    )
    home = HomeWindow(store, catalog, open_editor=lambda name, run_id=None: None)
    home.table.setCurrentCell(0, 0)
    home._rename_flow()
    assert store.list() == ["alpha"]  # 未动
