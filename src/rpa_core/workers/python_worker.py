import hashlib
import json
import sys
from pathlib import Path

from rpa_core.model.command import (
    CommandInvocation,
    CommandResult,
    EffectKind,
    EffectRecord,
)
from rpa_core.model.errors import ErrorCode


def execute(invocation: CommandInvocation) -> CommandResult:
    if invocation.command_id == "data.writeJson":
        workspace = Path(str(invocation.inputs["workspace"])).resolve()
        raw_path = Path(str(invocation.inputs["path"]))
        if raw_path.is_absolute():
            output_path = raw_path.resolve()
        else:
            output_path = (workspace / raw_path).resolve()
        if output_path != workspace and workspace not in output_path.parents:
            return CommandResult.failure(
                ErrorCode.CAPABILITY_DENIED,
                "Output path must stay inside workspace",
            )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(invocation.inputs.get("data"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
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
                    details={"operation": "writeJson"},
                )
            ],
        )
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
