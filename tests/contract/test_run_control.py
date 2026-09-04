import json
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from rpa_core.devserver import DevServer

ROOT = Path(__file__).resolve().parents[2]

DATA_WORKFLOW = {
    "schema_version": "1.0",
    "id": "rc-test",
    "name": "run-control test",
    "inputs": {"workspace": "."},
    "root": {
        "type": "sequence",
        "id": "root",
        "children": [
            {"type": "action", "id": "fmt", "command": "data.format",
             "with": {"template": "hello {name}", "values": {"name": "rc"}}},
            {"type": "return", "id": "ret", "value": "${steps.fmt.outputs.text}"},
        ],
    },
}


@pytest.fixture()
def server(tmp_path):
    dev = DevServer(commands_root=ROOT / "commands", workflows_root=tmp_path / "workflows", port=0)
    dev.start()
    try:
        yield dev
    finally:
        dev.stop()


def _request(method: str, path: str, payload=None, base: str = ""):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"{base}{path}", data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _save_workflow(base, name, doc):
    return _request("PUT", f"/api/workflows/{name}", doc, base=base)


def test_run_start_status_events(server):
    base = f"http://127.0.0.1:{server.port}"
    _save_workflow(base, "rc-flow", DATA_WORKFLOW)

    status, payload = _request("POST", "/api/runs", {"workflow": "rc-flow"}, base=base)
    assert status == 200
    run_id = payload["runId"]
    assert payload["pid"] > 0

    # 等子进程跑完
    deadline = time.time() + 20
    final = None
    while time.time() < deadline:
        status, s = _request("GET", f"/api/runs/{run_id}", base=base)
        assert status == 200
        if not s["running"]:
            final = s
            break
        time.sleep(0.3)
    assert final is not None, "run did not finish in time"
    assert final["result"] is not None
    assert final["result"]["status"] == "succeeded"
    assert final["result"]["return_value"] == "hello rc"

    status, ev = _request("GET", f"/api/runs/{run_id}/events", base=base)
    assert status == 200
    types = [e["type"] for e in ev["events"]]
    assert "runStarted" in types and "stepCompleted" in types


def test_run_start_unknown_workflow_404(server):
    base = f"http://127.0.0.1:{server.port}"
    status, payload = _request("POST", "/api/runs", {"workflow": "nope"}, base=base)
    assert status == 404


def test_run_start_missing_workflow_400(server):
    base = f"http://127.0.0.1:{server.port}"
    status, payload = _request("POST", "/api/runs", {}, base=base)
    assert status == 400


def test_run_cancel(server):
    base = f"http://127.0.0.1:{server.port}"
    _save_workflow(base, "rc-cancel", DATA_WORKFLOW)
    status, payload = _request("POST", "/api/runs", {"workflow": "rc-cancel"}, base=base)
    run_id = payload["runId"]
    status, c = _request("POST", f"/api/runs/{run_id}/cancel", base=base)
    assert status == 200
    assert c["cancelled"] is True
    # 取消后 status 不再 running
    status, s = _request("GET", f"/api/runs/{run_id}", base=base)
    assert s["running"] is False


def test_run_unknown_id_404(server):
    base = f"http://127.0.0.1:{server.port}"
    status, _ = _request("GET", "/api/runs/nope", base=base)
    assert status == 404
