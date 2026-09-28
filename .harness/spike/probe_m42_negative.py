"""M42 负向验证探针：捕获链路「已打开页面可用 + 红框不留残影」的两条门禁各自有效。

被测文件：``extension/content.js``（实例接管守卫 / 僵尸自愈 / 失败路径收框）
          ``extension/background.js``（补注入 / 统一撤防 / host 断开兜底）
判据脚本：``scripts/check_capture_lifecycle.mjs``（22 项）
          ``scripts/check_capture_broadcast.mjs``（23 项）

为什么两处都要逐条注入：这两条门禁各钉住**一组互相独立的语义**，任何一条都可能
「看着在钉、其实恒绿」。本仓反复踩到两类假绿灯——① 判据写歪（拿形态断言冒充语义），
② 注入打偏（没打在承重的那行上）。所以每个 case 都要求：
  **对照绿 → 注入后精确变红（红的标签必须落在期望的那条）→ 失败类型必须是断言失败
  而不是脚本自己崩 → 逐字节还原核 md5**。

用法（探针自己用系统 node 跑门禁脚本）::

    .venv/Scripts/python.exe .harness/spike/probe_m42_negative.py

**别用 ``git checkout`` 还原**：两个被测文件都带着本轮未提交改动。
探针走内存备份还原，收尾按 md5 逐字节核对。
"""

from __future__ import annotations

import hashlib
import pathlib
import shutil
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONTENT = ROOT / "extension" / "content.js"
BACKGROUND = ROOT / "extension" / "background.js"
LIFECYCLE = ROOT / "scripts" / "check_capture_lifecycle.mjs"
BROADCAST = ROOT / "scripts" / "check_capture_broadcast.mjs"
REPORT = ROOT / "_m42_negative_report.txt"

SENTINEL = "INJECTED-M42"

# node 自己炸掉时输出这些（区分「判据红」与「脚本崩」——后者退出码也是 1，是假红）
JS_CRASH_MARKERS = ("TypeError:", "ReferenceError:", "SyntaxError:", "RangeError:", "\n    at ")

# key: (被测文件, 门禁脚本, 注入前锚点, 注入后文本, 期望失败的标签子串, 说明)
CASES: dict[str, tuple[pathlib.Path, pathlib.Path, str, str, str, str]] = {
    # ---- content.js：实例生命周期 ----
    "c1": (
        CONTENT, LIFECYCLE,
        "    if (previous.alive()) return;",
        "    return;  // INJECTED-M42 c1",
        "S4 接管：新实例已登记且是活的",
        "守卫退回「装过就拒绝」：僵尸实例把补注入的新脚本挡在门外",
    ),
    "c2": (
        CONTENT, LIFECYCLE,
        """    try {
      previous.teardown();
    } catch { /* 僵尸实例拆不动（上下文已失效）：继续接管，至少新实例能工作 */ }""",
        "    // INJECTED-M42 c2: 不拆僵尸，直接接管",
        "S4 接管：旧监听器被摘干净",
        "不拆僵尸：旧实例的监听器留在页面上继续抢手势/画框",
    ),
    "c3": (
        CONTENT, LIFECYCLE,
        """    for (const [target, type, handler, options] of bound.splice(0)) {
      try {
        target.removeEventListener(type, handler, options);
      } catch { /* 页面已卸载：摘不掉也无所谓 */ }
    }""",
        "    // INJECTED-M42 c3: teardown 不摘监听器",
        "S4 接管：旧监听器被摘干净",
        "teardown 只清覆盖层不摘监听：僵尸实例的监听器叠加成两份",
    ),
    "c4": (
        CONTENT, LIFECYCLE,
        "    if (!isAlive()) { hideOverlay(); return; }",
        "    // INJECTED-M42 c4: 僵尸状态照画不误",
        "S3 扩展重载后鼠标移动：不再留框",
        "僵尸实例继续画框：红框永久赖在页面上（维护者报障的正面）",
    ),
    "c5": (
        CONTENT, LIFECYCLE,
        """      hideBox();
      setHint("扩展已重载 · 请刷新本页（⌘/Ctrl+R）后重新捕获");""",
        """      setHint("扩展已重载 · 请刷新本页（⌘/Ctrl+R）后重新捕获");  // INJECTED-M42 c5""",
        "S5 通道失效时点捕获：红框被收掉",
        "失败路径退回「保留红框 + 提示」：通道断了没人会再来清它",
    ),
    # ---- background.js：可达性与撤防 ----
    "b1": (
        BACKGROUND, BROADCAST,
        """    if (!armed || !(await ensureContentScript(tab))) continue;
    await armTab(tab.id, armed);   // 补注入成功：把这次 arm 送给刚接管的新脚本""",
        "    // INJECTED-M42 b1: 不补注入",
        "S1 首次 arm 失败后补注入 content.js",
        "broadcast 退回只发消息：已打开的标签页永远拿不到 arm",
    ),
    "b2": (
        BACKGROUND, BROADCAST,
        "    if (!armed || !(await ensureContentScript(tab))) continue;",
        "    if (!(await ensureContentScript(tab))) continue;  // INJECTED-M42 b2",
        "S4 撤防不补注入",
        "撤防也补注入：每个页面被白塞一遍脚本",
    ),
    "b3": (
        BACKGROUND, BROADCAST,
        '  if (!/^(https?|file):\\/\\//i.test(tab.url || "")) return false;   // 受保护页面注入必失败',
        "  // INJECTED-M42 b3: 不做协议白名单",
        "S3 受保护页面不做无谓注入",
        "去掉协议白名单：受保护页面每次 arm 都白跑一趟注入",
    ),
    "b4": (
        BACKGROUND, BROADCAST,
        "  setCaptureArmed(false).then(() => broadcast(false));",
        "  broadcast(false);  // INJECTED-M42 b4: 不落盘",
        "S6 撤防：捕获态落盘置假",
        "撤防不落盘：新开的页面查态会查到一个永远为真的 armed",
    ),
    "b5": (
        BACKGROUND, BROADCAST,
        """    ...descriptor,
  });
  disarmCapture();""",
        """    ...descriptor,
  });  // INJECTED-M42 b5: 回传后不撤防""",
        "S7 回传后立刻撤防",
        "捕获成功后不撤防：扩展与页面都停在捕获态",
    ),
    "b6": (
        BACKGROUND, BROADCAST,
        """      disarmCapture();
      scheduleReconnect();""",
        "      scheduleReconnect();  // INJECTED-M42 b6",
        "C1 port.onDisconnect 回调里必须撤防",
        "host 断开不撤防：推送模型没有心跳，红框永远清不掉",
    ),
    "b7": (
        BACKGROUND, BROADCAST,
        "      setCaptureArmed(true).then(() => broadcast(true));",
        "      broadcast(true); setCaptureArmed(true);  // INJECTED-M42 b7",
        "C2 落盘早于广播",
        "先广播后落盘：补注入的脚本在窗口期内查态读到 false，arm 在这一页丢失",
    ),
    "b8": (
        BACKGROUND, BROADCAST,
        '      files: ["content.js"],',
        '      files: ["content-script.js"],  // INJECTED-M42 b8',
        "P1 补注入确实用了它",
        "补注入的文件名与 manifest 声明脱钩（改名即静默失效）",
    ),
}


def _md5(path: pathlib.Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _node() -> str:
    found = shutil.which("node")
    if not found:
        raise SystemExit("找不到 node：门禁脚本需要它")
    return found


def _run(script: pathlib.Path) -> tuple[int | None, str]:
    """跑门禁脚本。返回 (退出码, 输出)；超时返回 (None, "TIMEOUT")。"""
    try:
        proc = subprocess.run(
            [_node(), str(script)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
        )
    except subprocess.TimeoutExpired:
        return None, "TIMEOUT"
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _verdict(code: int | None, out: str, expect_label: str) -> tuple[bool, str]:
    if code is None:
        return False, "挂住（超时）"
    crash = next((m for m in JS_CRASH_MARKERS if m in out), None)
    if crash:
        return False, f"脚本自崩（{crash}）—— 是假红，不算判据命中"
    failed_labels = [ln for ln in out.splitlines() if ln.startswith("FAIL |")]
    if code == 0 or not failed_labels:
        return False, "没红"
    if expect_label not in out:
        return False, f"红了但没命中期望断言（实红 {len(failed_labels)} 条）"
    return True, f"红 {len(failed_labels)} 条 · 命中「{expect_label}」"


def main() -> int:
    clean = {CONTENT: _md5(CONTENT), BACKGROUND: _md5(BACKGROUND)}
    backups = {path: path.read_bytes() for path in clean}

    lines: list[str] = [f"clean md5 content.js = {clean[CONTENT]}",
                        f"clean md5 background.js = {clean[BACKGROUND]}", ""]
    for path in clean:
        if SENTINEL in path.read_text(encoding="utf-8"):
            lines.append(f"ABORT：{path.name} 里还有上轮的哨兵，先人工还原再跑")
            print("\n".join(lines))
            return 1

    # ① 对照：干净态两条门禁都必须全绿（否则「红了」可能是别的原因）
    verdicts: list[bool] = []
    for script in (LIFECYCLE, BROADCAST):
        code, out = _run(script)
        green = code == 0 and "全部通过" in out
        verdicts.append(green)
        lines.append(f"[对照] {script.name} 干净态：{'GREEN' if green else 'RED —— 先修干净态'}")
        if not green:
            lines.append("      " + out.strip().replace("\n", "\n      ")[-800:])
    lines.append("")

    for key, (target, script, old, new, label, desc) in CASES.items():
        text = target.read_text(encoding="utf-8")
        if old not in text:
            lines.append(f"[{key}] 锚点未命中 —— 文件已变，探针需同步")
            verdicts.append(False)
            continue
        target.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")
        injected = SENTINEL in target.read_text(encoding="utf-8")

        code, out = _run(script)
        red, why = _verdict(code, out, label)

        target.write_bytes(backups[target])
        healed = SENTINEL not in target.read_text(encoding="utf-8")
        same = _md5(target) == clean[target]

        verdicts.append(injected and red and healed and same)
        lines.append(
            f"[{key}] {desc}  （{target.name} → {script.name}）\n"
            f"      写入={injected} 判据红={red}[{why}] 还原干净={healed} md5一致={same}"
        )
        if not red:
            lines.append("      " + out.strip().replace("\n", "\n      ")[-900:])

    # ② 收尾：还原后再跑一次，必须绿（否则留下了坏状态）
    for script in (LIFECYCLE, BROADCAST):
        code, out = _run(script)
        green = code == 0 and "全部通过" in out
        verdicts.append(green)
        lines.append(f"[收尾] {script.name} 还原后复跑：{'GREEN' if green else 'RED —— 状态没还干净'}")

    ok = all(verdicts)
    lines.append("")
    lines.append(f"最终 md5 content.js = {_md5(CONTENT)} | 一致 = {_md5(CONTENT) == clean[CONTENT]}")
    lines.append(
        f"最终 md5 background.js = {_md5(BACKGROUND)} | 一致 = {_md5(BACKGROUND) == clean[BACKGROUND]}"
    )
    lines.append("")
    lines.append(
        "NEGATIVE VERIFICATION PASSED" if ok else "NEGATIVE VERIFICATION FAILED（看上面哪条没命中）"
    )
    report = "\n".join(lines)
    REPORT.write_text(report, encoding="utf-8", newline="\n")
    print(report)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
