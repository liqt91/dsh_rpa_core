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
from src.server import _real_runner, make_context, make_server, run_collection

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


def test_http_collect_accepts_then_updates_monitor(tmp_path: Path) -> None:
    ctx = make_context(
        root=tmp_path, runner=lambda kw, bt: _ok_outcome(_items(["a", "b"]), run_id="r1")
    )
    with _live_server(ctx) as base:
        status, body = _post(base, "/api/collect", {"keyword": KW})
        assert status == 202
        assert body["accepted"] is True
        # 轮询监控直到不再是 running（假 runner 瞬时完成）
        for _ in range(50):
            mon = _get(base, "/api/monitor")
            entry = next((e for e in mon["entries"] if e["keyword"] == KW), None)
            if entry and entry["state"] != "running":
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
    """rpa-core 只在成功时落 result.json；中途失败会打印 run_id 但不写文件。

    _real_runner 必须把子进程 stderr 带进异常，否则 UI 只看到「result.json 不存在」，
    看不到真正的失败原因（这正是一次真实采集失败被误报为「原因不明」的根因）。
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
    assert "deadbeef" in msg
    assert "BOOM: browser session detached before grabItems" in msg
