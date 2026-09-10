import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from rpa_core.model.command import (
    CommandInvocation,
    CommandResult,
    EffectKind,
    EffectRecord,
)
from rpa_core.model.errors import ErrorCode

_TEMPLATE_PLACEHOLDER = re.compile(r"\{([A-Za-z_]\w*)\}")


def _resolve_output_path(inputs: dict) -> tuple[Path, Path]:
    workspace = Path(str(inputs["workspace"])).resolve()
    raw_path = Path(str(inputs["path"]))
    if raw_path.is_absolute():
        output_path = raw_path.resolve()
    else:
        output_path = (workspace / raw_path).resolve()
    return workspace, output_path


def _within_workspace(workspace: Path, output_path: Path) -> bool:
    return output_path == workspace or workspace in output_path.parents


def _write_effect(
    invocation: CommandInvocation, operation: str, output_path: Path
) -> CommandResult:
    normalized_path = str(output_path)
    idempotency_key = hashlib.sha256(normalized_path.encode("utf-8")).hexdigest()
    return CommandResult.success(
        outputs={"path": normalized_path},
        effects=[
            EffectRecord.committed(
                invocation,
                kind=EffectKind.IDEMPOTENT_WRITE,
                resource=f"file:{normalized_path}",
                idempotency_key=idempotency_key,
                details={"operation": operation},
            )
        ],
    )


def _jsonable(value: Any) -> Any:
    """把求值结果收敛为 JSON 可序列化形态，避免子进程回传失败。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return str(value)


def _eval_source(source: str, namespace: dict) -> Any:
    """执行多行表达式，返回最后一个表达式的值。

    运行在 worker 子进程内（不在 orchestrator 进程），与 data.* 命令同一隔离模型。
    """
    lines = [line for line in source.strip().splitlines() if line.strip()]
    if not lines:
        return None
    if len(lines) > 1:
        exec("\n".join(lines[:-1]), namespace)
    last = lines[-1]
    try:
        return eval(last, namespace)
    except SyntaxError:
        exec(last, namespace)
        return None


def _eval_expression(invocation: CommandInvocation) -> CommandResult:
    """python 模式字段求值：注入流程变量为局部变量，支持赋值并回传新增变量。"""
    expressions = invocation.inputs.get("expressions") or {}
    variables = invocation.inputs.get("variables") or {}
    before = dict(variables)
    namespace: dict = dict(variables)
    values: dict[str, Any] = {}
    error_field: str | None = None
    error_text: str | None = None
    for field, source in expressions.items():
        try:
            values[field] = _eval_source(str(source), namespace)
        except Exception as exc:
            error_field = field
            error_text = f"{type(exc).__name__}: {exc}"
            break
    if error_text is not None:
        return CommandResult.failure(
            ErrorCode.SCRIPT_FAILED,
            f"Python expression failed in field '{error_field}': {error_text}",
        )
    assigned = {
        key: _jsonable(value)
        for key, value in namespace.items()
        # 过滤 exec/eval 自动注入的 __builtins__ 等内部键，只回传用户变量
        if not key.startswith("__") and (key not in before or before[key] != value)
    }
    return CommandResult.success(
        outputs={
            "values": {key: _jsonable(value) for key, value in values.items()},
            "assignedVariables": assigned,
        }
    )


def execute(invocation: CommandInvocation) -> CommandResult:
    if invocation.command_id == "python.evalExpression":
        return _eval_expression(invocation)
    if invocation.command_id == "data.writeJson":
        workspace, output_path = _resolve_output_path(invocation.inputs)
        if not _within_workspace(workspace, output_path):
            return CommandResult.failure(
                ErrorCode.CAPABILITY_DENIED,
                "Output path must stay inside workspace",
            )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(invocation.inputs.get("data"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return _write_effect(invocation, "writeJson", output_path)
    if invocation.command_id == "data.writeText":
        workspace, output_path = _resolve_output_path(invocation.inputs)
        if not _within_workspace(workspace, output_path):
            return CommandResult.failure(
                ErrorCode.CAPABILITY_DENIED,
                "Output path must stay inside workspace",
            )
        if "text" in invocation.inputs:
            content = str(invocation.inputs["text"])
        else:
            content = "\n".join(str(line) for line in invocation.inputs["lines"])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")
        return _write_effect(invocation, "writeText", output_path)
    if invocation.command_id == "data.limit":
        items = invocation.inputs["items"]
        count = int(invocation.inputs["count"])
        sliced = list(items)[:count]
        return CommandResult.success(
            value=sliced,
            outputs={"items": sliced, "count": len(sliced)},
        )
    if invocation.command_id == "data.format":
        template = str(invocation.inputs["template"])
        values = invocation.inputs["values"]
        missing = sorted(
            {
                match
                for match in _TEMPLATE_PLACEHOLDER.findall(template)
                if match not in values
            }
        )
        if missing:
            return CommandResult.failure(
                ErrorCode.INVALID_INPUT,
                "Template placeholders missing from values",
                details={"missing": missing},
            )

        def _replace(match: re.Match) -> str:
            value = values[match.group(1)]
            return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)

        text = _TEMPLATE_PLACEHOLDER.sub(_replace, template)
        return CommandResult.success(value=text, outputs={"text": text})
    return CommandResult.failure(
        ErrorCode.COMMAND_NOT_FOUND,
        f"Unsupported Python worker command: {invocation.command_id}",
    )


def main() -> int:
    try:
        invocation = CommandInvocation.model_validate_json(sys.stdin.buffer.read())
        result = execute(invocation)
        sys.stdout.buffer.write(result.model_dump_json().encode("utf-8"))
        return 0
    except Exception as exc:
        sys.stderr.buffer.write(str(exc).encode("utf-8", errors="replace"))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
