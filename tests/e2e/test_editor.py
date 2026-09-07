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


def _request_json(base: str, method: str, path: str, payload=None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"{base}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    with urllib.request.urlopen(request) as response:
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


def test_editor_control_node_forms(server):
    base = f"http://127.0.0.1:{server.port}"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{base}/")
        page.wait_for_selector('[data-flow="if"]')

        page.click('[data-flow="if"]')
        page.wait_for_selector('#canvas li[data-node="if"]')
        page.select_option('#props-body select[data-field="条件操作符"]', "eq")
        page.fill('#props-body input[data-field="左值（left）"]', "${inputs.q}")
        page.press('#props-body input[data-field="左值（left）"]', "Tab")
        page.fill('#props-body input[data-field="右值（right，truthy 时留空）"]', "x")
        page.press('#props-body input[data-field="右值（right，truthy 时留空）"]', "Tab")

        page.click('[data-flow="forEach"]')
        page.wait_for_selector('#canvas li[data-node="forEach"]')
        page.fill('#props-body input[data-field="循环变量名（item_var）"]', "row")
        page.fill('#props-body textarea[data-field="items（数组或引用）"]', '["a", "b"]')
        page.press('#props-body textarea[data-field="items（数组或引用）"]', "Tab")

        page.click('[data-flow="return"]')
        page.wait_for_selector('#canvas li[data-node="return"]')
        page.fill('#props-body textarea[data-field="返回值（value）"]', '"ok"')
        page.press('#props-body textarea[data-field="返回值（value）"]', "Tab")

        page.fill("#file-name", "control-forms")
        page.click("#btn-save")
        page.wait_for_selector("#compile-panel .ok")
        browser.close()

    doc = _read_workflow(base, "control-forms")
    if_node = doc["root"]["children"][0]
    assert if_node["condition"] == {"op": "eq", "left": "${inputs.q}", "right": "x"}
    loop_node = doc["root"]["children"][1]
    assert loop_node["item_var"] == "row"
    assert loop_node["items"] == ["a", "b"]
    assert doc["root"]["children"][2]["value"] == "ok"


def test_editor_copy_paste_undo_redo(server):
    base = f"http://127.0.0.1:{server.port}"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{base}/")
        page.wait_for_selector('[data-command="data.limit"]')

        page.click('[data-command="data.limit"]')
        page.click('[data-command="data.writeText"]')
        page.wait_for_selector('#canvas li[data-node="writeText"]')
        assert _canvas_order(page) == ["limit", "writeText"]

        page.click('#canvas li[data-node="limit"]')
        page.keyboard.press("Control+C")
        page.keyboard.press("Control+V")
        page.wait_for_selector('#canvas li[data-node="limit2"]')
        order = _canvas_order(page)
        assert order == ["limit", "limit2", "writeText"]
        assert len(set(order)) == 3

        # 输入框聚焦时快捷键不劫持
        page.click("#palette-filter")
        page.keyboard.press("Control+V")
        assert len(_canvas_order(page)) == 3

        page.click('#canvas li[data-node="limit"]')
        page.keyboard.press("Control+Z")
        page.wait_for_function(
            "() => document.querySelectorAll('#canvas li[data-node]').length === 2"
        )
        assert _canvas_order(page) == ["limit", "writeText"]
        page.keyboard.press("Control+Y")
        page.wait_for_function(
            "() => document.querySelectorAll('#canvas li[data-node]').length === 3"
        )
        assert _canvas_order(page) == ["limit", "limit2", "writeText"]
        browser.close()


def test_editor_multi_select_batch(server):
    base = f"http://127.0.0.1:{server.port}"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.on("dialog", lambda dialog: dialog.accept())
        page.goto(f"{base}/")
        page.wait_for_selector('[data-command="browser.launch"]')

        page.click('[data-command="browser.launch"]')
        page.click('[data-command="browser.navigate"]')
        page.click('[data-command="browser.close"]')
        page.wait_for_selector('#canvas li[data-node="close"]')

        page.click('#canvas li[data-node="launch"]')
        page.click('#canvas li[data-node="navigate"]', modifiers=["Shift"])
        assert page.locator("#canvas li.multi-selected").count() == 2

        page.click("#btn-down")
        page.wait_for_function(
            "() => Array.from(document.querySelectorAll('#canvas li[data-node]'))"
            ".map(e => e.dataset.node).join(',') === 'close,launch,navigate'"
        )
        assert _canvas_order(page) == ["close", "launch", "navigate"]

        # 非连续多选：批量移动不可用
        page.click('#canvas li[data-node="close"]')
        page.click('#canvas li[data-node="navigate"]', modifiers=["Control"])
        assert page.locator("#canvas li.multi-selected").count() == 2
        assert page.locator("#btn-up").is_disabled()
        assert page.locator("#btn-down").is_disabled()

        page.click("#btn-delete")
        page.wait_for_function(
            "() => document.querySelectorAll('#canvas li[data-node]').length === 1"
        )
        assert _canvas_order(page) == ["launch"]

        # 容器删除连带子树：确认对话框接受后整树移除
        page.click('[data-flow="if"]')
        page.wait_for_selector('#canvas li[data-node="if"]')
        page.click('#canvas li[data-node="if"]')
        page.click("#btn-delete")
        page.wait_for_function(
            "() => document.querySelectorAll('#canvas li[data-node]').length === 1"
        )
        assert _canvas_order(page) == ["launch"]
        browser.close()


def test_editor_chinese_display_and_no_horizontal_scroll(server):
    base = f"http://127.0.0.1:{server.port}"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{base}/")
        page.wait_for_selector('[data-command="browser.launch"]')

        # 命令面板：中文名 + 英文副标；无横向滚动条
        palette_text = page.locator("#palette-list").inner_text()
        assert "启动浏览器" in palette_text
        assert "browser.launch" in palette_text
        overflow = page.evaluate(
            "() => { const el = document.getElementById('palette-list');"
            " return el.scrollWidth - el.clientWidth; }"
        )
        assert overflow <= 1

        # 画布节点：中文标题 + 英文副标
        page.click('[data-command="browser.launch"]')
        page.wait_for_selector('#canvas li[data-node="launch"] .item-title')
        title = page.locator('#canvas li[data-node="launch"] .item-title')
        assert title.inner_text() == "启动浏览器"
        sub = page.locator('#canvas li[data-node="launch"] .item-sub')
        assert "browser.launch" in sub.inner_text()

        # 控制流分支标签中文化
        page.click('[data-flow="if"]')
        page.wait_for_selector('#canvas li[data-node="if"]')
        branch_labels = page.locator("#canvas .branch-label").all_inner_texts()
        assert branch_labels == ["满足时", "否则"]

        # 属性面板字段中文化（命令参数的英文键降为副标）
        page.click('[data-command="browser.navigate"]')
        page.wait_for_selector('#canvas li[data-node="navigate"]')
        labels = page.locator("#props-body .field label").all_inner_texts()
        assert any(label.startswith("网址") for label in labels)
        assert any("url" in label for label in labels)

        # if 条件操作符下拉中文
        page.click('#canvas li[data-node="if"]')
        op_options = page.locator(
            '#props-body select[data-field="条件操作符"] option'
        ).all_inner_texts()
        assert "等于" in op_options
        assert "为真（非空）" in op_options

        # 术语表存在且含关键术语
        page.locator("#glossary summary").click()
        glossary_text = page.locator("#glossary-list").inner_text()
        assert "执行器" in glossary_text
        assert "副作用" in glossary_text
        browser.close()


def test_editor_multi_action_bar(server):
    base = f"http://127.0.0.1:{server.port}"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{base}/")
        page.wait_for_selector('[data-command="browser.launch"]')

        # 无选中时悬浮条隐藏
        assert page.locator("#multi-bar").is_hidden()

        page.click('[data-command="browser.launch"]')
        page.click('[data-command="browser.navigate"]')
        page.wait_for_selector('#canvas li[data-node="navigate"]')

        # 单选时出现，含批量按钮
        page.click('#canvas li[data-node="launch"]')
        assert page.locator("#multi-bar").is_visible()
        assert page.locator("#multi-count").inner_text() == "1 个已选"

        # 悬浮条复制按钮复制选中子树，随后粘贴产生 id 重映射的副本
        page.click("#btn-copy")
        page.keyboard.press("Control+V")
        page.wait_for_selector('#canvas li[data-node="launch2"]')
        assert _canvas_order(page) == ["launch", "launch2", "navigate"]
        browser.close()


def test_editor_element_library_insert(server):
    base = f"http://127.0.0.1:{server.port}"
    _request_json(
        base, "POST", "/api/workflows/element-e2e/elements/searchBox",
        {
            "kind": "browser",
            "selector": {"css": "#kw"},
            "verifyCount": 1,
            "metadata": {"tag": "input"},
        },
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{base}/")
        page.wait_for_selector('[data-command="browser.click"]')

        page.fill("#file-name", "element-e2e")
        page.wait_for_selector("#elements-list li.element-item")
        assert "searchBox" in page.locator("#elements-list").inner_text()

        page.click('[data-command="browser.click"]')
        page.wait_for_selector('#canvas li[data-node="click"]')

        item = page.locator("#elements-list li.element-item", has_text="searchBox")
        item.locator("button", has_text="插入").click()
        page.wait_for_selector('#props-body input[data-field="selector"]')

        page.click("#btn-save")
        page.wait_for_selector("#compile-panel .ok")
        browser.close()

    doc = _read_workflow(base, "element-e2e")
    assert doc["root"]["children"][0]["with"]["selector"] == "#kw"


def test_editor_element_library_delete(server):
    base = f"http://127.0.0.1:{server.port}"
    _request_json(
        base, "POST", "/api/workflows/del-flow/elements/tmpEl",
        {"kind": "browser", "selector": {"css": "#x"}, "verifyCount": 1, "metadata": {}},
    )
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.on("dialog", lambda dialog: dialog.accept())
        page.goto(f"{base}/")
        page.wait_for_selector('[data-command="browser.click"]')
        page.fill("#file-name", "del-flow")
        page.wait_for_selector("#elements-list li.element-item")
        item = page.locator("#elements-list li.element-item", has_text="tmpEl")
        item.locator("button", has_text="✕").click()
        page.wait_for_function(
            "() => !document.querySelector('#elements-list li.element-item')"
        )
        browser.close()
    listing = _request_json(base, "GET", "/api/workflows/del-flow/elements")
    assert listing == {"elements": []}


@pytest.fixture()
def server_no_extension(tmp_path):
    # 空 extension build 目录：/api/extension/status 返回 packed:null（不触碰真机注册表）
    dev = DevServer(
        commands_root=ROOT / "commands",
        workflows_root=tmp_path / "workflows",
        port=0,
        extension_build_dir=tmp_path / "empty-ext-build",
    )
    dev.start()
    try:
        yield dev
    finally:
        dev.stop()


def test_editor_extension_dialog_shows_both_browsers(server_no_extension):
    base = f"http://127.0.0.1:{server_no_extension.port}"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{base}/")
        page.wait_for_selector("#btn-extension")
        page.click("#btn-extension")
        page.wait_for_selector("#extension-dialog-mask:not(.hidden)")
        page.wait_for_selector("#extension-browsers .ext-row")
        rows = page.locator("#extension-browsers .ext-row")
        assert rows.count() >= 2
        text = page.locator("#extension-browsers").inner_text()
        assert "Chrome" in text and "Edge" in text
        assert rows.first.locator(".ext-install").is_visible()
        page.click("#extension-dialog-close")
        assert page.locator("#extension-dialog-mask").is_hidden()
        browser.close()
