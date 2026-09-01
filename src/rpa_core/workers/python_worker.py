import hashlib
import json
import re
import sys
from pathlib import Path

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


def execute(invocation: CommandInvocation) -> CommandResult:
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
