"""文档门禁：**现役导航文档**里的仓库内相对路径引用必须真实存在。

为什么只扫「现役文档」——**本门禁的设计要点，别扩面**：

- `PROGRESS.md` / `adr/**` / 已完成任务单里的引用是**历史记录**：当时文件确实存在，
  后来被删或改名（playwright 时代产物、bsk 路线、`command-inventory.md` 等）。
  改它们等于**篡改历史**，与本仓「PROGRESS 历史条目永不改写」直接冲突。
- `docs/` 下部分文档自带「保留为历史提案记录」banner（如 `extension-execution-plan.md`
  声明「现行行为以 ADR 0013 为准，下文中凡把 playwright 作为可用通道讲述的段落均属历史描述」），
  其正文里的旧路径同样豁免。

实测依据（2026-10-14，M62）：全量扫 `.harness/**` + `docs/` + 根 `*.md` 的 672 个引用，
得 12 个「失效」；**逐条定性后只有 1 个**是现役文档的真错误
（`.harness/README.md` 把 `set_feature.py` 写成了仓库根路径）。把历史台账一并扫进来
只会让门禁**长期恒红** ⇒ 退化成没人看的假判据。**假判据比没判据更坏。**
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# 只覆盖「AI 会照着执行」的导航文档：AGENTS.md 是开机自动注入的，README 是开工必读，
# STATE.md 是开机入口（M62）。
SCANNED = (
    ROOT / "AGENTS.md",
    ROOT / ".harness" / "STATE.md",
    ROOT / ".harness" / "README.md",
    ROOT / ".harness" / "architecture.md",
    ROOT / ".harness" / "invariants.md",
)

# 仓库根为基准的前缀。`.venv/`、`.workbuddy/` 故意不收：前者不进版本控制（新人没建 venv
# 会误红），后者路径里带 `YYYY-MM-DD.md` 这类示意写法（恒假）。
_PREFIXES = (
    ".harness/",
    "docs/",
    "tests/",
    "src/",
    "scripts/",
    "commands/",
    "examples/",
    "extension/",
    "tools/",
)
_EXTS = "md|py|json|mjs|js|txt|html|toml|cfg|ini|yaml|yml|sh|pem|exe|png"

# 前置断言 `(?<![\w/.\-])`：避免把 `https://host/docs/a.md` 或 `xdocs/a.md` 里的片段当引用。
# 尾部断言 `(?![A-Za-z0-9_])`：**必需**——否则短扩展名会抢在长扩展名前命中
# （`feature_list.json` 被截成 `feature_list.js`，产出假红）。扩展名顺序也按「长在前」排。
_PREFIX_ALT = "|".join(re.escape(prefix) for prefix in _PREFIXES)
REF = re.compile(
    rf"(?<![\w/.\-])((?:{_PREFIX_ALT})[\w./\-]*\.(?:{_EXTS}))(?![A-Za-z0-9_])"
)

# 显式豁免（路径 → 理由）。**加条目之前先问：为什么这句引用要指向一个不存在的文件？**
# 若答案是「文件还没创建」，那它就该写成计划项而不是路径引用。
EXEMPT: dict[str, str] = {}

# 防空转：SCANNED 里本应匹配到的引用数下限。正则被改坏、或文档被误删时，本门禁必须
# **拒绝报绿**，而不是「零覆盖地通过」——本仓哲学：假判据比没判据更坏。
MIN_REFS = 3


def check_state_consistency(errors: list[str]) -> None:
    """`.harness/STATE.md` 是开机入口 —— 它声明的排期必须与 `project_state.json` 一致。

    没有这条，STATE.md 会变成**第二个会漂移的状态源**（那比不建它还坏）：读者以为它是权威，
    实际早已过期。

    ★ 判据必须**锚定到表格单元格**、不能用 `value in text`：STATE.md 里
    `active_plan` 的值（`M52-selector-advisor.md`）**恰好包含** `active_feature` 的名字，
    用子串判断时两者互相兜底 —— 改掉其中一行仍能命中另一行，判据恒绿（本判据首版就是这个
    漏洞，被 `probe_m62_docs_gate_negative.py` 的 N4 注入当场抓出）。
    """
    state_path = ROOT / ".harness" / "STATE.md"
    if not state_path.is_file():
        errors.append("missing .harness/STATE.md (the session entry point)")
        return
    project = json.loads((ROOT / ".harness" / "project_state.json").read_text(encoding="utf-8"))
    text = state_path.read_text(encoding="utf-8")
    for key in ("active_plan", "active_feature"):
        expected = project.get(key)
        if not isinstance(expected, str):
            continue
        # 取「含 `key` 的表格行」的值单元格；标签文案随便改，但值必须一字不差。
        match = re.search(rf"\|\s*[^|\n]*`{re.escape(key)}`\s*\|\s*`([^`]+)`\s*\|", text)
        if match is None:
            errors.append(f"STATE.md missing a table row for `{key}`")
        elif match.group(1) != expected:
            errors.append(
                f"STATE.md stale: `{key}` row says {match.group(1)!r}, "
                f"but project_state says {expected!r}"
            )


def main() -> int:
    errors: list[str] = []
    check_state_consistency(errors)
    checked = 0
    for path in SCANNED:
        rel = path.relative_to(ROOT).as_posix()
        if not path.is_file():
            errors.append(f"missing scanned doc: {rel}")
            continue
        text = path.read_text(encoding="utf-8")
        for ref in sorted(set(REF.findall(text))):
            checked += 1
            if ref in EXEMPT:
                continue
            if not (ROOT / ref).exists():
                errors.append(f"{rel}: dead reference -> {ref}")

    if checked < MIN_REFS:
        errors.append(
            f"scanned only {checked} refs (< {MIN_REFS}) — regex or docs changed; "
            "refusing to report green"
        )

    if errors:
        print("DOC CHECK FAILED")
        print("\n".join(f"- {item}" for item in errors))
        return 1
    print(f"DOC CHECK PASSED ({len(SCANNED)} docs, {checked} refs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
