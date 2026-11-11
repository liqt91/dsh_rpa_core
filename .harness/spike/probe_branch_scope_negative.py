"""负向验证：证明 check_branch_scope.py 真的拦得住（2026-11-11）。

隔离做法：在 tempfile.mkdtemp() 里造一个最小 git repo，复制主仓的契约 /
闸门 / check_all.py / 治理文档，把契约的 split_point 改写成临时 repo 的 C0，
再造出 ppx-only / plugin-only 两条分支。所有注入只发生在临时 repo 的副本里，
**主仓工作区与 index 一律不动**，也从不删文件（删除守卫会拦）。

注入集按推理写期望片段，不照抄实际输出；每条跑完必须还原、还原后必须回绿，
并核治理文件 md5。最后一个 case 是正向对照：共享运行时文件改动应当放行。

运行：./.venv/Scripts/python.exe .harness/spike/probe_branch_scope_negative.py
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCOPE_REL = ".harness/branch-scope.json"
GATE_REL = ".harness/scripts/check_branch_scope.py"
CHECK_ALL_REL = ".harness/scripts/check_all.py"
WIRING_ANCHOR = 'str(ROOT / ".harness" / "scripts" / "check_branch_scope.py")'
_GIT_EXE: str | None = None


def git_exe() -> str:
    """和闸门同一口径：探测出 Python 真能启动的 git，并传给子进程。"""
    global _GIT_EXE
    if _GIT_EXE:
        return _GIT_EXE
    candidates = [
        os.environ.get("RPA_GIT"),
        shutil.which("git"),
        r"C:\Program Files\Git\cmd\git.exe",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            if subprocess.run([candidate, "--version"], capture_output=True).returncode == 0:
                _GIT_EXE = candidate
                os.environ["RPA_GIT"] = candidate
                return candidate
        except OSError:
            continue
    raise RuntimeError("找不到能启动的 git：请设 RPA_GIT 指向可执行文件")


def run(cmd: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    done = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and done.returncode:
        raise RuntimeError(f"{' '.join(cmd)} -> rc={done.returncode}\n{done.stdout}\n{done.stderr}")
    return done


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run([git_exe(), *args], cwd, check)


def gate(repo: Path) -> subprocess.CompletedProcess[str]:
    return run([sys.executable, str(repo / GATE_REL)], repo, check=False)


def md5_map(repo: Path, rels: list[str]) -> dict[str, str]:
    return {rel: hashlib.md5((repo / rel).read_bytes()).hexdigest() for rel in rels}


def make_tmp_root() -> Path:
    """沙箱会把 gettempdir() 指到仓库根 —— 临时 repo 绝不能落在工作区里。"""
    base = Path(tempfile.gettempdir())
    if base == ROOT or ROOT in base.parents:
        base = Path(os.environ.get("TEMP") or os.environ.get("TMP") or base)
    override = os.environ.get("RPA_NV_TMP_ROOT")
    if override:
        base = Path(override)
    base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="rpa-branch-scope-nv-", dir=base))


def preflight() -> None:
    """防呆：别在别人的暂存改动上跑，也别在错误的分支上跑。"""
    branch = git(ROOT, "symbolic-ref", "--quiet", "--short", "HEAD").stdout.strip()
    if branch not in ("ppx-only", "plugin-only"):
        raise SystemExit(f"主仓当前分支 {branch} 不是拆分目标分支，先切过去")
    staged = git(ROOT, "diff", "--cached", "--name-only").stdout.strip()
    if staged:
        raise SystemExit(f"主仓有暂存改动，先处理再跑探针：\n{staged}")


def build_template(tmp: Path, sources: dict[str, bytes]) -> tuple[Path, str]:
    """造一个和主仓同构、且基线全绿的最小 repo。返回 (模板目录, C1 sha)。"""
    repo = tmp / "template"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "branch-scope-nv")
    git(repo, "config", "user.email", "nv@example.invalid")
    # reset --hard 会按宿主 EOL 设置全量重写工作区（LF→CRLF），git 看不出、md5 能看出。
    git(repo, "config", "core.autocrlf", "false")
    git(repo, "symbolic-ref", "HEAD", "refs/heads/ppx-only")
    scope = json.loads(sources[SCOPE_REL].decode("utf-8"))
    scope["split_point"] = "0" * 40
    for rel, data in sources.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if rel == SCOPE_REL:
            data = (json.dumps(scope, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        target.write_bytes(data)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "template C0")
    c0 = git(repo, "rev-parse", "HEAD").stdout.strip()
    scope["split_point"] = c0
    (repo / SCOPE_REL).write_bytes(
        (json.dumps(scope, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "template C1")
    c1 = git(repo, "rev-parse", "HEAD").stdout.strip()
    git(repo, "branch", "plugin-only", c1)
    return repo, c1


def replace_once(repo: Path, rel: str, old: str, new: str) -> bytes:
    """锚点唯一性自检：短锚点常是更长行的子串，替换前必须确认只命中一次。"""
    path = repo / rel
    original = path.read_bytes()
    text = original.decode("utf-8")
    if text.count(old) != 1:
        raise RuntimeError(f"{rel}: 锚点命中 {text.count(old)} 次（须恰好 1）: {old[:50]!r}")
    path.write_bytes(text.replace(old, new, 1).encode("utf-8"))
    return original


def write_and_commit(repo: Path, rel: str, content: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    git(repo, "add", rel)
    git(repo, "commit", "-q", "-m", "injected")


def cases(c1: str, split_sha: str) -> list[dict]:
    """每个 case: name / expect(红时的期望片段或 None=期望绿) / inject(repo) -> restore()。"""

    def committed_plugin(repo: Path) -> object:
        write_and_commit(repo, "workbuddy-suite/release.json", "{}\n")
        return lambda: git(repo, "reset", "--hard", "HEAD~1")

    def staged_plugin(repo: Path) -> object:
        path = repo / "workbuddy-suite/release.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
        git(repo, "add", "workbuddy-suite/release.json")
        return lambda: git(repo, "restore", "--staged", "workbuddy-suite/release.json")

    def committed_ppx(repo: Path) -> object:
        git(repo, "checkout", "-q", "plugin-only")
        write_and_commit(repo, ".harness/demo/ppx_probe/app/gui/src/main.js", "// injected\n")
        return lambda: git(repo, "reset", "--hard", "HEAD~1")

    def unwired(repo: Path) -> object:
        original = replace_once(repo, CHECK_ALL_REL, WIRING_ANCHOR, "# unwired")
        return lambda: (repo / CHECK_ALL_REL).write_bytes(original)

    def broken_split(repo: Path) -> object:
        original = replace_once(repo, SCOPE_REL, split_sha, "f" * 40)
        return lambda: (repo / SCOPE_REL).write_bytes(original)

    def diverged_contract(repo: Path) -> object:
        scope_path = repo / SCOPE_REL
        original = scope_path.read_bytes()
        git(repo, "checkout", "-q", "plugin-only")
        scope_path.write_bytes(
            original.replace("PPX 桌面客户端专用分支".encode(), "分叉的 role".encode())
        )
        git(repo, "add", SCOPE_REL)
        git(repo, "commit", "-q", "-m", "diverge contract")
        git(repo, "checkout", "-q", "ppx-only")

        def restore() -> None:
            git(repo, "update-ref", "refs/heads/plugin-only", c1)
            scope_path.write_bytes(original)

        return restore

    def overlapping_prefixes(repo: Path) -> object:
        original = replace_once(
            repo,
            SCOPE_REL,
            '"workbuddy-suite/",',
            '".harness/",\n        "workbuddy-suite/",',
        )
        return lambda: (repo / SCOPE_REL).write_bytes(original)

    def shared_runtime_control(repo: Path) -> object:
        write_and_commit(repo, "bin/rpa-skill.mjs", "// shared runtime change\n")
        return lambda: git(repo, "reset", "--hard", "HEAD~1")

    return [
        {
            "name": "ppx-only 提交插件域文件",
            "expect": "归 plugin-only",
            "inject": committed_plugin,
        },
        {"name": "ppx-only 暂存插件域文件", "expect": "staged:", "inject": staged_plugin},
        {
            "name": "plugin-only 提交客户端域文件",
            "expect": "归 ppx-only",
            "inject": committed_ppx,
        },
        {"name": "摘掉 check_all.py 接线", "expect": "接线命中 0 次", "inject": unwired},
        {
            "name": "split_point 指向不存在的 commit",
            "expect": "不是本仓的 commit",
            "inject": broken_split,
        },
        {"name": "两分支契约分叉", "expect": "两分支内容分叉", "inject": diverged_contract},
        {
            "name": "exclusive 前缀互相覆盖",
            "expect": "exclusive 前缀互相覆盖",
            "inject": overlapping_prefixes,
        },
        {
            "name": "对照：shared_runtime 改动应放行",
            "expect": None,
            "inject": shared_runtime_control,
        },
    ]


def main() -> int:
    preflight()
    scope = json.loads((ROOT / SCOPE_REL).read_text(encoding="utf-8"))
    governed = list(scope["governance"])
    main_before = md5_map(ROOT, governed)
    tmp = make_tmp_root()
    passed = 0
    try:
        sources = {rel: (ROOT / rel).read_bytes() for rel in governed}
        sources[GATE_REL] = (ROOT / GATE_REL).read_bytes()
        template, c1 = build_template(tmp, sources)
        split_sha = json.loads((template / SCOPE_REL).read_text(encoding="utf-8"))["split_point"]

        baseline = gate(template)
        if baseline.returncode != 0 or "PASSED" not in baseline.stdout:
            print("TEMPLATE BASELINE FAILED")
            print(baseline.stdout, baseline.stderr)
            return 1
        print(f"基线全绿: {baseline.stdout.strip()}")

        all_cases = cases(c1, split_sha)
        for index, case in enumerate(all_cases, 1):
            repo = tmp / f"case{index:02d}"
            shutil.copytree(template, repo)
            before = md5_map(repo, governed)
            restore = case["inject"](repo)
            try:
                injected = gate(repo)
                want = case["expect"]
                if want is None:
                    if injected.returncode != 0:
                        raise RuntimeError(f"对照本该绿却红了：{injected.stdout}")
                    verdict = "GREEN(对照)"
                else:
                    if injected.returncode == 0:
                        raise RuntimeError(f"注入后没红（假绿灯）：{injected.stdout}")
                    if want not in injected.stdout:
                        raise RuntimeError(
                            f"期望片段 {want!r} 没命中，红的原因不对：\n{injected.stdout}"
                        )
                    verdict = "RED"
                restore()
                restored = gate(repo)
                if restored.returncode != 0:
                    raise RuntimeError(f"还原后没回绿：\n{restored.stdout}")
                if md5_map(repo, governed) != before:
                    raise RuntimeError("治理文件 md5 没还原（逐字节核对失败）")
                print(f"CASE {index:02d} {case['name']}: {verdict} → 还原后 GREEN / md5 一致")
                passed += 1
            except Exception as exc:  # noqa: BLE001 - 探针要报出每一条，不能半途静默
                print(f"CASE {index:02d} {case['name']}: FAILED\n{exc}")
        expected = len(all_cases)
        print(f"\n临时 repo 留在：{tmp}（探针不删文件，避免触发删除守卫）")
        if passed != expected:
            print(f"NEGATIVE VERIFICATION FAILED ({passed}/{expected})")
            return 1
        print(f"NEGATIVE VERIFICATION PASSED ({expected - 1} 注全红 + 1 对照绿 + 基线绿)")
        return 0
    finally:
        if md5_map(ROOT, governed) != main_before:
            print("主仓治理文件 md5 变了 —— 注入泄漏！")
            raise SystemExit(2)


if __name__ == "__main__":
    raise SystemExit(main())
