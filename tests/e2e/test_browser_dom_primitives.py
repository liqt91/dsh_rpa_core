import asyncio
import functools
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from rpa_core.executors import PlaywrightExecutor
from rpa_core.model.command import CommandInvocation

ROOT = Path(__file__).resolve().parents[2]


def _server():
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(ROOT / "testsite"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]


async def _open(executor, port):
    inv = CommandInvocation(
        command_id="browser.navigate", command_version="2.5.0",
        run_id="run", step_id="open",
        # 显式 playwright：本测试验证 playwright 通道的纯 DOM 原语，
        # 不随扩展在线状态路由到扩展通道
        inputs={"url": f"http://127.0.0.1:{port}/", "headless": True,
                "transport": "playwright"},
    )
    result = await executor.execute(inv, asyncio.Event())
    assert result.status == "success", result.error
    return result.outputs["sessionId"]


def test_pure_dom_primitives(tmp_path):
    server, port = _server()

    async def run():
        executor = PlaywrightExecutor()
        try:
            session_id = await _open(executor, port)
            sid = {"sessionId": session_id}

            async def invoke(command, inputs):
                return await executor.execute(
                    CommandInvocation(
                        command_id=command, command_version="1.0.0",
                        run_id="run", step_id=command,
                        inputs={**inputs, **sid},
                    ),
                    asyncio.Event(),
                )

            # 1) 设置元素值（value —— 直改属性，绕过事件）
            r = await invoke(
                "browser.setValue",
                {"selector": "#query", "value": "typed-by-setValue"},
            )
            assert r.status == "success" and r.outputs["matchedCount"] == 1
            r = await invoke("browser.getText", {"selector": "#query", "infoType": "value"})
            assert r.status == "success" and r.outputs["value"] == "typed-by-setValue"

            # 2) 设置元素属性
            r = await invoke(
                "browser.setAttribute",
                {"selector": "#query", "name": "data-mark", "value": "hot"},
            )
            assert r.status == "success" and r.outputs["matchedCount"] == 1
            r = await invoke("browser.getText", {"selector": "#query", "infoType": "outerHTML"})
            assert r.status == "success" and 'data-mark="hot"' in r.outputs["value"]

            # 3) 获取元素位置
            r = await invoke("browser.getPosition", {"selector": "#query"})
            assert r.status == "success"
            box = r.outputs
            assert all(k in box for k in ("x", "y", "width", "height"))

            # 4) 获取下拉框选项
            r = await invoke("browser.getSelectOptions", {"selector": "#city"})
            assert r.status == "success"
            assert r.outputs["count"] == 3
            assert r.outputs["options"][0] == {
                "index": 0, "value": "beijing", "label": "Beijing", "selected": False,
            }
            assert any(o["selected"] for o in r.outputs["options"])

            # 5) 滚动条位置（缺省=整页，返回 0 起点）
            r = await invoke("browser.getScrollPosition", {})
            assert r.status == "success"
            assert r.outputs["scrollX"] == 0 and r.outputs["scrollY"] == 0

            # 6) 停止网页加载（不影响已加载页面，仅验证成功返回）
            r = await invoke("browser.stopLoading", {})
            assert r.status == "success"
            assert r.outputs["url"].startswith("http://127.0.0.1")

            # 缺省会话：省略 sessionId 作用于最近激活会话
            r2 = await executor.execute(
                CommandInvocation(
                    command_id="browser.setValue", command_version="1.0.0",
                    run_id="run", step_id="setvalue",
                    inputs={"selector": "#query", "value": "default-session"},
                ),
                asyncio.Event(),
            )
            assert r2.status == "success"

            # 元素缺失 → ELEMENT_NOT_FOUND
            r = await invoke("browser.getPosition", {"selector": "#nope"})
            assert r.status == "error" and r.error.code == "ELEMENT_NOT_FOUND"
        finally:
            await executor.close()

    try:
        asyncio.run(run())
    finally:
        server.shutdown()
        server.server_close()