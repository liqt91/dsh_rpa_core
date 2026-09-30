"""GUI 模态弹框预算判据（M49 P1-3）。

**为什么要有这份白名单**：界面里「多一个弹框」几乎是零成本的（写一行 warning 就行），
于是弹框会随功能自然膨胀，最后每个操作都要点掉一个框。这份判据把模态框变成**显式预算**：

- 每个模态点都必须登记，并标注它是「决策型（用户必须选择）」还是「知会型（只是告知）」；
- 数量也钉住（同一文件同名弹框 3 处 → 变 4 处即红），所以「顺手再加一个」会立刻被拦下；
- 新增时要么说明为什么它值得打断用户，要么改用知会通道（状态栏 `showMessage` /
  页面内提示标签，如 home.py 的 `hint`）——这正是 M49 P1-3 的落点。

扫描面包含两种写法：``QMessageBox.warning(...)`` 这类静态调用（键=标题常量）与
``QMessageBox(self)`` 构造式（键=所在函数名，因为它的标题是随后 setText 的，没有稳定常量）。
"""

from __future__ import annotations

import ast
import pathlib
from collections import Counter

GUI_DIR = pathlib.Path(__file__).resolve().parents[2] / "src" / "rpa_core" / "gui"

# (文件, 调用方式, 标题/构造标识) -> (处数, 分类与理由)
MODAL_ALLOWLIST: dict[tuple[str, str, str], tuple[int, str]] = {
    # ---- 决策型：用户必须做选择，保留模态 ----
    ("app.py", "question", "未保存的修改"): (2, "决策型：丢弃/保存未保存修改、退出前三选一"),
    ("app.py", "构造", "_confirm_resume"): (1, "决策型：恢复运行前说明影响（默认不恢复）"),
    ("app.py", "warning", "浏览器插件离线"): (1, "决策型：是否仅桌面捕获（良性离线已跳过）"),
    ("app.py", "question", "同名元素已存在"): (1, "决策型：覆盖/取消"),
    ("home.py", "question", "删除流程"): (1, "决策型：不可逆删除，默认取消"),
    # ---- 知会型但保留：信息量大或属于「操作失败」，必须看得见 ----
    ("app.py", "warning", "打开失败"): (3, "知会型保留：文件/流程打不开，含异常详情"),
    ("app.py", "warning", "校验未通过"): (1, "知会型保留：校验问题清单可能多行，状态栏放不下"),
    ("home.py", "warning", "新建流程"): (1, "知会型保留：创建失败（store 校验/写入异常）"),
    ("home.py", "warning", "复制流程"): (1, "知会型保留：复制失败原因"),
    ("home.py", "warning", "重命名流程"): (1, "知会型保留：重命名失败原因"),
    ("home.py", "warning", "删除流程"): (1, "知会型保留：删除失败原因"),
    ("home.py", "warning", "导入流程"): (1, "知会型保留：导入失败原因"),
    ("home.py", "warning", "导出流程"): (1, "知会型保留：导出失败原因"),
    # 输入声明不合法时允许用户带着问题进编辑器修正，但必须先把问题说明白
    ("inputs_dialog.py", "warning", "现有输入声明不合法"): (1, "知会型保留"),
}

# 已被 P1-3 降级、并且**不许复活**的模态点：判据比对的是**正文**而不是标题——
# 「流程名称不能为空」的标题是「新建流程」（该标题下的「创建失败」仍合法保留），
# 只比标题会把两者混为一谈。
RETIRED_MODAL_TEXTS = (
    "流程名称不能为空",
    "正在编辑器里打开",
)


def _message_of(node: ast.Call) -> str:
    """静态调用第三个实参（正文）若是常量就取回，用于「退休判据」比对。"""
    if len(node.args) >= 3 and isinstance(node.args[2], ast.Constant):
        return str(node.args[2].value)
    return ""


def _modal_sites() -> list[tuple[str, str, str]]:
    """扫描 gui/*.py，返回 (文件名, 调用方式, 标题或所在函数名)。"""
    return [
        (site[0], site[1], site[2])
        for site in _modal_calls()
    ]


def _modal_calls() -> list[tuple[str, str, str, str]]:
    """扫描 gui/*.py，返回 (文件名, 调用方式, 标题或所在函数名, 正文)。"""
    sites: list[tuple[str, str, str, str]] = []
    for path in sorted(GUI_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        enclosing: dict[int, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for inner in ast.walk(node):
                    enclosing[id(inner)] = node.name
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            static = (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "QMessageBox"
            )
            ctor = isinstance(func, ast.Name) and func.id == "QMessageBox"
            if not (static or ctor):
                continue
            if static:
                title = ""
                if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                    title = str(node.args[1].value)
                sites.append((path.name, func.attr, title, _message_of(node)))
            else:
                sites.append((path.name, "构造", enclosing.get(id(node), "?"), _message_of(node)))
    return sites


def test_no_unregistered_modal_dialog() -> None:
    """所有模态弹框都必须在预算表里登记；新增即红。"""
    unknown = [
        f"{name}:{method}:{title!r}"
        for name, method, title in _modal_sites()
        if (name, method, title) not in MODAL_ALLOWLIST
    ]
    assert not unknown, (
        "新增了未登记的模态弹框：\n"
        + "\n".join(unknown)
        + "\n先问一句「用户需要为它做决定吗」——不需要就用知会通道"
        "（状态栏 showMessage 或页面内提示），确实需要才登记进 MODAL_ALLOWLIST。"
    )


def test_modal_dialog_budget_not_exceeded() -> None:
    """同名弹框的处数也要钉住：一处变两处同样是「弹框变多了」。"""
    counts = Counter(_modal_sites())
    over: list[str] = []
    for site, (expected, _reason) in MODAL_ALLOWLIST.items():
        actual = counts.get(site, 0)
        if actual != expected:
            over.append(f"{site}: 登记 {expected} 处，实际 {actual} 处")
    assert not over, "模态弹框处数与预算不符：\n" + "\n".join(over)


def test_retired_modals_do_not_come_back() -> None:
    """已降级为知会通道的场景不许再弹框（P1-3 的成果要用判据锁住）。

    比对的是弹框**正文**：同一标题下既有合法保留的弹框（「创建失败」），也有已退休的
    场景（「名称不能为空」），只比标题无法区分。
    """
    guilty = [
        f"{name}:{method}:{title!r} 正文含「{needle}」"
        for name, method, title, message in _modal_calls()
        for needle in RETIRED_MODAL_TEXTS
        if needle in message
    ]
    assert not guilty, (
        "这些场景已被降级为页面内提示（M49 P1-3），不该再弹模态框：\n" + "\n".join(guilty)
    )


def test_informational_channels_exist_in_home() -> None:
    """知会通道本身要还在：home 页的 hint 标签是「不打断」的落点。"""
    source = (GUI_DIR / "home.py").read_text(encoding="utf-8")
    assert "self.hint.setText" in source, "home 页的页面内提示通道不见了"
