"""M103 负向验证：元素库批量条必须是单例，且取消勾选后立刻消失。

现场（2026-11-19 维护者截图）：勾 5 项，界面叠出 5 行「已选 N 项」批量操作条。
根因 renderBatchBar 每次 append 一条、从不清理；取消最后一项时因为提前 return，
旧条还会赖着不走。旧判据只用 find 找「有一条」，所以一路假绿。

两个注入各自打一条新断言：

* ``no-clean``            整段删掉清理 ⇒ 勾 2 项后叠 2 条（防累积断言红）
* ``clean-after-return``  清理挪到提前 return 之后（合法语法的精准放宽）
                          ⇒ 只有「取消后还剩 1 条」红，证明第二条断言不是摆设

期望按推理写死（含 detail 关键字），跑完按原字节还原并核 md5；带哨兵防叠注入。
"""

from __future__ import annotations

import hashlib
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
MAIN = ROOT / "apps/editor/gui/src/main.js"
CHECK = "scripts/check_element_library.mjs"
CASE = "多选批量：勾选进选中集、批量条浮出、取消选择清空"
MARKER = "  // <<<M103-INJECTED>>>"

CLEAN = ("  const kept = Array.from(el.elemLibLayer.children).filter(\n"
         "    (node) => !node.classList?.contains('libBatchBar'))\n"
         "  el.elemLibLayer.replaceChildren(...kept)\n")
RETURN = "  if (libMenu || !elemSelected.size) return\n"
COMMENT = "  // 右键菜单开着时批量条让位；两者共用 layer，避免互相覆盖。\n"

CASES = [
    ("no-clean", [(CLEAN, MARKER)], "勾 2 项后批量条 2 条"),
    ("clean-after-return",
     [(CLEAN + COMMENT + RETURN, RETURN + COMMENT + CLEAN)],
     "取消选择后批量条还剩 1 条"),
]


def md5(path: pathlib.Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def main() -> int:
    original = MAIN.read_bytes()
    text = original.decode("utf-8")
    if "<<<M103-INJECTED>>>" in text:
        print("[哨兵] main.js 里还有上次的注入标记，拒绝运行")
        return 2
    for name, edits, _detail in CASES:
        for old, _new in edits:
            if text.count(old) != 1:
                print(f"[锚点] {name}: 出现 {text.count(old)} 次，必须恰好 1 次")
                return 2

    problems: list[str] = []
    before = md5(MAIN)
    restore_failed = False
    try:
        for name, edits, detail in CASES:
            body = text
            for old, new in edits:
                body = body.replace(old, new, 1)
            MAIN.write_bytes(body.encode("utf-8"))
            proc = subprocess.run(["node", CHECK], cwd=str(ROOT), capture_output=True,
                                  text=True, encoding="utf-8", errors="replace")
            output = proc.stdout + proc.stderr
            line = next((s for s in output.splitlines() if s.startswith(f"FAIL {CASE}")), "")
            if proc.returncode == 0 or detail not in line:
                problems.append(f"{name}: rc={proc.returncode} 失败行={line or '<没有失败行>'}")
                print(f"FAIL {name}: rc={proc.returncode} 失败行={line or '<没有失败行>'}")
            else:
                print(f"OK   {name}: {line.split(' — ', 1)[-1]}")
    finally:
        MAIN.write_bytes(original)
        if md5(MAIN) != before:
            print("[还原失败] main.js 字节不一致")
            restore_failed = True
    if restore_failed:
        return 3
    if problems:
        print("\n负向验证未通过：")
        for item in problems:
            print(" -", item)
        return 1
    print("\nM103 NEGATIVE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
