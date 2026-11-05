"""负向验证：安装器的冒烟判据真的会红吗？

十二处「合法语法的精准放宽」注入，每处只破坏一个行为，跑
workbuddy-suite/verify/skill_install_test.mjs，断言「实际红 == 期望红」，
然后逐字节还原并核 md5。

这一轮的重点变了：上一轮只验证「装出来的文件对不对」，判据看不出
「`rpa-core` 在用户机器上根本不存在」——所以新增的注入针对的是**运行时这条线**
（不写 runtime.json、stderr 不喊、rc.py 不认「import 失败」）。

期望集是按依赖关系推的，不是照抄实际输出：
  · 不写 runtime.json ⇒ 文件清单、形状校验、doctor 三处红；
    但 rc.py 仍能从源码树兜底，所以「命令能跑」不该红——这正是期望要精确的理由。
  · 跳过占位符替换 ⇒ 命令里留 {{…}}，shell 起来必失败，所以「命令能跑」跟着红。
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

INSTALLER = Path("bin/rpa-skill.mjs")
RC = Path("workbuddy-suite/skills/workflow-studio/scripts/rc.py")
STUDIO = Path("workbuddy-suite/skills/workflow-studio/scripts/studio.py")
SMOKE = "workbuddy-suite/verify/skill_install_test.mjs"
SENTINEL = "NEG-INJECT"

INJECTIONS = [
    {
        "name": "跳过占位符替换",
        "file": INSTALLER,
        "old": "    if (!PLACEHOLDERS.some((k) => text.includes(k))) continue;\n",
        "new": "    if (true) continue;  // " + SENTINEL + "\n",
        "expect": {
            "占位符全部替换（三个都查，不只是 SKILL_DIR）",
            "文档里那条命令照抄出来真的能跑",
            "doctor 全绿",  # doctor 也数残留占位符，同一处破坏被两个观测点看到
            # exe 形态断言「命令里要有 exe、不能有 rc.py」，占位符没替换时两者都不成立
            "exe 形态（PATH 上只有 rpa-core）：命令直指 exe，不绕 rc.py",
        },
    },
    {
        "name": "不写 runtime.json",
        "file": INSTALLER,
        "old": '  if (runtime.ok) {\n    writeFileSync(join(dest, "runtime.json")',
        "new": '  if (runtime.ok && false) {  // ' + SENTINEL + '\n    writeFileSync(join(dest, "runtime.json")',
        "expect": {
            "拷贝了完整技能树（含定位层与 runtime.json）",
            "runtime.json 指向真实存在的解释器与源码根",
            "doctor 全绿",
            # 补运行时那条也靠 runtime.json：没它，rc.py 只能走自身探测，而负向夹具里
            # 那个裸解释器 import 不到 rpa_core ⇒ 修完了也调不通。
            "给 --rpa-core-dir 就能补上，rc.py 随之可用",
            # 两条形态判据都拿 runtime.json 当夹具入口：没有它，doctor 必红（exe），
            # 或读文件直接 ENOENT（pip 形态改写 runtime.json 前要能读到）。
            "exe 形态（PATH 上只有 rpa-core）：命令直指 exe，不绕 rc.py",
            "pip 形态（mode=module）doctor 不误报「缺源码根」",
            # 诊断层的两条也是拿这个文件当夹具：一条要先删它看 doctor 怎么答，
            # 一条要先读它改成 exe 形态。文件根本没被写过 ⇒ 它们红在夹具上，
            # 不是在行为上 —— 这属于依赖关系，不是判据变强了。
            "runtime.json 丢了、但运行时就位：修法不该让人去翻仓库根",
            "studio.py doctor：没有源码树时不假红（src 形态仍要它）",
        },
    },
    {
        "name": "运行时探不到时不写 stderr 告警",
        "file": INSTALLER,
        "old": "    if (warn.length) process.stderr.write(",
        "new": "    if (warn.length && false) process.stderr.write(",
        "expect": {"仓库外安装：不报错，但把「没接上运行时」说清楚"},
    },
    {
        "name": "幂等检查失效",
        "file": INSTALLER,
        "old": "  if (installed && !force) {\n",
        "new": "  if (installed && !force && false) {  // " + SENTINEL + "\n",
        "expect": {"重复安装被拒绝（幂等）"},
    },
    {
        "name": "rc.py 把「import 失败」当成 rpa-core 的退出码吞掉",
        "file": RC,
        "old": '    if proc.returncode == EXIT_IMPORT_FAILED and IMPORT_MARKER in (proc.stderr or ""):\n',
        "new": "    if False:  # " + SENTINEL + "\n",
        "expect": {"这种安装里 rc.py 必须 exit 2 并给可照抄的修法"},
    },
    # 下面两处针对「非源码树形态」。它们的期望是**两条一起红**（第一处）：
    # source root 的判定一旦回到「无条件要求」，pip 与 exe 两种形态都会误红 ——
    # 只写一条期望就等于默许另一条继续假绿。
    {
        "name": "source root 恢复成无条件要求",
        "file": INSTALLER,
        "old": '    if (rc.mode === "src") {\n',
        "new": '    if (rc.mode === "src" || true) {  // ' + SENTINEL + "\n",
        "expect": {
            "pip 形态（mode=module）doctor 不误报「缺源码根」",
            "exe 形态（PATH 上只有 rpa-core）：命令直指 exe，不绕 rc.py",
        },
    },
    {
        "name": "exe 形态仍然绕 rc.py",
        "file": INSTALLER,
        "old": (
            "  const rpaCoreCmd =\n"
            '    runtime.mode === "exe" && runtime.exe\n'
            '      ? `"${toPosix(runtime.exe)}"`\n'
            '      : `${pythonCmd} "${posixDest}/scripts/rc.py"`;\n'
        ),
        "new": '  const rpaCoreCmd = `${pythonCmd} "${posixDest}/scripts/rc.py"`;  // ' + SENTINEL + "\n",
        "expect": {"exe 形态（PATH 上只有 rpa-core）：命令直指 exe，不绕 rc.py"},
    },
    # 诊断层两处：doctor 若只复述「安装那一刻」的历史，就会在「先装技能、后补运行时」
    # 这种真实时序上给出错修法。两个方向的放宽各注入一次，防止判据被单边蒙过。
    {
        "name": "doctor 不现场探测，只复述安装时的快照",
        "file": INSTALLER,
        "old": "    const probed = resolveRuntime({});\n",
        "new": "    const probed = { ok: false, searched: {} };  // " + SENTINEL + "\n",
        "expect": {"runtime.json 丢了、但运行时就位：修法不该让人去翻仓库根"},
    },
    {
        "name": "doctor 一律声称「现在探得到运行时」",
        "file": INSTALLER,
        "old": "    if (probed.ok) {\n",
        "new": "    if (true) {  // " + SENTINEL + "\n",
        "expect": {"探不到时仍要指向 --rpa-core-dir（上一条的反向锚）"},
    },
    {
        "name": "studio.py 的 source root 恢复无条件要求",
        "file": STUDIO,
        "old": '    elif mode in ("module", "exe"):\n',
        "new": "    elif False:  # " + SENTINEL + "\n",
        "expect": {"studio.py doctor：没有源码树时不假红（src 形态仍要它）"},
    },
    {
        "name": "studio.py 对 GUI 两条恢复成按假路径判红",
        "file": STUDIO,
        "old": '    if root is None and mode in ("module", "exe"):\n',
        "new": "    if False:  # " + SENTINEL + "\n",
        "expect": {"studio.py doctor：没有源码树时不假红（src 形态仍要它）"},
    },
    {
        "name": "备份失败时把异常甩出去（不给修法）",
        "file": INSTALLER,
        "old": "    } catch (err) {\n      // Windows 上目录被别的进程按住",
        "new": "    } catch (err) {\n      throw err;  // " + SENTINEL + "\n      // Windows 上目录被别的进程按住",
        "expect": {"备份挪不动时：说清怎么收场，别把旧目录弄丢"},
    },
]

TARGETS = [INSTALLER, RC, STUDIO]


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def run_smoke() -> tuple[int, set[str]]:
    proc = subprocess.run(
        ["node", SMOKE], capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    text = (proc.stdout or "") + (proc.stderr or "")
    failed = set()
    for line in text.splitlines():
        if not line.startswith("FAIL"):
            continue
        name = line[4:].split(" — ")[0].strip()
        if name:
            failed.add(name)
    return proc.returncode, failed


def main() -> int:
    for target in TARGETS:
        if SENTINEL in target.read_text(encoding="utf-8"):
            print(f"哨兵残留在 {target}，拒绝运行", file=sys.stderr)
            return 2

    baseline = {t: md5(t) for t in TARGETS}
    originals = {t: t.read_bytes() for t in TARGETS}
    problems: list[str] = []

    def restore_all() -> None:
        for t, data in originals.items():
            if md5(t) != baseline[t]:
                t.write_bytes(data)

    try:
        rc, failed = run_smoke()
        print(f"[基线] rc={rc} 红={sorted(failed) or '无'}")
        if rc != 0:
            problems.append("基线本身不绿，负向验证无意义")

        for item in INJECTIONS:
            target: Path = item["file"]
            text = target.read_text(encoding="utf-8")
            hits = text.count(item["old"])
            if hits != 1:
                problems.append(f"{item['name']}: 锚点命中 {hits} 次（应为 1）")
                continue
            target.write_bytes(text.replace(item["old"], item["new"]).encode("utf-8"))
            # 「注入落盘」按锚点消失判，不按哨兵判：有些精准放宽改在单行语句上，
            # 塞不进注释，硬塞会把语法搞坏——那就不是「精准」了。
            if item["old"] in target.read_text(encoding="utf-8"):
                problems.append(f"{item['name']}: 注入没落盘")
                restore_all()
                continue

            rc, failed = run_smoke()
            ok = rc != 0 and failed == item["expect"]
            mark = "PASS" if ok else "FAIL"
            print(f"[{mark}] 注入「{item['name']}」→ rc={rc} 红={sorted(failed)}")
            if not ok:
                problems.append(
                    f"{item['name']}: 期望 {sorted(item['expect'])} 实际 {sorted(failed)}"
                )

            restore_all()
            for t in TARGETS:
                if md5(t) != baseline[t]:
                    problems.append(f"{item['name']}: {t} 还原失败")
    finally:
        restore_all()

    rc, failed = run_smoke()
    print(f"[复跑] rc={rc} 红={sorted(failed) or '无'}")
    if rc != 0:
        problems.append("还原后复跑不绿")

    if problems:
        for p in problems:
            print("  ! " + p)
        print("\nNEGATIVE VERIFICATION FAILED")
        return 1
    print(f"\nNEGATIVE VERIFICATION PASSED（{len(INJECTIONS)} 注入全拦、md5 一致、复跑复绿）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
