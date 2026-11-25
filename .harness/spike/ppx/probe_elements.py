"""元素资产 RPC 探针：把「元素资产面板」后端承诺逐项打勾（headless 直调）。

与 `exec_probe.py` 同款机制：不走窗口，直接 import api.api 调 RPC 函数。
点按钮只能验「面板渲染了」，验不了「待补判定对不对」「未知会话报不报错」——
这些差别混过去，运行期才以 ELEMENT_REF_MISSING 的形式炸出来。

判据：
1. missingElements：扫描 elementRefs，引用了但 elements/ 下没同名文件 = 待补
   （带 nodeId/command/param，前端据此定位卡片）；已存在的列进 resolved，
   **不**报成待补（漏报是假绿灯，误报会骗用户去捕获不缺的东西）。
2. listElements：列出 elements/*.json 的 name/kind/selector（面板「已捕获」区）。
3. capturePoll：未知会话 → 结构化 UNKNOWN_CAPTURE（不是抛异常变 INTERNAL_ERROR，
   前端轮询打错名字不该炸）。
4. 三次 RPC 返回值都能 `json.dumps(allow_nan=False)`（bridge 就是这么送给 JS 的）。

刻意不调 startCapture：它 spawn `rpa-core capture` 阻塞等手势（浏览器 Ctrl+Click /
桌面 F9），headless 跑不了也不该跑；会话表的「查无此会话」路径由判据 3 覆盖，
真手势闭环另用界面截图确认。负向验证见 `../../spike/probe_elements_negative.py`。

夹具：`_sandbox/elements_probe.json`（引用 missing_button 未捕获 + captured_field 已捕获）
      `_sandbox/elements/captured_field.json` —— 都在 gitignored 的 _sandbox 里。
★ 本探针不删任何文件（删除守卫）；夹具只在缺失时创建。

用法：
    ./.venv/Scripts/python.exe probe_elements.py
退出码 0 = 全绿。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = next(p for p in HERE.parents if (p / "src" / "rpa_core").is_dir())
SANDBOX = HERE / "_sandbox"
FLOW = SANDBOX / "elements_probe.json"
ELEM_DIR = SANDBOX / "elements"

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


serialized = 0


def bridge_safe(payload: dict, label: str) -> None:
    """bridge 用 `json.dumps(data, allow_nan=False)`，这里同口径先验一遍。"""
    global serialized
    serialized += 1
    try:
        json.dumps(payload, allow_nan=False)
    except (TypeError, ValueError) as exc:
        check(f"{label} 可序列化给 JS", False, f"{type(exc).__name__}: {exc}")


def rel(path: Path) -> str:
    return path.relative_to(REPO).as_posix()


def write_json(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        return  # 夹具已存在就不动（删除守卫）
    path.write_bytes(
        (json.dumps(document, ensure_ascii=False, indent=2) + "\n").replace("\r\n", "\n").encode("utf-8")
    )


def fixtures() -> None:
    write_json(
        FLOW,
        {
            "schema_version": "1.0",
            "id": "elements-probe",
            "name": "elements probe",
            "inputs": {},
            "root": {
                "type": "sequence",
                "id": "root",
                "children": [
                    {
                        "type": "action",
                        "id": "clickMissing",
                        "command": "browser.click",
                        "elementRefs": {"target": "missing_button"},
                    },
                    {
                        "type": "action",
                        "id": "clickCaptured",
                        "command": "browser.click",
                        "elementRefs": {"target": "captured_field"},
                    },
                ],
            },
        },
    )
    write_json(
        ELEM_DIR / "captured_field.json",
        {"kind": "browser", "selector": {"css": "#captured"}, "name": None},
    )


def main() -> int:
    fixtures()
    sys.path.insert(0, str(HERE / "app"))
    import api.api as A  # noqa: E402 - 必须在 sys.path 之后导入

    path = rel(FLOW)

    # ---- missingElements：待补判定 ---------------------------------------
    miss = A.missing_elements(path)
    bridge_safe(miss, "missingElements")
    check("missingElements 返回 ok", miss.get("ok") is True, str(miss.get("code", "")))
    pairs = {(m.get("nodeId"), m.get("name")) for m in miss.get("missing", [])}
    check(
        "missingElements 命中待补元素 missing_button",
        ("clickMissing", "missing_button") in pairs,
        f"missing={sorted(pairs)}",
    )
    located = next((m for m in miss.get("missing", []) if m.get("name") == "missing_button"), {})
    check(
        "待补条目带 nodeId/command/param（前端定位卡片就靠它）",
        located.get("nodeId") == "clickMissing"
        and located.get("command") == "browser.click"
        and located.get("param") == "target",
        json.dumps(located, ensure_ascii=False),
    )
    check(
        "missingElements 不把已捕获元素报成待补（resolved 含 captured_field）",
        "captured_field" in (miss.get("resolved") or []),
        f"resolved={miss.get('resolved')}",
    )

    # ---- listElements：已捕获资产 ----------------------------------------
    listing = A.list_elements(path)
    bridge_safe(listing, "listElements")
    check("listElements 返回 ok", listing.get("ok") is True, str(listing.get("code", "")))
    names = {e.get("name") for e in listing.get("elements", [])}
    check(
        "listElements 列出已捕获元素 captured_field",
        "captured_field" in names,
        f"elements={sorted(n for n in names if n)}",
    )
    captured = next((e for e in listing.get("elements", []) if e.get("name") == "captured_field"), {})
    check(
        "listElements 带了 kind/selector",
        captured.get("kind") == "browser" and captured.get("selector") == "#captured",
        json.dumps(captured, ensure_ascii=False),
    )

    # ---- capturePoll：边界 ------------------------------------------------
    poll = A.capture_poll("no-such-capture")
    bridge_safe(poll, "capturePoll")
    check(
        "capturePoll 未知会话 → 结构化 UNKNOWN_CAPTURE",
        poll.get("ok") is False and poll.get("code") == "UNKNOWN_CAPTURE",
        f"code={poll.get('code')}",
    )

    check(f"{serialized} 次 RPC 返回都可序列化给 JS", serialized >= 3, f"共 {serialized} 次")

    print()
    if failures:
        print(f"{len(failures)} 项未通过：")
        for label in failures:
            print(f"  - {label}")
        return 1
    print("全部通过：元素资产 RPC（missingElements / listElements / capturePoll）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
