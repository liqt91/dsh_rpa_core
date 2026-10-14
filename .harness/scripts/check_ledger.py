"""门禁：台账字段的长度上限 + 换行符（M63 S5）。

背景：`title` / `notes` / `evidence` 的消费方式都是「会话开始扫一眼」，但此前**没有任何门禁**
看着它们的长度，于是必然膨胀。实测（2026-10-14，M62 审计）：`feature_list.title` 最长
**9394** 字符、`project_state.notes` 单字段 **35443** 字符（占整个 JSON 的 54%）、
`PROGRESS.evidence` 最长 **6168**。三者合起来把开机读物撑到约 175 KB，其中绝大部分是历史——
AI 每开一次工，先把一半以上预算烧在「过去」。

**清一次不设闸门，明天就会长回来**：所以存量治理（M63 S5）与闸门（本文件）必须同时落地。

三条长度判据 + 一条换行判据：

1. `feature_list.json` 的每条 `title` ≤ `TITLE_LIMIT`（**全量**生效）；
2. `project_state.json` 的 `notes` ≤ `NOTES_LIMIT`（全量生效）；
3. `PROGRESS.md` 的 **cutoff 标记之后**追加的条目：`evidence` ≤ `EVIDENCE_LIMIT`，
   且必须写成**单行**（多行续行会被判为违规——否则长内容塞进续行就绕过了上限）；
4. 三个台账文件必须是 **LF**（`CRLF` 数 = 0）。

**为什么 3 是「只对新条目」**：`PROGRESS.md` 是 append-only 且**历史条目一律不改写**（工程铁律），
现存 119 条 `evidence` > 1000 字符，**不可能**在不篡改历史的前提下压下来。把上限设成「只对新条目
生效」是「不许改历史」与「不许再膨胀」唯一能同时成立的口径；历史原文留在原处，不做任何标记。

**防空转自检**：cutoff 标记必须存在且**恰好出现 1 次**。没有这条，判据 3 会在标记缺失时
静默检查 0 条——那就是一条永远绿的假判据（比没有更坏）。

运行：uv run python .harness/scripts/check_ledger.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FEATURES = ROOT / ".harness" / "feature_list.json"
PROJECT_STATE = ROOT / ".harness" / "project_state.json"
PROGRESS = ROOT / ".harness" / "PROGRESS.md"

TITLE_LIMIT = 120
NOTES_LIMIT = 2000
EVIDENCE_LIMIT = 400

# cutoff 标记：`PROGRESS.md` 里出现**恰好一次**，其下追加的条目受 `EVIDENCE_LIMIT` 约束。
# 改这行 = 让所有既有条目重新变成「新条目」，**不要**改（历史必须保持历史）。
CUTOFF = (
    "<!-- M63-S5 evidence cutoff: 本行之下的条目 evidence ≤ 400 字符、且必须单行"
    "（.harness/scripts/check_ledger.py）；之上的历史条目原样保留。 -->"
)

ENTRY_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \|")

# 台账文件必须 LF（`.harness/README.md` 文末「全仓约定」）。
# 为什么值得一条门禁：CRLF 会把**整个文件**标成改动，把「一行 `passes`」的改动藏进几千行噪声里
# —— 那不是格式洁癖，是让 diff 失去信息量。2026-10-14 实锤：`.harness/scripts/set_feature.py`
# （README 指定的「改 `passes` 的官方入口」）用 `Path.write_text` 且没给 `newline="\n"`，
# 一次调用就把 feature_list.json 漂出 **426** 个 CRLF。该脚本已修；本条防的是「下一个写入点」。
LF_CHECKED = (FEATURES, PROJECT_STATE, PROGRESS)


def check_line_endings(errors: list[str]) -> int:
    for path in LF_CHECKED:
        count = path.read_bytes().count(b"\r\n")
        if count:
            errors.append(
                f"{path.name}: 有 {count} 个 CRLF —— 全仓约定 LF"
                '（写文件要显式 `newline="\\n"`；`Path.write_text` 默认会把 `\\n` 翻成 `\\r\\n`）'
            )
    return len(LF_CHECKED)


def check_feature_titles(errors: list[str]) -> int:
    data = json.loads(FEATURES.read_text(encoding="utf-8"))
    features = data.get("features") or []
    for feature in features:
        title = feature.get("title") or ""
        if len(title) > TITLE_LIMIT:
            errors.append(
                f"feature_list.json: {feature.get('id')!r} 的 title {len(title)} 字符 > "
                f"{TITLE_LIMIT}（title 是「扫一眼」字段，正文请落 tasks/M*.md）"
            )
    return len(features)


def check_single_field(errors: list[str]) -> list[str]:
    """`notes` 这类单字段的长度上限。返回检查过的字段名（供汇总行）。"""
    data = json.loads(PROJECT_STATE.read_text(encoding="utf-8"))
    notes = data.get("notes")
    if not isinstance(notes, str):
        errors.append(f"project_state.json: notes 不是字符串（{type(notes).__name__}）")
        return []
    if len(notes) > NOTES_LIMIT:
        errors.append(
            f"project_state.json: notes {len(notes)} 字符 > {NOTES_LIMIT}"
            "（本字段只记「跨里程碑仍成立」的决策；进度叙述落 PROGRESS.md / memory）"
        )
    return ["notes"]


def check_progress_tail(errors: list[str]) -> int:
    """cutoff 之后追加的条目。返回被检查的条目数（0 是合法的，但标记必须存在）。"""
    text = PROGRESS.read_text(encoding="utf-8")
    occurrences = text.count(CUTOFF)
    if occurrences != 1:
        errors.append(
            f"PROGRESS.md: cutoff 标记出现 {occurrences} 次（须恰好 1）—— "
            "缺了它，下面的 evidence 上限会静默检查 0 条（假判据）；多了它，口径就分叉了"
        )
        return 0

    tail = text.split(CUTOFF, 1)[1]
    checked = 0
    for line in tail.splitlines():
        if not line.strip() or line.lstrip().startswith("<!--"):
            continue
        if not ENTRY_RE.match(line):
            errors.append(
                f"PROGRESS.md: cutoff 之后出现非条目行（条目必须写成单行）→ {line[:70]!r}"
            )
            continue
        parts = line.split("|", 3)
        if len(parts) < 4:
            errors.append(f"PROGRESS.md: 条目不足 4 段（缺 evidence）→ {line[:70]!r}")
            continue
        evidence = parts[3].strip()
        checked += 1
        if len(evidence) > EVIDENCE_LIMIT:
            errors.append(
                f"PROGRESS.md: {parts[0].strip()} 的 evidence {len(evidence)} 字符 > "
                f"{EVIDENCE_LIMIT} → {evidence[:60]!r}…（详情落当日 memory，这里只留一行索引）"
            )
    return checked


def main() -> int:
    errors: list[str] = []

    feature_count = check_feature_titles(errors)
    field_names = check_single_field(errors)
    entry_count = check_progress_tail(errors)
    lf_count = check_line_endings(errors)

    if errors:
        print("LEDGER CHECK FAILED")
        print("\n".join(f"- {item}" for item in errors))
        return 1

    print(
        f"LEDGER CHECK PASSED ({feature_count} feature title ≤ {TITLE_LIMIT}；"
        f"{'/'.join(field_names)} ≤ {NOTES_LIMIT}；"
        f"cutoff 之后 {entry_count} 条 evidence ≤ {EVIDENCE_LIMIT}；"
        f"{lf_count} 个台账文件 LF)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
