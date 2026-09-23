"""S4.5+S4.6 一次性注入：cookies/screenshot/waitFor/waitLoad/stopLoading 的 l2 块。

期望取自 probe_browser_l2_misc.py 的真机实测值。
cookie 名按变体独立（共享 profile，防变体间互相污染读数）。
幂等保险同前：已有 l2 即报错。
"""

from __future__ import annotations

import json
from pathlib import Path

PATH = Path(__file__).resolve().parents[2] / "tests" / "commands" / "cases" / "browser.json"


def set_cookie(name: str, value: str) -> dict:
    """pre 步：往当前会话的页面域写 cookie。"""
    return {
        "command": "browser.cookieSet",
        "inputs": {
            "sessionId": "s",
            "cookies": [{"name": name, "value": value, "url": "{page}"}],
        },
    }


def get_cookie(name: str, value: str) -> dict:
    """verify 步：独立读回 cookie 值。"""
    return {
        "command": "browser.cookieGet",
        "inputs": {"sessionId": "s", "name": name},
        "expect": {"outputs": {"value": value}, "effect": "read"},
    }


BLOCKS: dict[tuple[str, str], dict] = {
    # -- cookieSet（3）----------------------------------------------------------
    ("browser.cookieSet", "cookies-array-is-forwarded-with-tab-scope"): {
        "page": "basic",
        "inputs": {"cookies": [{"name": "l2cs", "value": "v", "url": "{page}"}]},
        "expect": {"outputs": {"count": 1}, "effect": "idempotent-write"},
        "verify": [get_cookie("l2cs", "v")],
    },
    ("browser.cookieSet", "count-from-the-extension-is-returned-verbatim"): {
        "page": "basic",
        "inputs": {"cookies": [{"name": "l2cs2", "value": "1", "url": "{page}"}]},
        "expect": {"outputs": {"count": 1}, "effect": "idempotent-write"},
        "verify": [get_cookie("l2cs2", "1")],
    },
    ("browser.cookieSet", "empty-array-is-still-sent-not-silently-dropped"): {
        "page": "basic",
        "expect": {"outputs": {"count": 0}, "effect": "idempotent-write"},
    },

    # -- cookieGet（2）----------------------------------------------------------
    ("browser.cookieGet", "name-and-tab-scope-are-forwarded"): {
        "page": "basic",
        "inputs": {"name": "l2cg"},
        "pre": [set_cookie("l2cg", "got")],
        "expect": {"outputs": {"value": "got"}, "effect": "read"},
    },
    ("browser.cookieGet", "existing-value-is-returned-verbatim"): {
        "page": "basic",
        "inputs": {"name": "l2cg2"},
        "pre": [set_cookie("l2cg2", "verbatim")],
        "expect": {"outputs": {"value": "verbatim"}, "effect": "read"},
    },

    # -- cookieGetAll（4）--------------------------------------------------------
    ("browser.cookieGetAll", "no-filters-sends-only-the-tab-scope"): {
        "page": "basic",
        "pre": [set_cookie("l2ca", "ca")],
        "expect": {
            "outputListContains": {"cookies": {"name": "l2ca", "value": "ca"}},
            "effect": "read",
        },
    },
    ("browser.cookieGetAll", "all-three-filters-are-forwarded"): {
        "page": "basic",
        "inputs": {"name": "l2ca3", "domain": "127.0.0.1", "path": "/"},
        "pre": [set_cookie("l2ca3", "triple")],
        "expect": {
            "outputs": {"count": 1},
            "outputListContains": {"cookies": {"name": "l2ca3", "value": "triple"}},
            "effect": "read",
        },
    },
    ("browser.cookieGetAll", "each-filter-is-optional-independently"): {
        "page": "basic",
        "inputs": {"name": "l2ca4"},
        "pre": [set_cookie("l2ca4", "solo")],
        "expect": {"outputs": {"count": 1}, "effect": "read"},
    },
    ("browser.cookieGetAll", "returned-cookies-are-counted"): {
        "page": "basic",
        "expect": {"outputKeys": ["cookies", "count"], "effect": "read"},
    },

    # -- cookieRemove（2）---------------------------------------------------------
    ("browser.cookieRemove", "name-and-tab-scope-are-forwarded"): {
        "page": "basic",
        "inputs": {"name": "l2cr"},
        "pre": [set_cookie("l2cr", "gone")],
        "expect": {"outputKeys": ["sessionId"], "effect": "unsafe-write"},
        # 真机实测：删除后 cookieGet 返回空串（不是 null）
        "verify": [get_cookie("l2cr", "")],
    },
    ("browser.cookieRemove", "omitted-name-degrades-to-empty-string"): {
        "page": "basic",
        "expect": {"outputKeys": ["sessionId"], "effect": "unsafe-write"},
    },

    # -- screenshot（1）：真 PNG 落盘（文件存在即可，内容是浏览器位图） -------------
    ("browser.screenshot", "data-url-is-decoded-and-written-to-savePath"): {
        "page": "basic",
        "expect": {
            "outputPaths": {"filePath": "{tmp}/rpa-m38-shot.png"},
            "files": [{"path": "{tmp}/rpa-m38-shot.png"}],
            "effect": "idempotent-write",
        },
    },

    # -- waitFor（5）--------------------------------------------------------------
    ("browser.waitFor", "default-state-visible-counts-visible-matches"): {
        "page": "basic",
        "expect": {"outputs": {"matchedCount": 1}, "effect": "read"},
    },
    ("browser.waitFor", "state-hidden-succeeds-when-no-visible-match-remains"): {
        "page": "basic",
        # #hidden 是 display:none：attached 但不可见 → hidden 态满足、计数为 0
        "inputs": {"selector": "#hidden"},
        "expect": {"outputs": {"matchedCount": 0}, "effect": "read"},
    },
    ("browser.waitFor", "state-attached-ignores-visibility"): {
        "page": "basic",
        "inputs": {"selector": "#hidden"},
        "expect": {"outputs": {"matchedCount": 1}, "effect": "read"},
    },
    ("browser.waitFor", "state-detached-succeeds-when-element-is-gone"): {
        "page": "basic",
        "inputs": {"selector": "#absent"},
        "expect": {"outputs": {"matchedCount": 0}, "effect": "read"},
    },
    ("browser.waitFor", "unsatisfied-state-times-out"): {
        "page": "basic",
        "inputs": {"selector": "#absent", "timeoutMs": 600},
        "expect": {"errorCode": "TIMEOUT", "elapsedAtLeastMs": 600},
    },

    # -- waitLoad（2）--------------------------------------------------------------
    ("browser.waitLoad", "default-timeoutMs-is-forwarded-to-the-extension"): {
        "page": "basic",
        "expect": {"outputs": {"url": "{page}"}, "effect": "read"},
    },
    ("browser.waitLoad", "explicit-timeoutMs-is-forwarded-verbatim"): {
        "page": "basic",
        "expect": {"outputs": {"url": "{page}"}, "effect": "read"},
    },

    # -- stopLoading（1）：pre 触发向 slow 的导航（executeScript 不等加载）后立刻停 --
    ("browser.stopLoading", "stops-loading-and-returns-current-url"): {
        "page": "basic",
        "pre": [{
            "command": "browser.executeScript",
            "inputs": {
                "sessionId": "s",
                "script": "location.href = \"{page:slow}\"; return 1",
            },
        }],
        # 导航未提交前停掉 → 标签页仍停在 basic（实测）
        "expect": {"outputs": {"url": "{page}"}, "effect": "unsafe-write"},
    },
}


def main() -> None:
    data = json.loads(PATH.read_text(encoding="utf-8"))
    for (command, name), block in BLOCKS.items():
        spec = data.get(command)
        assert spec, f"命令不存在: {command}"
        hits = [v for v in spec["variants"] if v["name"] == name]
        assert len(hits) == 1, f"{command}::{name} 命中 {len(hits)} 条"
        assert "l2" not in hits[0], f"{command}::{name} 已有 l2 块"
        hits[0]["l2"] = block
    PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"injected {len(BLOCKS)} l2 blocks")


if __name__ == "__main__":
    main()
