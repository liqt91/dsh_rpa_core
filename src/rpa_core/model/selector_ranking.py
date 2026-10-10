"""M52 选择器优选：跨端共享的排序引擎与稳定性先验。

「唯一性硬门 + penalty 最小化」是**两腿共用**的唯一择优算法（:func:`rank_candidates`）——
先按命中数筛（唯一命中恒优先），同命中数再比 penalty。各端的 penalty 表（稳定先验）
分别定义：桌面 locator 在 ``model.desktop``（``LOCATOR_FIELD_PENALTIES``），
浏览器候选在本模块（:func:`web_candidate_penalty`）。

依据 2026-10-10 开源调研（@medv/finder 的 penalty 表 / Robula+ 的属性白黑名单 /
Playwright codegen 的语义阶梯）：**语义标签 / 固定 id / 测试属性**＝低 penalty；
**class 组合、位置序号、像哈希的 id**＝高 penalty。表是**先验**、**可调**，
判据只钉「表被读到、合成生效」，不钉具体数值（否则调表即静默变红）。
"""

from __future__ import annotations

import re


def looks_generated_id(value: str) -> bool:
    """标识是否像**框架 / 运行期生成**（而非人手写的固定值）。

    Playwright / Robula+ 的口径是「语义标签优先」——固定 id 稳，但 `css-1x2y3z`、
    `ember123`、长十六进制哈希这类**生成 id** 会随构建 / 渲染变，要**降权**而不是给
    最高分。这是「比『id 最稳』更细」的那一层（M52 调研）。**两端共用同一份判定**
    （桌面 ``automationId`` 与浏览器 ``#id`` 都经它降权），避免两套哈希启发式漂移。
    """
    text = value.strip()
    if not text:
        return False
    if re.fullmatch(r"[0-9a-f]{8,}", text, re.IGNORECASE):
        return True
    if re.fullmatch(r"css-[\w-]+", text, re.IGNORECASE):
        return True
    if re.fullmatch(r"ember\d+", text, re.IGNORECASE):
        return True
    return bool(re.fullmatch(r"\w*\d{6,}", text))


def rank_candidates(entries: list[dict], *, count_of, penalty_of) -> list[dict]:
    """按 ``(count 升序, penalty 升序)`` **稳定**排序——两腿共用的唯一择优算法。

    ``count`` 是**第一关键字** ⇒ 唯一命中（count==1）恒排在任何 count>1 之前，
    这就是「**唯一性硬门**」（不是加权因子）；同 count 再比 penalty；完全并列时
    Python 稳定排序**保留输入序** ⇒ 「**稳定序**」（不依赖集合遍历顺序，防 flaky）。

    只做排序、不做合法性清洗——各端入口自行清洗后传入（桌面要求 ``locator`` 是 dict，
    浏览器要求 ``selector`` 非空），「什么算合法候选」的判据因此留在各自的域里。
    """
    return sorted(entries, key=lambda entry: (count_of(entry), penalty_of(entry)))


# ---- 浏览器候选的稳定性先验 -------------------------------------------------
# ``kind`` 是捕获时给候选分的类：content.js 的 candidatesFor 用 id/attribute/path；
# 影刀导出与历史资产用语义 kind（name/placeholder/role…）。值越小越稳。
WEB_KIND_PENALTIES: dict[str, int] = {
    "id": 0,             # 固定 id 最稳（像哈希的 id 由 web_candidate_penalty 降权）
    "data-testid": 1,
    "data-test": 2,
    "data-qa": 2,
    "name": 3,           # 表单 name / 可访问名：语义强、跨改版稳
    "aria-label": 4,
    "placeholder": 5,
    "title": 6,
    "attribute": 10,     # 通用属性槽：按 selector 里的属性名细分（见下表）
    "path": 20,          # 祖先链的收缩写法（tag.class / 后代组合）：比属性弱、比裸 tag 强
    "role": 30,          # 角色定位较泛，容易命中一类而不止一个
}

# ``kind == "attribute"`` 时从 selector 抽出属性名再查这张表（content.js 收的 7 个属性）。
WEB_ATTRIBUTE_PENALTIES: dict[str, int] = {
    "data-testid": 1,
    "data-test": 2,
    "data-qa": 2,
    "name": 3,
    "aria-label": 4,
    "placeholder": 5,
    "title": 6,
}

WEB_HASHY_ID_PENALTY = 70           # kind=="id" 但值像框架生成哈希 → 比语义属性还脆
WEB_UNKNOWN_KIND_PENALTY = 50       # 表里没有的 kind
WEB_UNKNOWN_ATTRIBUTE_PENALTY = 15  # kind=="attribute" 但属性名不在表里
WEB_UNMEASURED_COUNT = 10 ** 9      # matchedCount 缺席 = 未实测 → 排到所有实测候选之后


def _attribute_name(selector: str) -> str | None:
    """从形如 ``div[data-testid="x"]`` 的 CSS 串里抽出属性名（小写）。"""
    match = re.search(r"\[\s*([\w-]+)\s*[=~|^$*]?=", selector)
    return match.group(1).lower() if match else None


def web_candidate_penalty(candidate: dict) -> int:
    """一个浏览器候选（``{kind, selector, matchedCount?}``）的 penalty——越小越稳。

    纯函数、表驱动：``kind`` 查 ``WEB_KIND_PENALTIES``；``kind == "attribute"`` 时
    再按 selector 里的属性名查 ``WEB_ATTRIBUTE_PENALTIES``；``kind == "id"`` 时值像
    哈希则改用 ``WEB_HASHY_ID_PENALTY``（两端共用 :func:`looks_generated_id`）。
    """
    kind = str(candidate.get("kind") or "").strip()
    selector = str(candidate.get("selector") or "")
    if kind == "id":
        raw = selector.lstrip("#").strip()
        if raw and looks_generated_id(raw):
            return WEB_HASHY_ID_PENALTY
        return WEB_KIND_PENALTIES["id"]
    if kind == "attribute":
        name = _attribute_name(selector)
        if name:
            return WEB_ATTRIBUTE_PENALTIES.get(name, WEB_UNKNOWN_ATTRIBUTE_PENALTY)
        return WEB_KIND_PENALTIES["attribute"]
    return WEB_KIND_PENALTIES.get(kind, WEB_UNKNOWN_KIND_PENALTY)


def _candidate_count(candidate: dict) -> int:
    """候选的命中数；``matchedCount`` 缺席/非法 ⇒ ``WEB_UNMEASURED_COUNT``（排最后）。"""
    value = candidate.get("matchedCount")
    if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return value
    return WEB_UNMEASURED_COUNT


def rank_web_candidates(candidates: list[dict]) -> list[dict]:
    """把浏览器候选按「唯一优先、其次稳定」排序（运行期自愈按此序逐个 try）。

    唯一事实来源：元素资产 ``selector.candidates``（捕获时落盘）。清洗只保留
    ``selector`` 非空的 dict（与 ``model.capture._candidate_errors`` 同口径）。
    """
    usable = [
        candidate
        for candidate in candidates
        if isinstance(candidate, dict)
        and isinstance(candidate.get("selector"), str)
        and candidate.get("selector").strip()
    ]
    return rank_candidates(
        usable,
        count_of=_candidate_count,
        penalty_of=web_candidate_penalty,
    )
