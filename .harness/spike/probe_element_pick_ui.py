"""M66 元素拾取判据的负向验证（注入 → 确认真红 → 逐字节还原）。

纪律（照 .harness/tasks/M63 与既有 spike 的口径）：
- 期望红集按**推理**写，不照抄实际；断言「实际红 == 期望红」，不是 >=。
- 注入必须是**合法语法的精准放宽**，注入后先过语法自检，避免「整片红」冒充命中。
- 锚点整行取、不带尾随换行，且 `count == 1`（短锚点常是更长缩进行的子串）。
- 哨兵 + try/finally + md5 逐字节还原；跑完不许留备份/哨兵。

用法：
  ./.venv/Scripts/python.exe .harness/spike/probe_element_pick_ui.py
  ./.venv/Scripts/python.exe .harness/spike/probe_element_pick_ui.py --inject <name>
"""

from __future__ import annotations

import ast
import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMP = Path("C:/Users/Administrator/AppData/Local/Temp/rpa-probe-pick")
SENTINEL = TEMP / ".probe-injected"
MAIN = ROOT / ".harness/demo/ppx_probe/app/gui/src/main.js"
CSS = ROOT / ".harness/demo/ppx_probe/app/gui/src/style.css"
API = ROOT / ".harness/demo/ppx_probe/app/api/api.py"
FILES = (MAIN, CSS, API)

UI_CHECK = ROOT / "scripts/check_element_pick_ui.mjs"
CONTRACT_CHECK = ROOT / ".harness/scripts/check_element_pick_contract.py"
CHECKS = (
    ("UI", ["node", str(UI_CHECK)]),
    ("CONTRACT", [sys.executable, str(CONTRACT_CHECK)]),
)
CHECK_ROW = re.compile(r"^(PASS|FAIL) (.+?)(?: — .*)?$")

UI_TWO = "渲染层：两个缺口都渲染出来、各恰好一个主按钮"
UI_WORDS = "文案表：所有用户可见值都不含实现名词"
UI_CSS = "样式层：主按钮命中区 ≥24×24、字号 ≥14px"
UI_ARIA = "渲染层：能力不在线时给状态条 + aria-live + 图形，不假装已就绪"
UI_BACKEND = "契约层：后端决策唯一、探测在 spawn 之前、复合键"
CON_CMD = "命令类型决定唯一 target"
CON_AUTO = "覆盖只本次生效、auto 不越权"
CON_OFFLINE = "能力离线：不 spawn、返回 blocked"
CON_OVERRIDE = "覆盖为网页腿也不能绕过能力闸"

# file / anchor / replacement / 按推理写出的期望红集 / 必须保持绿的关键项
INJECTIONS = {
    "two-main-buttons": {
        "file": MAIN,
        "anchor": "  row.append(main)",
        "replacement": "  row.append(main)\n  row.append(mk('button', 'pickMain', pickText('main')))",
        "must_red": {UI_TWO},
        "unchanged": {UI_WORDS, UI_CSS, UI_ARIA, UI_BACKEND, CON_CMD, CON_OFFLINE},
    },
    "banned-word": {
        "file": MAIN,
        "anchor": "  main: '补一下',",
        "replacement": "  main: '补一下（browser）',",
        "must_red": {UI_WORDS, UI_TWO},
        "unchanged": {UI_CSS, UI_ARIA, UI_BACKEND, CON_CMD, CON_OFFLINE},
    },
    "small-target": {
        "file": CSS,
        "anchor": "  min-height: 32px; min-width: 32px; height: 32px; padding: 0 12px; font-size: 14px;",
        "replacement": "  min-height: 22px; min-width: 32px; height: 32px; padding: 0 12px; font-size: 14px;",
        "must_red": {UI_CSS},
        "unchanged": {UI_WORDS, UI_TWO, UI_ARIA, UI_BACKEND, CON_CMD, CON_OFFLINE},
    },
    "no-aria-live": {
        "file": MAIN,
        "anchor": "  live.setAttribute('aria-live', 'polite')",
        "replacement": "  live.setAttribute('aria-hidden', 'true')",
        "must_red": {UI_ARIA},
        "unchanged": {UI_WORDS, UI_TWO, UI_CSS, UI_BACKEND, CON_CMD, CON_OFFLINE},
    },
    "single-capture-key": {
        "file": API,
        "anchor": "_CAPTURES: dict[tuple[str, str], dict[str, Any]] = {}",
        "replacement": "_CAPTURES: dict[str, dict[str, Any]] = {}",
        "must_red": {UI_BACKEND},
        "unchanged": {UI_WORDS, UI_TWO, UI_CSS, UI_ARIA, CON_CMD, CON_OFFLINE},
    },
    "ignore-command-type": {
        "file": API,
        "anchor": '    if cmd.startswith("browser."):',
        "replacement": '    if False and cmd.startswith("browser."):',
        # 推理：browser 命令会掉进 capability 分支 → auto 的 decidedBy 变 capability；
        # 离线时 browser.input 反被判成 desktop，能力闸失效 → spawn 前拦不住。
        "must_red": {CON_CMD, CON_AUTO, CON_OFFLINE},
        "unchanged": {UI_WORDS, UI_TWO, UI_CSS, UI_ARIA, UI_BACKEND, CON_OVERRIDE},
    },
    "offline-reports-ready": {
        "file": API,
        "anchor": '    return {\n        "item": item,',
        "replacement": '    blocked = False\n    return {\n        "item": item,',
        # 推理：能力闸被抹平 → 离线仍允许起会话；两条离线 case 都必须红。
        "must_red": {CON_OFFLINE, CON_OVERRIDE},
        "unchanged": {UI_WORDS, UI_TWO, UI_CSS, UI_ARIA, UI_BACKEND, CON_CMD, CON_AUTO},
    },
}


def md5s() -> dict[str, str]:
    return {str(p.relative_to(ROOT)): hashlib.md5(p.read_bytes()).hexdigest() for p in FILES}


def run_checks() -> tuple[set[str], set[str], list[str]]:
    red, green, notes = set(), set(), []
    for label, cmd in CHECKS:
        done = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, errors="replace")
        for line in (done.stdout + done.stderr).splitlines():
            match = CHECK_ROW.match(line.strip())
            if not match:
                continue
            (red if match.group(1) == "FAIL" else green).add(match.group(2))
        notes.append(f"{label} rc={done.returncode}")
    return red, green, notes


def syntax_ok() -> tuple[bool, str]:
    try:
        ast.parse(API.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        return False, f"api.py 注入后语法错：{exc}"
    done = subprocess.run(["node", "--check", str(MAIN)], capture_output=True, text=True)
    if done.returncode:
        return False, f"main.js 注入后语法错：{done.stderr[:200]}"
    return True, ""


def inject(name: str) -> int:
    spec = INJECTIONS[name]
    path: Path = spec["file"]
    original = path.read_bytes()
    text = original.decode("utf-8")
    anchor = spec["anchor"]
    count = text.count(anchor)
    if count != 1:
        print(f"FAIL 锚点不唯一：{name} count={count}")
        return 1
    before = md5s()
    TEMP.mkdir(parents=True, exist_ok=True)
    SENTINEL.write_text(name, encoding="utf-8")
    try:
        path.write_bytes(text.replace(anchor, spec["replacement"], 1).encode("utf-8"))
        ok, why = syntax_ok()
        if not ok:
            print(f"FAIL {why}")
            return 1
        red, green, notes = run_checks()
        expected = spec["must_red"]
        must_green = spec["unchanged"]
        bad = sorted(expected ^ red)
        hurt = sorted(must_green & red)
        print(f"--- 注入 {name} ---")
        print("    " + " | ".join(notes))
        print(f"    实际红 {len(red)} 条：{', '.join(sorted(red)) or '（无）'}")
        if bad or hurt:
            if bad:
                print(f"FAIL 期望红 != 实际红，差集：{bad}")
            if hurt:
                print(f"FAIL 误伤（本应仍绿却红）：{hurt}")
            return 1
        print(f"GREEN (negative verified)：{len(expected)} 条精准命中，"
              f"{len(must_green)} 条关键项保持绿")
        return 0
    finally:
        path.write_bytes(original)
        SENTINEL.unlink(missing_ok=True)
        after = md5s()
        if after != before:
            print(f"FAIL 还原不干净：{before} != {after}")
        else:
            print("    逐字节还原 md5 一致")


def main() -> int:
    if SENTINEL.exists():
        print(f"FAIL 发现上次注入留下的哨兵：{SENTINEL}（源码可能没还原，先人工检查）")
        return 1
    names = sys.argv[2:] if len(sys.argv) > 2 and sys.argv[1] == "--inject" else []
    if names:
        return 0 if all(inject(n) == 0 for n in names) else 1
    red, green, notes = run_checks()
    print("--- 基线 ---")
    print("    " + " | ".join(notes))
    if red:
        print(f"RESULT: RED 基线不绿，先修基线：{', '.join(sorted(red))}")
        return 1
    print(f"RESULT: GREEN 基线 {len(green)}/{len(green)} 通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
