import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "rpa_core"
COMMANDS = ROOT / "commands"
CONTROL_NAMES = {
    "sequence",
    "action",
    "if",
    "forEach",
    "try",
    "return",
    "while",
    "break",
    "continue",
}
FORBIDDEN_CALLS = {"eval", "exec", "__import__"}


def python_files():
    return SRC.rglob("*.py")


def check_forbidden_calls(errors):
    for path in python_files():
        # workers/ 是子进程执行面：按规则 5，用户 Python 只在 worker 子进程运行，
        # py 表达式模式的动态求值因此限定在 workers/ 内；orchestrator 进程
        # （runtime/executors/compiler/model/cli/devserver）仍然全面禁止 eval/exec。
        if path.parent == (SRC / "workers"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = node.func.id if isinstance(node.func, ast.Name) else None
                if name in FORBIDDEN_CALLS:
                    errors.append(f"forbidden dynamic execution {name} at {path}:{node.lineno}")


def check_import_direction(errors):
    model = SRC / "model"
    for path in model.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import | ast.ImportFrom):
                names = [alias.name for alias in node.names]
                if any(
                    name.startswith("rpa_core.") and not name.startswith("rpa_core.model")
                    for name in names
                ):
                    errors.append(f"model imports higher package at {path}:{node.lineno}")


def check_devserver_isolation(errors):
    devserver = SRC / "devserver"
    forbidden = ("rpa_core.runtime", "rpa_core.executors", "rpa_core.workers", "rpa_core.cli")
    for path in devserver.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import | ast.ImportFrom):
                names = [alias.name for alias in node.names]
                if any(
                    name.startswith(prefix) for name in names for prefix in forbidden
                ):
                    errors.append(f"devserver imports forbidden package at {path}:{node.lineno}")


def check_manifests(errors):
    ids = set()
    for path in sorted(COMMANDS.rglob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        command_id = data.get("id")
        if command_id in ids:
            errors.append(f"duplicate command id: {command_id}")
        ids.add(command_id)
        if not command_id or command_id.rsplit(".", 1)[-1] != path.stem:
            errors.append(f"manifest id/file mismatch: {path}")
        if command_id in CONTROL_NAMES:
            errors.append(f"control flow registered as command: {command_id}")
        for field in (
            "version",
            "executor",
            "kind",
            "risk",
            "stability",
            "effect",
            "input_schema",
            "output_schema",
            "errors",
            "implementation",
        ):
            if field not in data:
                errors.append(f"{path} missing {field}")


def main():
    errors = []
    check_forbidden_calls(errors)
    check_import_direction(errors)
    check_devserver_isolation(errors)
    check_manifests(errors)
    if errors:
        print("ARCHITECTURE CHECK FAILED")
        print("\n".join(f"- {item}" for item in errors))
        return 1
    python_count = len(list(python_files()))
    manifest_count = len(list(COMMANDS.rglob("*.json")))
    print(f"ARCHITECTURE CHECK PASSED ({python_count} python files, {manifest_count} manifests)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
