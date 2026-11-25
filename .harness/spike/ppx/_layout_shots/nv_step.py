"""负向验证的分步驱动（chrome 只能从被豁免的命令里起，所以拆成三步）。

    python nv_step.py backup                 # 记下原文件（md5 + 副本）
    python nv_step.py inject 0|1             # 注入第 N 个破坏
    python nv_step.py restore                # 逐字节写回并核 md5
    python nv_step.py check-md5              # 只核 md5

注入的锚点必须唯一（count == 1），不唯一直接拒跑。
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CSS = HERE.parent / "app" / "gui" / "src" / "style.css"
BACKUP = HERE / "_style_backup.css"
MD5FILE = HERE / "_style_backup.md5"

INJECTIONS = [
    (
        "缩进去掉：层级退回「只看颜色」",
        "  --indent: calc(var(--depth-n, 0) * 16px);",
        "  --indent: 0px;",
    ),
    (
        "折叠的 AST 重新吃满余高",
        "  flex: 1 1 auto; min-height: 0; overflow: auto;\n  padding: 10px 12px 4px;",
        "  flex: 0 1 auto; min-height: 0; overflow: auto;\n  padding: 10px 12px 4px;",
    ),
    (
        "卡片行高退回 46px、名字允许收缩",
        "  flex: 0 0 auto; min-width: 0; max-width: 46%;",
        "  flex: 0 1 auto; min-width: 0; max-width: 46%;",
    ),
]


def md5(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "backup":
        CSS.read_bytes()
        BACKUP.write_bytes(CSS.read_bytes())
        MD5FILE.write_text(md5(CSS.read_bytes()), encoding="utf-8")
        print(f"backup {MD5FILE.read_text()} -> {BACKUP.name}")
        return 0

    if cmd == "inject":
        idx = int(sys.argv[2])
        label, anchor, mutated = INJECTIONS[idx]
        original = BACKUP.read_bytes()
        text = original.decode("utf-8")
        count = text.count(anchor)
        if count != 1:
            print(f"锚点不唯一（{count} 处）：{label}")
            return 2
        CSS.write_bytes(text.replace(anchor, mutated).encode("utf-8"))
        print(f"inject[{idx}] {label}")
        return 0

    if cmd == "restore":
        expect = MD5FILE.read_text().strip()
        CSS.write_bytes(BACKUP.read_bytes())
        got = md5(CSS.read_bytes())
        print(f"restore md5 {got} (expect {expect}) -> {'一致' if got == expect else '不一致！'}")
        return 0 if got == expect else 3

    if cmd == "check-md5":
        expect = MD5FILE.read_text().strip()
        got = md5(CSS.read_bytes())
        print(f"md5 {got} (expect {expect}) -> {'一致' if got == expect else '不一致！'}")
        return 0 if got == expect else 3

    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
