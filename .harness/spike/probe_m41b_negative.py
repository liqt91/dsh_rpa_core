"""M41 S3 负向验证探针：捕获浮窗「躲鼠标」的 11 处判据逐处注入。

判据必须能失败才叫判据。与 ``probe_m41_negative.py`` 同款三条防「假绿灯机」设计：

1. **对照跑**：注入之前先用原始文件跑**同一个**判据，必须绿——否则一个「无论跑什么
   都红」的环境会让 11 处全部报 OK。
2. **失败类型判定**：必须是 ``N failed`` 且**不得**出现 ``N errors``（后者说明是夹具/
   导入炸了，不是判据被打破）。
3. **超时按挂住处理**：既不是红也不是绿，单独报出来修判据。

还原安全网：注入处留哨兵 ``# [M41B-NEGATIVE-INJECTED] idx=N``，起手按哨兵自愈
（凭证是「文件此刻是不是注入态」，**不是**「备份目录里有没有它」——后者在 M40 真出过把
陈旧备份盖回源码的事故）。

覆盖两个静默退化面（各有专门判据）：
- 纯函数侧（``capture_float_origin``）：不翻 / 翻得太晚 / 一去不返；
- 接线侧（``CaptureFloatWindow.avoid_cursor`` ↔ ``app.py``）：初始定位不避让、节拍没跑、
  节拍太慢、收尾不停表、拖动后仍被自动挪走。

跑法：``uv run python .harness/spike/probe_m41b_negative.py``
退出码 0 = 11 处全部「对照绿 → 注入精确红 → 逐字节还原」。
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "_m41b_negative_report.txt"
_SENTINEL = "# [M41B-NEGATIVE-INJECTED]"

_FLOAT = "src/rpa_core/gui/capture_float.py"
_APP = "src/rpa_core/gui/app.py"
_UNIT = "tests/unit/test_capture_float_geometry.py"
_CONTRACT = "tests/contract/test_gui_capture.py"
_WIRING = f"{_CONTRACT}::test_capture_float_avoidance_is_wired_into_the_capture_flow"

# (文件, 原文, 注入后, 期望变红的判据, 说明)
INJECTIONS: list[tuple[str, str, str, str, str]] = [
    # ---- 纯函数侧：避让几何 ---------------------------------------------------
    (
        _FLOAT,
        "    if near_default:\n"
        "        return ax + margin, ay + margin\n"
        "    return default_x, default_y\n",
        "    if False:\n"
        "        return ax + margin, ay + margin\n"
        "    return default_x, default_y\n",
        f"{_UNIT}::test_cursor_on_the_window_flips_it_to_the_other_side",
        "摘掉翻转分支 → 浮窗永远压在鼠标上（本里程碑要修的就是这个）",
    ),
    (
        _FLOAT,
        "        default_x - avoid_pad <= cx <= default_x + ww + avoid_pad\n",
        "        default_x - 0 <= cx <= default_x + ww + 0\n",
        f"{_UNIT}::test_cursor_about_to_reach_the_window_moves_it_early",
        "避让余量归零 → 只差一点就碰到时还不让开（等于「即将移动到」没实现）",
    ),
    (
        _FLOAT,
        "AVOID_PAD = 80\n",
        "AVOID_PAD = 10\n",
        f"{_UNIT}::test_avoid_pad_covers_a_fast_flick_between_refresh_ticks",
        "余量小于两帧之间的鼠标位移 → 甩一下鼠标就压上去了",
    ),
    (
        _FLOAT,
        "    if near_default:\n",
        "    if True:\n",
        f"{_UNIT}::test_decision_does_not_depend_on_where_the_window_currently_is",
        "鼠标走开后仍翻在左上（一去不返）→ 用户以为浮窗丢了",
    ),
    # ---- 纯函数侧：用户接管 ---------------------------------------------------
    (
        _FLOAT,
        "        if self.user_positioned:\n            return\n",
        "        if False:\n            return\n",
        f"{_CONTRACT}::test_capture_float_keeps_place_once_the_user_moves_it",
        "不理会「用户已接管」→ 用户拖好的位置会被下一次节拍拽走",
    ),
    (
        _FLOAT,
        "            self.user_positioned = True  # 用户接管位置：停止自动避让\n",
        "",
        f"{_CONTRACT}::test_capture_float_keeps_place_once_the_user_moves_it",
        "拖动入口忘了置位 → 判据④接线断言红（接一半照样失效）",
    ),
    # ---- 接线侧：初始定位 -----------------------------------------------------
    (
        _APP,
        "        # 初始定位就带鼠标：若用户此刻已在右下角，浮窗直接去左上（M41 S3）\n"
        "        window.avoid_cursor(QCursor.pos())\n"
        "        present_window(window)  # 主窗刚最小化：浮窗必须自己浮到最前\n",
        "        present_window(window)  # 主窗刚最小化：浮窗必须自己浮到最前\n",
        _WIRING,
        "初始定位不避让 → 浮窗打开的一瞬间就压在鼠标下（要等下一拍才让开）",
    ),
    # ---- 接线侧：避让节拍 -----------------------------------------------------
    (
        _APP,
        '    def _tick_capture_avoid(self) -> None:\n'
        '        """按鼠标位置刷新浮窗（躲开鼠标路径，避免挡住下方元素；M41 S3）。"""\n'
        "        window = self._capture_float\n"
        "        if window is None:\n"
        "            return\n"
        "        from PySide6.QtGui import QCursor\n"
        "\n"
        "        window.avoid_cursor(QCursor.pos())\n",
        "    def _tick_capture_avoid(self) -> None:\n"
        '        """按鼠标位置刷新浮窗（躲开鼠标路径，避免挡住下方元素；M41 S3）。"""\n'
        "        return None\n",
        _WIRING,
        "节拍空转 → 只有打开那一次避让，鼠标追上来后浮窗就不动了",
    ),
    (
        _APP,
        "        avoid_timer.setInterval(40)\n",
        "        avoid_timer.setInterval(500)\n",
        _WIRING,
        "节拍降到 500ms → 「即将移动到」来不及，浮窗会先被鼠标追上",
    ),
    (
        _APP,
        "        if self._capture_avoid_timer is not None:\n"
        "            self._capture_avoid_timer.stop()\n"
        "            self._capture_avoid_timer = None\n",
        "",
        _WIRING,
        "收尾不停表 → 浮窗销毁后定时器继续空打（且引用不清空）",
    ),
    # ---- 接线侧：浮窗真的调纯函数 ---------------------------------------------
    (
        _FLOAT,
        "        x, y = capture_float_origin(\n"
        "            (area.x(), area.y(), area.width(), area.height()),\n"
        "            (_WIDTH, _HEIGHT),\n"
        "            point,\n"
        "        )\n",
        "        x, y = (\n"
        "            area.x() + area.width() - _WIDTH - _MARGIN,\n"
        "            area.y() + area.height() - _HEIGHT - _MARGIN,\n"
        "        )\n",
        _WIRING,
        "自己算位置、不走纯函数 → 几何判据全绿也拦不住（纯函数成了摆设）",
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

    哨兵拼在替换串**后面**，锚点末端若切在一行中间，哨兵会把该行剩余部分顶到下一行、
    注入后语法就坏了（M40 真踩过）。
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


def _run(target: str) -> tuple[int, str, bool, bool]:
    """跑单个判据。返回 (returncode, 输出, 是否超时, 判据是否可运行)。"""
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


def _is_green(_target: str, rc: int, out: str) -> bool:
    return rc == 0 and bool(_PASSED_RE.search(out))


def _is_red(_target: str, rc: int, out: str) -> bool:
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
            lines.append(f"[UNRUNNABLE] 判据跑不起来 — {note}\n        {target}")
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
