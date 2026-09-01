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


def _read_workflow(base: str, name: str) -> dict:
    with urllib.request.urlopen(f"{base}/api/workflows/{name}") as response:
        return json.loads(response.read().decode("utf-8"))


def _canvas_order(page) -> list[str]:
    return page.eval_on_selector_all(
        "#canvas li[data-node]", "els => els.map(e => e.dataset.node)"
    )


def test_editor_vertical_slice(server):
    base = f"http://127.0.0.1:{server.port}"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{base}/")

        page.wait_for_selector('[data-command="browser.launch"]')
        assert page.locator("#palette-list li").count() >= 20

        page.click('[data-command="browser.launch"]')
        page.wait_for_selector('#canvas li[data-node="launch"]')
        page.click('[data-command="browser.navigate"]')
        page.wait_for_selector('#canvas li[data-node="navigate"]')

        page.locator('[data-command="browser.close"]').drag_to(
            page.locator('#canvas li[data-node="launch"]'),
            target_position={"x": 30, "y": 3},
        )
        page.wait_for_selector('#canvas li[data-node="close"]')
        assert _canvas_order(page) == ["close", "launch", "navigate"]

        page.locator('#canvas li[data-node="navigate"]').drag_to(
            page.locator('#canvas li[data-node="launch"]'),
            target_position={"x": 30, "y": 3},
        )
        assert _canvas_order(page) == ["close", "navigate", "launch"]

        page.click('#canvas li[data-node="navigate"]')
        url_field = page.locator('#props-body input[data-field="url"]')
        url_field.fill("${inputs.missing}")

        page.click("#btn-compile")
        page.wait_for_selector("#compile-panel .bad")
        assert "Unknown workflow input reference" in page.locator("#compile-panel").inner_text()
        assert page.locator("#valid-badge").inner_text() == "✗ invalid"

        url_field.fill("http://127.0.0.1:9/")
        page.wait_for_timeout(100)
        page.click("#btn-compile")
        page.wait_for_selector("#compile-panel .ok")
        assert page.locator("#valid-badge").inner_text() == "✓ valid"

        page.fill("#file-name", "editor-e2e")
        page.click("#btn-save")
        page.wait_for_selector("#compile-panel .ok")

        page.reload()
        page.wait_for_selector('[data-command="browser.launch"]')
        page.select_option("#open-select", "editor-e2e")
        page.wait_for_selector('#canvas li[data-node="navigate"]')
        assert page.locator("#canvas li[data-node]").count() == 3
        assert _canvas_order(page) == ["close", "navigate", "launch"]
        browser.close()

    doc = _read_workflow(base, "editor-e2e")
    commands = [child["command"] for child in doc["root"]["children"]]
    assert commands == ["browser.close", "browser.navigate", "browser.launch"]
    assert doc["root"]["children"][1]["with"]["url"] == "http://127.0.0.1:9/"


def test_editor_nested_containers(server):
    base = f"http://127.0.0.1:{server.port}"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{base}/")
        page.wait_for_selector('[data-command="browser.launch"]')

        # 拖入 if 容器（空画布落点）
        page.locator('[data-flow="if"]').drag_to(page.locator("#canvas li.hint"))
        page.wait_for_selector('#canvas li[data-node="if"]')

        # then 空分支落点拖入 launch，else 空分支落点拖入 navigate
        page.locator('[data-command="browser.launch"]').drag_to(
            page.locator('li[data-node="if"] li.drop-empty').first
        )
        page.wait_for_selector('li[data-node="if"] li[data-node="launch"]')
        page.locator('[data-command="browser.navigate"]').drag_to(
            page.locator('li[data-node="if"] li.drop-empty').first
        )
        page.wait_for_selector('li[data-node="if"] li[data-node="navigate"]')

        # 嵌套 forEach 拖入 then 分支 launch 之后
        page.locator('[data-flow="forEach"]').drag_to(
            page.locator('li[data-node="if"] li[data-node="launch"]'),
            target_position={"x": 60, "y": 30},
        )
        page.wait_for_selector('li[data-node="if"] li[data-node="forEach"]')

        page.fill("#file-name", "nested-e2e")
        page.click("#btn-save")
        page.wait_for_selector("#compile-panel .ok")

        page.reload()
        page.wait_for_selector('[data-command="browser.launch"]')
        page.select_option("#open-select", "nested-e2e")
        page.wait_for_selector('li[data-node="if"] li[data-node="launch"]')
        page.wait_for_selector('li[data-node="if"] li[data-node="navigate"]')
        page.wait_for_selector('li[data-node="if"] li[data-node="forEach"]')
        browser.close()

    doc = _read_workflow(base, "nested-e2e")
    if_node = doc["root"]["children"][0]
    assert if_node["type"] == "if"
    assert if_node["then"][0]["command"] == "browser.launch"
    assert if_node["then"][1]["type"] == "forEach"
    assert if_node["else"][0]["command"] == "browser.navigate"
