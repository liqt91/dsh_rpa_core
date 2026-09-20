import datetime
import hashlib
import json
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from rpa_core.model.command import (
    CommandInvocation,
    CommandResult,
    EffectKind,
    EffectRecord,
)
from rpa_core.model.errors import ErrorCode
from rpa_core.workers import data_table as _table

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


def _read_effect(invocation: CommandInvocation, path: Path) -> list[EffectRecord]:
    """读取类命令的 READ 证据（orchestrator 对非 pure 命令要求返回）。"""
    return [
        EffectRecord.committed(
            invocation,
            kind=EffectKind.READ,
            resource=f"file:{path}",
        )
    ]


def _unsafe_write_effect(
    invocation: CommandInvocation, operation: str, path: Path
) -> list[EffectRecord]:
    """非幂等写（追加/删除）的 UNSAFE_WRITE 证据。"""
    return [
        EffectRecord.committed(
            invocation,
            kind=EffectKind.UNSAFE_WRITE,
            resource=f"file:{path}",
            details={"operation": operation},
        )
    ]


# data.setVar 类型格式化的报错文案（对标影刀「设置变量」变量类型）
_TYPE_COERCE_MSG = {
    "number": "值无法转成数字",
    "boolean": "值无法转成布尔",
    "object": "值无法转成对象（需 JSON 对象或 JSON 字符串）",
    "array": "值无法转成数组（需 JSON 数组或 JSON 字符串）",
}


def _coerce_by_type(value: Any, var_type: str) -> Any:
    """把变量值按 varType 格式化为目标类型，空值给该类型默认值（对标影刀）。"""
    if var_type == "string":
        return "" if value is None else str(value)
    if var_type == "number":
        if value is None:
            return 0
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return 0
            try:
                return int(stripped)
            except ValueError:
                try:
                    return float(stripped)
                except ValueError:
                    raise ValueError(_TYPE_COERCE_MSG["number"]) from None
        raise ValueError(_TYPE_COERCE_MSG["number"])
    if var_type == "boolean":
        if value is None:
            return False
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            return value.strip().lower() in ("true", "1", "yes")
        raise ValueError(_TYPE_COERCE_MSG["boolean"])
    if var_type == "object":
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return {}
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                raise ValueError(_TYPE_COERCE_MSG["object"]) from None
            if not isinstance(parsed, dict):
                raise ValueError(_TYPE_COERCE_MSG["object"])
            return parsed
        raise ValueError(_TYPE_COERCE_MSG["object"])
    if var_type == "array":
        if value is None:
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return []
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                raise ValueError(_TYPE_COERCE_MSG["array"]) from None
            if not isinstance(parsed, list):
                raise ValueError(_TYPE_COERCE_MSG["array"])
            return parsed
        raise ValueError(_TYPE_COERCE_MSG["array"])
    # auto / 未知类型：原样返回
    return value


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


def _execute_table_command(invocation: CommandInvocation) -> CommandResult:
    """data.table.* 系列：读写流程数据表格（文件位于 flow_dir/data/*.json）。"""
    inputs = invocation.inputs
    flow_dir = inputs.get("flowDir")
    if not flow_dir:
        return CommandResult.failure(
            ErrorCode.INVALID_INPUT,
            "missing flowDir; table commands require runtime flowDir injection",
        )
    table = str(inputs.get("table") or "default")
    try:
        handler = _TABLE_HANDLERS[invocation.command_id]
    except KeyError:
        return CommandResult.failure(
            ErrorCode.COMMAND_NOT_FOUND,
            f"Unsupported table command: {invocation.command_id}",
        )
    try:
        return handler(invocation, flow_dir, table, inputs)
    except _TableInputError as exc:
        code = ErrorCode.CAPABILITY_DENIED if exc.outside else ErrorCode.INVALID_INPUT
        return CommandResult.failure(code, str(exc))
    except ValueError as exc:
        # 表格名非法等输入问题
        return CommandResult.failure(ErrorCode.INVALID_INPUT, str(exc))


class _TableInputError(Exception):
    """表格命令输入错误；outside=True 表示越权（如导出路径逃逸流程目录）。"""

    def __init__(self, message: str, *markers, outside: bool = False):
        super().__init__(message)
        self.outside = outside


def _table_row_index(value) -> int:
    try:
        index = int(value)
    except (TypeError, ValueError) as exc:
        raise _TableInputError("row must be an integer") from exc
    if index < 1:
        raise _TableInputError("row must be >= 1")
    return index


def _table_ensure_column(obj: dict, label: str) -> str:
    """按列 label 取列 key；列不存在则自动追加到 schema（对齐影刀动态加列语义）。

    行数据按列 label 寻址（key 即 label），导出按 columns 顺序带表头。
    """
    for column in obj["columns"]:
        if column.get("label") == label:
            return str(column["key"])
    obj["columns"].append({"key": str(label), "label": str(label), "type": "text"})
    return str(label)


def _table_effect(
    invocation: CommandInvocation, kind: EffectKind, resource: str, *, idempotency: bool
) -> list[EffectRecord]:
    """按 manifest 声明构造匹配的 COMMITTED 证据（orchestrator 校验非 pure 命令必须返回）。"""
    idempotency_key = hashlib.sha256(resource.encode("utf-8")).hexdigest() if idempotency else None
    return [
        EffectRecord.committed(
            invocation,
            kind=kind,
            resource=f"file:{resource}",
            idempotency_key=idempotency_key,
        )
    ]


def _table_get_cell(invocation, flow_dir: str, table: str, inputs: dict) -> CommandResult:
    obj = _table.load_table(flow_dir, table)
    row = _table_row_index(inputs.get("row"))
    column = str(inputs.get("column") or "")
    var_name = str(inputs["varName"])
    key = _table.column_key_by_label(obj["columns"], column)
    rows = obj["rows"]
    value = rows[row - 1].get(key) if 0 < row <= len(rows) else None
    return CommandResult.success(
        value=value,
        outputs={"varName": var_name, "value": value},
        effects=_table_effect(
            invocation, EffectKind.READ, str(_table.table_file(flow_dir, table)), idempotency=False
        ),
    )


def _table_set_cell(invocation, flow_dir: str, table: str, inputs: dict) -> CommandResult:
    obj = _table.load_table(flow_dir, table)
    row = _table_row_index(inputs.get("row"))
    column = str(inputs.get("column") or "")
    key = _table_ensure_column(obj, column)
    rows = obj["rows"]
    while len(rows) < row:
        rows.append({})
    rows[row - 1][key] = inputs["value"]
    _table.save_table(obj, flow_dir, table)
    resource = str(_table.table_file(flow_dir, table))
    return CommandResult.success(
        value=None,
        outputs={"cellCount": 1},
        effects=_table_effect(invocation, EffectKind.IDEMPOTENT_WRITE, resource, idempotency=True),
    )


def _table_append_row(invocation, flow_dir: str, table: str, inputs: dict) -> CommandResult:
    row_input = inputs.get("row")
    if not isinstance(row_input, dict):
        raise _TableInputError("row must be an object {column: value}")
    obj = _table.load_table(flow_dir, table)
    new_row = {
        _table_ensure_column(obj, str(label)): value
        for label, value in row_input.items()
    }
    obj["rows"].append(new_row)
    _table.save_table(obj, flow_dir, table)
    count = len(obj["rows"])
    resource = str(_table.table_file(flow_dir, table))
    return CommandResult.success(
        value=None,
        outputs={"index": count, "rowCount": count},
        effects=_table_effect(invocation, EffectKind.UNSAFE_WRITE, resource, idempotency=False),
    )


def _table_delete_row(invocation, flow_dir: str, table: str, inputs: dict) -> CommandResult:
    obj = _table.load_table(flow_dir, table)
    row = _table_row_index(inputs.get("row"))
    rows = obj["rows"]
    if row > len(rows):
        raise _TableInputError(f"row {row} out of range (only {len(rows)} rows)")
    del rows[row - 1]
    _table.save_table(obj, flow_dir, table)
    resource = str(_table.table_file(flow_dir, table))
    return CommandResult.success(
        value=None,
        outputs={"rowCount": len(rows)},
        effects=_table_effect(invocation, EffectKind.UNSAFE_WRITE, resource, idempotency=False),
    )


def _table_clear(invocation, flow_dir: str, table: str, inputs: dict) -> CommandResult:
    obj = _table.load_table(flow_dir, table)
    obj["rows"] = []
    _table.save_table(obj, flow_dir, table)
    resource = str(_table.table_file(flow_dir, table))
    return CommandResult.success(
        value=None,
        outputs={"rowCount": 0},
        effects=_table_effect(
            invocation, EffectKind.IDEMPOTENT_WRITE, resource, idempotency=True
        ),
    )


def _table_export_csv(invocation, flow_dir: str, table: str, inputs: dict) -> CommandResult:
    obj = _table.load_table(flow_dir, table)
    base = Path(flow_dir).resolve()
    raw_path = str(inputs.get("path") or "").strip()
    if raw_path:
        candidate = Path(raw_path)
        output_path = (
            candidate.resolve() if candidate.is_absolute() else (base / candidate).resolve()
        )
    else:
        output_path = _table.table_file(flow_dir, table).with_suffix(".csv")
    # 只允许导出到流程目录内，防止越权写任意路径
    if output_path != base and base not in output_path.parents:
        raise _TableInputError("export path must stay inside flow dir", outside=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(_table.rows_to_csv(obj["columns"], obj["rows"]))
    resource = str(output_path.resolve())
    return CommandResult.success(
        outputs={"path": resource},
        effects=_table_effect(
            invocation, EffectKind.IDEMPOTENT_WRITE, resource, idempotency=True
        ),
    )


_TABLE_HANDLERS = {
    "data.table.getCell": _table_get_cell,
    "data.table.setCell": _table_set_cell,
    "data.table.appendRow": _table_append_row,
    "data.table.deleteRow": _table_delete_row,
    "data.table.clear": _table_clear,
    "data.table.exportCsv": _table_export_csv,
}


def execute(invocation: CommandInvocation) -> CommandResult:
    if invocation.command_id.startswith("data.table."):
        return _execute_table_command(invocation)
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
    if invocation.command_id == "data.readText":
        workspace, output_path = _resolve_output_path(invocation.inputs)
        if not _within_workspace(workspace, output_path):
            return CommandResult.failure(
                ErrorCode.CAPABILITY_DENIED, "Path must stay inside workspace"
            )
        if not output_path.is_file():
            return CommandResult.failure(
                ErrorCode.INVALID_INPUT, f"File not found: {output_path}"
            )
        text = output_path.read_text(encoding="utf-8")
        return CommandResult.success(
            value=text,
            outputs={"text": text, "path": str(output_path)},
            effects=_read_effect(invocation, output_path),
        )
    if invocation.command_id == "data.appendText":
        workspace, output_path = _resolve_output_path(invocation.inputs)
        if not _within_workspace(workspace, output_path):
            return CommandResult.failure(
                ErrorCode.CAPABILITY_DENIED, "Path must stay inside workspace"
            )
        if "text" in invocation.inputs:
            content = str(invocation.inputs["text"])
        else:
            content = "\n".join(str(line) for line in invocation.inputs["lines"])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        return CommandResult.success(
            outputs={"path": str(output_path)},
            effects=_unsafe_write_effect(invocation, "appendText", output_path),
        )
    if invocation.command_id == "data.fileExists":
        workspace, output_path = _resolve_output_path(invocation.inputs)
        if not _within_workspace(workspace, output_path):
            return CommandResult.failure(
                ErrorCode.CAPABILITY_DENIED, "Path must stay inside workspace"
            )
        exists = output_path.exists()
        return CommandResult.success(
            value=exists,
            outputs={"exists": exists, "path": str(output_path)},
            effects=_read_effect(invocation, output_path),
        )
    if invocation.command_id == "data.deletePath":
        workspace, output_path = _resolve_output_path(invocation.inputs)
        if not _within_workspace(workspace, output_path):
            return CommandResult.failure(
                ErrorCode.CAPABILITY_DENIED, "Path must stay inside workspace"
            )
        if not output_path.exists():
            return CommandResult.success(
                value=False,
                outputs={"deleted": False, "path": str(output_path)},
                effects=_unsafe_write_effect(invocation, "deletePath-noop", output_path),
            )
        try:
            if output_path.is_dir():
                if bool(invocation.inputs.get("recursive", False)):
                    shutil.rmtree(output_path)
                else:
                    output_path.rmdir()
            else:
                output_path.unlink()
        except OSError as exc:
            return CommandResult.failure(
                ErrorCode.INVALID_INPUT, f"delete failed: {exc}"
            )
        return CommandResult.success(
            value=True,
            outputs={"deleted": True, "path": str(output_path)},
            effects=_unsafe_write_effect(invocation, "deletePath", output_path),
        )
    if invocation.command_id == "data.datetimeNow":
        fmt = invocation.inputs.get("format")
        now = datetime.datetime.now()
        iso = now.isoformat()
        value = now.strftime(str(fmt)) if fmt else iso
        return CommandResult.success(
            value=value,
            outputs={"value": value, "iso": iso, "timestamp": int(time.time())},
        )
    if invocation.command_id == "workflow.sleep":
        seconds = float(invocation.inputs["seconds"])
        if seconds < 0:
            return CommandResult.failure(
                ErrorCode.INVALID_INPUT, "seconds must be >= 0"
            )
        time.sleep(seconds)
        return CommandResult.success(
            value=int(seconds * 1000),
            outputs={"sleptMs": int(seconds * 1000)},
        )
    if invocation.command_id == "data.limit":
        items = invocation.inputs["items"]
        count = int(invocation.inputs["count"])
        sliced = list(items)[:count]
        return CommandResult.success(
            value=sliced,
            outputs={"items": sliced, "count": len(sliced)},
        )
    if invocation.command_id == "data.setVar":
        # 变量写入由 orchestrator 按 manifest x-var-write 完成（规则 3：handler 不改 scopes）。
        # 这里按 varType 对 value 类型格式化（对标影刀），回传 varName + value。
        var_name = str(invocation.inputs["varName"])
        value = invocation.inputs["value"]
        # 缺省类型=字符串（对标影刀：默认按字符串格式化）；auto 已移除，未知值按原样兜底
        var_type = invocation.inputs.get("varType") or "string"
        if var_type != "auto":
            try:
                value = _coerce_by_type(value, var_type)
            except ValueError as exc:
                return CommandResult.failure(
                    ErrorCode.INVALID_INPUT, f"varType={var_type}: {exc}"
                )
        return CommandResult.success(
            value=value, outputs={"varName": var_name, "value": value}
        )
    if invocation.command_id == "data.log":
        # 打印日志（对标影刀「打印日志」）：纯透传——message 已由 orchestrator
        # 按 ${} / fx 标签解析为最终文本，回传 outputs 供运行日志/结果消费。
        message = str(invocation.inputs.get("message", ""))
        level = str(invocation.inputs.get("level") or "info")
        return CommandResult.success(
            value=message, outputs={"message": message, "level": level}
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
