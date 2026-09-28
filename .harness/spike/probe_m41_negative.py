"""M41 负向验证探针：逐处注入、确认对应判据**真的红**、再逐字节还原。

判据必须能失败才叫判据。这里针对 M41 的 12 处新增/修改判据，一路注入破坏，跑**指定**
的判据（不是整个套件），要求「精确命中」而不是「顺带崩一堆」。

与 M40 探针（``probe_m40_negative.py``）同款三条防「假绿灯机」的设计：

1. **对照跑**：注入之前先用原始文件跑同一个判据，必须**绿**。只看注入跑红、不看对照跑绿
   的话，一个「无论跑什么都红」的环境会让 12 处全部报 OK。
2. **失败类型判定**：pytest 注入跑必须出现 ``N failed`` 且**不出现** ``N errors``；
   node 门禁必须非 0 退出且输出含 ``FAIL``。
3. **超时按挂住处理**：判据被破坏后若走进真模态对话框就会永久阻塞，那既不是「红」也不是
   「绿」，必须单独报出来修判据。

还原安全网：注入时在改动处**留哨兵**（``# [M41-NEGATIVE-INJECTED] idx=N``），起手先按
哨兵自愈（凭证是「文件此刻是不是注入态」，不是「备份目录里有没有它」——后者真出过事故，
见 M40 探针头部）。

两类判据混用（本里程碑特有）：桌面几何是 Python 纯函数 → pytest；页内几何**只在浏览器里
跑** → node 门禁脚本（``node:check_capture_overlay_geometry.mjs``）。两类的判绿/判红口径
不同，各自实现。

跑法：``uv run python .harness/spike/probe_m41_negative.py``
退出码 0 = 12 处全部「对照绿 → 注入精确红 → 逐字节还原」。
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "_m41_negative_report.txt"
_SENTINEL = "# [M41-NEGATIVE-INJECTED]"
_NODE_PREFIX = "node:"

# (文件, 原文, 注入后, 期望变红的判据, 说明)
#
# 判据字段有两种形态：
#   ``tests/...::test_x``        → pytest nodeid
#   ``node:<script>.mjs``        → node 门禁脚本（退出码 / FAIL 行）
INJECTIONS: list[tuple[str, str, str, str, str]] = [
    # ---- S1 桌面高亮几何 -----------------------------------------------------
    (
        "src/rpa_core/capture/desktop_agent.py",
        "    return left - outset, top - outset, right + outset, bottom + outset\n",
        "    return left + outset, top + outset, right - outset, bottom - outset\n",
        "tests/unit/test_desktop_overlay_geometry.py",
        "外扩改内缩（回退旧几何）→ 框线带又占住元素最外 3px",
    ),
    (
        "src/rpa_core/capture/desktop_agent.py",
        "        bounds = overlay_bounds(left, top, right, bottom)\n",
        "        bounds = (left, top, right, bottom)\n",
        "tests/unit/test_desktop_overlay_geometry.py::test_show_rect_uses_overlay_bounds",
        "纯函数都对、执行器没用上 → 外扩只是摆设（只有这条接线判据拦得住）",
    ),
    (
        "src/rpa_core/capture/desktop_agent.py",
        "OVERLAY_OUTSET = 5\n",
        "OVERLAY_OUTSET = 3\n",
        "tests/unit/test_desktop_overlay_geometry.py::test_outset_is_border_plus_cursor_hotspot",
        "外移量不足（3 < 框线宽 3 + 热区 2）→ 贴边指针仍被框线压住",
    ),
    # ---- S1 页内高亮几何（只在浏览器里跑 → node 门禁） ------------------------
    (
        "extension/content.js",
        "    left: r.left - OVERLAY_OUTSET,\n",
        "    left: r.left,\n",
        "node:check_capture_overlay_geometry.mjs",
        "页内框不外移 → 框线压在元素边界上（桌面同款缺陷）",
    ),
    (
        "extension/content.js",
        "    width: r.width + OVERLAY_OUTSET * 2,\n",
        "    width: r.width,\n",
        "node:check_capture_overlay_geometry.mjs",
        "外扩了 left/top 却没扩 width/height → 框线带跑进元素内部",
    ),
    (
        "extension/content.js",
        "        + `box-sizing:border-box;border:${OVERLAY_BORDER}px solid #ff3b30`;\n",
        "        + `background:rgba(255,59,48,.12);`\n"
        "        + `border:${OVERLAY_BORDER}px solid #ff3b30`;\n",
        "node:check_capture_overlay_geometry.mjs",
        "12% 红填充回来了 → 元素内容被整片染色，指针下看不真切",
    ),
    (
        "extension/content.js",
        "        + `box-sizing:border-box;border:${OVERLAY_BORDER}px solid #ff3b30`;\n",
        "        + `border:${OVERLAY_BORDER}px solid #ff3b30`;\n",
        "node:check_capture_overlay_geometry.mjs",
        "不声明 border-box → 外扩后的 width 不再是外沿，几何口径漂",
    ),
    # ---- S2 启动加载提示 ------------------------------------------------------
    (
        "src/rpa_core/gui/splash.py",
        "            | Qt.WindowType.WindowDoesNotAcceptFocus\n",
        "",
        "tests/contract/test_gui_splash.py::test_splash_does_not_take_keyboard_focus",
        "不收焦点没声明 → 初始化期间用户敲的键被卡片吃掉",
    ),
    (
        "src/rpa_core/gui/splash.py",
        "    splash = open_startup_splash(app)\n"
        "    try:\n"
        "        yield splash\n"
        "    finally:\n"
        "        close_startup_splash(splash)\n",
        "    splash = open_startup_splash(app)\n"
        "    yield splash\n",
        "tests/contract/test_gui_splash.py::test_splash_closes_when_initialization_raises",
        "去掉 try/finally → 初始化抛异常时卡片留在屏幕上挡住报错",
    ),
    (
        "src/rpa_core/gui/splash.py",
        "    splash.show()\n    app.processEvents()\n",
        "    splash.show()\n",
        "tests/contract/test_gui_splash.py::test_open_startup_splash_pumps_events",
        "显示后不泵事件 → 卡片要等初始化跑完才画出来，等于什么都没盖住",
    ),
    (
        "src/rpa_core/gui/app.py",
        "    with startup_splash(app):\n"
        "        catalog = load_catalog(Path(commands_root))\n",
        "    catalog = load_catalog(Path(commands_root))\n"
        "    with startup_splash(app):\n",
        "tests/contract/test_gui_splash.py::test_run_gui_wraps_initialization_in_startup_splash",
        "命令目录读在卡片起来之前 → 最长的那 420ms 没有提示",
    ),
    (
        "src/rpa_core/gui/app.py",
        "        window.show()\n        if isinstance(window, MainWindow):\n",
        "        app.processEvents()\n"
        "        window.show()\n"
        "        if isinstance(window, MainWindow):\n",
        "tests/contract/test_gui_splash.py::test_run_gui_wraps_initialization_in_startup_splash",
        "首帧泵事件挪到 show() 之前 → 撤卡片时主窗还没画出来（会闪一下）",
    ),
]

_FAILED_RE = re.compile(r"\b\d+ failed\b")
_ERROR_RE = re.compile(r"\b\d+ errors?\b")
_PASSED_RE = re.compile(r"\b\d+ passed\b")


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _injected_text(original: str, index: int, old: str, new: str) -> str:
    """注入：替换锚点并在改动处留下可反推的哨兵。"""
    return original.replace(old, new + f"{_SENTINEL} idx={index}\n", 1)


def _anchor_ends_at_line_end(text: str, old: str) -> bool:
    """锚点在文件里的末端是否正好落在行尾（其后是换行或文件结束）。

    哨兵拼在替换串**后面**，所以锚点末端的边界决定它会不会插进一行中间（M40 真踩过：
    锚点从一行中间切一刀 → 注入后语法坏掉，报错离现场很远）。
    """
    pos = text.find(old)
    if pos < 0:
        return False
    end = pos + len(old)
    return end >= len(text) or text[end] == "\n"


def _heal() -> list[str]:
    """起手自愈：把「上次被硬杀」留在注入态的文件按哨兵还原。"""
    healed: list[str] = []
    for index, (path_text, old, new, _target, _note) in enumerate(INJECTIONS):
        path = ROOT / path_text
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        injected = new + f"{_SENTINEL} idx={index}\n"
        if injected not in text:
            continue
        recovered = text.replace(injected, old, 1)
        path.write_text(recovered, encoding="utf-8", newline="\n")
        healed.append(f"{path_text} (idx={index}, 锚点已复原: {old in recovered})")
    return healed


def _node_command(script: str) -> list[str] | None:
    node = shutil.which("node")
    if not node:
        return None
    return [node, str(ROOT / "scripts" / script)]


def _run(target: str) -> tuple[int, str, bool, bool]:
    """跑单个判据。返回 (returncode, 输出, 是否超时, 判据是否可运行)。"""
    if target.startswith(_NODE_PREFIX):
        command = _node_command(target[len(_NODE_PREFIX):])
        if command is None:
            # node 不在路径上时**不能**静默跳过：那会让这一处「验证通过」变成空话
            return -3, "[NODE-MISSING] 找不到 node 可执行文件", False, False
    else:
        command = [
            sys.executable, "-m", "pytest", target,
            "-o", "addopts=", "-q", "-p", "no:cacheprovider",
        ]
    try:
        proc = subprocess.run(
            command, cwd=ROOT, capture_output=True, text=True,
            errors="replace", timeout=240,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        return -1, stdout, True, True
    return proc.returncode, (proc.stdout or "") + (proc.stderr or ""), False, True


def _is_green(target: str, rc: int, out: str) -> bool:
    if target.startswith(_NODE_PREFIX):
        return rc == 0 and "全部通过" in out
    return rc == 0 and bool(_PASSED_RE.search(out))


def _is_red(target: str, rc: int, out: str) -> bool:
    if target.startswith(_NODE_PREFIX):
        return rc != 0 and "FAIL" in out
    return rc != 0 and bool(_FAILED_RE.search(out)) and not _ERROR_RE.search(out)


def main() -> int:
    lines: list[str] = []
    failures: list[str] = []

    healed = _heal()
    if healed:
        lines.append("[HEALED] 上次留下注入态，已按哨兵还原：" + "；".join(healed))
        lines.append("")

    # 起手前置检查：锚点必须唯一、末端落在行尾、文件不带哨兵
    for path_text, old, new, _target, note in INJECTIONS:
        path = ROOT / path_text
        text = path.read_text(encoding="utf-8")
        count = text.count(old)
        if count != 1:
            lines.append(f"[SETUP-FAIL] {path_text}: 注入锚点出现 {count} 次（应为 1）— {note}")
            failures.append(path_text)
        if new and not new.endswith("\n") and not _anchor_ends_at_line_end(text, old):
            lines.append(
                f"[SETUP-FAIL] {path_text}: 锚点末端不在行尾，哨兵会插进一行中间 — {note}"
            )
            failures.append(path_text)
        if _SENTINEL in text:
            lines.append(f"[SETUP-FAIL] {path_text}: 仍带注入哨兵（自愈没还原干净）")
            failures.append(path_text)
    if failures:
        lines.append("")
        lines.append("锚点检查失败，已中止（先还原源码再跑）。")
        _emit(lines)
        return 1

    for index, (path_text, old, new, target, note) in enumerate(INJECTIONS):
        path = ROOT / path_text
        original = path.read_bytes()
        before = _md5(path)

        # ---- 1) 对照跑：原始文件必须绿 ----
        control_rc, control_out, control_to, runnable = _run(target)
        if not runnable:
            failures.append(path_text)
            lines.append(f"[UNRUNNABLE] 判据跑不起来（环境缺依赖？）— {note}\n        {target}\n        {_tail(control_out)}")
            continue
        if control_to:
            failures.append(path_text)
            lines.append(f"[CONTROL-HANG] {note}\n        {target}")
            continue
        if not _is_green(target, control_rc, control_out):
            failures.append(path_text)
            lines.append(
                f"[CONTROL-NOT-GREEN] 对照跑没绿，负向验证不可信 — {note}\n"
                f"        rc={control_rc} {target}\n        {_tail(control_out)}"
            )
            continue

        # ---- 2) 注入 ----
        text = original.decode("utf-8")
        path.write_text(
            _injected_text(text, index, old, new), encoding="utf-8", newline="\n"
        )
        syntax_ok = True
        if not path_text.endswith(".js"):
            try:
                compile(path.read_text(encoding="utf-8"), str(path), "exec")
            except SyntaxError as exc:
                syntax_ok = False
                lines.append(f"[SYNTAX-FAIL] 注入后语法不合法，验证没打到判据：{exc}")
        try:
            rc, out, timed_out, _ = _run(target) if syntax_ok else (-2, "", False, True)
        finally:
            path.write_bytes(original)

        if before != _md5(path):
            failures.append(path_text)
            lines.append(f"[RESTORE-FAIL] {path_text}: 还原后 md5 不一致")
            continue
        if not syntax_ok:
            failures.append(path_text)
            continue
        if timed_out:
            failures.append(path_text)
            lines.append(
                f"[HANG] 判据被破坏后**挂住**（不是干净变红）——先修判据 — {note}\n"
                f"        {target}"
            )
            continue
        if _is_red(target, rc, out):
            lines.append(f"[OK  ] {note}\n        {target}")
        else:
            failures.append(path_text)
            why = "假绿灯（没红）" if rc == 0 else "红得不对（不是判据失败）"
            lines.append(f"[{why}] {note}\n        rc={rc} {target}\n        {_tail(out)}")

    lines.append("")
    if failures:
        lines.append(f"NEGATIVE VERIFICATION FAILED（{len(failures)} 处）: {failures}")
    else:
        lines.append(
            f"NEGATIVE VERIFICATION PASSED（{len(INJECTIONS)} 处："
            "对照绿 → 注入精确红 → 逐字节还原）"
        )
    _emit(lines)
    return 1 if failures else 0


def _tail(text: str, count: int = 12) -> str:
    body = [ln for ln in text.strip().splitlines() if ln.strip()]
    return "\n        ".join(body[-count:])


def _emit(lines: list[str]) -> None:
    report = "\n".join(lines)
    REPORT.write_text(report + "\n", encoding="utf-8", newline="\n")
    print(report)


if __name__ == "__main__":
    sys.exit(main())
