import json

import pytest

from rpa_core.devserver.store import (
    WorkflowDirStore,
    WorkflowNameError,
    WorkflowNotFoundError,
    WorkflowStore,
)

DOC = {"schema_version": "1.0", "id": "x", "root": {"type": "sequence", "children": []}}


def test_flat_store_roundtrip(tmp_path):
    store = WorkflowStore(tmp_path / "flat")
    assert store.write("alpha", DOC) > 0
    assert store.list() == ["alpha"]
    assert store.read("alpha") == DOC
    assert (tmp_path / "flat" / "alpha.json").is_file()
    store.delete("alpha")
    assert store.list() == []
    with pytest.raises(WorkflowNotFoundError):
        store.read("alpha")


def test_dir_store_uses_flow_directory(tmp_path):
    store = WorkflowDirStore(tmp_path / "workflows")
    assert store.write("flow-a", DOC) > 0
    assert (tmp_path / "workflows" / "flow-a" / "workflow.json").is_file()
    assert not (tmp_path / "workflows" / "flow-a.json").exists()
    assert store.list() == ["flow-a"]
    assert store.read("flow-a") == DOC


def test_dir_store_ignores_dirs_without_workflow_json(tmp_path):
    root = tmp_path / "workflows"
    store = WorkflowDirStore(root)
    (root / "flow-a").mkdir(parents=True)
    (root / "flow-a" / "workflow.json").write_text(json.dumps(DOC), encoding="utf-8")
    (root / "no-doc").mkdir()
    (root / "no-doc" / "elements").mkdir(parents=True)
    (root / "stray.json").write_text("{}", encoding="utf-8")
    assert store.list() == ["flow-a"]


def test_dir_store_delete_removes_empty_dir(tmp_path):
    root = tmp_path / "workflows"
    store = WorkflowDirStore(root)
    store.write("flow-a", DOC)
    (root / "flow-a" / "elements").mkdir()
    (root / "flow-a" / "elements" / "keep.json").write_text("{}", encoding="utf-8")
    store.delete("flow-a")
    assert (root / "flow-a").exists()  # 目录仍含附属资产，保留
    (root / "flow-a" / "elements" / "keep.json").unlink()
    (root / "flow-a" / "elements").rmdir()
    with pytest.raises(WorkflowNotFoundError):
        store.delete("flow-a")  # workflow.json 已删 -> NotFound


def test_dir_store_delete_removes_empty_dir_when_empty(tmp_path):
    root = tmp_path / "workflows"
    store = WorkflowDirStore(root)
    store.write("flow-b", DOC)
    store.delete("flow-b")
    assert not (root / "flow-b").exists()


def test_name_validation_rejects_traversal(tmp_path):
    store = WorkflowDirStore(tmp_path / "w")
    for name in ("..", "../x", "a/b", "1abc"):
        with pytest.raises(WorkflowNameError):
            store.write(name, DOC)
    store_flat = WorkflowStore(tmp_path / "e")
    for name in ("..", "../x", "a/b"):
        with pytest.raises(WorkflowNameError):
            store_flat.write(name, DOC)


def test_directory_returns_validated_path(tmp_path):
    root = tmp_path / "w"
    store = WorkflowDirStore(root)
    assert store.directory("flow") == (root / "flow").resolve()
    with pytest.raises(WorkflowNameError):
        store.directory("../escape")


def test_store_without_create_does_not_mkdir(tmp_path):
    store = WorkflowStore(tmp_path / "missing", create=False)
    assert not (tmp_path / "missing").exists()
    assert store.list() == []
    with pytest.raises(WorkflowNotFoundError):
        store.read("anything")
    store.write("made", DOC)
    assert (tmp_path / "missing" / "made.json").is_file()
