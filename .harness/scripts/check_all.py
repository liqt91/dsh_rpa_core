import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
commands = [
    [sys.executable, "-m", "pytest"],
    [sys.executable, "-m", "ruff", "check", "."],
    [sys.executable, str(ROOT / ".harness" / "scripts" / "check_architecture.py")],
    [sys.executable, str(ROOT / ".harness" / "scripts" / "check_tasks.py")],
]

# 门禁跑完整覆盖：启用真实桌面 E2E（会弹窗抢焦点，故只在门禁里开；
# 日常 `uv run pytest` 缺省跳过，不打断开发者——见 tests/conftest.py）
gate_env = {**os.environ, "RPA_DESKTOP_E2E": "1"}

# 前端纯函数一致性校验（编辑器渲染逻辑与后端语义共用同一份源码）。
# node 不在时跳过，避免门禁在无 node 的机器上硬失败。
node = shutil.which("node")
if node:
    for script in ("check_param_groups.mjs", "check_retry_policy.mjs"):
        path = ROOT / "scripts" / script
        if path.exists():
            commands.append([node, str(path)])

for command in commands:
    print("$", " ".join(command))
    completed = subprocess.run(command, cwd=ROOT, env=gate_env)
    if completed.returncode:
        raise SystemExit(completed.returncode)
print("FULL GATE PASSED")
