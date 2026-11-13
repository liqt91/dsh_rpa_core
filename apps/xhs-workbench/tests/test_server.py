"""HTTP 服务与采集触发的测试：数据层 + HTTP 层都用假 runner，不占机。

关键约束：服务端触发采集的路径必须和 verify_live 的「已验证路径」一致，这里通过
复用 ``build_run_command`` / ``snapshot_from_outcome`` 并在 run_collection 里直接落库来
保证。HTTP 只是外壳——所以单测既测 run_collection 的纯逻辑，也测 HTTP 路由返回真实数据。
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from src.collector import RunOutcome
from src.server import (
    _real_runner,
    _record_editor_run,
    make_context,
    make_server,
    run_collection,
)

KW = "AI效率工具"


def _ok_outcome(items: list[dict[str, Any]], run_id: str = "run-fake") -> RunOutcome:
    raw = json.dumps(items, ensure_ascii=False)
    return RunOutcome(
        run_id=run_id,
        status="succeeded",
        group="ok",
        outputs={"grabItems": {"outputs": {"result": raw}}},
        error=None,
    )


def _failed_outcome(run_id: str = "run-bad") -> RunOutcome:
    return RunOutcome(
        run_id=run_id,
        status="failed",
        group="failed",
        outputs={},
        error="boom",
    )


def _items(titles: list[str]) -> list[dict[str, Any]]:
    return [
        {"href": f"https://xhs/{KW}/{i}", "title": t, "text": f"body-{i}"}
        for i, t in enumerate(titles)
    ]


@contextmanager
def _live_server(ctx):
    server = make_server(ctx, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _get(base: str, path: str) -> dict[str, Any]:
    with urllib.request.urlopen(base + path, timeout=5) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def _post(base: str, path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(  # noqa: S310
        base + path, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # noqa: BLE001
        return exc.code, json.loads(exc.read().decode("utf-8"))


# --------------------------------------------------------------------------- #
# run_collection 纯逻辑
# --------------------------------------------------------------------------- #


def test_run_collection_records_snapshot_and_marks_ok(tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []
    ctx = make_context(
        root=tmp_path,
        runner=lambda kw, bt: (calls.append((kw, bt)) or _ok_outcome(_items(["a", "b"]))),
    )
    res = __import__("src.server", fromlist=["run_collection"]).run_collection(
        ctx, KW, "chrome"
    )
    assert res["ok"] is True
    assert res["snapshot_items"] == 2
    assert res["alerts_added"] == 0  # 第一次只有一条快照，没得比
    assert calls == [(KW, "chrome")]
    assert ctx.store.latest(KW) is not None
    assert ctx.store.latest(KW).count == 2
    assert ctx.monitor.get(KW).state == "ok"


def test_run_collection_detects_alerts_on_second_run(tmp_path: Path) -> None:
    ctx = make_context(
        root=tmp_path,
        runner=lambda kw, bt: _ok_outcome(_items(["a", "b"]), run_id="r1"),
    )
    run_collection(ctx, KW)
    # 第二次：b 标题变了，应产生一条 changed 告警
    ctx.runner = lambda kw, bt: _ok_outcome(
        [{"href": f"https://xhs/{KW}/0", "title": "a", "text": "body-0"},
         {"href": f"https://xhs/{KW}/1", "title": "B-改了", "text": "body-1"}],
        run_id="r2",
    )
    res = run_collection(ctx, KW)
    assert res["alerts_added"] == 1
    alerts = ctx.alerts.all()
    assert any(a.kind == "changed" for a in alerts)


def test_run_collection_marks_failed_on_non_ok(tmp_path: Path) -> None:
    ctx = make_context(root=tmp_path, runner=lambda kw, bt: _failed_outcome())
    res = run_collection(ctx, KW)
    assert res["ok"] is False
    assert ctx.monitor.get(KW).state == "failed"
    assert ctx.monitor.get(KW).last_error == "boom"
    assert ctx.store.latest(KW) is None  # 失败不落快照


def test_run_collection_marks_failed_when_runner_raises(tmp_path: Path) -> None:
    def boom(keyword: str, browser_type: str) -> RunOutcome:
        raise RuntimeError("cli crashed")

    ctx = make_context(root=tmp_path, runner=boom)
    res = run_collection(ctx, KW)
    assert res["ok"] is False
    entry = ctx.monitor.get(KW)
    assert entry.state == "failed"
    assert "RuntimeError" in (entry.last_error or "")


# --------------------------------------------------------------------------- #
# HTTP 层
# --------------------------------------------------------------------------- #


def test_http_get_endpoints_return_real_data(tmp_path: Path) -> None:
    ctx = make_context(
        root=tmp_path, runner=lambda kw, bt: _ok_outcome(_items(["a", "b"]), run_id="r1")
    )
    run_collection(ctx, KW)  # 先落一条真实数据
    with _live_server(ctx) as base:
        health = _get(base, "/api/health")
        assert health == {"ok": True}

        mon = _get(base, "/api/monitor")
        assert any(e["keyword"] == KW and e["state"] == "ok" for e in mon["entries"])

        snaps = _get(base, "/api/snapshots")
        assert snaps["keywords"][0]["keyword"] == KW
        assert len(snaps["keywords"][0]["latest"]["items"]) == 2

        prods = _get(base, "/api/products")
        assert prods["count"] == 2

        alerts = _get(base, "/api/alerts")
        assert alerts["count"] == 0

        detail = _get(base, f"/api/snapshots/{urllib.parse.quote(KW)}")
        assert len(detail["history"]) == 1


def test_http_products_exposes_post_detail_fields(tmp_path: Path) -> None:
    """前端要展示互动数据和评论，前提是 /api/products 真把它们带出来。

    数据层单测只证明 store 存得住；这里证明 HTTP 出口没有把新字段裁掉。
    """
    post = {
        **_items(["详情"])[0],
        "likes": "1.2万",
        "collects": "345",
        "comments": "88",
        "commentList": [{"author": "甲", "text": "第一条"}],
    }
    ctx = make_context(
        root=tmp_path, runner=lambda kw, bt: _ok_outcome([post], run_id="r1")
    )
    run_collection(ctx, KW)
    with _live_server(ctx) as base:
        prods = _get(base, "/api/products")
        assert prods["count"] == 1
        item = prods["items"][0]
        assert item["likes"] == "1.2万" and item["collects"] == "345"
        assert item["comments"] == "88"
        assert item["commentList"] == [{"author": "甲", "text": "第一条"}]


def test_http_collect_accepts_then_updates_monitor(tmp_path: Path) -> None:
    ctx = make_context(
        root=tmp_path, runner=lambda kw, bt: _ok_outcome(_items(["a", "b"]), run_id="r1")
    )
    with _live_server(ctx) as base:
        status, body = _post(base, "/api/collect", {"keyword": KW})
        assert status == 202
        assert body["accepted"] is True
        # 轮询监控直到收尾：pending（线程还没起）和 running 都算「还在跑」，只有
        # ok/failed 才是终态——不能拿「非 running」当完成（那条竞态真实踩到过）。
        for _ in range(50):
            mon = _get(base, "/api/monitor")
            entry = next((e for e in mon["entries"] if e["keyword"] == KW), None)
            if entry and entry["state"] in ("ok", "failed"):
                break
            time.sleep(0.05)
        assert entry["state"] == "ok"
        snaps = _get(base, "/api/snapshots")
        assert len(snaps["keywords"][0]["latest"]["items"]) == 2


def test_http_collect_rejects_missing_keyword(tmp_path: Path) -> None:
    ctx = make_context(root=tmp_path, runner=lambda kw, bt: _ok_outcome(_items(["a"])))
    with _live_server(ctx) as base:
        status, body = _post(base, "/api/collect", {})
        assert status == 400
        assert "keyword" in body["error"]


def test_http_pause_resume_endpoints(tmp_path: Path) -> None:
    ctx = make_context(
        root=tmp_path, runner=lambda kw, bt: _ok_outcome(_items(["a"]), run_id="r1")
    )
    run_collection(ctx, KW)
    with _live_server(ctx) as base:
        status, body = _post(base, "/api/pause", {"keyword": KW})
        assert status == 200
        assert body["state"] == "paused"
        assert ctx.monitor.get(KW).state == "paused"

        status, body = _post(base, "/api/resume", {"keyword": KW})
        assert status == 200
        assert body["state"] == "ok"
        assert ctx.monitor.get(KW).state == "ok"


def test_http_pause_all_resume_all(tmp_path: Path) -> None:
    ctx = make_context(
        root=tmp_path, runner=lambda kw, bt: _ok_outcome(_items(["a"]), run_id="r1")
    )
    run_collection(ctx, "kw-a")
    run_collection(ctx, "kw-b")
    with _live_server(ctx) as base:
        _, body = _post(base, "/api/pause-all", {})
        assert body["paused"] == 2
        counts = _get(base, "/api/monitor")["counts"]
        assert counts["paused"] == 2

        _, body = _post(base, "/api/resume-all", {})
        assert body["resumed"] == 2
        counts = _get(base, "/api/monitor")["counts"]
        assert counts["paused"] == 0


def test_http_unknown_keyword_snapshots_404(tmp_path: Path) -> None:
    ctx = make_context(root=tmp_path, runner=lambda kw, bt: _ok_outcome(_items(["a"])))
    with _live_server(ctx) as base:
        try:
            urllib.request.urlopen(base + "/api/snapshots/nope", timeout=5)
            raise AssertionError("should have 404")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404


def test_http_serves_index_html(tmp_path: Path) -> None:
    ctx = make_context(root=tmp_path, runner=lambda kw, bt: _ok_outcome(_items(["a"])))
    with _live_server(ctx) as base:
        with urllib.request.urlopen(base + "/", timeout=5) as resp:
            body = resp.read().decode("utf-8")
        assert resp.status == 200
        assert "小红书工作台" in body
        assert "/api/monitor" in body  # 前端确实消费真实 API，而非写死假数据


def test_real_runner_surfaces_stderr_when_result_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """rpa-core 中途失败时不写 result.json；_real_runner 必须把子进程 stderr 带进异常，
    否则 UI 只看到「result.json 不存在」，看不到真正的失败原因。
    """

    class _FakeProc:
        returncode = 0
        stdout = '{"run_id": "deadbeef-0000-0000-0000-000000000000"}'
        stderr = "BOOM: browser session detached before grabItems"

    monkeypatch.setattr("src.server.subprocess.run", lambda *a, **k: _FakeProc())

    ctx = make_context(root=tmp_path)
    with pytest.raises(RuntimeError) as exc:
        _real_runner(ctx, KW, "chrome")
    msg = str(exc.value)
    assert "result.json" in msg
    assert "BOOM: browser session detached before grabItems" in msg


def test_real_runner_locates_result_json_by_filesystem_not_stdout_run_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """回归测试：rpa-core 在 stdout 打印的「run 开始」事件 id 与真正写 result.json 的
    目录名不是同一个。旧实现用 stdout 里的 run_id 拼路径，永远找错目录 → 每次采集都误报
    「result.json 不存在」。新实现直接到 run_artifacts 下找本次 run 新写出的 result.json。
    """

    def _fake_run(*args, **kwargs):
        cmd = kwargs.get("args") or args[0]
        art = Path(cmd[cmd.index("--artifacts") + 1])
        final_id = "final-run-id-0000-0000-000000000000"
        # 模拟 rpa-core：stdout 首行是「开始」事件 id（与目录名不同），结果写在另一目录。
        (art / final_id / "result.json").parent.mkdir(parents=True, exist_ok=True)
        (art / final_id / "result.json").write_text(
            f'{{"run_id": "{final_id}", "status": "succeeded", "outputs": {{}}, "error": null}}',
            encoding="utf-8",
        )

        class _P:
            returncode = 0
            stdout = '{"run_id": "initial-event-id-0000-0000-000000000000"}'
            stderr = ""

        return _P()

    monkeypatch.setattr("src.server.subprocess.run", _fake_run)

    ctx = make_context(root=tmp_path)
    outcome = _real_runner(ctx, KW, "chrome")
    assert outcome.run_id == "final-run-id-0000-0000-000000000000"
    assert outcome.status == "succeeded"


def test_make_context_resolves_root_to_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--data-root 传相对路径时必须转成绝对路径。

    否则 _real_runner 的 artifact 目录会同时被「服务进程 rglob」和「rpa-core 子进程
    (cwd=REPO_ROOT)」两套不同 cwd 解析，刚写出的 result.json 会被 rglob 找错地方，
    导致每次采集都误报「未写出 result.json」（真实踩过）。

    注意：``SnapshotStore`` 会 mkdir，所以这里必须 chdir 到 tmp_path，
    否则会在仓库根建出 ``some_relative_dir/`` 垃圾目录（已踩过）。
    """
    monkeypatch.chdir(tmp_path)
    ctx = make_context(root="some_relative_dir")
    assert ctx.root.is_absolute()
    assert ctx.root == (tmp_path / "some_relative_dir").resolve()


def _seed_run(root: Path, run_id: str = "run-seed") -> Path:
    """造一次完整的 run 目录（result.json + events.jsonl + 截图），供流程接口断言。"""
    run_dir = root / "run_artifacts" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "result.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "status": "succeeded",
                "error": None,
                "outputs": {"grabItems": {"outputs": {"result": json.dumps(_items(["a", "b"]))}}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    events = [
        {"type": "stepStarted", "node_id": "grabItems", "timestamp": "2026-11-11T07:00:00Z",
         "payload": {"command": "browser.executeScript"}},
        {"type": "stepCompleted", "node_id": "grabItems", "timestamp": "2026-11-11T07:00:01Z",
         "payload": {"attempt": 1, "outputs": {"result": "[]"}}},
    ]
    (run_dir / "events.jsonl").write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n", encoding="utf-8"
    )
    (run_dir / "results.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    return run_dir


def test_http_workflow_exposes_steps_and_wait_before_grab(tmp_path: Path) -> None:
    ctx = make_context(root=tmp_path)
    with _live_server(ctx) as base:
        doc = _get(base, "/api/workflow")
    ids = [s["id"] for s in doc["steps"]]
    assert ids[0] == "openHome"
    assert ids.index("waitItems") < ids.index("grabItems")
    assert doc["count"] == len(ids)


def test_http_runs_lists_and_detail_with_shot(tmp_path: Path) -> None:
    _seed_run(tmp_path)
    ctx = make_context(root=tmp_path)
    with _live_server(ctx) as base:
        listing = _get(base, "/api/runs")
        assert [r["run_id"] for r in listing["runs"]] == ["run-seed"]
        assert listing["runs"][0]["items"] == 2
        detail = _get(base, "/api/runs/run-seed")
        assert detail["run_id"] == "run-seed"
        assert [s["id"] for s in detail["steps"]] == ["grabItems"]
        assert detail["artifacts"]["screenshots"] == ["results.png"]
        with urllib.request.urlopen(base + "/api/runs/run-seed/shot", timeout=5) as resp:  # noqa: S310
            assert resp.status == 200
            assert resp.read().startswith(b"\x89PNG")


def test_http_unknown_run_and_bad_run_id(tmp_path: Path) -> None:
    _seed_run(tmp_path)
    ctx = make_context(root=tmp_path)
    with _live_server(ctx) as base:
        with pytest.raises(urllib.error.HTTPError) as missing:
            urllib.request.urlopen(base + "/api/runs/nope", timeout=5)  # noqa: S310
        assert missing.value.code == 404
        with pytest.raises(urllib.error.HTTPError) as escape:
            urllib.request.urlopen(base + "/api/runs/..%2Fetc", timeout=5)  # noqa: S310
        assert escape.value.code == 400


def test_monitor_is_running_while_runner_executes(tmp_path: Path) -> None:
    """监控池必须在 runner 执行期间就是 running。

    之前是「跑完才置 running」，整段采集期间监控池仍挂着上一次的 ok——
    前端轮询 1 秒就读到「完成」，表现为「点了采集立刻跳出完成、没有任何数据」（真实踩到）。
    """
    ctx = make_context(root=tmp_path)
    seen: list[str] = []

    def runner(keyword: str, browser: str) -> RunOutcome:
        entry = ctx.monitor.get(keyword)
        seen.append(entry.state if entry else "(none)")
        return _ok_outcome(_items(["a"]))

    ctx.runner = runner
    res = run_collection(ctx, KW, "chrome")
    assert seen == ["running"], f"runner 执行时监控池状态应为 running，实际 {seen}"
    assert res["ok"] is True
    assert ctx.monitor.get(KW).state == "ok"
    assert ctx.monitor.get(KW).last_run_id == "run-fake"


def test_paused_keyword_is_refused_before_running(tmp_path: Path) -> None:
    ctx = make_context(root=tmp_path, runner=lambda k, b: _ok_outcome(_items(["a"])))
    ctx.monitor.register(KW)
    ctx.monitor.pause(KW)
    res = run_collection(ctx, KW, "chrome")
    assert res["ok"] is False
    assert "paused" in res["error"]
    assert ctx.monitor.get(KW).state == "paused"


def test_http_collect_rejects_paused_keyword(tmp_path: Path) -> None:
    ctx = make_context(root=tmp_path, runner=lambda k, b: _ok_outcome(_items(["a"])))
    ctx.monitor.register(KW)
    ctx.monitor.pause(KW)
    with _live_server(ctx) as base:
        status, body = _post(base, "/api/collect", {"keyword": KW})
    assert status == 409
    assert "暂停" in body["error"]


def _flow_with_keyword(tmp_path: Path, keyword: str) -> Path:
    flow = tmp_path / "flows" / "my_flow.json"
    flow.parent.mkdir(parents=True, exist_ok=True)
    flow.write_text(
        json.dumps(
            {"id": "my-flow", "name": "我的流程", "inputs": {"keyword": keyword}, "root": {}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return flow


def _editor_run_dir(data_root: Path, uuid: str, items: list[dict[str, Any]]) -> Path:
    run_dir = data_root / "_sandbox" / "runs" / "run-1" / "artifacts" / uuid
    run_dir.mkdir(parents=True)
    (run_dir / "result.json").write_text(
        json.dumps({"run_id": uuid, "status": "succeeded", "error": None,
                    "outputs": {
                        "grabItems": {
                            "outputs": {"result": json.dumps(items, ensure_ascii=False)}
                        }
                    }},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    return run_dir


def test_record_editor_run_lands_snapshot_and_monitor(tmp_path: Path) -> None:
    """编辑器里跑的 run 必须落进快照/监控——它不经过 run_collection，得靠桥层回调补。

    关键词取流程文档的 inputs.keyword（编辑器执行不传 --inputs，跑的就是这个默认值），
    这样编辑器采集和批量采集的历史能连成同一条线做前后比对。
    """
    flow = _flow_with_keyword(tmp_path, "露营装备")
    ctx = make_context(root=tmp_path / "data", workflow=flow)
    run_dir = _editor_run_dir(tmp_path / "data", "uuid-1", _items(["a", "b"]))

    res = _record_editor_run(ctx, "workflows/my_flow.json", run_dir / "result.json")

    assert res["ok"] is True
    assert res["snapshot_items"] == 2
    latest = ctx.store.latest("露营装备")
    assert latest is not None and latest.count == 2
    assert ctx.monitor.get("露营装备").state == "ok"
    assert ctx.monitor.get("露营装备").last_run_id == "uuid-1"


def test_record_editor_run_falls_back_to_flow_stem(tmp_path: Path) -> None:
    """流程没声明 inputs.keyword 时用文件名记账，不能整轮不落库。"""
    flow = tmp_path / "flows" / "no_keyword.json"
    flow.parent.mkdir(parents=True)
    flow.write_text(
        json.dumps({"id": "x", "name": "x", "inputs": {}, "root": {}}), encoding="utf-8"
    )
    ctx = make_context(root=tmp_path / "data", workflow=flow)
    run_dir = _editor_run_dir(tmp_path / "data", "uuid-2", _items(["a"]))

    _record_editor_run(ctx, "workflows/no_keyword.json", run_dir / "result.json")

    assert ctx.store.latest("no_keyword") is not None


def test_http_runs_lists_editor_runs(tmp_path: Path) -> None:
    """「流程」页必须能列出编辑器里跑出来的 run（嵌套在 _sandbox/runs/run-N/artifacts/）。"""
    ctx = make_context(root=tmp_path)
    _editor_run_dir(tmp_path, "uuid-http", _items(["a", "b"]))
    with _live_server(ctx) as base:
        listing = _get(base, "/api/runs")
        assert [r["run_id"] for r in listing["runs"]] == ["uuid-http"]
        detail = _get(base, "/api/runs/uuid-http")
        assert detail["run_id"] == "uuid-http"
        assert detail["items"] == 2


def test_http_editor_ppx_page_rewrites_assets_and_injects_bridge(tmp_path: Path) -> None:
    """托管 PPX dist 时必须改写 asset 前缀 + 注入 bridge.js + 换掉原型标题。

    三项都是「页面能起来」的前提：路径不改写 → 资源 404；bridge 不先注入 →
    ppx-js 认不出宿主、退回演示模式；标题不换 → 标签页写着「RPA Probe · PPX」。
    """
    ctx = make_context(root=tmp_path)
    with _live_server(ctx) as base:
        with urllib.request.urlopen(base + "/editor-ppx/", timeout=5) as resp:  # noqa: S310
            html = resp.read().decode("utf-8")
        with urllib.request.urlopen(base + "/editor-ppx/bridge.js", timeout=5) as resp:  # noqa: S310
            bridge = resp.read().decode("utf-8")
    assert 'src="/editor-ppx/assets/' in html
    assert '"/assets/' not in html
    assert '<script src="/editor-ppx/bridge.js"></script>' in html
    assert "小红书工作台" in html and "RPA Probe · PPX" not in html
    assert "window.pywebview" in bridge and "/editor-ppx-rpc" in bridge


def test_http_editor_ppx_rejects_path_escape(tmp_path: Path) -> None:
    """dist 之外的路径不许借道静态托管读出去（run_artifacts 里是用户数据）。"""
    ctx = make_context(root=tmp_path)
    with _live_server(ctx) as base:
        with pytest.raises(urllib.error.HTTPError) as bad:
            urllib.request.urlopen(base + "/editor-ppx/../../api/health", timeout=5)  # noqa: S310
    assert bad.value.code in (400, 404)
