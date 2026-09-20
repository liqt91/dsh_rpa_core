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
