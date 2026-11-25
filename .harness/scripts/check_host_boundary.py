"""宿主边界门禁：i18n 冻结 + 编辑器隔离 + 编辑器住址（M65 → ADR 0021）。

四条判据，各管一件事：

1. **i18n 残留冻结**（`src/rpa_core/gui/**/*.py` 的集合与逐文件 sha256 必须与
   `.harness/gui-freeze.json` 一致）。Qt GUI 已于 2026-11 退役（提交 `7aaeac2`），
   gui 包里只剩 `i18n.py`（中文标签的唯一 Python 入口）与 `__init__.py`。冻结现在
   管的是**别再往这具空壳里加东西**：想加就得 `--update-freeze` 签字，签字会出现在
   diff 里，而不是一句随时会过期的话。

2. **编辑器隔离**（`apps/editor/`）：
   - 不得引入 Qt（`PySide6`）或 gui 包的 UI 模块 —— 「WebView 壳不靠 Qt」是这个形态
     成立的前提，偷偷 import 一个 `gui.app` 就把结论作废了；
   - 唯一豁免是 `rpa_core.gui.i18n`：它只做文本解析、不 import PySide6。

3. **编辑器住址**（ADR 0021）：编辑器必须在 `apps/editor/`（有 `ppx.toml`），且
   `.harness/demo/ppx_probe/` 不得复活 —— 那是脚手架目录，产品代码不住那儿。

4. 反向一条：`src/rpa_core/` 不得 import `ppx_py` 或编辑器路径 —— 应用层不许渗进
   能力层，能力层仍是唯一事实来源。

用法::

    python .harness/scripts/check_host_boundary.py                  # 校验
    python .harness/scripts/check_host_boundary.py --update-freeze  # 重建基线
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GUI = ROOT / "src" / "rpa_core" / "gui"
FREEZE = ROOT / ".harness" / "gui-freeze.json"
EDITOR = ROOT / "apps" / "editor"
LEGACY_EDITOR = ROOT / ".harness" / "demo" / "ppx_probe"
SRC = ROOT / "src" / "rpa_core"

# 原型允许依赖的 gui 模块：只有纯文本解析的 i18n（含它下面的名字）。
GUI_ALLOWED_IN_EDITOR = {"rpa_core.gui.i18n"}
# 原型不得出现的第三方 GUI 栈。
FORBIDDEN_IN_EDITOR = ("PySide6", "shiboken6")
# 不许被原型扫描穿过的目录（体积大、且不是我们写的）。
PRUNE = {".venv", "node_modules", "__pycache__", ".git", "dist", "build"}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _gui_files(root: Path | None = None) -> dict[str, str]:
    """GUI 目录下所有 .py 的 {相对路径: sha256}。路径用 POSIX 分隔符。"""
    base = GUI if root is None else root
    out: dict[str, str] = {}
    for path in sorted(base.rglob("*.py")):
        if any(part in PRUNE for part in path.parts):
            continue
        out[path.relative_to(ROOT).as_posix()] = _sha256(path)
    return out


def _write_freeze(errors: list[str]) -> None:
    current = _gui_files()
    FREEZE.write_text(
        json.dumps({"gui": current}, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"重写冻结基线：{len(current)} 个文件 → {FREEZE.relative_to(ROOT).as_posix()}")


def check_gui_frozen(errors: list[str]) -> None:
    if not FREEZE.exists():
        errors.append(
            f"缺少冻结基线 {FREEZE.relative_to(ROOT).as_posix()}"
            "（跑 --update-freeze 生成，并把结果一起提交）"
        )
        return
    frozen: dict[str, str] = json.loads(FREEZE.read_text(encoding="utf-8"))["gui"]
    current = _gui_files()

    removed = sorted(set(frozen) - set(current))
    added = sorted(set(current) - set(frozen))
    changed = sorted(p for p in set(frozen) & set(current) if frozen[p] != current[p])

    for path in removed:
        errors.append(f"GUI 已冻结，但文件被删除：{path}")
    for path in added:
        errors.append(f"GUI 已冻结，但新增了文件：{path}")
    for path in changed:
        errors.append(f"GUI 已冻结，但内容被修改：{path}")
    if removed or added or changed:
        errors.append(
            "若这是有意的解冻，跑 `check_host_boundary.py --update-freeze` 重建基线，"
            "并在提交信息里写明原因（ADR 0019）"
        )


def _iter_editor_py() -> list[Path]:
    if not EDITOR.exists():
        return []
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(EDITOR):
        dirnames[:] = [d for d in dirnames if d not in PRUNE]
        for name in filenames:
            if name.endswith(".py"):
                found.append(Path(dirpath) / name)
    return sorted(found)


def _imported_modules(tree: ast.AST) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                out.append((node.module, node.lineno))
            # `from rpa_core.gui import app` 这种：模块名只有 rpa_core.gui，
            # 真正碰到的 UI 模块在 names 里，要拼出来才算数。
            for alias in node.names:
                full = f"{node.module}.{alias.name}" if node.module else alias.name
                out.append((full, node.lineno))
    return out


def check_editor_isolation(errors: list[str]) -> None:
    for path in _iter_editor_py():
        rel = path.relative_to(ROOT).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        except SyntaxError as exc:
            errors.append(f"编辑器代码语法错误，无法校验隔离性：{rel}:{exc.lineno}")
            continue
        for module, lineno in _imported_modules(tree):
            if any(module == bad or module.startswith(f"{bad}.") for bad in FORBIDDEN_IN_EDITOR):
                errors.append(f"编辑器不得引入 Qt：{rel}:{lineno} import {module}")
            if module == "rpa_core.gui" or module.startswith("rpa_core.gui."):
                # `from rpa_core.gui.i18n import load_command_display_names` 会被拆成
                # `rpa_core.gui.i18n.load_command_display_names`，故按模块前缀放行，
                # 不要求精确相等——否则白名单永远打不中，反而把合法用法判红。
                if not any(
                    module == allowed or module.startswith(f"{allowed}.")
                    for allowed in GUI_ALLOWED_IN_EDITOR
                ):
                    errors.append(
                        f"编辑器不得依赖 gui 的 UI 模块：{rel}:{lineno} import {module}"
                        f"（允许：{', '.join(sorted(GUI_ALLOWED_IN_EDITOR))}）"
                    )


def check_editor_location(errors: list[str]) -> None:
    """ADR 0021：产品代码不睡脚手架 —— 搬回来、或整体消失，都要报。"""
    if not (EDITOR / "ppx.toml").is_file():
        errors.append(
            "编辑器不在 apps/editor（缺 ppx.toml）—— 转正后它不该再搬家或消失（ADR 0021）"
        )
    if LEGACY_EDITOR.exists():
        errors.append(
            "编辑器又回到 .harness/demo/ppx_probe —— 那是脚手架目录，产品代码不住那儿（ADR 0021）"
        )


def check_no_ppx_in_src(errors: list[str]) -> None:
    for path in sorted(SRC.rglob("*.py")):
        if any(part in PRUNE for part in path.parts):
            continue
        rel = path.relative_to(ROOT).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        except SyntaxError:
            continue
        for module, lineno in _imported_modules(tree):
            if module == "ppx_py" or module.startswith("ppx_py."):
                errors.append(f"能力层不得依赖宿主壳：{rel}:{lineno} import {module}")
            if (
                ".harness.demo" in module
                or "apps.editor" in module
                or module.startswith("ppx_probe")
            ):
                errors.append(f"能力层不得依赖应用层：{rel}:{lineno} import {module}")


def main(argv: list[str]) -> int:
    errors: list[str] = []
    if "--update-freeze" in argv:
        _write_freeze(errors)
        return 0
    check_gui_frozen(errors)
    check_editor_isolation(errors)
    check_editor_location(errors)
    check_no_ppx_in_src(errors)
    if errors:
        for message in errors:
            print(f"[host-boundary] {message}")
        return 1
    print(
        f"[host-boundary] i18n 冻结基线一致（{len(_gui_files())} 文件）；"
        "编辑器在 apps/editor 且未引入 Qt / gui UI 模块"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
