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

# 前端纯函数一致性校验（编辑器渲染逻辑与后端语义共用同一份源码）。
# node 不在时跳过，避免门禁在无 node 的机器上硬失败。
node = shutil.which("node")
if node:
    for script in ("check_param_groups.mjs", "check_channel_preview.mjs", "check_retry_policy.mjs"):
        path = ROOT / "scripts" / script
        if path.exists():
            commands.append([node, str(path)])

for command in commands:
    print("$", " ".join(command))
    completed = subprocess.run(command, cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)
print("FULL GATE PASSED")
