"""流程数据表格的读写帮助模块（运行态）。

仅依赖 stdlib 与 rpa_core.model，不可 import 设计态层（devserver），避免依赖倒挂。
表文件为流程目录下的独立资产：``<flow_dir>/data/table.json``，单 JSON 对象。
"""

from __future__ import annotations

import csv
import io
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

SCHEMA_VERSION = 1
# 表格名白名单：仅允许字母/数字/下划线/连字符，防路径穿越（表名→文件名）
_TABLE_NAME_RE = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"


def validate_table_name(table: str) -> str:
    """校验表格名并返回对应表文件名；非法名称抛 ValueError。"""
    if not table or not all(ch in _TABLE_NAME_RE for ch in table):
        raise ValueError(f"invalid table name: {table!r}")
    # default 表 = 单表主表，固定映射为 table.json；其余表名 → <name>.json（预留多表）
    return "table.json" if table == "default" else f"{table}.json"


def table_file(flow_dir: str | Path, table: str = "default") -> Path:
    """表格文件绝对路径（默认表 table.json）。"""
    return Path(flow_dir) / "data" / validate_table_name(table)


def empty_table() -> dict:
    """缺省空表结构（files 不存在 / 尚未建表时使用）。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "columns": [],
        "rows": [],
        "updated_at": _now_iso(),
    }


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def load_table(flow_dir: str | Path, table: str = "default") -> dict:
    """读取表对象；表文件不存在时返回空表。

    返回结构：``{"schema_version", "columns", "rows", "updated_at"}``。
    """
    path = table_file(flow_dir, table)
    if not path.exists():
        return empty_table()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # 损坏表文件按空表处理，避免单条脏数据拖垮流程
        return empty_table()
    return {
        "schema_version": int(raw.get("schema_version", SCHEMA_VERSION)),
        "columns": list(raw.get("columns") or []),
        "rows": list(raw.get("rows") or []),
        "updated_at": raw.get("updated_at") or _now_iso(),
    }


def save_table(obj: dict, flow_dir: str | Path, table: str = "default") -> None:
    """原子写回表对象（mkstemp + os.replace）。"""
    path = table_file(flow_dir, table)
    path.parent.mkdir(parents=True, exist_ok=True)
    obj["updated_at"] = _now_iso()
    _atomic_write_json(path, obj)


def _atomic_write_json(path: Path, obj: dict) -> None:
    payload = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".table-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def column_key_by_label(columns: list, label: str) -> str | None:
    """按列 label 找到列 key；找不到返回 None（列可增删，引用可能失效）。"""
    for column in columns:
        if column.get("label") == label:
            key = column.get("key")
            if key is None:
                # 兼容仅提供 label 的列（编辑器自动生成 key）
                key = str(label)
                column["key"] = key
            return str(key)
    # 宽容：即使列未在 columns 中共声明，也允许按 label 当作 key 读写
    return str(label)


def rows_to_csv(columns: list, rows: list) -> bytes:
    """按 columns 顺序导出 CSV（utf-8-sig 带 BOM，便于 Excel 打开中文）。

    columns 的键决定列顺序；行数据是 ``{key: value}`` 的 dict。
    """
    keys = [str(c.get("key", c.get("label", ""))) for c in columns]
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    if keys:
        writer.writerow([str(c.get("label", key)) for key, c in zip(keys, columns, strict=True)])
    for row in rows:
        writer.writerow([_csv_cell(row.get(key)) for key in keys])
    return buffer.getvalue().encode("utf-8-sig")


def _csv_cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)