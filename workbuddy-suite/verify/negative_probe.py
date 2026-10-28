"""负向验证：证明新加的门禁真的会红，而不是恒绿。

只验「我这次加的判据」，每条都做「注入 → 期望红 → 逐字节还原 → 核 md5」：

  1. manifest 去声明 `alarms` 但代码仍在用 → verify_suite 必须红（权限门禁）
  2. SKILL.md 提到不存在的命令        → pack_connector --check 必须红（能力文档门禁）
  3. catalog.json 的 digest 改成假值   → pack_connector --check 必须红（版本漂移门禁）
  4. 技能包换成 flat 布局              → pack.pack_skill 必须红（平台目录结构门禁）

跑法：python verify/negative_probe.py
输出最后一行 `NEGATIVE OK` 才算过。脚本自身带哨兵：源码已被改过就拒绝运行。
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

SUITE = Path(__file__).resolve().parents[1]
PY = sys.executable
MANIFEST = SUITE / "extension" / "manifest.json"
SKILL_MD = SUITE / "connector" / "skills" / "rpa-automation" / "SKILL.md"
CATALOG = SUITE / "connector" / "skills" / "rpa-automation" / "references" / "catalog.json"

failures: list[str] = []


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=SUITE, capture_output=True, text=True)


def _mark(proc: subprocess.CompletedProcess) -> str:
    return "PASS" if proc.returncode == 0 else "FAIL"


def expect(name: str, proc: subprocess.CompletedProcess, needle: str) -> None:
    output = (proc.stdout or "") + (proc.stderr or "")
    ok = proc.returncode != 0 and needle in output
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  rc={proc.returncode}  {needle!r}")
    if not ok:
        failures.append(
            f"{name}：期望 rc≠0 且包含 {needle!r}，实际 rc={proc.returncode}\n{output[-800:]}"
        )


def main() -> int:
    # 哨兵：上一轮注入没还原就别再动源码（两种方向分别判）
    if MANIFEST.is_file() and '"alarms"' not in MANIFEST.read_text(encoding="utf-8"):
        print("哨兵：manifest 里已经没有 alarms，疑似上次注入未还原，拒绝运行")
        return 2
    if SKILL_MD.is_file() and "browser.nonexistent" in SKILL_MD.read_text(encoding="utf-8"):
        print("哨兵：SKILL.md 里残留 browser.nonexistent，疑似上次注入未还原，拒绝运行")
        return 2

    originals = {path: (path.read_bytes(), md5(path)) for path in (MANIFEST, SKILL_MD, CATALOG)}

    try:
        # 1) 权限门禁：代码用 chrome.alarms，manifest 不声明
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        manifest["permissions"] = [item for item in manifest["permissions"] if item != "alarms"]
        MANIFEST.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        expect(
            "权限门禁：alarms 未声明要红",
            run([PY, "verify/verify_suite.py"]),
            "用到的 chrome.* API 都声明了权限",
        )
        MANIFEST.write_bytes(originals[MANIFEST][0])

        # 2) 能力文档门禁：SKILL.md 提到不存在的命令
        text = SKILL_MD.read_text(encoding="utf-8")
        SKILL_MD.write_text(
            text + "\n调用 `browser.nonexistent` 即可。\n", encoding="utf-8", newline="\n"
        )
        expect(
            "能力文档门禁：提到不存在的命令要红",
            run([PY, "connector/tools/pack_connector.py", "--check"]),
            "不存在的命令",
        )
        SKILL_MD.write_bytes(originals[SKILL_MD][0])

        # 3) 版本漂移门禁：digest 改假
        snapshot = json.loads(CATALOG.read_text(encoding="utf-8"))
        snapshot["digest"] = "0" * 64
        CATALOG.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        expect(
            "版本漂移门禁：digest 不一致要红",
            run([PY, "connector/tools/pack_connector.py", "--check"]),
            "不同代",
        )
        CATALOG.write_bytes(originals[CATALOG][0])

        # 4) 目录结构门禁：用 flat 布局重打上架包，verify_suite 必须红
        flat = run([PY, "tools/pack.py", "--layout", "flat"])
        print(f"      (flat 打包 rc={flat.returncode})")
        expect(
            "目录结构门禁：flat 布局的技能包要红",
            run([PY, "verify/verify_suite.py"]),
            "技能包入口是",
        )
    finally:
        for path, (blob, digest) in originals.items():
            path.write_bytes(blob)
            if md5(path) != digest:
                failures.append(f"还原失败：{path}")
        # flat 布局那一步会覆盖上架包，这里用默认（folder）重打回来
        restored = run([PY, "tools/pack.py"])
        if restored.returncode != 0:
            failures.append("恢复 folder 布局的上架包失败\n" + restored.stdout[-500:])

    # 还原后必须仍然是绿的，否则说明「红了」其实是别的原因
    healthy = run([PY, "verify/verify_suite.py"])
    print(f"[{_mark(healthy)}] 还原后整套恢复绿灯  rc={healthy.returncode}")
    if healthy.returncode != 0:
        failures.append("还原后 verify_suite 仍然红\n" + healthy.stdout[-800:])
    connector = run([PY, "connector/tools/pack_connector.py", "--check"])
    print(f"[{_mark(connector)}] 还原后连接器校验通过  rc={connector.returncode}")
    if connector.returncode != 0:
        failures.append("还原后连接器校验仍然红\n" + connector.stdout[-800:])

    if failures:
        print(f"\n{len(failures)} 项未通过：")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("\nNEGATIVE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
