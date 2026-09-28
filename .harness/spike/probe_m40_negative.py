"""M40 负向验证探针：逐处注入、确认对应判据**真的红**、再逐字节还原。

判据必须能失败才叫判据。这里针对 M40 的 8 处新增/修改判据，一路注入破坏，
跑**指定**的用例（不是整个文件），要求「精确命中」而不是「顺带崩一堆」。

三条防止「假绿灯机」的设计（本片真吃过亏，见下）：
1. **对照跑**：注入之前先用原始文件跑同一个 nodeid，必须**绿**。只看注入跑红、
   不看对照跑绿的话，一个「无论跑什么都红」的环境（语法坏、收集错、沙箱退出码
   污染）会让 7 处全部报 OK。
2. **失败类型判定**：注入跑必须出现 ``N failed`` 且**不出现** ``N error`` ——
   ``returncode != 0`` 既可能是断言失败，也可能是语法/收集错误，后者根本证明不了
   判据承重。
3. **超时按挂住处理**：判据被破坏后若走进真模态对话框就会永久阻塞（offscreen），
   那既不是「红」也不是「绿」，必须单独报出来修判据而不是硬等 600 秒。

还原安全网：注入前把原文另存到系统临时目录（``<tmp>/rpa_core_m40_originals/``，**不往仓库里塞源码副本**），
并注册 atexit 还原——探针被**硬杀**（Stop-Process）时 finally 不会执行，靠这两条兜底。

跑法：``uv run python .harness/spike/probe_m40_negative.py``
退出码 0 = 8 处全部「对照绿 → 注入精确红 → 逐字节还原」。
"""

from __future__ import annotations

import atexit
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKUP_DIR = Path(tempfile.gettempdir()) / "rpa_core_m40_originals"
REPORT = ROOT / "_m40_negative_report.txt"

# (文件, 原文, 注入后, 期望变红的 nodeid, 说明)
#
# 前两条针对 hybrid 的同一段逻辑，但**打的用例不同**——这是本片实测出来的分工：
# `test_hybrid_extension_without_ack_is_marked_dead` 对「acked 也要放行」这个子句
# **不敏感**（无 ack 场景下腿本来就该判死，摘掉子句它照样通过），它真正承重的是
# 「判死 + 如实标注未响应」；而 `not self._extension_armed()` 一旦摘掉，**已 ack 的腿**
# 也会在宽限期到点被判死，只有 `test_hybrid_extension_ack_keeps_leg_alive` 拦得住。
# 第一版探针把前者的期望 nodeid 写成 without-ack，结果「注入后仍绿」——正是这么发现的。
INJECTIONS: list[tuple[str, str, str, str, str]] = [
    (
        "src/rpa_core/capture/hybrid.py",
        "                self._extension_unresponsive = True\n",
        "",
        "tests/contract/test_capture_hybrid.py::test_hybrid_extension_without_ack_is_marked_dead",
        "判死但不标注「未响应」→ 用户拿到的是泛化失败而不是可行动原因",
    ),
    (
        "src/rpa_core/capture/hybrid.py",
        "                and not self._extension_armed()\n",
        "",
        "tests/contract/test_capture_hybrid.py::test_hybrid_extension_ack_keeps_leg_alive",
        "读侧：去掉「未 ack 即判死」→ 已 ack 的腿也在宽限期到点被判死",
    ),
    (
        "src/rpa_core/capture/extension.py",
        '                if message_type == "capture_armed":\n'
        "                    # ack：扩展真的收到了本会话的 arm（见 armed 属性）\n"
        "                    self._armed.set()\n"
        "                    continue\n",
        "",
        "tests/contract/test_capture_hybrid.py::test_hybrid_extension_ack_keeps_leg_alive",
        "写侧：不记录 capture_armed → 已确认的腿被误判死",
    ),
    (
        "src/rpa_core/gui/app.py",
        "        if self._capture_session is not None:\n"
        "            self._cancel_capture()\n"
        "            return\n",
        "        if self._capture_session is not None:\n"
        '            self.statusBar().showMessage("已有捕获会话进行中（Esc 取消）", 4000)\n'
        "            return\n",
        "tests/contract/test_gui_capture.py::test_capture_second_click_cancels_running_session",
        "第二次点击只提示、不取消 → 用户被锁到超时",
    ),
    (
        "src/rpa_core/gui/app.py",
        "            except Exception as exc:  # noqa: BLE001 - 会话异常也必须收场\n"
        "                # 少了这一步，finished 永不 emit → `_capture_session` 永不复位、主窗\n"
        "                # 永不还原：用户看到「点了没反应，再点说进行中」，只能重启进程。\n"
        '                result = {"error": f"捕获会话异常：{exc}"}\n',
        "",
        "tests/contract/test_gui_capture.py::test_capture_pick_exception_still_resets_and_restores",
        "去掉保底 emit → 异常后会话永不复位、主窗永不还原",
    ),
    (
        "src/rpa_core/gui/app.py",
        "        if (\n"
        "            session is not None\n"
        "            and self._capture_session is not None\n"
        "            and session is not self._capture_session\n"
        "        ):\n"
        "            return\n",
        "",
        "tests/contract/test_gui_capture.py::test_capture_late_result_from_replaced_session_is_ignored",
        "不拦迟到结果 → 旧会话收尾把新会话清掉",
    ),
    (
        "src/rpa_core/gui/home.py",
        "        if defer_refresh:\n",
        "        if False:\n",
        "tests/contract/test_gui_home.py::test_home_defer_refresh_skips_artifact_scan_at_construction",
        "defer_refresh 失效 → 构造期又同步扫产物",
    ),
    (
        "src/rpa_core/gui/home.py",
        "        self.refresh_history(runs)\n",
        "        self.refresh_history()\n",
        "tests/contract/test_gui_home.py::test_home_refresh_scans_artifacts_once",
        "两页签各扫一遍 → 启动路径重复全量读 result.json",
    ),
]

_FAILED_RE = re.compile(r"\b\d+ failed\b")
_ERROR_RE = re.compile(r"\b\d+ errors?\b")
_PASSED_RE = re.compile(r"\b\d+ passed\b")


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _restore_tree() -> None:
    """把备份目录里的原文全部写回（硬杀后的安全网）。"""
    if not BACKUP_DIR.is_dir():
        return
    for backup in BACKUP_DIR.glob("*.py"):
        target = ROOT / backup.name.replace("__", "/")
        if target.exists() and target.read_bytes() != backup.read_bytes():
            target.write_bytes(backup.read_bytes())


def _run(nodeid: str) -> tuple[int, str, bool]:
    """跑单个 nodeid。返回 (returncode, stdout, timed_out)。"""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", nodeid, "-o", "addopts=", "-q",
             "-p", "no:cacheprovider"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=240,
        )
    except subprocess.TimeoutExpired as exc:
        return -1, (exc.stdout or "") if isinstance(exc.stdout, str) else "", True
    return proc.returncode, (proc.stdout or "") + (proc.stderr or ""), False


def main() -> int:
    lines: list[str] = []
    failures: list[str] = []

    # 起手先做一次「上次被硬杀」的自愈
    _restore_tree()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    # 备份（每次运行覆盖，代表「本次认为的原文」）
    for path_text, *_ in INJECTIONS:
        path = ROOT / path_text
        shutil.copyfile(path, BACKUP_DIR / path_text.replace("/", "__"))
    atexit.register(_restore_tree)

    # 起手前置检查：注入锚点必须唯一；缺失说明文件已经被注入过或已被重构
    for path_text, old, _new, _nodeid, note in INJECTIONS:
        path = ROOT / path_text
        count = path.read_text(encoding="utf-8").count(old)
        if count != 1:
            msg = f"[SETUP-FAIL] {path_text}: 注入锚点出现 {count} 次（应为 1）— {note}"
            lines.append(msg)
            failures.append(path_text)
    if failures:
        lines.append("")
        lines.append("锚点检查失败，已中止（先还原源码再跑）。")
        _emit(lines)
        return 1

    for path_text, old, new, nodeid, note in INJECTIONS:
        path = ROOT / path_text
        original = path.read_bytes()
        before = _md5(path)

        # ---- 1) 对照跑：原始文件必须绿 ----
        control_rc, control_out, control_to = _run(nodeid)
        if control_to:
            failures.append(path_text)
            lines.append(f"[CONTROL-HANG] {note}\n        {nodeid}")
            continue
        if control_rc != 0 or not _PASSED_RE.search(control_out):
            failures.append(path_text)
            lines.append(
                f"[CONTROL-NOT-GREEN] 对照跑没绿，负向验证不可信 — {note}\n"
                f"        rc={control_rc} {nodeid}\n        {_tail(control_out)}"
            )
            continue

        # ---- 2) 注入 ----
        text = original.decode("utf-8")
        path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")
        syntax_ok = True
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        except SyntaxError as exc:
            syntax_ok = False
            lines.append(f"[SYNTAX-FAIL] 注入后语法不合法，验证没打到判据：{exc}")
        try:
            rc, out, timed_out = _run(nodeid) if syntax_ok else (-2, "", False)
        finally:
            path.write_bytes(original)
            after = _md5(path)

        if before != after:
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
                f"        {nodeid}"
            )
            continue
        is_failure = bool(_FAILED_RE.search(out)) and not _ERROR_RE.search(out)
        if rc != 0 and is_failure:
            lines.append(f"[OK  ] {note}\n        {nodeid}")
        else:
            failures.append(path_text)
            why = "假绿灯（没红）" if rc == 0 else "红得不对（不是测试失败）"
            lines.append(
                f"[{why}] {note}\n        rc={rc} {nodeid}\n        {_tail(out)}"
            )

    lines.append("")
    if failures:
        lines.append(f"NEGATIVE VERIFICATION FAILED（{len(failures)} 处）: {failures}")
    else:
        lines.append(
            f"NEGATIVE VERIFICATION PASSED（{len(INJECTIONS)} 处：对照绿 → 注入精确红 → 逐字节还原）"
        )
    _emit(lines)
    return 1 if failures else 0


def _tail(text: str, lines: int = 12) -> str:
    body = [ln for ln in text.strip().splitlines() if ln.strip()]
    return "\n        ".join(body[-lines:])


def _emit(lines: list[str]) -> None:
    report = "\n".join(lines)
    REPORT.write_text(report + "\n", encoding="utf-8", newline="\n")
    print(report)


if __name__ == "__main__":
    sys.exit(main())
