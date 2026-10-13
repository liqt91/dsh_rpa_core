"""M58 只读探针：验证编辑态双缓冲 / 多行输入区 / Py 两态样式。

不修改任何被跟踪文件；只打印中间态，供人工核对后写判据。
"""

from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPlainTextEdit  # noqa: E402

from rpa_core.gui.param_form import ParamForm  # noqa: E402


def schema() -> dict:
    return {
        "type": "object",
        "required": [],
        "properties": {
            "text": {"type": "string", "x-fx": True, "x-python": True},
            "script": {"type": "string", "x-multiline": True},
            "both": {"type": "string", "x-fx": True, "x-python": True, "x-multiline": True},
            "plain": {"type": "string"},
        },
    }


def editor(form: ParamForm, name: str):
    return form._expr_rows[name][2]


def field_widget(form: ParamForm, name: str):
    """按字段名取取值控件（多行字段可能没有表达式行，得从 _fields 拿）。"""
    return next(w for f, _kind, w in form._fields if f == name)


def main() -> int:
    app = QApplication.instance() or QApplication([])  # noqa: F841
    form = ParamForm(schema(), {"text": "abc", "script": "line1\nline2"})

    print("=== 1. 控件形态 ===")
    for name in ("text", "script", "both", "plain"):
        row = form._expr_rows.get(name)
        if row is None:
            print(f"  {name:<8} 无表达式行")
            continue
        py, fx, ed = row
        print(
            f"  {name:<8} 编辑器={type(ed).__name__:<15} "
            f"Py={py is not None} fx={fx is not None} "
            f"Py宽度={py.minimumWidth() if py else '-'} "
            f"Py样式={'有' if py and py.styleSheet() else '无'}"
        )

    print()
    print("=== 2. 双缓冲：切换不丢内容 ===")
    ed = editor(form, "text")
    print(f"  初始            内容={ed.text()!r} 模式={form.expr_modes()}")
    py_btn = form._expr_rows["text"][0]
    py_btn.setChecked(True)
    print(f"  点 Py           内容={ed.text()!r} 模式={form.expr_modes()}")
    ed.setText("len(abc)")
    print(f"  python 里改写   内容={ed.text()!r} 模式={form.expr_modes()}")
    py_btn.setChecked(False)
    print(f"  取消 Py         内容={ed.text()!r} 模式={form.expr_modes()}")
    py_btn.setChecked(True)
    print(f"  再点 Py         内容={ed.text()!r} 模式={form.expr_modes()}")
    print(f"  槽内容          {form._expr_slots.get('text')}")

    print()
    print("=== 3. 多行字段取值 ===")
    md = field_widget(form, "script")
    print(f"  script 类型={type(md).__name__} 内容={md.toPlainText()!r}")
    print(f"  values() -> {json.dumps(form.values(), ensure_ascii=False)}")

    print()
    print("=== 4. 多行字段的 fx 插入（should insert at cursor） ===")
    md2 = editor(form, "both")
    py2, fx2, _ = form._expr_rows["both"]
    form._insert_variable("both", md2, "web")
    print(f"  插入后 内容={md2.toPlainText()!r} 模式={form.expr_modes()}")
    py2.setChecked(True)
    form._insert_variable("both", md2, "web")
    print(f"  python 下插入 内容={md2.toPlainText()!r} 模式={form.expr_modes()}")

    print()
    print("=== 5. 表达式态底色（多行控件的 QSS 选择器） ===")
    print(f"  both 样式={md2.styleSheet()!r}")
    print(f"  script(非表达式) 样式={md.styleSheet()!r}")

    print()
    print("=== 6. 落盘形状：只有当前模式那一份 ===")
    print(f"  values() -> {json.dumps(form.values(), ensure_ascii=False)}")
    print(f"  expr_modes() -> {form.expr_modes()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
