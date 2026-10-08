"""负向验证：「校验元素」取页口径 + 双浏览器「都闪框」修复（2026-10-08）。

**问题一（取页口径）**：真机 trace 抓到同一条 css 的回复一拨来自 explore 页
（count 0）、一拨来自 search_result_ai 页（count 1）——计数抖动其实是**打到了
哪一页**在抖。根因是 ``runVerify`` 用
``tabs.query({active:true, lastFocusedWindow:true})`` 取页，而 ``lastFocusedWindow``
是**窗口**级的「最近聚焦」记忆，在 GUI 抢焦点 / 多窗口下会失准。修法：新增可切片
的纯函数 ``pickVerifyTab()``，真前台窗口优先 → 最近聚焦兜底 → 任一活跃页。

**问题二（双浏览器都闪框）**：维护者报「Chrome 与 Edge 同开同样页面，校验时两个
页面都闪黄框，虽然命中数只算 1 个」。根因是 host 侧 ``ElementVerifier._exchange``
把 ``capture_verify`` 广播给**全部在线端点**（本机实测 chrome + msedge 两个）。修法：
扩展自判「本浏览器是否 OS 级前台」——非前台则不闪框（``silent``）且延后 120ms 应答，
把「首个应答」让给前台那个（host 认首个回传）。

本探针按仓库负向验证四条规矩办：
  ① 每处注入前先确认**原始文件绿**（跑一次对照）；
  ② 注入后必须 ``N failed`` 且**不得**出现 ``N error``（区分断言失败与语法/收集错误）；
  ③ 超时判「挂住」要报出，不算通过；
  ④ 逐字节还原核 md5。
且每处注入的期望**必须落在具体的断言上**（nodeid / 断言文本），不能只判「退出码非 0」
——那等于一台「无论跑什么都报红」的假绿灯机（M40 首版探针的教训）。

**注意**：node 门禁的失败判据以**退出码 + 期望文本**为准，不只看汇总行——注入把
切片打成语法错误时门禁会 ``process.exit(1)`` 却**不打印**「N 项失败」（首版探针栽过）。

用法：
    ./.venv/Scripts/python.exe .harness/spike/probe_verify_target_negative.py
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
# 哨兵用**目标文件的注释语法**：`#` 在 JS 里是语法错误（首版探针栽在这——注入把切片
# 打成语法错误，门禁 `new Function` 抛异常、退出码 1 却不打印「N 项失败」，只数汇总行
# 会误判成绿）。
SENTINEL_JS = "// [VERIFY-TARGET-NEGATIVE-INJECTED]"
SENTINEL_PY = "# [VERIFY-TARGET-NEGATIVE-INJECTED]"

BG = REPO / "extension" / "background.js"
CT = REPO / "extension" / "content.js"
CHK = REPO / "scripts" / "check_verify.mjs"
MANIFEST = REPO / "extension" / "manifest.json"

NODE = os.environ.get("RPA_NODE") or shutil.which("node") or "node"
PY = REPO / ".venv" / "Scripts" / "python.exe"


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _restore(path: Path, backup: Path) -> None:
    path.write_bytes(backup.read_bytes())


def run_node() -> subprocess.CompletedProcess:
    return subprocess.run(
        [NODE, str(CHK)],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


def run_pytest_three_way() -> subprocess.CompletedProcess:
    """EXT_BUILD 三方一致判据（Python 侧）。

    **不能再传 `-q`**：`pyproject.toml` 的 `addopts` 已经有一个，两个叠加成 `-qq`
    ——pytest 的 `-qq` 会把汇总行（`1 failed, 2 passed`）一起吞掉，只剩 `F..` 与
    失败详情。探针首版因此把「确实红了」误判成 failed=0（2026-10-08 实测）。
    """
    return subprocess.run(
        [
            str(PY),
            "-m",
            "pytest",
            "tests/contract/test_capture_extension.py",
            "-k",
            "build",
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )


def _failed_count(out: str) -> int:
    """node 门禁自己的汇总行。"""
    import re

    match = re.search(r"(\d+) 项失败", out)
    return int(match.group(1)) if match else 0


def parse_counts(out: str) -> tuple[int, int]:
    """从 pytest 汇总行取 (failed, error)。"""
    import re

    failed = error = 0
    match = re.search(r"(\d+) failed", out)
    if match:
        failed = int(match.group(1))
    match = re.search(r"(\d+) error", out)
    if match:
        error = int(match.group(1))
    return failed, error


class Injection:
    def __init__(self, label: str, path: Path, runner: str, expect_in: str):
        self.label = label
        self.path = path
        self.runner = runner            # "node" | "pytest"
        self.expect_in = expect_in      # 期望变红时输出里必须出现的文本


INJECTIONS: list[Injection] = []


def add(label: str, path: Path, runner: str, expect_in: str) -> None:
    INJECTIONS.append(Injection(label, path, runner, expect_in))


# ---- 注入清单 ---------------------------------------------------------------
add(
    "N1 pickVerifyTab 退回 lastFocusedWindow 单链（原 bug 复现）",
    BG,
    "node",
    "W6 真前台窗口的活跃页胜过",
)
add(
    "N2 取页不再限定 focused 窗口（真前台优先失效）",
    BG,
    "node",
    "W6 未退化成按 lastFocusedWindow 取页",
)
add(
    "N3 去掉 lastFocusedWindow 兜底腿（Chrome 不在前台即无页）",
    BG,
    "node",
    "W7 无前台窗口 → 回退最近聚焦窗口的活跃页",
)
add(
    "N4 windows API 抛错直接穿透（兜底链失效）",
    BG,
    "node",
    "W8 windows API 抛错仍能取到页",
)
add(
    "N5 EXT_BUILD 只改 background 不改 manifest（三方一致破裂）",
    BG,
    "pytest",
    "构建标识三方不一致",
)
# ---- 双浏览器「都闪框」修复（2026-10-08 维护者报障）--------------------------
add(
    "N6 非前台判定恒 true（双浏览器又都闪框——原 bug 复现）",
    BG,
    "node",
    "W11 非前台浏览器不下发闪框请求",
)
add(
    "N7 silent 不再透传给 content（非前台也会画框）",
    BG,
    "node",
    "W11 回传带 silent:true",
)
add(
    "N8 content 忽略 silent（收到也不当回事，照闪）",
    CT,
    "node",
    "C1 silent 时跳过 flashElements",
)


def apply_n1(src: str) -> str:
    old = """async function pickVerifyTab() {
  try {
    const windows = await chrome.windows.getAll({ populate: false });
    const focused = windows.find((win) => win.focused && win.id != null);
    if (focused) {
      const tabs = await chrome.tabs.query({ active: true, windowId: focused.id });
      if (tabs.length) return tabs[0] || null;
    }
  } catch {
    // windows API 不可用/无窗口：退回下面的兜底链，不让取页失败变成校验失败
  }
  let tabs = await chrome.tabs.query({ active: true, lastFocusedWindow: true });"""
    new = f"""async function pickVerifyTab() {{ {SENTINEL_JS}
  let tabs = await chrome.tabs.query({{ active: true, lastFocusedWindow: true }});"""
    assert old in src, "N1 锚点未命中"
    return src.replace(old, new, 1)


def apply_n2(src: str) -> str:
    old = "const focused = windows.find((win) => win.focused && win.id != null);"
    new = f"const focused = windows[0]; {SENTINEL_JS}"
    assert old in src, "N2 锚点未命中"
    return src.replace(old, new, 1)


def apply_n3(src: str) -> str:
    old = """  let tabs = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (!tabs.length) tabs = await chrome.tabs.query({ active: true });
  return tabs[0] || null;"""
    new = f"""  const tabs = await chrome.tabs.query({{ active: true }}); {SENTINEL_JS}
  return tabs[0] || null;"""
    assert old in src, "N3 锚点未命中"
    return src.replace(old, new, 1)


def apply_n4(src: str) -> str:
    old = """  } catch {
    // windows API 不可用/无窗口：退回下面的兜底链，不让取页失败变成校验失败
  }"""
    new = f"""  }} catch (err) {{ {SENTINEL_JS}
    throw err;
  }}"""
    assert old in src, "N4 锚点未命中"
    return src.replace(old, new, 1)


def apply_n6(src: str) -> str:
    """非前台判定失效 ⇒ 双浏览器又都闪框（原 bug 复现）。"""
    old = """    const win = await chrome.windows.getLastFocused();
    return !!win.focused;"""
    new = f"""    const win = await chrome.windows.getLastFocused(); {SENTINEL_JS}
    return true;"""
    assert old in src, "N6 锚点未命中"
    return src.replace(old, new, 1)


def apply_n7(src: str) -> str:
    """silent 不再回传给 host ⇒ 断言读不到 silent:true（W11 红）。"""
    old = '    post({ ...reply, ...(resp || {}), silent, url: tab.url || "" });'
    new = f'    post({{ ...reply, ...(resp || {{}}), url: tab.url || "" }}); {SENTINEL_JS}'
    assert old in src, "N7 锚点未命中"
    return src.replace(old, new, 1)


def apply_n8(src: str) -> str:
    """content 忽略 silent ⇒ 非前台也画框（C1 红）。"""
    old = "    if (!silent && !reply.error && reply.count > 0) {"
    new = f"    if (!reply.error && reply.count > 0) {{ {SENTINEL_JS}"
    assert old in src, "N8 锚点未命中"
    return src.replace(old, new, 1)


def main() -> int:
    if not BG.exists() or not CHK.exists():
        print("FAIL: 仓库路径不对（找不到 extension/background.js 或 scripts/check_verify.mjs）")
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="rpa-verify-target-neg-"))
    backups = {
        BG: tmp / "background.js",
        CT: tmp / "content.js",
        CHK: tmp / "check_verify.mjs",
        MANIFEST: tmp / "manifest.json",
    }
    for src, dst in backups.items():
        dst.write_bytes(src.read_bytes())
    originals = {p: _md5(p) for p in backups}

    def restore_all() -> None:
        for src, dst in backups.items():
            src.write_bytes(dst.read_bytes())

    print("=== 对照（注入前）===")
    control = run_node()
    if control.returncode != 0 or _failed_count(control.stdout) != 0:
        print("FAIL: 对照未绿，先修再谈负向验证")
        print(control.stdout)
        print(control.stderr)
        restore_all()
        return 1
    print("对照绿（node 门禁 0 失败）")

    failures = 0
    for idx, inj in enumerate(INJECTIONS, start=1):
        print(f"\n=== {idx}/{len(INJECTIONS)} {inj.label} ===")
        try:
            src = inj.path.read_text(encoding="utf-8")
            if inj.label.startswith("N1"):
                mutated = apply_n1(src)
            elif inj.label.startswith("N2"):
                mutated = apply_n2(src)
            elif inj.label.startswith("N3"):
                mutated = apply_n3(src)
            elif inj.label.startswith("N4"):
                mutated = apply_n4(src)
            elif inj.label.startswith("N5"):
                mutated = src.replace(
                    'const EXT_BUILD = "0.6.3";',
                    f'const EXT_BUILD = "9.9.9"; {SENTINEL_JS}',
                    1,
                )
                assert mutated != src, "N5 锚点未命中"
            elif inj.label.startswith("N6"):
                mutated = apply_n6(src)
            elif inj.label.startswith("N7"):
                mutated = apply_n7(src)
            elif inj.label.startswith("N8"):
                mutated = apply_n8(src)
            else:
                raise AssertionError(f"未知注入 {inj.label}")
            inj.path.write_bytes(mutated.encode("utf-8"))

            if inj.runner == "node":
                try:
                    proc = run_node()
                except subprocess.TimeoutExpired:
                    print("FAIL: 门禁挂住（超时）——不是红也不是绿，按失败计")
                    failures += 1
                    restore_all()
                    continue
                # **退出码也是判据**：锚点定位失败 / 切片求值抛异常时门禁会
                # `process.exit(1)` 且**不打印**「N 项失败」——只数汇总行会把
                # 「切片语法被注入打坏」误判成绿（本探针首版就栽在这，实测抓到）。
                combined = proc.stdout + proc.stderr
                now_failed = _failed_count(proc.stdout)
                ok = proc.returncode != 0 and inj.expect_in in combined
                print(f"  注入后 exit={proc.returncode} 失败数={now_failed}"
                      f"（期望 exit!=0 且含「{inj.expect_in}」）")
                if not ok:
                    print("  ✗ 未按预期变红")
                    print(combined[-2500:])
                    failures += 1
            else:
                try:
                    proc = run_pytest_three_way()
                except subprocess.TimeoutExpired:
                    print("FAIL: pytest 挂住（超时）")
                    failures += 1
                    restore_all()
                    continue
                combined = proc.stdout + proc.stderr
                failed_n, error_n = parse_counts(combined)
                # 判据以**退出码 + 期望文本**为准（不依赖汇总行是否被 -qq 吞掉）；
                # error 计数只用来保证「红的是断言失败而非收集/语法错误」。
                ok = (
                    proc.returncode != 0
                    and inj.expect_in in combined
                    and error_n == 0
                )
                print(f"  pytest exit={proc.returncode} failed={failed_n} error={error_n}"
                      f"（要求 exit!=0、含期望文本、error==0）")
                if not ok:
                    print("  ✗ 未按预期变红")
                    print(combined[-2500:])
                    failures += 1
        finally:
            restore_all()

        # 逐字节还原核 md5
        for p, want in originals.items():
            got = _md5(p)
            if got != want:
                print(f"  ✗ 还原失败 {p.name}: {got} != {want}")
                failures += 1

    print("\n=== 复绿 ===")
    try:
        final = run_node()
    except subprocess.TimeoutExpired:
        print("FAIL: 复绿门禁挂住")
        failures += 1
        final = None
    if final is not None:
        if final.returncode != 0 or _failed_count(final.stdout) != 0:
            print("FAIL: 还原后门禁未复绿")
            print(final.stdout)
            failures += 1
        else:
            print("还原后门禁复绿")

    print(f"\n{'全部通过' if failures == 0 else f'{failures} 处失败'}")
    return 1 if failures else 0


def _atexit_guard() -> None:
    """收尾保险：异常/中断退出时按**哨兵**还原注入行（M40 探针事故的教训）。

    只还原带哨兵的行——用陈旧备份整体覆盖会盖掉本轮已改好的文件（M40 真事故）。
    两条还原路径：① 行尾哨兵（``...; // [SENTINEL]``）→ 行内删除；② 整行哨兵。
    """
    for path in (BG, CT, CHK, MANIFEST):
        try:
            src = path.read_text(encoding="utf-8")
        except OSError:
            continue
        hit = SENTINEL_JS if path.suffix == ".js" else SENTINEL_PY
        if hit not in src:
            continue
        lines = [ln for ln in src.splitlines() if hit not in ln]
        path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))


if __name__ == "__main__":
    import atexit

    atexit.register(_atexit_guard)
    sys.exit(main())
