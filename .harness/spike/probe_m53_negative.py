"""M53 负向验证探针：getText / getPosition / getScrollPosition 参数缺口补全。

判据有**两层**，注入必须逐层打中：

- **J1 = JS 层** `node scripts/check_url_prefix_helpers.mjs`（补前缀纯函数 + 接线断言）
  —— 「智能识别并补充地址前缀」的字符串语义、以及它到底接没接上（只对 href 生效、
  三个入参传对、attribute 分支读 attributeName）。这些只在浏览器里跑。
- **J2 = host 层** `pytest tests/commands/test_browser_matrix.py -k "getText or getPosition
  or getScrollPosition"`（扩展返回信封 → outputs 整形）——八项位置输出派生、
  滚动条口径（轴向 × 当前/底部）、getText 三参数转发。

**期望失败集**（I1~I4 → {J1}；I5~I7 → {J2}）：两层判据各自精确命中、互不顶替，
任一注入若跨层连带（或漏红）都要查清。

受控文件（注入后逐字节还原 + 核 md5）：
`extension/background.js`、`src/rpa_core/executors/browser.py`

为何不用 `| head` / 管道：输出量大时进程会在 print 处 BrokenPipe 被打断，
`restore_all()` 不执行 ⇒ 哨兵留在源码里（2026-09-27 真事故）。本探针输出重定向到
文件再读，且**起手先自愈**（哨兵 + 唯一凭证备份）。

运行：./.venv/Scripts/python.exe .harness/spike/probe_m53_negative.py
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKUP_DIR = ROOT / ".harness" / "spike" / "_nv_m53"
SENTINEL = "NV-M53-INJECTED"

CONTROLLED = (
    "extension/background.js",
    "src/rpa_core/executors/browser.py",
    "src/rpa_core/gui/param_form.py",
)


def bak_path(rel: str) -> Path:
    return BACKUP_DIR / (rel.replace("/", "__") + ".orig")


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def heal_and_refresh_backups() -> None:
    """起手自愈 + 刷新备份。

    顺序不能颠倒：**先**确认源码干净（带哨兵就从唯一凭证备份还原），**再**刷新备份——
    否则会把「带哨兵的工作区」当成干净基线备份下来，此后每次还原都是脏的。
    """
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    for rel in CONTROLLED:
        src = ROOT / rel
        bak = bak_path(rel)
        if bak.exists():
            old = bak.read_bytes()
            if SENTINEL.encode() in old:
                raise SystemExit(f"备份被污染（含哨兵），拒绝继续：{bak}")
        current = src.read_bytes()
        if SENTINEL.encode() in current:
            if not bak.exists():
                raise SystemExit(f"{rel} 带哨兵且无备份，需人工清理后再跑（不自动猜原文）")
            src.write_bytes(bak.read_bytes())
            print(f"自愈：{rel} 已按唯一凭证备份还原")
    # 走到这里所有受控文件都干净 → 刷新备份
    for rel in CONTROLLED:
        bak_path(rel).write_bytes((ROOT / rel).read_bytes())


def inject(rel: str, old: str, new: str) -> None:
    """把 `old` 换成 `new` + 本行尾哨兵注释。锚点未命中一律硬失败（静默注入最坑）。"""
    path = ROOT / rel
    text = path.read_text(encoding="utf-8")
    assert old in text, f"锚点未命中：{rel} :: {old[:60]!r}"
    assert text.count(old) == 1, f"锚点不唯一（{text.count(old)} 处）：{rel} :: {old[:60]!r}"
    comment = "//" if rel.endswith(".js") else "#"
    patched = text.replace(old, f"{new}  {comment} {SENTINEL}")
    # 哨兵必须落在「本行尾」，绝不吞掉下一行：断言被替换的那一行确实以哨兵结尾
    assert patched != text, f"注入未产生变化：{rel}"
    for line in patched.split("\n"):
        if SENTINEL in line:
            assert line.rstrip().endswith(SENTINEL), f"哨兵未落在行尾（会污染下一行）：{line!r}"
    path.write_text(patched, encoding="utf-8", newline="\n")


def restore_all() -> None:
    for rel in CONTROLLED:
        src = ROOT / rel
        src.write_bytes(bak_path(rel).read_bytes())


def run(cmd: list[str], *, env_extra: dict[str, str] | None = None) -> tuple[int, str]:
    import os

    env = dict(os.environ)
    env.update(env_extra or {})
    completed = subprocess.run(
        cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=600
    )
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
NODE = shutil.which("node")


def j1() -> tuple[bool, str]:
    """JS 层：rc != 0 才算红（门禁失败可能只打 rc 不打印汇总行，故以 rc 为准）。"""
    rc, out = run([NODE, "scripts/check_url_prefix_helpers.mjs"])
    return rc != 0, out


def j2() -> tuple[bool, str]:
    """host 层：要求 failed >= 1（rc != 0 分不清断言失败与收集/语法错误）。"""
    rc, out = run(
        [PY, "-m", "pytest", "tests/commands/test_browser_matrix.py",
         "-k", "getText or getPosition or getScrollPosition"],
        env_extra={"RPA_COMMAND_MATRIX": "1"},
    )
    red = rc != 0 or "failed" in out
    return red, out


#: J3 的两条 nodeid：新判据（起勾按默认 + 等于默认不写）与它要保护的「两端相等」判据。
#: 之所以列两条：`values()` 那一处注入应当**同时**让两条红（同一症状的两个观测面），
#: 而「起勾」那一处只该让新判据红——两份都能区分「谁在管什么」。
_J3_NODES = [
    "tests/contract/test_gui_param_form.py"
    "::test_bool_defaults_seed_the_checkbox_and_stay_out_of_values",
    "tests/contract/test_gui_panels.py"
    "::test_param_form_element_pick_matches_insert_element_output",
]


def j3() -> tuple[bool, str]:
    """GUI 层：布尔字段的「起勾」与「写不写进参数」。"""
    rc, out = run([PY, "-m", "pytest", *_J3_NODES])
    red = rc != 0 or "failed" in out
    return red, out


def failed_names(out: str) -> list[str]:
    """从 pytest 输出里取 `FAILED <nodeid>` 的**用例名**（逐条精确到断言面）。

    只看 rc 分不清「哪一条红」；靠汇总行则会被 teardown / 环境问题掩盖
    （`FAILED` 行是跑的过程中逐条打的，先于收尾）。
    """
    names = []
    for line in out.split("\n"):
        stripped = line.strip()
        if stripped.startswith("FAILED "):
            # 形如 `FAILED tests/x.py::test_f[a::b]`：取末段并去掉参数化尾括号
            names.append(stripped.split("::")[-1].split()[0].rstrip("]"))
    return sorted(names)


def summarize(out: str) -> str:
    for line in reversed(out.strip().split("\n")):
        if line.strip():
            return line.strip()[:160]
    return "<空输出>"


INJECTIONS = [
    (
        "I1",
        "JS：把「相对引用用浏览器解析值」改成常量（/next 会被拼坏）",
        "extension/background.js",
        '    if (/^[./#?]/.test(text)) return String(resolved || text);',
        '    if (/^[./#?]/.test(text)) return String("__nv__");',
        {"J1"},
    ),
    (
        "I2",
        "JS：补前缀的协议选择反了（页面是 http 却补 https）",
        "extension/background.js",
        '    const scheme = String(pageProtocol) === "http:" ? "http:" : "https:";',
        '    const scheme = String(pageProtocol) === "http:" ? "https:" : "http:";',
        {"J1"},
    ),
    (
        "I3",
        "JS：补前缀接线放宽到所有 infoType（不止 href）",
        "extension/background.js",
        '      if (info === "href" && args.completeUrlPrefix) {',
        "      if (args.completeUrlPrefix) {",
        {"J1"},
    ),
    (
        "I4",
        "JS：attribute 分支写死属性名、不读 attributeName",
        "extension/background.js",
        '        result = el.getAttribute(String(args.attributeName || "")) || "";',
        '        result = el.getAttribute("data-x") || "";',
        {"J1"},
    ),
    (
        "I5",
        "host：滚动条「底部位置」的轴向取反（纵向取 scrollWidth）",
        "src/rpa_core/executors/browser.py",
        '        return _as_number(metrics.get("scrollWidth" if horizontal else "scrollHeight"))',
        '        return _as_number(metrics.get("scrollHeight" if horizontal else "scrollWidth"))',
        {"J2"},
    ),
    (
        "I6",
        "host：八项输出里的 right 不派生（直接等于 x）",
        "src/rpa_core/executors/browser.py",
        '                        "right": pos_x + size_w,',
        '                        "right": pos_x,',
        {"J2"},
    ),
    (
        "I7",
        "host：getText 读了 completeUrlPrefix 但不转发给扩展（接线断）",
        "src/rpa_core/executors/browser.py",
        '                    args={\n'
        '                        "infoType": info_type,\n'
        '                        "attributeName": attribute_name,\n'
        '                        "completeUrlPrefix": complete_url_prefix,\n'
        "                    },",
        '                    args={\n'
        '                        "infoType": info_type,\n'
        '                        "attributeName": attribute_name,\n'
        '                        "completeUrlPrefix": False,\n'
        "                    },",
        {"J2"},
    ),
    (
        "I8",
        "GUI：布尔起勾退回一律 False（默认 true 的字段显示成没勾）",
        "src/rpa_core/gui/param_form.py",
        '            widget.setChecked(bool(args.get(name, field_schema.get("default", False))))',
        "            widget.setChecked(bool(args.get(name, False)))",
        {"J3"},
    ),
    (
        "I9",
        "GUI：values() 又无条件写入布尔（等于默认也写）",
        "src/rpa_core/gui/param_form.py",
        "                if name in self._bool_defaults and self._bool_defaults[name] is checked:\n"
        "                    continue\n"
        "                result[name] = checked",
        "                result[name] = checked",
        {"J3"},
    ),
]


def main() -> int:
    if NODE is None:
        raise SystemExit("PATH 里找不到 node，J1 无法运行")

    heal_and_refresh_backups()
    clean_md5 = {rel: md5((ROOT / rel).read_bytes()) for rel in CONTROLLED}

    # 对照跑：三条判据都必须绿，且 J2/J3 必须真的跑了用例（防 0 收集的假对照）
    j1_red, j1_out = j1()
    j2_red, j2_out = j2()
    j3_red, j3_out = j3()
    print(f"对照 J1 rc-red={j1_red} | {summarize(j1_out)}")
    print(f"对照 J2 rc-red={j2_red} | {summarize(j2_out)}")
    print(f"对照 J3 rc-red={j3_red} | {summarize(j3_out)}")
    if j1_red or j2_red or j3_red:
        raise SystemExit("对照跑未全绿，先修基线再谈负向验证")
    if "passed" not in j2_out:
        raise SystemExit("J2 对照跑没有 passed 汇总行：选择器可能没命中（0 收集）")
    if "2 passed" not in j3_out:
        raise SystemExit(f"J3 对照跑没跑满 2 条：{summarize(j3_out)}")

    failures: list[str] = []
    problems: list[str] = []
    try:
        for iid, desc, rel, old, new, expected in INJECTIONS:
            inject(rel, old, new)
            red1, out1 = j1()
            red2, out2 = j2()
            red3, out3 = j3()
            actual = {
                name
                for name, red in (("J1", red1), ("J2", red2), ("J3", red3))
                if red
            }
            mark = "命中" if actual == expected else "不符"
            detail = failed_names(out1) + failed_names(out2) + failed_names(out3)
            print(
                f"[{iid}] {desc} → 红在 {sorted(actual) or ['无']}"
                f"（期望 {sorted(expected)}）{mark}"
                + (f" · 红名单 {detail}" if detail else "")
            )
            if actual != expected:
                problems.append(
                    f"{iid} 期望红 {sorted(expected)}，实得 {sorted(actual) or ['无']}；"
                    f"J1={summarize(out1)} / J2={summarize(out2)} / J3={summarize(out3)}"
                )
            else:
                failures.append(f"{iid} → {sorted(actual)}")
            restore_all()
            # 每条注入后核 md5：注入未落盘（或还原没生效）都会在这里现形
            for rel2 in CONTROLLED:
                got = md5((ROOT / rel2).read_bytes())
                if got != clean_md5[rel2]:
                    problems.append(f"{iid} 还原后 {rel2} md5 不一致：{got} != {clean_md5[rel2]}")
    finally:
        restore_all()

    for rel in CONTROLLED:
        text = (ROOT / rel).read_text(encoding="utf-8")
        if SENTINEL in text:
            problems.append(f"{rel} 仍残留哨兵（还原失败）")
        got = md5((ROOT / rel).read_bytes())
        if got != clean_md5[rel]:
            problems.append(f"{rel} 最终 md5 不一致：{got} != {clean_md5[rel]}")

    # 还原后复跑：留下坏状态比不验证更糟
    j1_red, j1_out = j1()
    j2_red, j2_out = j2()
    j3_red, j3_out = j3()
    print(f"复跑 J1 rc-red={j1_red} | {summarize(j1_out)}")
    print(f"复跑 J2 rc-red={j2_red} | {summarize(j2_out)}")
    print(f"复跑 J3 rc-red={j3_red} | {summarize(j3_out)}")
    if j1_red or j2_red or j3_red:
        problems.append("还原后复跑未复绿")

    print(f"\n命中汇总：{'；'.join(failures)}")
    if problems:
        print("\n问题：")
        for item in problems:
            print(f"- {item}")
        return 1
    print(f"\nM53 负向验证通过：{len(INJECTIONS)} 处注入全部命中，逐字节还原核 md5，复跑复绿")
    return 0


if __name__ == "__main__":
    sys.exit(main())
