"""E2E：元素编辑确认对话框（捕获后确认入库 / 面板点元素名编辑）。"""
import json
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from rpa_core.devserver import DevServer

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def server(tmp_path):
    dev = DevServer(commands_root=ROOT / "commands", workflows_root=tmp_path / "workflows", port=0)
    dev.start()
    try:
        yield dev
    finally:
        dev.stop()


def _request_json(base: str, method: str, path: str, payload=None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"{base}{path}", data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read().decode("utf-8"))


def _seed(base, flow, name):
    _request_json(base, "POST", f"/api/workflows/{flow}/elements/{name}", {
        "kind": "browser", "selector": {"css": "#kw"}, "verifyCount": 1,
        "metadata": {"tag": "input", "text": ""},
    })


def test_element_edit_dialog_opens_and_saves(server):
    base = f"http://127.0.0.1:{server.port}"
    _seed(base, "edit-e2e", "searchBox")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{base}/")
        page.wait_for_selector('[data-command="browser.click"]')
        page.fill("#file-name", "edit-e2e")
        page.wait_for_selector("#elements-list li.element-item")

        # 点元素名打开编辑对话框
        page.click('#elements-list li.element-item .element-name')
        page.wait_for_selector("#element-dialog-mask:not(.hidden)")
        assert page.locator("#element-dialog-name").input_value() == "searchBox"
        assert page.locator("#element-dialog-selector").input_value() == "#kw"
        assert "捕获时命中 1 个" in page.locator("#element-dialog-verify").inner_text()

        # 改 selector 并保存（覆盖同名）
        page.fill("#element-dialog-selector", "#kw2")
        page.click("#element-dialog-save")
        page.wait_for_function(
            "() => document.querySelector('#element-dialog-mask').classList.contains('hidden')"
        )

        browser.close()

    stored = _request_json(base, "GET", "/api/workflows/edit-e2e/elements/searchBox")
    assert stored["selector"]["css"] == "#kw2"


def test_element_dialog_cancel_does_not_save(server):
    base = f"http://127.0.0.1:{server.port}"
    _seed(base, "edit-e2e", "cancelMe")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{base}/")
        page.wait_for_selector('[data-command="browser.click"]')
        page.fill("#file-name", "edit-e2e")
        page.wait_for_selector("#elements-list li.element-item")

        page.click('#elements-list li.element-item .element-name')
        page.wait_for_selector("#element-dialog-mask:not(.hidden)")
        page.fill("#element-dialog-selector", "#changed")
        page.click("#element-dialog-cancel")
        page.wait_for_function(
            "() => document.querySelector('#element-dialog-mask').classList.contains('hidden')"
        )

        browser.close()

    stored = _request_json(base, "GET", "/api/workflows/edit-e2e/elements/cancelMe")
    assert stored["selector"]["css"] == "#kw"  # 未被覆盖


def test_element_dialog_rename_deletes_old(server):
    base = f"http://127.0.0.1:{server.port}"
    _seed(base, "edit-e2e", "oldName")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{base}/")
        page.wait_for_selector('[data-command="browser.click"]')
        page.fill("#file-name", "edit-e2e")
        page.wait_for_selector("#elements-list li.element-item")

        page.click('#elements-list li.element-item .element-name')
        page.wait_for_selector("#element-dialog-mask:not(.hidden)")
        page.fill("#element-dialog-name", "newName")
        page.click("#element-dialog-save")
        page.wait_for_function(
            "() => document.querySelector('#element-dialog-mask').classList.contains('hidden')"
        )

        browser.close()

    listing = _request_json(base, "GET", "/api/workflows/edit-e2e/elements")
    assert "newName" in listing["elements"]
    assert "oldName" not in listing["elements"]


def test_variable_completion_dropdown(server):
    """属性表单输入 ${ 弹出可引用路径下拉，选中补全。"""
    base = f"http://127.0.0.1:{server.port}"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{base}/")
        page.wait_for_selector('[data-command="browser.navigate"]')

        # 加 navigate + click，选中 click，selector 字段输 ${ 触发补全
        page.click('[data-command="browser.navigate"]')
        page.wait_for_selector('#canvas li[data-node="navigate"]')
        page.click('[data-command="browser.click"]')
        page.wait_for_selector('#canvas li[data-node="click"]')

        selector_field = page.locator('#props-body input[data-field="selector"]')
        selector_field.click()
        selector_field.type("${")
        page.wait_for_selector(".ref-completion .ref-item")
        items = page.locator(".ref-completion .ref-item").all_text_contents()
        assert any("steps.navigate.outputs.sessionId" in i for i in items), items
        # 选中 sessionId 项补全
        page.locator(".ref-completion .ref-item",
                     has_text="steps.navigate.outputs.sessionId").first.click()
        assert "steps.navigate.outputs.sessionId" in selector_field.input_value()
        browser.close()


def test_run_status_highlight(server):
    """运行状态高亮：画布节点按最近运行 events 标 run-succeeded。"""
    base = f"http://127.0.0.1:{server.port}"
    # 种一个运行证据（events.jsonl 含 node_id）
    artifacts = server.app._store.root.parent / "run_artifacts" / "run-x"
    artifacts.mkdir(parents=True)
    (artifacts / "events.jsonl").write_text(
        '{"seq":1,"run_id":"run-x","type":"runStarted","node_id":null,"payload":{}}\n'
        '{"seq":2,"run_id":"run-x","type":"stepStarted","node_id":"navigate","payload":{}}\n'
        '{"seq":3,"run_id":"run-x","type":"stepCompleted","node_id":"navigate","payload":{}}\n',
        encoding="utf-8",
    )
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{base}/")
        page.wait_for_selector('[data-command="browser.navigate"]')
        page.click('[data-command="browser.navigate"]')
        page.wait_for_selector('#canvas li[data-node="navigate"]')

        page.click("#btn-run-status")
        page.wait_for_selector('#canvas li[data-node="navigate"].run-succeeded')
        browser.close()


def test_editor_run_control(server):
    """编辑器运行控制：运行 data 流程 → 状态转完成。"""
    base = f"http://127.0.0.1:{server.port}"
    data_workflow = {
        "schema_version": "1.0",
        "id": "rc-e2e",
        "name": "rc e2e",
        "inputs": {"workspace": "."},
        "root": {
            "type": "sequence",
            "id": "root",
            "children": [
                {"type": "action", "id": "fmt", "command": "data.format",
                 "with": {"template": "hello {name}", "values": {"name": "e2e"}}},
                {"type": "return", "id": "ret", "value": "${steps.fmt.outputs.text}"},
            ],
        },
    }
    _request_json(base, "PUT", "/api/workflows/rc-e2e", data_workflow)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{base}/")
        page.wait_for_selector('[data-command="data.format"]')

        page.fill("#file-name", "rc-e2e")
        page.select_option("#open-select", "rc-e2e")
        page.wait_for_selector('#canvas li[data-node="fmt"]')

        page.click("#btn-run")
        # 流程声明了 inputs → 运行参数对话框出现，预填 workspace="."，直接点「运行」
        page.wait_for_selector("#run-params-mask:not(.hidden)")
        page.click("#run-params-run")
        page.wait_for_selector("#run-panel:not(.hidden)")
        # 等运行完成（状态文本不再含"运行中"）
        page.wait_for_function(
            "() => !document.querySelector('#run-status-text').textContent.includes('运行中')",
            timeout=30000,
        )
        status_text = page.locator("#run-status-text").inner_text()
        assert "succeeded" in status_text or "完成" in status_text
        browser.close()


def test_capture_entry_menu_toggles(server):
    """元素库「＋捕获」入口：按钮开合菜单，含网页/桌面两个选项。"""
    base = f"http://127.0.0.1:{server.port}"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{base}/")
        page.wait_for_selector('[data-command="browser.click"]')

        page.click("#btn-element-capture")
        page.wait_for_selector("#capture-menu:not(.hidden)")
        options = page.locator("#capture-menu button").all_text_contents()
        assert len(options) == 2
        assert "网页" in options[0]
        assert "桌面" in options[1]

        # 点外部关闭
        page.click("#elements-head h2")
        page.wait_for_function(
            "() => document.querySelector('#capture-menu').classList.contains('hidden')"
        )
        browser.close()
