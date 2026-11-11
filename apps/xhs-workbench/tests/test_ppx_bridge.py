"""PPX 桥单测：信封语义、假 ppx_py、原型 api 装载、工作台适配（flows 追加/新建流程/暂停守卫）。

装载真 api.py 的用例会 import rpa_core（ADR 0022-R1 松绑的编辑面），慢几百毫秒，值得。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src import ppx_bridge as pb

APP = Path(__file__).resolve().parents[1]


def test_dispatch_envelope_and_method_not_found() -> None:
    env = pb.dispatch("rpa.nope")
    assert env["ok"] is False
    assert env["error"]["code"] == "METHOD_NOT_FOUND"
    assert env["data"] is None
    assert env["requestId"]


def test_dispatch_rejects_scalar_params() -> None:
    env = pb.dispatch("rpa.bootstrap", params=42)
    assert env["ok"] is False
    assert env["error"]["code"] == "INVALID_PARAMS"


def test_real_api_loads_and_bootstrap_lists_commands() -> None:
    env = pb.dispatch("rpa.bootstrap")
    assert env["ok"] is True
    data = env["data"]
    assert len(data["commands"]) >= 80
    assert data["workspace"].endswith("xhs-workbench")


def test_bootstrap_adaptation_puts_xhs_flow_first() -> None:
    data = pb.bootstrap_with_xhs_flows()
    assert data["flows"][0]["path"] == "workflows/xhs_product_search.json"
    assert all(not f["path"].endswith(".json") or f["path"].count("/") >= 1 for f in data["flows"])


def test_open_flow_and_compile_check_on_main_flow() -> None:
    env = pb.dispatch("rpa.openFlow", {"path": "workflows/xhs_product_search.json"})
    assert env["ok"] is True
    assert len(env["data"]["cards"]) == 11  # 与流程文件的 11 个 action 对齐
    env2 = pb.dispatch("rpa.compileCheck", {"path": "workflows/xhs_product_search.json"})
    assert env2["ok"] is True


def test_create_flow_writes_valid_skeleton(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pb, "XHS_WORKFLOWS", tmp_path)
    created = pb._create_flow("测试流程")
    path = tmp_path / "测试流程.json"
    assert path.is_file()
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["root"]["children"] == []
    assert doc["inputs"]["keyword"]
    assert created["path"] == "workflows/测试流程.json"
    with pytest.raises(pb.BridgeError):
        pb._create_flow("测试流程")  # 重名拒绝
    with pytest.raises(pb.BridgeError):
        pb._create_flow("  ")  # 空名拒绝


def test_pause_resume_without_run_are_clear_errors() -> None:
    with pytest.raises(pb.BridgeError, match="没有这个 run"):
        pb._pause_run("run-999")
    with pytest.raises(pb.BridgeError, match="没有这个 run"):
        pb._resume_run("run-999")


def test_active_run_is_none_when_idle() -> None:
    assert pb._active_run() is None


def test_sandbox_root_follows_data_root(tmp_path: Path) -> None:
    """编辑器 run 必须落在 <data_root>/_sandbox/runs，与流程页读的根同源。

    原型默认写在 <WORKSPACE>/_sandbox；若不复用 data root，用 --data-root 起服务时
    编辑器写的 run 和「流程」页读的 run 就是两个目录（真机上表现为「编辑器里跑完，
    流程页看不到这次运行」）。
    """
    pb.configure(tmp_path)
    assert pb.runs_root(tmp_path) == (tmp_path / "_sandbox" / "runs").resolve()
    api = pb._load_api()
    assert Path(api.RUNS).resolve() == pb.runs_root(tmp_path)
    assert Path(api.SANDBOX).resolve() == (tmp_path / "_sandbox").resolve()


def test_run_poll_hook_records_once_at_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """runPoll 的落库钩子：运行中不记、终态记一次、paused 不记账（等 resume 后再记）。"""
    run_dir = tmp_path / "_sandbox" / "runs" / "run-1" / "artifacts" / "uuid-1"
    run_dir.mkdir(parents=True)
    (run_dir / "result.json").write_text('{"status": "succeeded", "outputs": {}}', encoding="utf-8")

    calls: list[tuple[str, Path]] = []
    pb.set_recorder(lambda flow, path: calls.append((flow, Path(path))) or {"ok": True})
    monkeypatch.setattr(pb, "_runs", lambda: {"run-1": {"path": "workflows/a.json"}})

    base = {"ok": True, "runId": "run-1", "realRunId": "uuid-1",
            "artifacts": str(tmp_path / "_sandbox" / "runs" / "run-1" / "artifacts")}

    pb._record_if_finished("run-1", dict(base, running=True))
    assert calls == []  # 运行中不记

    pb._record_if_finished("run-1", dict(base, running=False))
    assert len(calls) == 1 and calls[0][0] == "workflows/a.json"

    pb._record_if_finished("run-1", dict(base, running=False))
    assert len(calls) == 1  # 同一个 run 只记一次

    # paused 是暂时收口：不记账，resume 后的真终态才记
    pb.set_recorder(lambda flow, path: calls.append((flow, Path(path))) or {"paused": True})
    pb._record_if_finished("run-1", dict(base, running=False))
    assert len(calls) == 2
    pb.set_recorder(lambda flow, path: calls.append((flow, Path(path))) or {"ok": True})
    pb._record_if_finished("run-1", dict(base, running=False))
    assert len(calls) == 3, "paused 之后必须允许再记一次终态"
    pb.set_recorder(None)


def test_run_poll_hook_skips_cancelled_and_missing_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    pb.set_recorder(lambda flow, path: calls.append(flow) or {"ok": True})
    monkeypatch.setattr(pb, "_runs", lambda: {"run-2": {"path": "workflows/a.json"}})
    base = {"ok": True, "runId": "run-2", "realRunId": "uuid-2", "artifacts": str(tmp_path)}
    pb._record_if_finished("run-2", dict(base, running=False, cancelled=True))
    pb._record_if_finished("run-2", dict(base, running=False))  # result.json 不存在
    assert calls == []
    pb.set_recorder(None)


def test_dispatched_run_poll_writes_snapshot_end_to_end(tmp_path: Path) -> None:
    """端到端接线：dispatch('rpa.runPoll') → 桥层钩子 → 快照库。

    单独测钩子和单独测落库都不够——真正会坏的是**它们之间没接上**：
    make_context 里的 set_recorder、registry 里的包装、流程关键词解析，任一环断了，
    用户在编辑器里跑完，商品库都不动。这条就钉这条缝。
    """
    from src.server import make_context

    flow = tmp_path / "flows" / "f.json"
    flow.parent.mkdir(parents=True)
    flow.write_text(
        json.dumps({"id": "f", "name": "f", "inputs": {"keyword": "端到端关键词"}, "root": {}},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    ctx = make_context(root=tmp_path / "data", workflow=flow)

    artifacts = tmp_path / "data" / "_sandbox" / "runs" / "run-1" / "artifacts"
    run_dir = artifacts / "uuid-e2e"
    run_dir.mkdir(parents=True)
    items = [{"title": "t1", "href": "https://x/1", "text": ""},
             {"title": "t2", "href": "https://x/2", "text": ""}]
    payload = {
        "run_id": "uuid-e2e",
        "status": "succeeded",
        "error": None,
        "outputs": {"grabItems": {"outputs": {"result": json.dumps(items, ensure_ascii=False)}}},
    }
    (run_dir / "result.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )

    api = pb._load_api()
    api._runs["run-e2e"] = {
        "path": "workflows/f.json",
        "proc": type("P", (), {"poll": lambda self: 0, "pid": 4242})(),
        "artifacts": artifacts,
        "work": tmp_path,
        "stdout": [],
        "stderr": [],
        "realRunId": "uuid-e2e",
        "cancelled": False,
        "t0": 0.0,
        "startedAt": 0.0,
    }
    pb._recorded.clear()
    try:
        env = pb.dispatch("rpa.runPoll", {"run_id": "run-e2e"})
    finally:
        api._runs.pop("run-e2e", None)

    assert env["ok"] is True
    latest = ctx.store.latest("端到端关键词")
    assert latest is not None and latest.count == 2
    assert ctx.monitor.get("端到端关键词").state == "ok"
