"""负向验证：变量可见范围的判据真拦得住吗？（2026-10-14）

做法：对**每一条**新判据，往实现里注入一处**语法合法的精准放宽**，跑该判据，
断言它**必须红**；然后逐字节还原并核 md5。全部方向都验：

1. Python 作用域函数把 if 的 then/else 顺序写反 → 契约测试必须红
2. Python 去掉「自身声明」分支（退回只报 Forward）→ 文案判据必须红
3. Python 作用域函数把**本节点自身**的声明也算进可见集 → 判据必须红
4. JS visibleScopeAt 把 action 目标的判定写坏 → **JS 门禁**必须红（且 Python 侧仍绿，
   证明 JS 门禁不是冗余摆设）
5. JS collectUserVariables 不喂当前节点（传 null）→ JS 门禁的接线判据必须红
6. GUI `_reference_groups` 不按当前节点算（传一个不存在的 id）→ GUI 判据必须红
7. app.js 锚点被复制一份 → JS 门禁的锚点唯一性自检必须红

安全：① 开头扫全部受管文件，发现哨兵即硬失败（不靠人记性）；② 注入循环包
`try/finally: restore_all()`；③ 输出重定向到文件再读（**别接管道**，BrokenPipe 会
打断 restore —— 2026-10-08 踩过）。

用法：``./.venv/Scripts/python.exe .harness/spike/probe_scope_visibility_negative.py``
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
SENTINEL = "RPA_SCOPE_NEGVER_SENTINEL"

COMPILER = ROOT / "src" / "rpa_core" / "compiler" / "compiler.py"
APP_PY = ROOT / "src" / "rpa_core" / "gui" / "app.py"
APP_JS = ROOT / "src" / "rpa_core" / "devserver" / "static" / "app.js"
MANAGED = [COMPILER, APP_PY, APP_JS]

PYTEST_SCOPE = (
    str(PY),
    "-m",
    "pytest",
    "tests/contract/test_variable_scope_visibility.py",
    "-p",
    "no:cacheprovider",
)
PYTEST_GUI = (
    str(PY),
    "-m",
    "pytest",
    "tests/contract/test_gui_param_form.py",
    "tests/contract/test_gui_flow_inputs_linkage.py",
    "-p",
    "no:cacheprovider",
)
NODE_GATE = ("node", "scripts/check_scope_visibility.mjs", )

# (名称, 文件, old, new, 判定命令, 期望：是否必须红)
INJECTIONS: list[tuple[str, Path, str, str, tuple[str, ...], bool]] = [
    (
        "① 作用域函数把 if 的 then/else 顺序写反",
        COMPILER,
        "        elif isinstance(node, IfNode):\n"
        "            for child in node.then:\n"
        "                visit(child, loop_vars, error_vars)\n"
        "            for child in node.otherwise:\n"
        "                visit(child, loop_vars, error_vars)\n",
        "        elif isinstance(node, IfNode):\n"
        "            for child in node.otherwise:\n"
        "                visit(child, loop_vars, error_vars)\n"
        "            for child in node.then:\n"
        "                visit(child, loop_vars, error_vars)\n",
        PYTEST_SCOPE,
        True,
    ),
    (
        "② 去掉「自身声明」分支（自引用退回只报 Forward）",
        COMPILER,
        "                elif root in own_names:\n"
        "                    # 变量就由**当前节点自己**声明 → 引用它必然取不到（赋值还没发生）\n"
        "                    raise WorkflowCompileError(\n"
        "                        f\"Self variable reference (declared by this node): {reference}\"\n"
        "                    )\n",
        f"                elif root in own_names:  # {SENTINEL}\n                    pass\n",
        PYTEST_SCOPE,
        True,
    ),
    (
        "③ 作用域函数把本节点自身的声明也算成可见",
        COMPILER,
        "        if node.id == node_id:\n"
        "            ordered = list(dict.fromkeys(seen))\n",
        "        if node.id == node_id:\n"
        "            if isinstance(node, ActionNode):\n"
        "                seen.extend(_declared_names(node, catalog))\n"
        "            ordered = list(dict.fromkeys(seen))\n",
        PYTEST_SCOPE,
        True,
    ),
    (
        "④ JS 作用域函数对 action 目标判错（自身声明被算进去）",
        APP_JS,
        "    if (scopeStepsEqual(path, targetPath)) {",
        '    if (scopeStepsEqual(path, targetPath) && node.type !== "action") {',
        NODE_GATE,
        True,
    ),
    (
        "⑤ JS collectUserVariables 不喂当前节点（传 null）",
        APP_JS,
        "  return visibleScopeAt(state.workflow.root, targetPath, manifestOf).variables;",
        "  return visibleScopeAt(state.workflow.root, null, manifestOf).variables;",
        NODE_GATE,
        True,
    ),
    (
        "⑥ GUI 菜单不按当前节点算（传不存在的 id）",
        APP_PY,
        "        scope = visible_scope(workflow.root, self.catalog, node_id)",
        '        scope = visible_scope(workflow.root, self.catalog, "no-such-node")',
        PYTEST_GUI,
        True,
    ),
    (
        "⑦ app.js 锚点被复制一份（门禁的锚点唯一性自检）",
        APP_JS,
        "// [scope-helpers:end]",
        "// [scope-helpers:end]\n// [scope-helpers:end]",
        NODE_GATE,
        True,
    ),
]


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


ORIGINALS = {path: path.read_bytes() for path in {item[1] for item in INJECTIONS}}


def restore_all() -> None:
    for path, raw in ORIGINALS.items():
        path.write_bytes(raw)


def main() -> int:
    # 哨兵自检：工作区若已带哨兵（上次没还原干净），一律拒跑——别在脏基线上做验证
    dirty = [str(p) for p in ORIGINALS if SENTINEL.encode() in p.read_bytes()]
    if dirty:
        print(f"REFUSE: 工作区带着上次的哨兵，先还原再跑：{dirty}")
        return 2

    before = {path: md5(path) for path in ORIGINALS}
    failures: list[str] = []
    logs = ROOT / ".harness" / "spike"
    try:
        for index, (label, path, old, new, judge, expect_red) in enumerate(INJECTIONS, 1):
            raw = path.read_text(encoding="utf-8")
            count = raw.count(old)
            if count != 1:
                failures.append(f"{label}: 锚点在 {path.name} 出现 {count} 次（要求恰好 1）")
                print(f"ANCHOR-FAIL | {label} → {path.name} 命中 {count} 次")
                continue
            path.write_text(raw.replace(old, new), encoding="utf-8", newline="\n")
            # 输出重定向到文件再读（不接管道：BrokenPipe 会打断还原）
            log_path = logs / f"_scope_negver_{index}.log"
            with log_path.open("wb") as handle:
                rc = subprocess.run(judge, cwd=ROOT, stdout=handle, stderr=handle).returncode
            output = log_path.read_text(encoding="utf-8", errors="replace")

            # ★ 判「真红」四条：rc≠0；**且**（pytest）报出 `N failed`（不是 0）与
            # `N passed`（说明真跑起来过）；**且**没有 `N error`（error 是收集/夹具炸了，
            # 不是判据拦住了）。退出阶段的环境级非零（SystemExit: 1）不算红。
            if "pytest" in judge:
                failed_n = re.search(r"(\d+) failed", output)
                passed_n = re.search(r"(\d+) passed", output)
                error_n = re.search(r"(\d+) error", output)
                really_red = (
                    rc != 0
                    and failed_n is not None
                    and int(failed_n.group(1)) > 0
                    and passed_n is not None
                    and error_n is None
                )
            else:
                really_red = rc != 0 and ("项失败" in output or "FAIL:" in output)

            went_red = rc != 0
            ok = (really_red == expect_red) and (went_red == expect_red)
            if not ok:
                failures.append(
                    f"{label}: 期望{'真红' if expect_red else '绿'}，实际 rc={rc}，"
                    f"计数={'/'.join(m.group(0) for m in (failed_n, passed_n) if m) if 'pytest' in judge else 'n/a'}"
                    f"（详见 {log_path.name}）"
                )
            tail = [line for line in output.splitlines() if line.strip()][-1:] or [""]
            print(
                f"{'OK  ' if ok else 'MISS'} | {label} → rc={rc} "
                f"({'真红' if really_red else '红?/绿'}) :: {tail[0][:110]}"
            )
            restore_all()
    finally:
        restore_all()

    after = {path: md5(path) for path in ORIGINALS}
    for path, digest in before.items():
        if after[path] != digest:
            failures.append(f"{path.name} 未逐字节还原（md5 不一致）")

    print()
    if failures:
        print("负向验证失败：")
        for line in failures:
            print(" -", line)
        return 1
    print(f"负向验证通过：{len(INJECTIONS)} 个方向全部符合预期，且受管文件 md5 一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
