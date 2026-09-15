"""指令参数表单（GUI 切片 3）。

把 ``CommandManifest.input_schema``（JSON Schema dict）渲染成原生控件表单，
并支持把编辑结果收回为参数 dict 写回画布卡片。

控件映射（最小切片，fx 表达式切换在后续切片接入）：

- ``string`` + ``enum`` → QComboBox：首项「未设置」，``x-enum-labels``
  提供中文显示文本，itemData 存实际枚举值；
- ``string`` → QLineEdit：空文本 = 未设置，placeholder 提示默认值；
- ``integer`` / ``number`` → QLineEdit + 校验器：空文本 = 未设置；
- ``boolean`` → QCheckBox；
- ``array`` / ``object`` 等复合类型 → QLineEdit 接受 JSON 文本，
  空文本 = 未设置，非法 JSON 在收集时抛 ValueError；
- type 写成列表（如 ``["string", "null"]``）时取首个非 null 类型。
"""

from __future__ import annotations

import json
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QDoubleValidator, QIntValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QWidget,
)

# 字段条目类型标记（决定 values() 如何从控件取值）
_KIND_ENUM = "enum"
_KIND_TEXT = "text"
_KIND_INT = "integer"
_KIND_NUMBER = "number"
_KIND_BOOL = "boolean"
_KIND_JSON = "json"


def _effective_type(field_schema: dict[str, Any]) -> str:
    """取字段实际类型；type 为列表时选第一个非 'null' 的候选。"""
    raw_type = field_schema.get("type", "string")
    if isinstance(raw_type, list):
        for candidate in raw_type:
            if candidate != "null":
                return candidate
        return "string"
    return raw_type


class ParamForm(QWidget):
    """按 input_schema 构建的参数表单；values() 收集当前编辑结果。"""

    def __init__(
        self,
        schema: dict[str, Any],
        args: dict[str, Any] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        args = dict(args or {})
        self._required: set[str] = set(schema.get("required", []))
        # (字段名, 种类, 控件)：顺序即 properties 声明顺序
        self._fields: list[tuple[str, str, QWidget]] = []

        self._form = QFormLayout(self)
        self._form.setContentsMargins(8, 8, 8, 8)
        self._form.setLabelAlignment(Qt.AlignmentFlag.AlignTop)

        properties = schema.get("properties", {})
        for name, field_schema in properties.items():
            self._build_field(name, field_schema, args)

    # ---- 逐类型构建 -------------------------------------------------------
    def _build_field(
        self, name: str, field_schema: dict[str, Any], args: dict[str, Any]
    ) -> None:
        """按字段类型创建控件并登记；description 进 tooltip。"""
        value_type = _effective_type(field_schema)
        has_value = name in args

        if value_type == "string" and "enum" in field_schema:
            widget = self._build_enum(field_schema, args.get(name), has_value)
            kind = _KIND_ENUM
        elif value_type == "string":
            widget = self._build_text(field_schema, args.get(name), has_value)
            kind = _KIND_TEXT
        elif value_type == "integer":
            widget = self._build_number_line(
                field_schema, args.get(name), has_value, QIntValidator(-10**9, 10**9),
            )
            kind = _KIND_INT
        elif value_type == "number":
            widget = self._build_number_line(
                field_schema, args.get(name), has_value, QDoubleValidator(-10**9, 10**9, 6),
            )
            kind = _KIND_NUMBER
        elif value_type == "boolean":
            widget = QCheckBox()
            widget.setChecked(bool(args.get(name, False)))
            kind = _KIND_BOOL
        else:
            # array/object 等复合类型：单行 JSON 文本编辑
            text = json.dumps(args[name], ensure_ascii=False) if has_value else ""
            widget = self._build_text(field_schema, text, has_value)
            widget.setPlaceholderText("JSON，如 [\"a\", \"b\"]")
            kind = _KIND_JSON

        self._attach(name, kind, widget, field_schema)

    @staticmethod
    def _build_enum(
        field_schema: dict[str, Any], current: Any, has_value: bool
    ) -> QComboBox:
        """enum → 下拉框：首项「未设置」(None)，标签优先取 x-enum-labels。"""
        combo = QComboBox()
        combo.addItem("未设置", None)
        labels = field_schema.get("x-enum-labels", {})
        for enum_value in field_schema["enum"]:
            combo.addItem(str(labels.get(enum_value, enum_value)), enum_value)
        if has_value:
            index = combo.findData(current)
            combo.setCurrentIndex(index if index >= 0 else 0)
        return combo

    @staticmethod
    def _build_text(
        field_schema: dict[str, Any], current: Any, has_value: bool
    ) -> QLineEdit:
        """string → 单行输入；空 = 未设置；default 进 placeholder。"""
        edit = QLineEdit()
        if has_value and current is not None:
            edit.setText(str(current))
        default = field_schema.get("default")
        if default is not None:
            edit.setPlaceholderText(f"默认：{default}")
        return edit

    @staticmethod
    def _build_number_line(
        field_schema: dict[str, Any],
        current: Any,
        has_value: bool,
        validator,
    ) -> QLineEdit:
        """integer/number → 带校验器的单行输入（空 = 未设置）。"""
        edit = QLineEdit()
        edit.setValidator(validator)
        if has_value and current is not None:
            edit.setText(str(current))
        minimum = field_schema.get("minimum")
        default = field_schema.get("default")
        hints = []
        if minimum is not None:
            hints.append(f"≥ {minimum}")
        if default is not None:
            hints.append(f"默认 {default}")
        if hints:
            edit.setPlaceholderText("，".join(hints))
        return edit

    def _attach(
        self,
        name: str,
        kind: str,
        widget: QWidget,
        field_schema: dict[str, Any],
    ) -> None:
        """登记字段并挂到表单；label 带必填星号，description 进 tooltip。"""
        self._fields.append((name, kind, widget))
        label_text = f"{name} *" if name in self._required else name
        description = field_schema.get("description")
        if description:
            widget.setToolTip(description)
        label = QLabel(label_text)
        if description:
            label.setToolTip(description)
        self._form.addRow(label, widget)

    # ---- 收集 -------------------------------------------------------------
    def values(self) -> dict[str, Any]:
        """把控件当前值收集为参数 dict（保持字段声明顺序）。

        未设置项不进入结果；JSON 字段解析失败时抛 ValueError。
        """
        result: dict[str, Any] = {}
        for name, kind, widget in self._fields:
            if kind == _KIND_ENUM:
                chosen = widget.currentData()
                if chosen is None:
                    continue
                result[name] = chosen
            elif kind == _KIND_TEXT:
                text = widget.text()
                if not text:
                    continue
                result[name] = text
            elif kind in (_KIND_INT, _KIND_NUMBER):
                text = widget.text()
                if not text:
                    continue
                result[name] = int(text) if kind == _KIND_INT else float(text)
            elif kind == _KIND_BOOL:
                result[name] = widget.isChecked()
            else:  # JSON 复合字段
                text = widget.text()
                if not text:
                    continue
                try:
                    result[name] = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"字段 {name} 不是合法 JSON：{exc}") from exc
        return result


def build_param_form(
    manifest_or_schema: Any, args: dict[str, Any] | None = None
) -> ParamForm:
    """便捷构造：可传 manifest（取其 input_schema）或直接传 schema dict。"""
    schema = getattr(manifest_or_schema, "input_schema", manifest_or_schema)
    return ParamForm(schema, args)
