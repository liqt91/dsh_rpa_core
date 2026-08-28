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
for command in commands:
    print("$", " ".join(command))
    completed = subprocess.run(command, cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)
print("FULL GATE PASSED")
