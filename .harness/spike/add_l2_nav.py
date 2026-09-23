"""S4.4 一次性注入：导航/attach/标签页族 24 个 l2 块写进 cases/browser.json。

期望取自 probe_browser_l2_nav.py 的真机实测值（probe_nav2.txt，含三处产品修复
之后的基准：goBack→页内 history、timeoutMs 真转发、已有会话路径的 onTimeout）。

幂等保险同 add_l2_interact.py：目标变体已有 l2 即报错。
"""

from __future__ import annotations

import json
from pathlib import Path

PATH = Path(__file__).resolve().parents[2] / "tests" / "commands" / "cases" / "browser.json"

GOTO_OTHER = {  # pre 步：同会话导航到 other 页（back/forward 的历史栈第二页）
    "command": "browser.navigate",
    "inputs": {
        "sessionId": "s",  # 驱动替换成真会话
        "browserType": "msedge",
        "action": "goto",
        "url": "{page:other}",
    },
}

BLOCKS: dict[tuple[str, str], dict] = {
    # -- navigate（8）-----------------------------------------------------------
    ("browser.navigate", "goto-without-session-creates-a-tab-and-binds-a-session"): {
        "page": "basic",
        "inputs": {"url": "{page}"},
        "expect": {
            "outputs": {"url": "{page}", "resourceType": "webPage"},
            "outputKeys": ["sessionId", "tabId"],
            "effect": "session",
        },
    },
    ("browser.navigate", "goto-on-an-existing-session-navigates-that-tab"): {
        "page": "basic",
        "inputs": {"url": "{page:other}"},
        "expect": {
            "outputs": {"url": "{page:other}", "resourceType": "webPage"},
            "effect": "session",
        },
    },
    ("browser.navigate", "action-back-uses-history"): {
        "page": "basic",
        "pre": [GOTO_OTHER],
        "expect": {"outputs": {"url": "{page}"}, "effect": "session"},
    },
    ("browser.navigate", "action-forward-uses-history"): {
        "page": "basic",
        "pre": [
            GOTO_OTHER,
            {"command": "browser.navigate",
             "inputs": {"sessionId": "s", "action": "back"}},
        ],
        "expect": {"outputs": {"url": "{page:other}"}, "effect": "session"},
    },
    ("browser.navigate", "action-reload-uses-history"): {
        "page": "basic",
        "expect": {"outputs": {"url": "{page}"}, "effect": "session"},
    },
    # slow 路由睡 3s，timeoutMs=1000 必然超时；stop=停止加载并继续
    ("browser.navigate", "onTimeout-stop-stops-loading-and-succeeds"): {
        "page": "basic",
        "inputs": {"url": "{page:slow}", "timeoutMs": 1000},
        "expect": {
            "outputs": {"url": "{page:slow}", "resourceType": "webPage"},
            "effect": "session",
        },
    },
    ("browser.navigate", "onTimeout-error-fails-with-timeout-on-a-timed-out-load"): {
        "page": "basic",
        "inputs": {"url": "{page:slow}", "timeoutMs": 1000},
        "expect": {"errorCode": "TIMEOUT"},
    },
    ("browser.navigate", "goto-without-url-is-rejected-before-any-channel-call"): {
        "page": "basic",
        "expect": {"errorCode": "INVALID_INPUT"},
    },

    # -- attach（6，无会话命令；启动页 basic 标签页天然在场） --------------------
    ("browser.attach", "url-substring-match-binds-its-tab"): {
        "page": "basic",
        "inputs": {"pattern": "basic"},
        "expect": {
            "outputs": {"url": "{page}", "resourceType": "webPage"},
            "outputKeys": ["sessionId"],
            "effect": "session",
        },
    },
    ("browser.attach", "matchBy-title-searches-title-field"): {
        "page": "basic",
        "pre": [{  # 开一个 Other Page 标签页给标题匹配当靶子
            "command": "browser.navigate",
            "inputs": {"browserType": "msedge", "action": "goto", "url": "{page:other}"},
        }],
        "expect": {"outputs": {"url": "{page:other}"}, "effect": "session"},
    },
    ("browser.attach", "matchBy-url-does-not-consult-title"): {
        "page": "basic",
        "expect": {"errorCode": "ELEMENT_NOT_FOUND"},
    },
    ("browser.attach", "useRegex-true-matches-by-regex"): {
        "page": "basic",
        "inputs": {"pattern": r"^http://127\.0\.0\.1:\d+/basic\.html$"},
        "expect": {"outputs": {"url": "{page}"}, "effect": "session"},
    },
    ("browser.attach", "invalid-regex-is-a-miss-not-a-crash"): {
        "page": "basic",
        "expect": {"errorCode": "ELEMENT_NOT_FOUND"},
    },
    ("browser.attach", "no-match-reports-element-not-found"): {
        "page": "basic",
        "expect": {"errorCode": "ELEMENT_NOT_FOUND"},
    },

    # -- close（3）---------------------------------------------------------------
    ("browser.close", "detach-does-not-touch-the-extension"): {
        "page": "basic",
        "expect": {"effect": "session"},
        "verify": [{  # 解绑后再用该会话：必须显式失败（真机实测 EXECUTOR_FAILED）
            "command": "browser.getText",
            "inputs": {"sessionId": "s", "selector": "#t"},
            "expect": {"errorCode": "EXECUTOR_FAILED"},
        }],
    },
    ("browser.close", "close-without-any-session-fails-explicitly"): {
        "page": "basic",
        "session": False,
        "expect": {"errorCode": "EXECUTOR_FAILED"},
    },
    ("browser.close", "explicit-sessionId-detaches-that-session-without-extension-call"): {
        "page": "basic",
        "expect": {"effect": "session"},
    },

    # -- closeTabs（6；all=true 不收——会关掉整个测试窗口、端掉共享浏览器） --------
    ("browser.closeTabs", "explicit-tabIds-go-out-as-one-batch"): {
        "page": "basic",
        "inputs": {"tabIds": ["{tab}"]},
        "expect": {
            "outputs": {
                "closedCount": 1,
                "closedTabIds": ["{tab}"],
                "failedTabIds": [],
            },
            "effect": "unsafe-write",
        },
    },
    ("browser.closeTabs", "ignoreBeforeUnload-unspecified-is-not-sent"): {
        "page": "basic",
        "inputs": {"tabIds": ["{tab}"]},
        "expect": {"outputs": {"closedCount": 1}, "effect": "unsafe-write"},
    },
    ("browser.closeTabs", "partial-failure-is-recounted-not-flattened"): {
        "page": "basic",
        "inputs": {"tabIds": ["{tab}", 999999]},
        "expect": {
            "outputs": {
                "closedCount": 1,
                "closedTabIds": ["{tab}"],
                "failedTabIds": [999999],
            },
            "effect": "unsafe-write",
        },
    },
    ("browser.closeTabs", "tabIds-and-all-are-mutually-exclusive"): {
        "page": "basic",
        "expect": {"errorCode": "INVALID_INPUT"},
    },
    ("browser.closeTabs", "neither-tabIds-nor-all-is-rejected"): {
        "page": "basic",
        "expect": {"errorCode": "INVALID_INPUT"},
    },
    ("browser.closeTabs", "tabIds-must-be-an-array"): {
        "page": "basic",
        "expect": {"errorCode": "INVALID_INPUT"},
    },

    # -- listPages（1；no-open-tabs 不可真机构造——关完最后一个标签页浏览器即退出） ---
    ("browser.listPages", "pages-are-enumerated-with-stable-index"): {
        "page": "basic",
        "expect": {
            "outputListContains": {
                "pages": {"url": "{page}", "title": "RPA L2 靶页 basic"}
            },
            "effect": "read",
        },
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
