import json
import sys
from pathlib import Path

from rpa_core import cli
from rpa_core.catalog import load_catalog

ROOT = Path(__file__).resolve().parents[2]

_BROWSER_DESCRIPTOR = {
    "kind": "browser",
    "selector": {"css": "#go"},
    "verifyCount": 1,
    "metadata": {"tag": "button"},
}
_DESKTOP_DESCRIPTOR = {
    "kind": "desktop",
    "selector": {"locator": {"backend": "uia", "controlType": "Button",
                             "automationId": "submitButton"}},
    "verifyCount": 1,
    "metadata": {},
}


class _FakeBrowserSession:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = False
        self.cancelled = False
        self.closed = False
        _FakeBrowserSession.instances.append(self)

    def start(self):
        self.started = True
        return ["sid"]

    def pick(self, timeout_seconds=60, click_css=None):
        return dict(_BROWSER_DESCRIPTOR)

    def cancel(self):
        self.cancelled = True

    def close(self):
        self.closed = True


class _FakeDesktopSession:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.cancelled = False
        self.closed = False
        _FakeDesktopSession.instances.append(self)

    def pick(self, timeout_seconds=90):
        return dict(_DESKTOP_DESCRIPTOR)

    def cancel(self):
        self.cancelled = True

    def close(self):
        self.closed = True


def _run(argv, capsys):
    old = sys.argv
    sys.argv = ["rpa-core", *argv]
    try:
        code = cli.main()
    finally:
        sys.argv = old
    return code, capsys.readouterr().out.strip()


def test_cli_catalog_matches_load_catalog(capsys):
    code, out = _run(["catalog"], capsys)
    assert code == 0
    payload = json.loads(out)
    catalog = load_catalog(ROOT / "commands")
    assert payload["digest"] == catalog.digest
    assert [command["id"] for command in payload["commands"]] == sorted(catalog)


def test_cli_capture_browser_persistent_dispatch(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "BrowserCaptureSession", _FakeBrowserSession)
    _FakeBrowserSession.instances = []
    code, _ = _run([
        "capture", "browser", "--transport", "persistent", "--headless",
        "--start-url", "https://example.com",
    ], capsys)
    assert code == 0
    session = _FakeBrowserSession.instances[0]
    assert session.kwargs["transport"] == "persistent"
    assert session.kwargs["headless"] is True


def test_cli_capture_desktop_point_and_save(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "DesktopCaptureSession", _FakeDesktopSession)
    _FakeDesktopSession.instances = []
    workflows = tmp_path / "workflows"
    code, out = _run([
        "capture", "desktop", "--point", "10,20",
        "--save-as", "submitBtn", "--flow", "demo", "--workflows", str(workflows),
    ], capsys)
    assert code == 0
    payload = json.loads(out)
    assert payload["savedAs"] == "submitBtn"
    session = _FakeDesktopSession.instances[0]
    assert session.kwargs["point"] == {"x": 10, "y": 20}
    stored = workflows / "demo" / "elements" / "submitBtn.json"
    assert stored.is_file()
    assert json.loads(stored.read_text(encoding="utf-8"))["kind"] == "desktop"


def test_cli_capture_desktop_hover_passthrough(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "DesktopCaptureSession", _FakeDesktopSession)
    _FakeDesktopSession.instances = []
    code, _ = _run([
        "capture", "desktop", "--hover", "--timeout", "5",
    ], capsys)
    assert code == 0
    session = _FakeDesktopSession.instances[0]
    assert session.kwargs["hover"] is True


def test_cli_capture_save_requires_flow(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "BrowserCaptureSession", _FakeBrowserSession)
    _FakeBrowserSession.instances = []
    code, out = _run([
        "capture", "browser", "--save-as", "x", "--workflows", str(tmp_path / "w"),
    ], capsys)
    assert code == 2
    assert json.loads(out)["error"] == "BAD_REQUEST"


def test_cli_elements_list_show_verify(tmp_path, capsys):
    workflows = tmp_path / "workflows"
    element_dir = workflows / "demo" / "elements"
    element_dir.mkdir(parents=True)
    (element_dir / "ok.json").write_text(json.dumps(_BROWSER_DESCRIPTOR), encoding="utf-8")
    bad = {"kind": "browser", "selector": {"css": ""}, "verifyCount": 0, "metadata": {}}
    (element_dir / "bad.json").write_text(json.dumps(bad), encoding="utf-8")

    code, out = _run(["elements", "list", "--flow", "demo", "--workflows", str(workflows)], capsys)
    assert code == 0
    assert json.loads(out) == {"elements": ["bad", "ok"]}

    code, out = _run(
        ["elements", "show", "--flow", "demo", "--workflows", str(workflows), "ok"], capsys
    )
    assert code == 0
    assert json.loads(out)["selector"]["css"] == "#go"

    code, out = _run(
        ["elements", "verify", "--flow", "demo", "--workflows", str(workflows), "ok"], capsys
    )
    assert json.loads(out)["valid"] is True

    code, out = _run(
        ["elements", "verify", "--flow", "demo", "--workflows", str(workflows), "bad"], capsys
    )
    payload = json.loads(out)
    assert payload["valid"] is False
    assert any("css" in err["path"] for err in payload["errors"])


def test_cli_elements_missing_and_forbidden(tmp_path, capsys):
    code, out = _run(
        ["elements", "show", "--flow", "demo", "--workflows", str(tmp_path / "w"), "nope"],
        capsys,
    )
    assert code == 1
    assert json.loads(out)["error"] == "NOT_FOUND"

    code, out = _run(["elements", "list", "--flow", ".."], capsys)
    assert code == 3
    assert json.loads(out)["error"] == "FORBIDDEN"


def test_cli_run_compile_failure_is_clean_structured_error(tmp_path, capsys):
    """编译失败要输出结构化错误 + 退出码 2，不能甩一屏 traceback。

    回归点：`browser.navigate`（replay=unsafe）配了重试次数时，CLI 原来直接抛
    WorkflowCompileError 透出 traceback；devserver 侧看不到原因，界面只剩 exit 1。
    """
    workflow = {
        "schema_version": "1.0",
        "id": "cli-unsafe",
        "name": "unsafe retry",
        "inputs": {},
        "root": {
            "type": "sequence",
            "id": "root",
            "children": [
                {"type": "action", "id": "openPage", "command": "browser.navigate",
                 "with": {"url": "https://example.test/", "transport": "extension"},
                 "retry_count": 3},
            ],
        },
    }
    path = tmp_path / "unsafe.json"
    path.write_text(json.dumps(workflow, ensure_ascii=False), encoding="utf-8")

    old = sys.argv
    sys.argv = ["rpa-core", "run", str(path), "--artifacts", str(tmp_path / "art")]
    try:
        code = cli.main()
    finally:
        sys.argv = old
    captured = capsys.readouterr()

    assert code == 2
    assert "Traceback" not in captured.err
    payload = json.loads(captured.err.strip().splitlines()[-1])
    assert payload["error"] == "COMPILE_FAILED"
    assert "cannot be retried" in payload["message"]
    assert "browser.navigate" in payload["message"]
