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

    home = HomeWindow(store, catalog, open_editor=lambda name: None)
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
    opened: list[str] = []
    from rpa_core.gui.home import HomeWindow

    home = HomeWindow(store, catalog, open_editor=opened.append)
    home.table.setCurrentCell(0, 0)
    home._open_selected()
    assert opened == ["alpha"]

    # 新建：桩掉命名对话框，确认落库并打开
    import rpa_core.gui.home as home_module

    monkeypatch.setattr(
        home_module.QInputDialog, "getText", staticmethod(lambda *a, **k: ("gamma", True))
    )
    home._create_flow()
    assert "gamma" in store.list()
    assert opened[-1] == "gamma"
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
    opened: list[str] = []
    home = HomeWindow(store, catalog, open_editor=opened.append)

    monkeypatch.setattr(
        home_module.QInputDialog, "getText", staticmethod(lambda *a, **k: ("   ", True))
    )
    home._create_flow()
    assert store.list() == []
    assert opened == []
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

    home = HomeWindow(store, catalog, open_editor=lambda name: None)
    assert home.table.rowCount() == 0
    assert "空的" in home.hint.text() or "新建流程" in home.hint.text()
