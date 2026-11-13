"""PPX 桥单测：信封语义、假 ppx_py、原型 api 装载、工作台适配（flows 追加/新建流程/暂停守卫）。

装载真 api.py 的用例会 import rpa_core（ADR 0022-R1 松绑的编辑面），慢几百毫秒，值得。
"""

from __future__ import annotations

import json
import re
import sys
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
    # ★ 不硬编码卡片数：流程是维护者会在编辑器里改的活文档，写死 11 会随每次
    # 编辑腐烂（2026-11-13 流程加了步骤后这条凭空红）。对齐语义才是契约——
    # 文件里每个节点都有卡，控制流的 ::then/::else 合成卡必须挂回真实节点。
    flow = json.loads(
        (pb.XHS_WORKFLOWS / "xhs_product_search.json").read_text(encoding="utf-8")
    )
    node_ids: list[str] = []

    def walk(node: object) -> None:
        if not isinstance(node, dict):
            return
        if node.get("id"):
            node_ids.append(node["id"])
        for key in ("children", "then", "else", "catch"):
            for child in node.get(key) or []:
                walk(child)

    walk(flow["root"])
    file_ids = set(node_ids) - {"root"}
    card_ids = {card["id"] for card in env["data"]["cards"]}
    assert file_ids <= card_ids, f"文件节点没出卡：{sorted(file_ids - card_ids)}"
    dangling = {cid for cid in card_ids if cid.split("::")[0] not in file_ids}
    assert not dangling, f"合成卡挂在不存在的节点上：{sorted(dangling)}"
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


def _flow_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, Path]:
    """把新建流程落到临时工作区：_flow_path 认 api.WORKSPACE，_create_flow 认 XHS_WORKFLOWS。"""
    (tmp_path / "workflows").mkdir()
    monkeypatch.setattr(pb, "XHS_WORKFLOWS", tmp_path / "workflows")
    api = pb._load_api()
    monkeypatch.setattr(api, "WORKSPACE", tmp_path)
    created = pb._create_flow("控制流探针")
    # 桥层是单例：上一条用例可能在 _flows 里留下同名 path 的 sha，乐观锁会误判 FLOW_CHANGED
    api._flows.pop(created["path"], None)
    return created["path"], tmp_path / "workflows" / "控制流探针.json"


def test_insert_control_node_writes_real_ast_and_exposes_branch_anchors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """用户走的那条路：工作台 RPC → 原型 api → 磁盘 workflow → 画布组行 → 往分支再插。"""
    path, fp = _flow_fixture(tmp_path, monkeypatch)
    for kind in ("if", "forEach", "try", "return"):
        env = pb.dispatch("rpa.insertControlNode", {"path": path, "kind": kind})
        assert env["ok"] is True, env
        assert env["data"]["ok"] is True, env["data"]
        assert env["data"]["kind"] == kind
        assert env["data"]["compile"]["ok"] is True
        last = env["data"]

    doc = json.loads(fp.read_text(encoding="utf-8"))
    nodes = doc["root"]["children"]
    assert [n["type"] for n in nodes] == ["if", "forEach", "try", "return"]
    assert nodes[0]["then"] == [] and nodes[0]["else"] == []
    assert nodes[0]["condition"]["op"] == "truthy"
    assert nodes[1]["children"] == [] and nodes[1]["item_var"] == "item"
    assert nodes[2]["children"] == [] and nodes[2]["catch"] == []

    # 空分支也必须出虚拟组行，否则前端找不到「否则 / 异常」的落点
    cards = last["flow"]["cards"]
    anchors = {(c.get("parentId"), c.get("branch")) for c in cards if c["kind"] == "group"}
    assert (nodes[0]["id"], "then") in anchors
    assert (nodes[0]["id"], "else") in anchors
    assert (nodes[2]["id"], "catch") in anchors

    # 前端选中「否则」组行时发的参数：anchor_id=父容器 id + branch
    env = pb.dispatch(
        "rpa.insertNode",
        {"path": path, "command": "data.log", "anchor_id": nodes[0]["id"], "branch": "else"},
    )
    assert env["ok"] is True and env["data"]["ok"] is True
    assert env["data"]["placed"]["branch"] == "else"
    doc2 = json.loads(fp.read_text(encoding="utf-8"))
    assert doc2["root"]["children"][0]["else"][0]["command"] == "data.log"


def test_insert_control_node_rejects_unknown_kind_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, fp = _flow_fixture(tmp_path, monkeypatch)
    before = fp.read_bytes()
    env = pb.dispatch("rpa.insertControlNode", {"path": path, "kind": "while"})
    assert env["ok"] is True  # 信封成功，业务失败在 data.code
    assert env["data"]["code"] == "UNKNOWN_CONTROL_KIND"
    assert fp.read_bytes() == before


def test_editor_palette_wires_control_flow_group_to_new_rpc() -> None:
    """编辑面接线：左栏分组、四种节点、点击走新 RPC、虚拟组行的 branch 回传。

    这是静态判据（前端没有 headless 测试宿主），所以负向验证必须证明每条都能红：
    `.harness/spike/negative_control_flow.py` 的 I / J 两例分别打掉这两处。
    """
    main_js = (
        APP.parents[1]
        / ".harness"
        / "demo"
        / "ppx_probe"
        / "app"
        / "gui"
        / "src"
        / "main.js"
    ).read_text(encoding="utf-8")
    assert "流程控制 · ${controls.length}" in main_js
    assert "'rpa.insertControlNode'" in main_js
    for kind in ("if", "forEach", "try", "return"):
        assert f"{{ id: '{kind}'" in main_js
    assert "const branch = isGroup ? selected.branch : ''" in main_js
    assert "if (card.branch)" in main_js


def test_create_flow_id_is_valid_for_chinese_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """全中文名曾写出 `xhs-控制流探针` 这种 id，openFlow/保存/编译一律 ValidationError。"""
    monkeypatch.setattr(pb, "XHS_WORKFLOWS", tmp_path)
    pb._create_flow("控制流探针")
    doc = json.loads((tmp_path / "控制流探针.json").read_text(encoding="utf-8"))
    assert re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", doc["id"])
    repo_src = APP.parents[1] / "src"
    if str(repo_src) not in sys.path:
        sys.path.insert(0, str(repo_src))
    from rpa_core.model.workflow import Workflow

    assert Workflow.model_validate(doc).id == doc["id"]
