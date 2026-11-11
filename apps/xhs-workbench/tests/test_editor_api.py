"""编辑器 HTTP 接口单测：指令目录、参数保存（类型收拢）、插/删/移、执行控制守卫。

写接口一律用**临时 workflow 副本**（make_context(workflow=...)），不碰仓库真流程。
validate 接口真跑 CLI 子进程——它是编辑闭环的验收闸，值得这几秒。
"""

from __future__ import annotations

import json
import shutil
import threading
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from src.server import make_context, make_server

APP = Path(__file__).resolve().parents[1]
WF = APP / "workflows" / "xhs_product_search.json"


@contextmanager
def _server(tmp_path: Path):
    wf = tmp_path / "wf.json"
    shutil.copy(WF, wf)
    ctx = make_context(root=tmp_path / "data", workflow=wf)
    server = make_server(ctx, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}", wf, ctx
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _get(base: str, path: str) -> dict[str, Any]:
    with urllib.request.urlopen(base + path, timeout=5) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def _post(base: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(  # noqa: S310
        base + path, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def test_commands_endpoint_serves_palette(tmp_path: Path) -> None:
    with _server(tmp_path) as (base, _, _):
        body = _get(base, "/api/commands")
    assert len(body["commands"]) >= 80
    nav = next(c for c in body["commands"] if c["id"] == "browser.navigate")
    assert nav["label"] == "打开网页"


def test_validate_endpoint_passes_on_real_workflow(tmp_path: Path) -> None:
    with _server(tmp_path) as (base, _, _):
        body = _post(base, "/api/validate", {})
    assert body["valid"] is True
    assert body["rc"] == 0


def test_update_params_roundtrip_and_type_coercion(tmp_path: Path) -> None:
    with _server(tmp_path) as (base, wf, _):
        body = _post(base, "/api/workflow/update", {
            "node_id": "waitItems",
            "params": {"selector": "section.note-item", "state": "attached", "timeoutMs": "25000"},
        })
        assert body["ok"] is True
        assert body["validate"]["valid"] is True
        doc = json.loads(wf.read_text(encoding="utf-8"))
        node = next(n for n in doc["root"]["children"] if n["id"] == "waitItems")
        assert node["with"]["timeoutMs"] == 25000  # 字符串被收拢成 int
        assert isinstance(node["with"]["timeoutMs"], int)


def test_update_rejects_bad_type_with_field_name(tmp_path: Path) -> None:
    import urllib.error

    with _server(tmp_path) as (base, wf, _):
        before = wf.read_bytes()
        try:
            _post(base, "/api/workflow/update", {
                "node_id": "waitItems",
                "params": {"selector": "s", "timeoutMs": "not-a-number"},
            })
            raise AssertionError("应当 400")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
            body = json.loads(exc.read().decode("utf-8"))
            assert "timeoutMs" in body["error"]
        assert wf.read_bytes() == before  # 非法输入不许落盘


def test_insert_move_delete_cycle(tmp_path: Path) -> None:
    with _server(tmp_path) as (base, wf, _):
        ins = _post(
            base, "/api/workflow/insert", {"command": "browser.scroll", "after": "waitItems"}
        )
        new_id = ins["nodeId"]
        assert ins["validate"]["valid"] is True
        ids = [s["id"] for s in ins["steps"]]
        assert ids.index(new_id) == ids.index("waitItems") + 1

        moved = _post(base, "/api/workflow/move", {"node_id": new_id, "direction": -1})
        ids = [s["id"] for s in moved["steps"]]
        assert ids.index(new_id) == ids.index("waitItems") - 1

        deleted = _post(base, "/api/workflow/delete", {"node_id": new_id})
        assert new_id not in [s["id"] for s in deleted["steps"]]
        assert deleted["validate"]["valid"] is True


def test_delete_referenced_step_is_refused(tmp_path: Path) -> None:
    import urllib.error

    with _server(tmp_path) as (base, wf, _):
        before = wf.read_bytes()
        try:
            _post(base, "/api/workflow/delete", {"node_id": "grabItems"})
            raise AssertionError("应当 400")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
            assert "引用" in json.loads(exc.read().decode("utf-8"))["error"]
        assert wf.read_bytes() == before


def test_editor_run_endpoints_guard_states(tmp_path: Path) -> None:
    import urllib.error

    with _server(tmp_path) as (base, _, _):
        st = _get(base, "/api/editor/status")
        assert st["state"] == "idle"
        log = _get(base, "/api/editor/log?since=0")
        assert log["lines"] == [] and log["state"] == "idle"
        for op in ("pause", "resume", "stop"):
            try:
                _post(base, f"/api/editor/{op}", {})
                raise AssertionError("没有 run 时应当 409")
            except urllib.error.HTTPError as exc:
                assert exc.code == 409


def test_editor_run_real_collection_records_outcome(tmp_path: Path) -> None:
    """编辑器路径与批量路径共用终态记录：假子进程由 verify_live 级联测试覆盖，
    这里用注入 runner 的方式不行（编辑器自己 Popen）——所以这条只验证「POST 收单」语义：
    锁被占时再来一发必须 409。真机段在收尾时跑。
    """
    import urllib.error

    with _server(tmp_path) as (base, _, ctx):
        ctx.lock.acquire()
        try:
            _post(base, "/api/editor/run", {"keyword": "k"})
            raise AssertionError("锁被占时应当 409")
        except urllib.error.HTTPError as exc:
            assert exc.code == 409
        finally:
            ctx.lock.release()


def test_editor_log_endpoint_returns_lines_and_cursor(tmp_path: Path) -> None:
    """/api/editor/log 曾把 EditorRun 整个传给 items 计数器（该吃 run.outcome）→ 500。
    这条钉死「有 run 时 log 端点能回行 + cursor + items」。"""
    from src import editor_run as er

    with _server(tmp_path) as (base, _, ctx):
        run = er.EditorRun(keyword="k", browser="chrome")
        run.state = "finished"
        run_dir = ctx.root / "run_artifacts" / "run-x"
        run_dir.mkdir(parents=True)
        (run_dir / "events.jsonl").write_text(
            json.dumps({"type": "runStarted", "node_id": None,
                        "timestamp": "2026-11-11T08:00:00Z", "payload": {}}) + "\n",
            encoding="utf-8",
        )
        run.run_dir = run_dir
        ctx.editor = run
        body = _get(base, "/api/editor/log?since=0")
        assert body["state"] == "finished"
        assert body["cursor"] == 1 and "运行开始" in body["lines"][0]
        again = _get(base, "/api/editor/log?since=1")
        assert again["lines"] == []
