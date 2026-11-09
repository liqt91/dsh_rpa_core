"""负向验证：新加的「版本事实源 / 上架包内容」判据真的会红吗？

四条注入，每条只破坏一件事，跑 verify/verify_suite.py（或
connector/tools/pack_connector.py --check），断言「实际红 == 期望红」，
然后逐字节还原并核 md5。

期望集按推理补全，不照抄实际输出：

  A. 只改 release.json 的 skill 版本，源文件不动
     ⇒ 只有「版本口径与 release.json 一致」该红；zip 没变、SKILL.md 没变，
       check_dist 不该陪着红。
  B. 只改 SKILL.md 正文，不重打包
     ⇒ 只有「技能包包内逐文件与源一致」该红；frontmatter 版本行没动，
       check_versions 不该红（这正是旧包恒绿要拦的那种漂移）。
  C. 只改 zip 里的 SKILL.md（读侧），源码不动
     ⇒ 同 B 的 FAIL 集合，但方向相反：证明判据是双向的。
  D. 把连接器配件技能目录改名
     ⇒ 「合并」的结构判据该红；还原后 --check 回绿。

「红了也要证明是谁报的」：A 关掉 check_versions、B/C 关掉 check_dist 后
整套必须变绿——否则红可能来自别处，判据等于没验到。

跑法：python .harness/spike/probe_release_dist_negative.py
输出最后一行 `NEGATIVE OK` 才算过。脚本自带哨兵：上一轮注入没还原就拒绝运行。
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SUITE = REPO / "workbuddy-suite"
VERIFY = SUITE / "verify"
PY = sys.executable
SENTINEL = "NEG-INJECT"

RELEASE = SUITE / "release.json"
SKILL_MD = SUITE / "skills" / "workflow-studio" / "SKILL.md"
SKILL_ZIP = SUITE / "dist" / "workflow-studio-skill.zip"
COMPANION = SUITE / "connector" / "skills" / "rpa-core-cli"
RENAMED = SUITE / "connector" / "skills" / "_probe_second_skill"
ZIP_ENTRY = "workflow-studio/SKILL.md"

EXPECT_DIST = {"技能包包内逐文件与源一致"}
EXPECT_VERSION = {"版本口径与 release.json 一致"}

failures: list[str] = []


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=SUITE, capture_output=True, text=True)


def fail_names(proc: subprocess.CompletedProcess) -> set[str]:
    return set(re.findall(r"^\[FAIL\] (.+?)(?:  .*)?$", proc.stdout, re.M))


def expect(name: str, proc: subprocess.CompletedProcess, expected: set[str], needle: str) -> None:
    actual = fail_names(proc)
    ok = proc.returncode != 0 and actual == expected and needle in proc.stdout
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  rc={proc.returncode}  红={sorted(actual)}")
    if not ok:
        failures.append(
            f"{name}：期望 rc≠0、失败集 {sorted(expected)}、输出含 {needle!r}；"
            f"实际 rc={proc.returncode}、失败集 {sorted(actual)}\n{proc.stdout[-900:]}"
        )


def meta_check(name: str, disabled_attr: str) -> None:
    """元验证：关掉新判据后整套必须变绿，否则刚才的红不是它报的。"""
    code = (
        "import sys; "
        f"sys.path.insert(0, r'{VERIFY}'); "
        "import verify_suite as v; "
        f"v.{disabled_attr} = lambda: None; "
        "sys.exit(v.main())"
    )
    proc = run([PY, "-c", code])
    ok = proc.returncode == 0
    print(f"[{'PASS' if ok else 'FAIL'}] 元验证：{name}  rc={proc.returncode}")
    if not ok:
        failures.append(f"元验证 {name}：关掉判据后仍红\n{proc.stdout[-900:]}")


def rewrite_zip_old_skill() -> None:
    """把 zip 里那一份 SKILL.md 换成旧内容，其余条目逐字节保留。"""
    with zipfile.ZipFile(SKILL_ZIP) as bundle:
        entries = [(item, bundle.read(item.filename)) for item in bundle.infolist()]
    with zipfile.ZipFile(SKILL_ZIP, "w", zipfile.ZIP_DEFLATED) as bundle:
        for item, blob in entries:
            bundle.writestr(item, b"old skill\n" if item.filename == ZIP_ENTRY else blob)


def main() -> int:
    # 哨兵：上一轮注入没还原就别再动源码（四个方向分别判）。
    if SENTINEL in SKILL_MD.read_text(encoding="utf-8"):
        print("哨兵：SKILL.md 里有注入残留，拒绝运行")
        return 2
    if '"skill": "9.9.9"' in RELEASE.read_text(encoding="utf-8"):
        print("哨兵：release.json 停在注入态，拒绝运行")
        return 2
    if not COMPANION.is_dir() or RENAMED.exists():
        print("哨兵：连接器配件目录名不在基线状态，拒绝运行")
        return 2
    with zipfile.ZipFile(SKILL_ZIP) as bundle:
        if bundle.read(ZIP_ENTRY) == b"old skill\n":
            print("哨兵：技能 zip 停在注入态，拒绝运行")
            return 2

    originals = {
        path: (path.read_bytes(), md5(path))
        for path in (RELEASE, SKILL_MD, SKILL_ZIP)
    }

    try:
        # A) 只改事实源：版本判据该红，且只有它红。
        release = RELEASE.read_text(encoding="utf-8")
        anchor = '"skill": "1.1.0"'
        assert release.count(anchor) == 1, "版本锚点不唯一，注入会打偏"
        RELEASE.write_bytes(release.replace(anchor, '"skill": "9.9.9"').encode("utf-8"))
        expect(
            "A 版本事实源漂移",
            run([PY, "verify/verify_suite.py"]),
            EXPECT_VERSION,
            "版本口径与 release.json 一致",
        )
        meta_check("A 红的是 check_versions", "check_versions")
        RELEASE.write_bytes(originals[RELEASE][0])

        # B) 只改源：哈希判据该红，版本判据不该陪着红。
        SKILL_MD.write_bytes(
            SKILL_MD.read_bytes() + f"\n<!-- {SENTINEL}: 源改了但没重打包 -->\n".encode("utf-8")
        )
        expect(
            "B 源码变了却没重打包",
            run([PY, "verify/verify_suite.py"]),
            EXPECT_DIST,
            "内容不是当前源码：" + ZIP_ENTRY,
        )
        meta_check("B 红的是 check_dist", "check_dist")
        SKILL_MD.write_bytes(originals[SKILL_MD][0])

        # C) 只改包：反向注入，证明判据双向都咬得住。
        rewrite_zip_old_skill()
        expect(
            "C zip 里换成旧技能文件",
            run([PY, "verify/verify_suite.py"]),
            EXPECT_DIST,
            "内容不是当前源码：" + ZIP_ENTRY,
        )
        meta_check("C 红的是 check_dist", "check_dist")
        SKILL_ZIP.write_bytes(originals[SKILL_ZIP][0])

        # D) 连接器里又长出第二份技能：结构判据该红，还原后回绿。
        COMPANION.rename(RENAMED)
        proc = run([PY, "connector/tools/pack_connector.py", "--check"])
        ok = proc.returncode != 0 and "连接器 skills/ 只应有" in (proc.stdout + proc.stderr)
        print(f"[{'PASS' if ok else 'FAIL'}] D 连接器冒出第二份技能  rc={proc.returncode}")
        if not ok:
            failures.append(f"D 没拦住第二份技能\n{(proc.stdout + proc.stderr)[-700:]}")
    finally:
        if RENAMED.exists():
            RENAMED.rename(COMPANION)
        for path, (blob, digest) in originals.items():
            path.write_bytes(blob)
            if md5(path) != digest:
                failures.append(f"还原失败：{path}")

    # 还原后必须仍然是绿的，否则「红了」其实是别的原因。
    healthy = run([PY, "verify/verify_suite.py"])
    ok = healthy.returncode == 0 and not fail_names(healthy)
    print(f"[{'PASS' if ok else 'FAIL'}] 还原后整套恢复绿灯  rc={healthy.returncode}")
    if not ok:
        failures.append(f"还原后 verify_suite 仍红\n{healthy.stdout[-900:]}")
    connector = run([PY, "connector/tools/pack_connector.py", "--check"])
    print(f"[{'PASS' if connector.returncode == 0 else 'FAIL'}] 还原后连接器校验通过  rc={connector.returncode}")
    if connector.returncode != 0:
        failures.append(f"还原后连接器校验仍红\n{(connector.stdout + connector.stderr)[-700:]}")

    if failures:
        print(f"\n{len(failures)} 项未通过：")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("\nNEGATIVE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
