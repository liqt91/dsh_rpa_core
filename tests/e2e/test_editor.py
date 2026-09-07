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


def test_editor_extension_dialog_shows_guide_for_both_browsers(server_no_extension):
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
        # 引导：目录路径输入框 + 复制/打开目录 + 打开浏览器(第2步) + 状态行(第3步无按钮)
        assert page.locator("#extension-dir-path").input_value()
        assert page.locator("#btn-ext-copy-path").is_visible()
        assert page.locator("#btn-ext-open-dir").is_visible()
        assert page.locator("#extension-open-browsers .ext-open-browser").count() == 2
        # 第 3 步扩展页只能手动输入（无按钮），只渲染状态徽标
        assert page.locator("#extension-browsers .ext-open-page").count() == 0
        assert rows.first.locator(".ext-badge").count() >= 1
        # 点击遮罩不关闭（仅「关闭」按钮关闭）
        page.mouse.click(5, 5)
        page.wait_for_timeout(200)
        assert not page.locator("#extension-dialog-mask").is_hidden()
        page.click("#extension-dialog-close")
        assert page.locator("#extension-dialog-mask").is_hidden()
        browser.close()


def test_editor_resizable_panels_persist(server):
    base = f"http://127.0.0.1:{server.port}"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{base}/")
        page.wait_for_selector('[data-command="browser.launch"]')

        def palette_width():
            return page.evaluate("document.getElementById('palette').offsetWidth")

        before = palette_width()
        handle = page.locator('.resize-v[data-resize="palette"]')
        box = handle.bounding_box()
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + 50)
        page.mouse.down()
        page.mouse.move(box["x"] + box["width"] / 2 + 90, box["y"] + 50, steps=6)
        page.mouse.up()
        after = palette_width()
        assert after > before, f"palette should widen: {before} -> {after}"

        # 刷新后宽度保持（localStorage 记忆）
        page.reload()
        page.wait_for_selector('[data-command="browser.launch"]')
        restored = palette_width()
        assert abs(restored - after) <= 2, f"width should persist: {after} -> {restored}"
        browser.close()


def test_editor_bottom_element_dock_resizable(server):
    """元素库在底部 dock（#bottom-panels），高度可拖且记忆。"""
    base = f"http://127.0.0.1:{server.port}"
    _request_json(
        base, "POST", "/api/workflows/bottom-dock-flow/elements/searchBox",
        {"kind": "browser", "selector": {"css": "#kw"},
         "verifyCount": 1, "metadata": {"tag": "input"}},
    )
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{base}/")
        page.wait_for_selector('[data-command="browser.launch"]')
        page.fill("#file-name", "bottom-dock-flow")
        page.wait_for_selector("#bottom-panels #elements-list li.element-item")
        assert "searchBox" in page.locator("#bottom-panels").inner_text()
        # props 面板不再含元素库
        assert page.locator("#props #elements-list").count() == 0

        def bottom_height():
            return page.evaluate("document.getElementById('bottom-panels').offsetHeight")

        before = bottom_height()
        handle = page.locator("#bottom-resize")
        box = handle.bounding_box()
        page.mouse.move(box["x"] + 200, box["y"] + box["height"] / 2)
        page.mouse.down()
        # dock 在页面底部：向上拖（y 减小）增高
        page.mouse.move(box["x"] + 200, box["y"] + box["height"] / 2 - 60, steps=6)
        page.mouse.up()
        after = bottom_height()
        assert after > before, f"dock should grow: {before} -> {after}"

        page.reload()
        page.wait_for_selector('[data-command="browser.launch"]')
        page.fill("#file-name", "bottom-dock-flow")
        page.wait_for_selector("#bottom-panels #elements-list li.element-item")
        restored = bottom_height()
        assert abs(restored - after) <= 2, f"dock height should persist: {after} -> {restored}"
        browser.close()


def test_editor_element_auto_refresh_on_poll(server):
    """切片 F：捕获在后台落库时，dock 经轮询自动出现新元素（无需手动 ↻）。"""
    base = f"http://127.0.0.1:{server.port}"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{base}/")
        page.wait_for_selector('[data-command="browser.launch"]')
        page.fill("#file-name", "auto-refresh-flow")
        page.wait_for_selector("#bottom-panels #elements-list li.elements-empty")

        # 模拟后台捕获落库：页面开着，外部（同源 API）新增一个元素
        _request_json(
            base, "POST", "/api/workflows/auto-refresh-flow/elements/polledEl",
            {"kind": "browser", "selector": {"css": "#poll"},
             "verifyCount": 1, "metadata": {"tag": "input"}},
        )
        # 不等手动刷新，轮询（2s）应自动带出
        page.wait_for_selector("#bottom-panels #elements-list li.element-item", timeout=8000)
        assert "polledEl" in page.locator("#bottom-panels").inner_text()
        browser.close()


def test_editor_run_params_dialog_when_inputs_declared(server):
    """切片 G：流程声明顶层 inputs 时，▶ 运行先弹参数对话框；取消则不启动。"""
    base = f"http://127.0.0.1:{server.port}"
    # 带顶层 inputs 的最小流程（写入后经编辑器打开）
    _request_json(
        base, "PUT", "/api/workflows/param-flow",
        {"schema_version": "1.0", "id": "param-flow", "name": "param flow",
         "inputs": {"name": "world", "count": 2},
         "root": {"type": "sequence", "id": "root", "children": []}},
    )
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{base}/")
        page.wait_for_selector('[data-command="browser.launch"]')
        # 打开 param-flow（open-select 触发 openWorkflow，填充 state.workflow.inputs）
        page.select_option("#open-select", "param-flow")
        page.wait_for_function(
            "() => document.getElementById('file-name').value === 'param-flow'"
        )

        page.click("#btn-run")
        page.wait_for_selector("#run-params-mask:not(.hidden)")
        # 两个声明键出现在对话框中
        fields_text = page.locator("#run-params-fields").inner_text()
        assert "name" in fields_text and "count" in fields_text
        # 取消 → 不启动运行（run-panel 保持隐藏）
        page.click("#run-params-cancel")
        page.wait_for_timeout(300)
        assert page.locator("#run-panel").is_hidden()
        browser.close()


def test_editor_selector_field_picks_element_from_library(server):
    """切片 D：属性面板 selector 字段可从元素库下拉选择（浏览器元素），自动填 css + kind 徽标。"""
    base = f"http://127.0.0.1:{server.port}"
    _request_json(
        base, "POST", "/api/workflows/pick-flow/elements/searchBox",
        {"kind": "browser", "selector": {"css": "#kw"},
         "verifyCount": 1, "metadata": {"tag": "input"}},
    )
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{base}/")
        page.wait_for_selector('[data-command="browser.click"]')
        page.fill("#file-name", "pick-flow")
        page.wait_for_selector("#bottom-panels #elements-list li.element-item")

        page.click('[data-command="browser.click"]')
        page.wait_for_selector('#canvas li[data-node="click"]')
        page.click('#canvas li[data-node="click"]')
        page.wait_for_selector('#props-body input[data-field="selector"]')

        # 下拉出现且含元素库条目
        page.select_option("#props-body .element-pick", "searchBox")
        # change handler 异步 GET 元素后填值 → 轮询等待生效
        page.wait_for_function(
            "() => document.querySelector('#props-body input[data-field=\"selector\"]')"
            ".value === '#kw'"
        )
        badge = page.locator("#props-body .element-pick-badge")
        assert "searchBox" in badge.inner_text()
        browser.close()
