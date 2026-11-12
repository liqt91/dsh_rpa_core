"""门禁：多条分支的职责边界（2026-11-11 拆分，同日泛化为三分支）。

`ppx-only`（PPX 桌面客户端）、`plugin-only`（WorkBuddy 插件套件）与
`xhs-workbench`（小红书专用工作台）从同一个拆分点出发，基线相同、职责不同。
事实源是 `.harness/branch-scope.json`，分支集合只在这一处声明，闸门不写死条数。

只管「拆分之后的改动」：相对 split_point 的已提交改动 + 已暂存改动。
拆分点之前的历史（含插件套件合进来的那几笔）是共同基线，一律不追溯。
不检查未暂存的工作区脏文件 —— 那是维护者的在制品，不是提交意图。

判据分四层，缺一层就会出现「看起来绿、其实没看住」：
1. 契约自洽：字段形状、治理文件真存在、各条独占域不互相覆盖；
2. 接线哨兵：check_all.py 里摘掉本闸门，闸门自己就不会再跑；
3. 共同基线：split_point 存在且是 HEAD 的祖先，所有分支契约/闸门 blob 相同；
4. 越界：当前分支上出现其他分支独占域的路径即红。

单分支 clone 里缺对方 ref 时，第 3 条对该 ref 明确打印 skipped 并放行，
其余判据照常生效——不让 CI 因为只 fetch 一条分支而假红。

运行：uv run python .harness/scripts/check_branch_scope.py
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCOPE_REL = ".harness/branch-scope.json"
CHECK_ALL_REL = ".harness/scripts/check_all.py"
EXPECTED_BRANCHES = {"ppx-only", "plugin-only", "xhs-workbench"}
WIRING_RE = re.compile(r'str\(ROOT / "\.harness" / "scripts" / "check_branch_scope\.py"\)')
_GIT_BIN: str | None = None


def git_bin() -> str:
    """选一个 Python 真能启动的 git。

    某些宿主把自带 PortableGit 排在 PATH 最前，而 Python 的 CreateProcess 启动它会
    报 WinError 5；逐个探测候选，选第一个 `git --version` 跑得通的，不硬编码路径。
    也可用 RPA_GIT 显式指定。
    """
    global _GIT_BIN
    if _GIT_BIN:
        return _GIT_BIN
    candidates = [
        os.environ.get("RPA_GIT"),
        shutil.which("git"),
        r"C:\Program Files\Git\cmd\git.exe",
        r"C:\Program Files (x86)\Git\cmd\git.exe",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            probe = subprocess.run([candidate, "--version"], capture_output=True)
        except OSError:
            continue
        if probe.returncode == 0:
            _GIT_BIN = candidate
            return candidate
    raise RuntimeError("找不到能启动的 git：请设 RPA_GIT 指向可执行文件")


def launch(args: list[str]) -> subprocess.CompletedProcess[str]:
    """统一入口：CreateProcess 被拒时给一句人话，而不是裸 traceback。"""
    try:
        return subprocess.run(
            [git_bin(), *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        raise RuntimeError(f"启动 git 失败（{' '.join(args)}）：{exc}") from exc


def git(*args: str) -> str:
    """跑 git；失败必须炸，不能让判据静默空过。"""
    done = launch(list(args))
    if done.returncode:
        raise RuntimeError(f"git {' '.join(args)} -> rc={done.returncode}: {done.stderr.strip()}")
    return done.stdout.strip()


def git_ok(*args: str) -> bool:
    return launch(list(args)).returncode == 0


def load_scope(errors: list[str]) -> dict | None:
    path = ROOT / SCOPE_REL
    if not path.is_file():
        errors.append(f"缺 {SCOPE_REL}：没有事实源，边界检查无意义")
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{SCOPE_REL} 读不了：{exc}")
        return None
    if not isinstance(data, dict) or data.get("schema") != 1:
        errors.append(f"{SCOPE_REL}: schema 必须为 1")
        return None
    sp = data.get("split_point")
    if not isinstance(sp, str) or not re.fullmatch(r"[0-9a-f]{40}", sp):
        errors.append("split_point 必须是 40 位小写 sha")
    for key in (
        "shared_runtime",
        "governance",
        "must_match_across_branches",
        "branch_local",
    ):
        value = data.get(key)
        if not isinstance(value, list) or not all(isinstance(x, str) and x for x in value):
            errors.append(f"{SCOPE_REL}: {key} 必须由非空字符串组成")
    branches = data.get("branches")
    if not isinstance(branches, dict) or set(branches) != EXPECTED_BRANCHES:
        errors.append(f"{SCOPE_REL}: branches 必须恰好是 {sorted(EXPECTED_BRANCHES)}")
    else:
        for name, cfg in branches.items():
            prefixes = cfg.get("exclusive") if isinstance(cfg, dict) else None
            if not isinstance(prefixes, list) or not prefixes or not all(
                isinstance(x, str) and x for x in prefixes
            ):
                errors.append(f"{SCOPE_REL}: {name}.exclusive 必须是非空前缀列表")
    return None if errors else data


def check_scope_sanity(scope: dict, errors: list[str]) -> int:
    governance = scope["governance"]
    for rel in governance:
        if not (ROOT / rel).is_file():
            errors.append(f"governance 列了不存在的文件：{rel}")
    # shared_runtime 里以 / 结尾的条目是**目录前缀**（整棵子树共享，ADR 0022-R3）。
    # 目录写错不会让越界检查报错，只会让本该独占的路径悄悄变成无人认领，故先查存在性。
    for rel in scope["shared_runtime"]:
        if rel.endswith("/") and not (ROOT / rel).is_dir():
            errors.append(f"shared_runtime 的目录前缀不存在：{rel}")
    if not set(scope["must_match_across_branches"]) <= set(governance):
        errors.append("must_match_across_branches 必须是 governance 的子集")
    if not set(scope["branch_local"]) <= set(governance):
        errors.append("branch_local 必须是 governance 的子集")
    if set(scope["branch_local"]) & set(scope["must_match_across_branches"]):
        errors.append("branch_local 与 must_match_across_branches 重叠")
    prefixes = [
        (name, prefix)
        for name, cfg in scope["branches"].items()
        for prefix in cfg["exclusive"]
    ]
    for index, (a_name, a_prefix) in enumerate(prefixes):
        for b_name, b_prefix in prefixes[index + 1 :]:
            overlap = a_prefix.startswith(b_prefix) or b_prefix.startswith(a_prefix)
            if a_name != b_name and overlap:
                errors.append(f"exclusive 前缀互相覆盖：{a_prefix} / {b_prefix}")
    return len(governance)


def check_wiring(errors: list[str]) -> None:
    """接线哨兵：摘掉 check_all.py 里那一行，本闸门就不再进全门禁。"""
    path = ROOT / CHECK_ALL_REL
    if not path.is_file():
        errors.append(f"缺 {CHECK_ALL_REL}")
        return
    hits = WIRING_RE.findall(path.read_text(encoding="utf-8"))
    if len(hits) != 1:
        errors.append(f"{CHECK_ALL_REL}: 接线命中 {len(hits)} 次（须恰好 1）")


def check_split_point(scope: dict, errors: list[str]) -> None:
    sp = scope["split_point"]
    if not git_ok("cat-file", "-e", f"{sp}^{{commit}}"):
        errors.append(f"split_point {sp[:12]} 不是本仓的 commit")
        return
    if not git_ok("merge-base", "--is-ancestor", sp, "HEAD"):
        errors.append(f"split_point {sp[:12]} 不是 HEAD 的祖先 —— 共同基线被改写")


def check_matching(scope: dict, branch: str, errors: list[str]) -> int:
    """所有分支的事实源必须逐字节相同；单个 ref 缺失时只跳过那一条。"""
    checked = 0
    for other in scope["branches"]:
        if other == branch:
            continue
        if not git_ok("show-ref", "--verify", "--quiet", f"refs/heads/{other}"):
            print(f"[branch-scope] 跨分支一致性: skipped（本地没有 {other} ref）")
            continue
        for rel in scope["must_match_across_branches"]:
            try:
                mine = git("rev-parse", f"HEAD:{rel}")
                theirs = git("rev-parse", f"{other}:{rel}")
            except RuntimeError as exc:
                errors.append(f"{rel}: 取不到 {branch}/{other} 的 blob —— {exc}")
                continue
            if mine != theirs:
                errors.append(
                    f"{rel}: {branch} 与 {other} 内容分叉（{mine[:8]} vs {theirs[:8]}）"
                )
            checked += 1
    return checked


def owner_of(path: str, scope: dict) -> str | None:
    """查路径归属：治理 / 共享层 → 无人独占；否则按各分支 exclusive 前缀认领。

    ``shared_runtime`` 的条目有两种形状：精确文件路径，或以 ``/`` 结尾的目录前缀。
    后者整棵子树都算共享支撑层（ADR 0022-R3：PPX 原型编辑面被工作台运行时复用，
    为它每加一个文件就改一次治理是纯噪声）。

    优先级是**精确声明 > 目录前缀**：精确共享文件、各分支独占域先判，
    剩下的才落到共享目录兜底。这样原型目录整体共享，但客户端打包配置
    （``ppx.toml`` 等）仍能用一条精确的 exclusive 条目留在 ppx-only。
    """
    if path in scope["governance"] or path in scope["shared_runtime"]:
        return None
    for name, cfg in scope["branches"].items():
        for prefix in cfg["exclusive"]:
            if path == prefix or path.startswith(prefix):
                return name
    if any(
        prefix.endswith("/") and path.startswith(prefix)
        for prefix in scope["shared_runtime"]
    ):
        return None
    return None


def check_violations(scope: dict, branch: str, errors: list[str]) -> tuple[int, int]:
    sp = scope["split_point"]
    try:
        committed = [p for p in git("diff", "--name-only", f"{sp}..HEAD").splitlines() if p]
        staged = [p for p in git("diff", "--cached", "--name-only").splitlines() if p]
    except RuntimeError as exc:
        # split_point 被改坏时 git diff 会炸；要落成一条红，而不是裸 traceback。
        errors.append(f"相对拆分点取改动失败：{exc}")
        return 0, 0
    for phase, paths in (("committed", committed), ("staged", staged)):
        for path in paths:
            owner = owner_of(path, scope)
            if owner is not None and owner != branch:
                errors.append(f"{phase}: {path} 归 {owner}，不该落在 {branch}")
    if not committed and not staged:
        print("[branch-scope] WARN: 相对拆分点 0 条改动，越界检查为空")
    return len(committed), len(staged)


def main() -> int:
    errors: list[str] = []
    detail = "scope invalid"
    scope = load_scope(errors)
    if scope is not None:
        checked_files = check_scope_sanity(scope, errors)
        check_wiring(errors)
        check_split_point(scope, errors)
        try:
            branch = git("symbolic-ref", "--quiet", "--short", "HEAD")
        except RuntimeError as exc:
            errors.append(f"取不到当前分支：{exc}")
            branch = ""
        if branch in scope["branches"]:
            matched = check_matching(scope, branch, errors)
            committed, staged = check_violations(scope, branch, errors)
            detail = (
                f"branch={branch} governance={checked_files} "
                f"committed={committed} staged={staged} matched={matched}"
            )
        else:
            detail = f"branch={branch or 'detached'} inactive"
            print(f"[branch-scope] 非目标分支，只跑契约/接线/基线校验（{detail}）")
    if errors:
        print("BRANCH SCOPE CHECK FAILED")
        for item in errors:
            print(f"- {item}")
        return 1
    print(f"BRANCH SCOPE CHECK PASSED ({detail}; wiring + split_point ok)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
