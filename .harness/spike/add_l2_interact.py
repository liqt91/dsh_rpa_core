"""S4.3 一次性注入：交互族 46 个 l2 块写进 cases/browser.json。

期望全部取自 `.harness/spike/probe_browser_l2_interact.py` 的真机实测值
（probe_interact2.txt，check 修复 + 1280x900 钉窗后的基准）。

browser.json 的 json.dumps(indent=2, ensure_ascii=False) 往返逐字节稳定
（已验证），所以脚本注入不会产生格式噪音。幂等保险：目标变体已有 l2 即报错。
"""

from __future__ import annotations

import json
from pathlib import Path

PATH = Path(__file__).resolve().parents[2] / "tests" / "commands" / "cases" / "browser.json"

# -- 读侧快捷构造 ------------------------------------------------------------

def v_status(value: str) -> dict:
    return {
        "command": "browser.getText",
        "inputs": {"selector": "#status", "infoType": "text"},
        "expect": {"outputs": {"value": value}, "effect": "read"},
    }


def v_mods(value: str) -> dict:
    return {
        "command": "browser.getText",
        "inputs": {"selector": "#mods", "infoType": "text"},
        "expect": {"outputs": {"value": value}, "effect": "read"},
    }


def v_kw(value: str) -> dict:
    return {
        "command": "browser.getText",
        "inputs": {"selector": "#kw", "infoType": "value"},
        "expect": {"outputs": {"value": value}, "effect": "read"},
    }


def v_sel(value: str) -> dict:
    return {
        "command": "browser.getText",
        "inputs": {"selector": "#sel", "infoType": "value"},
        "expect": {"outputs": {"value": value}, "effect": "read"},
    }


def v_chk(checked: bool) -> dict:
    return {
        "command": "browser.executeScript",
        "inputs": {"script": "return document.querySelector('#chk').checked"},
        "expect": {"outputs": {"result": checked}, "effect": "unsafe-write"},
    }


def v_gsp(scroll_x: int, scroll_y: int, selector: str | None = None) -> dict:
    step = {
        "command": "browser.getScrollPosition",
        "inputs": {},
        "expect": {
            "outputs": {"scrollX": scroll_x, "scrollY": scroll_y},
            "effect": "read",
        },
    }
    if selector:
        step["inputs"]["selector"] = selector
    return step


def absent() -> dict:
    return {"page": "basic", "inputs": {"selector": "#absent"},
            "expect": {"errorCode": "ELEMENT_NOT_FOUND"}}


def click_block(verify: list, extra_expect: dict | None = None) -> dict:
    expect = {"outputs": {"matchedCount": 1}, "effect": "unsafe-write"}
    if extra_expect:
        expect.update(extra_expect)
    return {
        "page": "basic",
        "inputs": {"selector": "#btn"},
        "expect": expect,
        "verify": verify,
    }


# (命令, 变体名) → l2 块
BLOCKS: dict[tuple[str, str], dict] = {
    # -- click（9）------------------------------------------------------------
    ("browser.click", "defaults-are-forwarded-explicitly"):
        click_block([v_status("clicked:1")]),
    ("browser.click", "clickType-double"):
        click_block([v_status("dblclicked")]),
    ("browser.click", "button-right"):
        click_block([v_status("ctx")]),
    ("browser.click", "button-middle"):
        click_block([v_status("middle-down")]),
    ("browser.click", "modifiers-are-forwarded-verbatim"):
        click_block([v_mods("CW")]),
    ("browser.click", "simulateHuman-false-is-honored"):
        click_block([v_status("clicked:1")]),
    ("browser.click", "clickPosition-random"):
        click_block([v_status("clicked:1")]),
    ("browser.click", "postDelayMs-delays-after-the-action"):
        click_block([v_status("clicked:1")], {"elapsedAtLeastMs": 50}),
    ("browser.click", "zero-match-reports-element-not-found"): absent(),

    # -- input（10）-----------------------------------------------------------
    ("browser.input", "defaults-mode-fill-and-all-flags-off"): {
        "page": "basic", "inputs": {"selector": "#kw"},
        "expect": {"outputs": {"matchedCount": 1}, "effect": "unsafe-write"},
        "verify": [v_kw("hi")],
    },
    ("browser.input", "mode-type"): {
        "page": "basic", "inputs": {"selector": "#kw"},
        "expect": {"outputs": {"matchedCount": 1}, "effect": "unsafe-write"},
        # type 模式实测**追加**在既有值后（fill/clipboard 才是替换）——真实语义差
        "verify": [v_kw("presethi")],
    },
    ("browser.input", "mode-clipboard"): {
        "page": "basic", "inputs": {"selector": "#kw"},
        "expect": {"outputs": {"matchedCount": 1}, "effect": "unsafe-write"},
        "verify": [v_kw("hi")],
    },
    ("browser.input", "keyIntervalMs-is-forwarded"): {
        "page": "basic", "inputs": {"selector": "#kw"},
        # 2 字符 × 200ms 间隔：实测 216.9ms，下界取 200
        "expect": {"outputs": {"matchedCount": 1}, "effect": "unsafe-write",
                   "elapsedAtLeastMs": 200},
        "verify": [v_kw("presethi")],
    },
    ("browser.input", "keyIntervalMs-zero-lower-boundary-is-accepted"): {
        "page": "basic", "inputs": {"selector": "#kw"},
        "expect": {"outputs": {"matchedCount": 1}, "effect": "unsafe-write"},
        "verify": [v_kw("presethi")],
    },
    ("browser.input", "append-true"): {
        "page": "basic", "inputs": {"selector": "#kw"},
        "expect": {"outputs": {"matchedCount": 1}, "effect": "unsafe-write"},
        "verify": [v_kw("presethi")],
    },
    ("browser.input", "pressEnter-true"): {
        "page": "basic", "inputs": {"selector": "#kw"},
        "expect": {"outputs": {"matchedCount": 1}, "effect": "unsafe-write"},
        "verify": [v_kw("hi")],
    },
    ("browser.input", "clickBeforeInput-true"): {
        "page": "basic", "inputs": {"selector": "#kw"},
        "expect": {"outputs": {"matchedCount": 1}, "effect": "unsafe-write"},
        "verify": [v_kw("hi")],
    },
    ("browser.input", "postDelayMs-delays-after-the-input"): {
        "page": "basic", "inputs": {"selector": "#kw"},
        "expect": {"outputs": {"matchedCount": 1}, "effect": "unsafe-write",
                   "elapsedAtLeastMs": 50},
        "verify": [v_kw("hi")],
    },
    ("browser.input", "zero-match-reports-element-not-found"): absent(),

    # -- select（4）-----------------------------------------------------------
    ("browser.select", "default-selectBy-value"): {
        "page": "basic", "inputs": {"selector": "#sel", "value": "beta"},
        "expect": {"outputs": {"matchedCount": 1}, "effect": "unsafe-write"},
        "verify": [v_sel("beta")],
    },
    ("browser.select", "selectBy-label"): {
        # value 也要覆盖：L1 的 "Label" 是桩世界的值，真页面上不存在（首跑实测红过）
        "page": "basic", "inputs": {"selector": "#sel", "value": "Gamma"},
        "expect": {"outputs": {"matchedCount": 1}, "effect": "unsafe-write"},
        "verify": [v_sel("gamma")],
    },
    ("browser.select", "selectBy-index"): {
        "page": "basic", "inputs": {"selector": "#sel"},
        "expect": {"outputs": {"matchedCount": 1}, "effect": "unsafe-write"},
        "verify": [v_sel("gamma")],
    },
    ("browser.select", "zero-match-reports-element-not-found"): absent(),

    # -- check（4）：S4.3 修复前三个操作全部反转（合成 click 的激活行为） -------
    ("browser.check", "default-operation-is-check"): {
        "page": "basic", "inputs": {"selector": "#chk"},
        "expect": {"outputs": {"checked": True}, "effect": "unsafe-write"},
        "verify": [v_chk(True)],
    },
    ("browser.check", "operation-uncheck"): {
        "page": "basic", "inputs": {"selector": "#chk"},
        "expect": {"outputs": {"checked": False}, "effect": "unsafe-write"},
        "verify": [v_chk(False)],
    },
    ("browser.check", "operation-toggle"): {
        "page": "basic", "inputs": {"selector": "#chk"},
        "expect": {"outputs": {"checked": True}, "effect": "unsafe-write"},
        "verify": [v_chk(True)],
    },
    ("browser.check", "zero-match-reports-element-not-found"): absent(),

    # -- scroll（6）：scrollY 精确值依赖 1280x900 钉窗（l2_harness） ------------
    ("browser.scroll", "window-scroll-when-no-selector-is-given"): {
        "page": "basic",
        "expect": {"outputs": {"scrollY": 0}, "effect": "unsafe-write"},
        "verify": [v_gsp(0, 0)],
    },
    ("browser.scroll", "position-bottom"): {
        "page": "basic",
        "expect": {"outputs": {"scrollY": 2595}, "effect": "unsafe-write"},
        "verify": [v_gsp(0, 2595)],
    },
    ("browser.scroll", "position-page"): {
        "page": "basic",
        "expect": {"outputs": {"scrollY": 808}, "effect": "unsafe-write"},
        "verify": [v_gsp(0, 808)],
    },
    ("browser.scroll", "position-point-forwards-coordinates"): {
        "page": "basic",
        "expect": {"outputs": {"scrollY": 20}, "effect": "unsafe-write"},
        "verify": [v_gsp(0, 20)],
    },
    ("browser.scroll", "selector-switches-to-element-scroll"): {
        "page": "basic", "inputs": {"position": "bottom"},
        "expect": {"outputs": {"scrollY": 700}, "effect": "unsafe-write"},
        "verify": [v_gsp(0, 700, "#box")],
    },
    # smooth 不收：平滑滚动是动画，命令返回时位置未到位（实测 0.0），时序竞态
    # 无法同步断言；scroll-offset-is-returned-as-float 是桩形状用例，L1-only。
    ("browser.scroll", "zero-match-does-not-fail-for-scroll"): {
        "page": "basic",
        "expect": {"outputs": {"scrollY": 0}, "effect": "unsafe-write"},
    },

    # -- getScrollPosition（3）-------------------------------------------------
    ("browser.getScrollPosition", "without-selector-reads-the-window-scroll"): {
        "page": "basic",
        "expect": {"outputs": {"scrollX": 0, "scrollY": 0}, "effect": "read"},
    },
    ("browser.getScrollPosition", "with-selector-reads-the-element-scroll"): {
        "page": "basic",
        "expect": {"outputs": {"scrollX": 0, "scrollY": 0}, "effect": "read"},
    },
    ("browser.getScrollPosition", "zero-match-reports-element-not-found"): absent(),

    # -- setValue（4）-----------------------------------------------------------
    ("browser.setValue", "default-setWay-value"): {
        "page": "basic", "inputs": {"selector": "#kw", "value": "v2"},
        "expect": {"outputs": {"matchedCount": 1}, "effect": "unsafe-write"},
        "verify": [v_kw("v2")],
    },
    ("browser.setValue", "setWay-innerText"): {
        "page": "basic", "inputs": {"value": "v2"},
        "expect": {"outputs": {"matchedCount": 1}, "effect": "unsafe-write"},
        "verify": [{
            "command": "browser.getText",
            "inputs": {"selector": "#t", "infoType": "text"},
            "expect": {"outputs": {"value": "v2"}, "effect": "read"},
        }],
    },
    ("browser.setValue", "setWay-innerHTML"): {
        "page": "basic", "inputs": {"value": "<b>b</b>"},
        "expect": {"outputs": {"matchedCount": 1}, "effect": "unsafe-write"},
        "verify": [{
            "command": "browser.getText",
            "inputs": {"selector": "#t", "infoType": "html"},
            "expect": {"outputs": {"value": "<b>b</b>"}, "effect": "read"},
        }],
    },
    ("browser.setValue", "zero-match-reports-element-not-found"): absent(),

    # -- setAttribute（2）--------------------------------------------------------
    ("browser.setAttribute", "name-and-value-are-forwarded"): {
        "page": "basic",
        "expect": {"outputs": {"matchedCount": 1}, "effect": "unsafe-write"},
        "verify": [{
            "command": "browser.executeScript",
            "inputs": {"script": "return document.querySelector('#t').getAttribute('data-x')"},
            "expect": {"outputs": {"result": "y"}, "effect": "unsafe-write"},
        }],
    },
    ("browser.setAttribute", "zero-match-reports-element-not-found"): absent(),

    # -- hover（2）--------------------------------------------------------------
    ("browser.hover", "selector-is-forwarded-to-the-hover-primitive"): {
        "page": "basic", "inputs": {"selector": "#hv"},
        "expect": {"outputs": {"matchedCount": 1}, "effect": "unsafe-write"},
        "verify": [v_status("hovered")],
    },
    ("browser.hover", "zero-match-reports-element-not-found"): absent(),

    # -- drag（2）---------------------------------------------------------------
    ("browser.drag", "source-and-target-selectors-are-forwarded"): {
        "page": "basic", "inputs": {"selector": "#src", "targetSelector": "#drop"},
        "expect": {"outputs": {"matchedCount": 1}, "effect": "unsafe-write"},
        "verify": [v_status("dropped")],
    },
    ("browser.drag", "zero-match-reports-element-not-found"): {
        "page": "basic", "inputs": {"selector": "#absent", "targetSelector": "#drop"},
        "expect": {"errorCode": "ELEMENT_NOT_FOUND"},
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
