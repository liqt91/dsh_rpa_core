"""画布布局探针：用真实后端数据 + 假 pywebview 桥，跑真实 main.js 渲染。

为什么不在浏览器里手搓 DOM：手搓只能验证「我以为的样子」，验证不了 renderCards()。
这里把 `window.pywebview.api.call` 换成返回真实 RPC 数据的假桥，main.js 一行不改地跑起来，
所以看到的就是 `rpa-core gui` 里 PPX 窗口会长成的样子（只差 Qt/PyWebView 的窗口边框）。

用法：
    python _layout_probe.py build      # 生成 gui/_layout/mock.json
    python _layout_probe.py shot 1280 777  # 截图（依赖 vite dev server 已起）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
APP = HERE / "app"
GUI = APP / "gui"
OUT = GUI / "_layout"
sys.path.insert(0, str(APP))
# 直接按文件路径加载：`app/api` 是个同名目录，普通 `import api` 会拿到命名空间包而不是 api.py
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location("ppx_api", APP / "api" / "api.py")
api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(api)  # type: ignore[union-attr]


def _deep_cards() -> list[dict[str, Any]]:
    """人造深层嵌套：真实工作流最深的只有 depth=1，压不出层级收缩的问题。"""
    ast = {
        "type": "sequence",
        "id": "root",
        "children": [
            {
                "type": "action",
                "id": "a_open",
                "command": "browser.launch",
                "with": {"session_id": "s1", "url": "https://news.baidu.com/", "headless": False},
            },
            {
                "type": "forEach",
                "id": "fe_outer",
                "items": "${inputs.keywords}",
                "item_var": "kw",
                "children": [
                    {
                        "type": "try",
                        "id": "t_inner",
                        "error_var": "err",
                        "children": [
                            {
                                "type": "if",
                                "id": "if_deep",
                                "condition": {
                                    "op": "contains",
                                    "left": "${loop.kw}",
                                    "right": "新闻",
                                },
                                "then": [
                                    {
                                        "type": "action",
                                        "id": "a_click",
                                        "command": "browser.click",
                                        "with": {
                                            "target": {"$element": "search_box"},
                                            "timeout_ms": 5000,
                                        },
                                    },
                                    {
                                        "type": "action",
                                        "id": "a_type",
                                        "command": "browser.input",
                                        "with": {"target": {"$element": "search_box"}, "text": "${loop.kw}"},
                                    },
                                ],
                                "else": [
                                    {
                                        "type": "action",
                                        "id": "a_skip",
                                        "command": "browser.wait",
                                        "with": {"ms": 800},
                                    }
                                ],
                            }
                        ],
                        "catch": [
                            {
                                "type": "action",
                                "id": "a_log",
                                "command": "browser.screenshot",
                                "with": {"path": "runs/${loop.kw}.png"},
                            }
                        ],
                    }
                ],
            },
            {
                "type": "action",
                "id": "a_save",
                "command": "browser.extract_table",
                "with": {"target": {"$element": "news_list"}, "output": "rows"},
            },
            {"type": "return", "id": "a_ret", "value": "${rows}"},
        ],
    }
    flat = api._Flattener(api._display_names())
    flat.walk(ast, 0)
    return flat.cards


def build() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    boot = api.bootstrap()
    struct_path = ".harness/demo/ppx_probe/_sandbox/struct.json"
    struct = api.open_flow(struct_path)
    deep_path = "workflows/xiaohongshu/workflow.json"
    deep = api.open_flow(deep_path)
    deep = dict(deep, path="sandbox/deep-nest.json", name="深层嵌套压测（人造）", cards=_deep_cards())

    # 右栏要点亮：选一个真有字段的节点。键必须是「前端认的路径」，RPC 走真实文件路径。
    detail: dict[str, Any] = {}
    for card in deep["cards"]:
        node_id = card.get("id")
        if not node_id or card.get("kind") == "group":
            continue
        got = api.node_detail(deep_path, node_id)
        detail[f"{deep['path']}::{node_id}"] = got

    elements = api.list_elements(struct_path)
    missing = api.missing_elements(struct_path)

    payload = {
        "bootstrap": boot,
        "flows": {struct_path: struct, deep["path"]: deep},
        "details": detail,
        "elements": elements,
        "missing": missing,
    }
    (OUT / "mock.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT / 'mock.json'}")
    print(f"  struct cards={len(struct['cards'])} deep cards={len(deep['cards'])}")
    print(f"  deep maxdepth={max(c.get('depth', 0) for c in deep['cards'])}")
    print(f"  details keys={list(detail)[:4]}")


if __name__ == "__main__":
    build()
