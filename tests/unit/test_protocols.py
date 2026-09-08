from pathlib import Path

import pytest

from rpa_core.catalog import load_catalog
from rpa_core.compiler import WorkflowCompileError, WorkflowCompiler
from rpa_core.model.workflow import Workflow

ROOT = Path(__file__).resolve().parents[2]


def catalog():
    return load_catalog(ROOT / "commands")


def workflow(root):
    return Workflow.model_validate(
        {"id": "test", "name": "test", "inputs": {"url": "string"}, "root": root}
    )


def test_catalog_is_loaded_and_digest_is_stable():
    first = catalog()
    second = catalog()
    assert len(first) == 55
    assert first.digest == second.digest
    with pytest.raises(TypeError):
        first._commands["x"] = None


def test_compile_accepts_explicit_references_and_capabilities():
    plan = WorkflowCompiler(catalog()).compile(
        workflow(
            {
                "type": "action",
                "id": "launch",
                "command": "browser.launch",
                "with": {"headless": True},
            }
        ),
        {"browser.control", "process.start"},
    )
    assert plan.catalog_digest == catalog().digest


def test_compile_rejects_unknown_command_and_forward_step():
    compiler = WorkflowCompiler(catalog())
    with pytest.raises(WorkflowCompileError, match="Unknown command"):
        compiler.compile(
            workflow({"type": "action", "id": "bad", "command": "browser.nope", "with": {}}),
            set(),
        )
    with pytest.raises(WorkflowCompileError, match="forward step"):
        compiler.compile(
            workflow(
                {
                    "type": "sequence",
                    "id": "root",
                    "children": [
                        {
                            "type": "action",
                            "id": "later",
                            "command": "browser.getText",
                            "with": {
                                "sessionId": "${steps.future.outputs.sessionId}",
                                "selector": "h1",
                            },
                        },
                        {"type": "action", "id": "future", "command": "browser.launch", "with": {}},
                    ],
                }
            ),
            {"browser.read", "process.start", "browser.control"},
        )
