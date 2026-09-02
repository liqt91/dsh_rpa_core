import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from rpa_core.catalog import load_catalog
from rpa_core.devserver import DevServer

ROOT = Path(__file__).resolve().parents[2]
COMMANDS = ROOT / "commands"
VALID_WORKFLOW = json.loads((ROOT / "examples" / "search-and-save" / "workflow.json").read_text())


@pytest.fixture()
def server(tmp_path):
    dev = DevServer(commands_root=COMMANDS, workflows_root=tmp_path / "workflows", port=0)
    dev.start()
    try:
        yield dev
    finally:
        dev.stop()


def _request(method: str, path: str, payload=None, base: str = ""):
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{base}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_devserver_binds_loopback_only(server):
    assert server.host == "127.0.0.1"


def test_catalog_endpoint_matches_load_catalog(server):
    base = f"http://127.0.0.1:{server.port}"
    status, payload = _request("GET", "/api/catalog", base=base)
    assert status == 200
    catalog = load_catalog(COMMANDS)
    assert payload["digest"] == catalog.digest
    assert [command["id"] for command in payload["commands"]] == sorted(catalog)
    for command in payload["commands"]:
        manifest = catalog[command["id"]]
        assert command["version"] == manifest.version
        assert command["kind"] == manifest.kind.value
        assert command["effect"] == manifest.effect.model_dump(mode="json")
        assert command["input_schema"] == manifest.input_schema
        assert command["output_schema"] == manifest.output_schema
        assert command["errors"] == [error.value for error in manifest.errors]


def test_compile_endpoint_accepts_valid_workflow(server):
    base = f"http://127.0.0.1:{server.port}"
    status, payload = _request("POST", "/api/compile", {"workflow": VALID_WORKFLOW}, base=base)
    assert status == 200
    assert payload["valid"] is True
    assert payload["errors"] == []
    assert payload["catalog_digest"] == load_catalog(COMMANDS).digest


def test_compile_endpoint_reports_invalid_workflow(server):
    base = f"http://127.0.0.1:{server.port}"
    unknown_command = json.loads(json.dumps(VALID_WORKFLOW))
    unknown_command["root"]["children"][1]["command"] = "browser.nonexistent"
    status, payload = _request("POST", "/api/compile", {"workflow": unknown_command}, base=base)
    assert status == 200
    assert payload["valid"] is False
    assert payload["errors"]

    bad_reference = json.loads(json.dumps(VALID_WORKFLOW))
    bad_reference["root"]["children"][1]["with"]["url"] = "${inputs.nope}"
    status, payload = _request("POST", "/api/compile", {"workflow": bad_reference}, base=base)
    assert status == 200
    assert payload["valid"] is False
    assert payload["errors"]


def test_compile_endpoint_rejects_malformed_body(server):
    base = f"http://127.0.0.1:{server.port}"
    status, payload = _request("POST", "/api/compile", {}, base=base)
    assert status == 400
    assert payload["error"] == "BAD_REQUEST"
    status, payload = _request(
        "POST", "/api/compile", {"capabilities": ["browser.read"]}, base=base
    )
    assert status == 400


def test_workflow_roundtrip(server):
    base = f"http://127.0.0.1:{server.port}"
    status, payload = _request(
        "PUT", "/api/workflows/search-and-save", VALID_WORKFLOW, base=base
    )
    assert status == 200
    assert payload == {"name": "search-and-save", "bytes": payload["bytes"]}

    status, stored = _request("GET", "/api/workflows/search-and-save", base=base)
    assert status == 200
    assert stored == VALID_WORKFLOW

    status, listing = _request("GET", "/api/workflows", base=base)
    assert status == 200
    assert listing == {"workflows": ["search-and-save"]}


def test_workflow_stored_as_directory_with_element_assets(server):
    base = f"http://127.0.0.1:{server.port}"
    status, payload = _request(
        "PUT", "/api/workflows/demo-flow", VALID_WORKFLOW, base=base
    )
    assert status == 200
    root = server.app._store.root
    assert (root / "demo-flow" / "workflow.json").is_file()
    assert not (root / "demo-flow.json").exists()

    element = {
        "kind": "browser",
        "selector": {"css": "#result"},
        "verifyCount": 1,
        "metadata": {},
    }
    status, payload = _request(
        "POST", "/api/workflows/demo-flow/elements/btn", element, base=base
    )
    assert status == 200
    assert (root / "demo-flow" / "elements" / "btn.json").is_file()


def test_workflow_list_ignores_stray_root_files(server):
    base = f"http://127.0.0.1:{server.port}"
    root = server.app._store.root
    (root / "stray.json").write_text("{}", encoding="utf-8")
    (root / "no-doc-dir").mkdir()
    (root / "no-doc-dir" / "elements").mkdir()
    status, payload = _request("PUT", "/api/workflows/ok-flow", VALID_WORKFLOW, base=base)
    assert status == 200
    status, listing = _request("GET", "/api/workflows", base=base)
    assert listing == {"workflows": ["ok-flow"]}


def test_workflow_name_rejects_path_traversal(server):
    base = f"http://127.0.0.1:{server.port}"
    status, payload = _request("PUT", "/api/workflows/..", VALID_WORKFLOW, base=base)
    assert status == 403
    assert payload["error"] == "FORBIDDEN"

    status, payload = _request("PUT", "/api/workflows/..%2Fescape", VALID_WORKFLOW, base=base)
    assert status == 404
    assert payload["error"] == "NOT_FOUND"

    status, payload = _request("GET", "/api/workflows/missing", base=base)
    assert status == 404
    assert payload["error"] == "NOT_FOUND"


def test_workflow_put_rejects_non_object(server):
    base = f"http://127.0.0.1:{server.port}"
    status, payload = _request("PUT", "/api/workflows/list-doc", [1, 2, 3], base=base)
    assert status == 400
    assert payload["error"] == "BAD_REQUEST"


def test_capture_endpoints_without_backend_are_501(server):
    base = f"http://127.0.0.1:{server.port}"

    status, payload = _request("POST", "/api/capture/desktop/start", {}, base=base)
    assert status == 501
    assert payload["error"] == "NOT_IMPLEMENTED"

    start_body = {"transport": "persistent"}
    status, payload = _request("POST", "/api/capture/browser/start", start_body, base=base)
    assert status == 501
    assert payload["error"] == "NOT_IMPLEMENTED"

    # 请求体校验（400）优先于后端配置检查（501）
    bad = _request("POST", "/api/capture/browser/start", {"transport": "cookie-magic"}, base=base)
    assert bad[0] == 400
    assert bad[1]["error"] == "BAD_REQUEST"

    status, payload = _request("GET", "/api/workflows/no-such-flow/elements", base=base)
    assert status == 200
    assert payload == {"elements": []}


def test_unknown_routes_and_methods(server):
    base = f"http://127.0.0.1:{server.port}"
    status, payload = _request("GET", "/api/nope", base=base)
    assert status == 404
    assert payload["error"] == "NOT_FOUND"

    status, payload = _request("GET", "/api/compile", base=base)
    assert status == 405
    assert payload["error"] == "METHOD_NOT_ALLOWED"

    status, payload = _request("POST", "/api/workflows", {}, base=base)
    assert status == 405


def test_request_body_size_limit(server):
    base = f"http://127.0.0.1:{server.port}"
    bloated = {"workflow": VALID_WORKFLOW, "pad": "x" * (1024 * 1024 + 1)}
    status, payload = _request("POST", "/api/compile", bloated, base=base)
    assert status == 413
    assert payload["error"] == "PAYLOAD_TOO_LARGE"


def test_editor_page_served_at_root(server):
    base = f"http://127.0.0.1:{server.port}"
    request = urllib.request.Request(f"{base}/")
    with urllib.request.urlopen(request) as response:
        assert response.status == 200
        assert response.headers["Content-Type"].startswith("text/html")
        body = response.read().decode("utf-8")
    assert body.startswith("<!DOCTYPE html>")
    assert "rpa_core 编辑器" in body


def test_editor_page_no_static_leak(server):
    base = f"http://127.0.0.1:{server.port}"
    for path in ("/static/index.html", "/..%2Fpyproject.toml", "/editor", "/index.html"):
        status, payload = _request("GET", path, base=base)
        assert status == 404
        assert payload["error"] == "NOT_FOUND"


def test_static_assets_served_from_allowlist(server):
    base = f"http://127.0.0.1:{server.port}"
    for name, content_type in (
        ("app.js", "text/javascript"),
        ("styles.css", "text/css"),
    ):
        request = urllib.request.Request(f"{base}/static/{name}")
        with urllib.request.urlopen(request) as response:
            assert response.status == 200
            assert response.headers["Content-Type"].startswith(content_type)
            assert response.read()


def test_static_assets_reject_unknown_and_traversal(server):
    base = f"http://127.0.0.1:{server.port}"
    for path in (
        "/static/nope.js",
        "/static/index.html",
        "/static/..%2Fserver.py",
        "/static/..%2F..%2Fpyproject.toml",
        "/static/sub%2Fapp.js",
    ):
        status, payload = _request("GET", path, base=base)
        assert status == 404
        assert payload["error"] == "NOT_FOUND"

    status, payload = _request("POST", "/static/app.js", {}, base=base)
    assert status == 405
    assert payload["error"] == "METHOD_NOT_ALLOWED"


def test_editor_page_rejects_post(server):
    base = f"http://127.0.0.1:{server.port}"
    status, payload = _request("POST", "/", {}, base=base)
    assert status == 405
    assert payload["error"] == "METHOD_NOT_ALLOWED"
