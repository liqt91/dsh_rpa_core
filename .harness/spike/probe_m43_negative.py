"""M43 负向验证探针：混合捕获「用户取消是会话级信号」的三条判据真的拦得住。

被测文件：``src/rpa_core/capture/hybrid.py``
  - ``pick`` 的两条腿分支（新增 ``_is_cancelled`` 提前收场）
  - ``_finish_cancelled``（disarm 另一条腿 + 回收桌面 agent）
判据：``tests/contract/test_capture_hybrid.py`` 的三条新用例
  - ``test_hybrid_desktop_esc_cancels_whole_session``（桌面按 Esc）
  - ``test_hybrid_extension_esc_cancels_whole_session``（网页按 Esc）
  - ``test_hybrid_leg_failure_is_not_a_cancel``（**防过度泛化**的对照）

为什么逐条注入：一处改动被三条判据覆盖，必须确认它们**各自精确命中**（注入只让对应的
那条红、其余仍绿），否则其中一条可能只是摆设。每个 case 都要求：
  **对照绿 → 注入后红名单恰好等于预期 → 失败类型必须是「N failed」（出现 error 就是
  收集/语法错，那与「断言失败」同形不可混）→ 逐字节还原核 md5**。

**两条取消用例的注入是「挂住」而不是「红」**：宿主传的 ``timeout_seconds`` 是 ``inf``
（M41 S5 的 ``CAPTURE_TIMEOUT_SECONDS``，即生产值），旧行为就是**永不返回**——把修复
摘掉后判据会一直等下去。按仓规「挂住 ≠ 红」，探针把这一形态**单独报出**并说明它正是
维护者报障的现场（「按 Esc 只不显示红框，但还是在捕获模式中」），而不是含糊算作命中。

用法::

    .venv/Scripts/python.exe .harness/spike/probe_m43_negative.py

**别用 ``git checkout`` 还原**：被测文件带着本轮未提交改动。探针走内存备份还原，
收尾按 md5 逐字节核对。
"""

from __future__ import annotations

import hashlib
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
TARGET = ROOT / "src" / "rpa_core" / "capture" / "hybrid.py"
TESTS = "tests/contract/test_capture_hybrid.py"
REPORT = ROOT / "_m43_negative_report.txt"

SENTINEL = "INJECTED-M43"

T1 = f"{TESTS}::test_hybrid_desktop_esc_cancels_whole_session"
T2 = f"{TESTS}::test_hybrid_extension_esc_cancels_whole_session"
T3 = f"{TESTS}::test_hybrid_leg_failure_is_not_a_cancel"
ALL_THREE = [T1, T2, T3]

FAIL_RE = re.compile(r"^FAILED \S+::(\w+)", re.M)
ERROR_RE = re.compile(r"^\d+ error|\b\d+ error[s]?\b", re.M)
FAILED_RE = re.compile(r"\b\d+ failed\b")

_CANCEL_DESKTOP_ARM = """                if _is_cancelled(result):
                    # 用户按 Esc（桌面 hover）——这正是「只不显示红框」那一刻：
                    # agent 的 finally 已经 destroy 掉 overlay，此处必须收掉会话本身
                    return self._finish_cancelled(thread)
"""

_CANCEL_EXTENSION_ARM = """                if _is_cancelled(result):
                    # 用户按 Esc（网页侧）收掉整个会话——桌面 agent 也一并回收
                    return self._finish_cancelled(thread)
"""

# key: (old, new, nodeids, expect_failed{短名}, expect_label, hang_ok, desc)
CASES: dict[str, tuple[str, str, list[str], set[str], str, bool, str]] = {
    "x1": (
        _CANCEL_DESKTOP_ARM,
        "                pass  # INJECTED-M43 x1: 桌面腿的取消退回「腿失败」\n",
        [T1],
        {"test_hybrid_desktop_esc_cancels_whole_session"},
        "",
        True,
        "桌面腿的取消退回腿失败 ⇒ 判据不应只是红：旧行为下会话永不结束（timeout=inf）",
    ),
    "x2": (
        _CANCEL_EXTENSION_ARM,
        "                pass  # INJECTED-M43 x2: 扩展腿的取消退回「腿失败」\n",
        ALL_THREE,
        {"test_hybrid_extension_esc_cancels_whole_session"},
        "网页 Esc 必须收掉会话",
        False,
        "扩展腿的取消退回腿失败 ⇒ 只有网页 Esc 那条红（桌面 Esc 那条仍绿）",
    ),
    "x3": (
        '    return isinstance(payload, dict) and payload.get("cancelled") is True',
        "    return isinstance(payload, dict)  # INJECTED-M43 x3: 任何非描述符结果都当取消",
        ALL_THREE,
        {"test_hybrid_leg_failure_is_not_a_cancel"},
        "腿失败不是取消",
        False,
        "过度泛化（腿失败也当取消）⇒ **只有**防泛化那条红，两条取消用例仍绿",
    ),
    "x4": (
        "        self._extension.close()  # 撤防扩展：网页里的红框随 capture_disarm 下线",
        "        # INJECTED-M43 x4: 收场时不撤防扩展",
        ALL_THREE,
        {"test_hybrid_desktop_esc_cancels_whole_session"},
        "扩展腿必须被撤防",
        False,
        "收场不撤防另一条腿 ⇒ 只有「扩展仍 arm」那条红（网页里红框留在场上）",
    ),
    "x5": (
        """        self._cancel_desktop(thread)
        return {"cancelled": True}""",
        """        return {"cancelled": True}  # INJECTED-M43 x5: 不回收桌面 agent""",
        ALL_THREE,
        {
            "test_hybrid_desktop_esc_cancels_whole_session",
            "test_hybrid_extension_esc_cancels_whole_session",
        },
        "必须被回收",
        False,
        "收场不回收桌面 agent ⇒ 两条取消用例都红（子进程还在轮询全局按键）",
    ),
}


def _md5(path: pathlib.Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _run(nodeids: list[str], timeout: int) -> tuple[int | None, str]:
    """跑 pytest。返回 (退出码, 输出)；超时被终止返回 (None, 已捕获的输出)。"""
    cmd = [
        str(ROOT / ".venv" / "Scripts" / "python.exe"),
        "-m", "pytest", *nodeids,
        "-o", "addopts=", "-q", "--tb=short", "-p", "no:randomly",
    ]
    try:
        proc = subprocess.run(
            cmd, cwd=ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        partial = (exc.stdout or "") + (exc.stderr or "")
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", "replace")
        return None, partial
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _failed_names(out: str) -> set[str]:
    return set(FAIL_RE.findall(out))


def _verdict(code: int | None, out: str, case: tuple) -> tuple[bool, str]:
    _, _, _, expect_failed, label, hang_ok, _ = case
    if code is None:
        if hang_ok:
            return True, "挂住并被终止 —— 即旧行为的现场（会话永不结束，timeout=inf）"
        return False, "挂住（超时）—— 既不是红也不是绿，判据被破坏后走进了无限等待"
    # 「error」= 收集/语法/夹具错误，和「断言失败」同形不可混
    if ERROR_RE.search(out):
        return False, "出现 error —— 收集/夹具坏掉，是假红，不算判据命中"
    if not FAILED_RE.search(out):
        return False, "没红"
    got = _failed_names(out)
    if got != expect_failed:
        return False, (
            f"红名单不符：期望 {sorted(expect_failed)}，实际 {sorted(got)}"
            "（多/少都说明判据之间在互相顶替）"
        )
    if label and label not in out:
        return False, f"红了但没命中期望断言（期望含「{label}」）"
    return True, f"红名单精确 = {sorted(got)}" + (f" · 命中「{label}」" if label else "")


def main() -> int:
    clean_md5 = _md5(TARGET)
    backup = TARGET.read_bytes()

    lines: list[str] = [f"clean md5 hybrid.py = {clean_md5}", ""]
    if SENTINEL in TARGET.read_text(encoding="utf-8"):
        lines.append("ABORT：hybrid.py 里还有上轮的哨兵，先人工还原再跑")
        print("\n".join(lines))
        return 1

    verdicts: list[bool] = []

    # ① 对照：干净态三条判据必须全绿（否则「红了」可能是别的原因）
    code, out = _run(ALL_THREE, 180)
    green = code == 0 and "3 passed" in out
    verdicts.append(green)
    lines.append(f"[对照] 干净态三条判据：{'GREEN' if green else 'RED —— 先修干净态'}")
    if not green:
        lines.append("      " + out.strip().replace("\n", "\n      ")[-900:])
        report = "\n".join(lines)
        REPORT.write_text(report, encoding="utf-8", newline="\n")
        print(report)
        return 1
    lines.append("")

    for key, case in CASES.items():
        old, new, nodeids, _expect, _label, hang_ok, desc = case
        text = TARGET.read_text(encoding="utf-8")
        if old not in text:
            lines.append(f"[{key}] 锚点未命中 —— 文件已变，探针需同步")
            verdicts.append(False)
            continue
        TARGET.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")
        injected = SENTINEL in TARGET.read_text(encoding="utf-8")

        code, out = _run(nodeids, 30 if hang_ok else 180)
        hit, why = _verdict(code, out, case)

        TARGET.write_bytes(backup)
        healed = SENTINEL not in TARGET.read_text(encoding="utf-8")
        same = _md5(TARGET) == clean_md5

        verdicts.append(injected and hit and healed and same)
        lines.append(
            f"[{key}] {desc}\n"
            f"      写入={injected} 命中={hit}[{why}] 还原干净={healed} md5一致={same}"
        )
        if not hit:
            lines.append("      " + out.strip().replace("\n", "\n      ")[-900:])

    # ② 收尾：还原后再跑一次，必须绿（否则留下了坏状态）
    code, out = _run(ALL_THREE, 180)
    green = code == 0 and "3 passed" in out
    verdicts.append(green)
    lines.append(f"[收尾] 还原后复跑三条判据：{'GREEN' if green else 'RED —— 状态没还干净'}")
    if not green:
        lines.append("      " + out.strip().replace("\n", "\n      ")[-900:])

    ok = all(verdicts)
    lines.append("")
    lines.append(f"最终 md5 hybrid.py = {_md5(TARGET)} | 一致 = {_md5(TARGET) == clean_md5}")
    lines.append("")
    lines.append(
        "NEGATIVE VERIFICATION PASSED" if ok else "NEGATIVE VERIFICATION FAILED（看上面哪条没命中）"
    )
    report = "\n".join(lines)
    REPORT.write_text(report, encoding="utf-8", newline="\n")
    print(report)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
