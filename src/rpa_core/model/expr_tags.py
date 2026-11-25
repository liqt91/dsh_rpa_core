"""fx 表达式标签的**语法事实源**：``[name]`` / ``[name.field]``。

为什么单独一个模块：这套标签有两类消费者，而它们分处能力层的两端——

* 运行期求值：``runtime/resolver.py`` 把 ``[name]`` 就地替换成变量值；
* 编译期校验：``compiler/compiler.py`` 对同一批标签做引用有效性检查
  （「菜单里给出的变量必须能编译过」的另一半）。

两处各写一份正则就是「菜单说能用、编译说不行」的经典漂移源，所以正则只有这一份。
放在 ``model`` 包而不是任何一侧，是为了避开 ``compiler → runtime`` 的循环导入
（``runtime/__init__`` 会拉起 orchestrator，而 orchestrator 依赖 compiler）。
本模块只依赖 ``re``，不引入任何上层包。
"""

from __future__ import annotations

import re
from typing import Any

TAG_PATTERN = r"\[([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\]"
_TAG = re.compile(TAG_PATTERN)


def has_tags(value: Any) -> bool:
    """文本里是否含 fx 标签。

    判据只允许有一个事实源：编辑器靠它决定「字段是否仍处于标签模式」（最后一个标签
    被删 ⇒ 撤销 ``fx`` 态），运行期靠它决定要不要走标签替换。
    """
    return isinstance(value, str) and _TAG.search(value) is not None


def iter_tags(value: Any):
    """递归抽出一段输入里的全部标签名（``[a.b]`` → ``a.b``）。"""
    if isinstance(value, str):
        yield from _TAG.findall(value)
    elif isinstance(value, list):
        for item in value:
            yield from iter_tags(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from iter_tags(item)
