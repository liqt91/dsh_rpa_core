"""命令面板（Ctrl+P，M49 P3）。

与 :mod:`rpa_core.gui.command_palette` **不是一回事**：后者是左指令树的卡片渲染
（影刀式指令卡片的 ``QStyledItemDelegate``），本模块是快捷键唤起的搜索面板——
一个输入框 + 结果列表，回车即执行。名字相近是历史原因（左树那块先叫了
command_palette），这里刻意另起一个模块，避免两件事混在一个文件里。

两类条目，执行各走一条**既有**通路，不新造语义：

- ``command``：插入画布 —— 复用 ``MainWindow.add_command``（等价左树双击）；
- ``node``：跳到画布已有节点 —— 复用画布内查找的「展开祖先 + 选中 + 滚动居中」。

匹配逻辑抽成模块级**纯函数** :func:`rank_entries`，对话框只是它的壳：判据可以直测
排序规则，不必合成键盘事件去驱动一个真模态窗口。
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

# 条目类型（与 MainWindow._apply_palette_entry 的分派一一对应）
KIND_COMMAND = "command"
KIND_NODE = "node"

# 结果上限：再多也只是滚动噪声，还拖慢每次输入的重建
MAX_RESULTS = 50

# 匹配档位（越小越靠前）：0 命中标题 / 1 命中标识 / 2 仅命中副文本 / 3 子序列兜底
_RANK_LABEL = 0
_RANK_PAYLOAD = 1
_RANK_DETAIL = 2
_RANK_SUBSEQUENCE = 3


@dataclass(frozen=True)
class PaletteEntry:
    """一条可执行结果。

    ``payload`` 是执行所需的标识（command 存命令 id，node 存节点 id）；
    ``label`` 是给用户看的主文本，``detail`` 是副文本（命令 id / 执行器 / 节点 id）。
    """

    kind: str
    label: str
    detail: str = ""
    payload: str = ""


def _is_subsequence(query: str, text: str) -> bool:
    """``query`` 的字符是否按序出现在 ``text`` 中（"brnav" → "browser.navigate"）。"""
    it = iter(text)
    return all(ch in it for ch in query)


def _match_rank(entry: PaletteEntry, query: str) -> tuple[int, int, int] | None:
    """返回 ``(档位, 次要键, 长度)`` 排序键；未命中返回 None。

    次要键是「命中位置」（标题内越靠前越优先）或「文本长度」（短的更可能是用户想要的，
    例如 ``nav`` 命中 ``browser.navigate`` 比命中一长串说明更该排在前面）。
    """
    label = entry.label.lower()
    at = label.find(query)
    if at >= 0:
        return (_RANK_LABEL, at, len(label))
    payload = entry.payload.lower()
    if query in payload:
        return (_RANK_PAYLOAD, len(payload), 0)
    detail = entry.detail.lower()
    if query in detail:
        return (_RANK_DETAIL, len(detail), 0)
    if len(query) >= 2 and _is_subsequence(query, payload):
        return (_RANK_SUBSEQUENCE, len(payload), 0)
    return None


def rank_entries(
    entries: list[PaletteEntry], query: str, *, limit: int = MAX_RESULTS
) -> list[PaletteEntry]:
    """按 ``query`` 过滤并排序；空查询返回原序（只截 ``limit`` 条）。

    排序键带上原始下标，保证同档位内**稳定**——不依赖 dict/set 的遍历顺序，
    否则同一份输入在两次打开面板时可能给出不同顺序。
    """
    query = (query or "").strip().lower()
    if not query:
        return list(entries[:limit])
    scored: list[tuple[tuple[int, int, int], int, PaletteEntry]] = []
    for index, entry in enumerate(entries):
        rank = _match_rank(entry, query)
        if rank is not None:
            scored.append((rank, index, entry))
    scored.sort(key=lambda row: (row[0], row[1]))
    return [entry for _rank, _index, entry in scored[:limit]]


class PaletteDialog(QDialog):
    """命令面板对话框：输入框 + 结果列表；选中条目由 :attr:`chosen` 带出。"""

    def __init__(self, entries: list[PaletteEntry], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("命令面板")
        self.setObjectName("paletteDialog")
        self.setMinimumWidth(480)
        self.chosen: PaletteEntry | None = None
        self._all = list(entries)
        self._shown: list[PaletteEntry] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)
        self.input = QLineEdit(self)
        self.input.setObjectName("paletteInput")
        self.input.setPlaceholderText("搜索指令（插入画布）或节点（跳转）…")
        self.input.setClearButtonEnabled(True)
        layout.addWidget(self.input)
        self.list = QListWidget(self)
        self.list.setObjectName("paletteList")
        layout.addWidget(self.list)
        self.hint = QLabel("", self)
        layout.addWidget(self.hint)

        self.input.textChanged.connect(self.refresh)
        self.input.returnPressed.connect(self.accept_current)
        self.input.installEventFilter(self)  # ↑↓ 在输入框里也能选
        self.list.itemActivated.connect(lambda _item: self.accept_current())
        self.list.itemDoubleClicked.connect(lambda _item: self.accept_current())
        self.refresh(self.input.text())

    def eventFilter(self, obj, event):  # noqa: N802 (Qt naming)
        """输入框内的 ↑↓ 移动列表选中项（焦点始终留在输入框，便于继续输入）。"""
        if obj is self.input and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                row = self.list.currentRow()
                row += 1 if key == Qt.Key.Key_Down else -1
                if 0 <= row < self.list.count():
                    self.list.setCurrentRow(row)
                return True
        return super().eventFilter(obj, event)

    def refresh(self, text: str) -> None:
        """按输入重算结果列表；有结果时自动选中首行（回车即执行第一条）。"""
        self._shown = rank_entries(self._all, text)
        self.list.clear()
        for entry in self._shown:
            row = f"{entry.label}    {entry.detail}".rstrip()
            item = QListWidgetItem(row)
            item.setToolTip(entry.detail or entry.label)
            self.list.addItem(item)
        if self._shown:
            self.list.setCurrentRow(0)
        total = len(self._shown)
        self.hint.setText(
            f"{total} 条结果 · ↑↓ 选择 · Enter 执行 · Esc 关闭"
            if total
            else "没有匹配项 · Esc 关闭"
        )

    def accept_current(self) -> None:
        """把当前选中项记为结果并关闭（Enter / 双击 / 激活都走这里）。"""
        row = self.list.currentRow()
        if 0 <= row < len(self._shown):
            self.chosen = self._shown[row]
            self.accept()


def palette_prompt(
    entries: list[PaletteEntry], parent=None
) -> PaletteEntry | None:
    """弹出命令面板；返回选中条目，取消返回 None。

    独立成模块级函数（同 ``app.flow_inputs_prompt``）：测试直接替换它就能验证
    「插入指令 / 跳转节点 / 取消」三条分支，不必合成键盘事件去驱动真模态窗口。
    """
    dialog = PaletteDialog(entries, parent)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return dialog.chosen
