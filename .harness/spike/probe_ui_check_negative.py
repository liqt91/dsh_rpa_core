"""负向验证：编辑期校验的 6 条新判据真的拦得住吗。

做法：逐条注入「一种合理的退化」→ 跑 check → 必须变红且命中预期文案 → 逐字节还原。

三条自保：开头扫哨兵（残留注入直接硬失败）、锚点唯一性自检（每处必须恰好 1 次）、
``try/finally`` 无条件还原。
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAIN = ROOT / "apps/editor/gui/src/main.js"
CSS = ROOT / "apps/editor/gui/src/style.css"
CHECK = ROOT / "scripts/check_element_pick_ui.mjs"
SENTINEL = "__NEG_INJECT__"

# (标签, 文件, 原文, 替换, 期望出现的判据文案)
INJECTIONS = [
    (
        "不收起编辑层",
        MAIN,
        "  el.dialog?.classList.add('checking')\n",
        "  // __NEG_INJECT__\n",
        "校验时没有收起编辑层",
    ),
    (
        "退回旧接口（不跑活体）",
        MAIN,
        "    res = await ppx.call('rpa.verifyDraft', args)",
        "    res = await ppx.call('rpa.elementValidate', args) // __NEG_INJECT__",
        "没有走活体校验",
    ),
    (
        "只给一个分支作废旧结论",
        MAIN,
        "      el.dialogPreview.value = res.selector\n      invalidateDialogCheck()",
        "      el.dialogPreview.value = res.selector // __NEG_INJECT__",
        "实际 1 处",
    ),
    (
        "藏层规则失效",
        CSS,
        ".modal.checking { display: none; }",
        ".modal.checking { display: block; } /* __NEG_INJECT__ */",
        "没有藏起来",
    ),
    (
        "成功态被写成警告态",
        MAIN,
        "setDialogNote(res.message || '找到 1 个元素', 'ok')",
        "setDialogNote(res.message || '找到 1 个元素', 'warn') // __NEG_INJECT__",
        "成功却没走 ok 态",
    ),
    (
        "静态校验失败仍然继续",
        MAIN,
        "      setDialogNote((local && local.error) || '名字或定位不合格', 'error')\n      return",
        "      setDialogNote((local && local.error) || '名字或定位不合格', 'error') // __NEG_INJECT__",
        "名字没过还去跑了活体",
    ),
]


def digest(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def run_check() -> tuple[int, str]:
    done = subprocess.run(
        ["node", str(CHECK)], cwd=ROOT, capture_output=True, text=True, encoding="utf-8"
    )
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def write_exact(path: Path, text: str) -> None:
    """★ 必须按字节写：``Path.write_text`` 在 Windows 上会把整个文件的 \\n 转成
    \\r\\n，一次注入就能把 3000 行的 LF 仓库变成 CRLF（2026-11-20 实测）。"""
    path.write_bytes(text.encode("utf-8"))


def main() -> int:
    for path in (MAIN, CSS):
        if SENTINEL in path.read_text(encoding="utf-8"):
            print(f"FATAL: {path.name} 里残留注入标记，先人工还原")
            return 2

    before = {p: digest(p) for p in (MAIN, CSS)}
    rc, out = run_check()
    if rc == 0 and "PASSED" in out:
        print("基线：PASSED")
    else:
        print("FATAL: 基线就不绿，负向验证没意义")
        return 2

    failures = 0
    try:
        for label, path, old, new, expect in INJECTIONS:
            source = path.read_text(encoding="utf-8")
            count = source.count(old)
            if count != 1:
                print(f"FATAL: 锚点不唯一（{label}，命中 {count} 次）")
                return 2
            write_exact(path, source.replace(old, new, 1))
            try:
                rc, out = run_check()
            finally:
                write_exact(path, source)
            ok = rc != 0 and expect in out
            print(("PASS" if ok else "FAIL"), label, "| rc =", rc, "| 命中 =", expect in out)
            if not ok:
                failures += 1
                tail = [ln for ln in out.splitlines() if "FAIL" in ln][:3]
                print("   实际输出：", tail)
    finally:
        mismatched = [p.name for p, v in before.items() if digest(p) != v]
        if mismatched:
            print("FATAL: 还原后 md5 不一致：", mismatched)
            failures += 1
        else:
            print("还原核验：md5 全部一致")

    print("VERDICT", "PASS" if failures == 0 else f"FAIL ({failures})")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
