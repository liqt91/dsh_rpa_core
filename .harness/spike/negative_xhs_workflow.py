"""负向验证：重写的七步流程契约（test_workflow_contract.py）是否真会红。

每条用例都是「合法 JSON 的精准放宽」：按节点 id 改一处语义，跑契约测试，
断言 **实际失败集 == 按推理写下的期望失败集**（不是「有红就行」，也不是 >=）。
末尾 META 是元验证：把那条判据本身放宽后，同一处注入必须变绿——证明红真是它报的。

跑完逐字节还原流程文件与契约测试文件并核 sha256；任何异常都在 finally 里还原。
跑法：./.venv/Scripts/python.exe .harness/spike/negative_xhs_workflow.py
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
SENTINEL = "NV-M70-INJECTED"
WF = ROOT / "apps/xhs-workbench/workflows/xhs_product_search.json"
TESTS = ROOT / "apps/xhs-workbench/tests/test_workflow_contract.py"
APP = ROOT / "apps/xhs-workbench"
BRANCHES = ("children", "then", "else", "catch")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_contract() -> set[str]:
    done = subprocess.run(
        [str(PY), "-m", "pytest", "tests/test_workflow_contract.py", "-q",
         "--tb=no", "--basetemp=C:/Users/Administrator/AppData/Local/Temp/pt-nvxhs"],
        cwd=str(APP), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=180,
    )
    failed: set[str] = set()
    for line in (done.stdout or "").splitlines():
        hit = re.match(r"^FAILED\s+\S+::(\w+)", line.strip())
        if hit:
            failed.add(hit.group(1))
    return failed


def find_node(root: dict[str, Any], node_id: str) -> dict[str, Any]:
    """锚点唯一性自检：id 必须恰好出现一次，否则注入打在错误的副本上。"""
    hits: list[dict[str, Any]] = []

    def visit(node: dict[str, Any]) -> None:
        if node.get("id") == node_id:
            hits.append(node)
        for branch in BRANCHES:
            for child in node.get(branch, []):
                visit(child)

    visit(root)
    if len(hits) != 1:
        raise RuntimeError(f"锚点 {node_id} 命中 {len(hits)} 次，拒绝注入")
    return hits[0]


def drop_child(parent: dict[str, Any], node_id: str) -> None:
    before = len(parent.get("children", []))
    parent["children"] = [c for c in parent.get("children", []) if c.get("id") != node_id]
    if len(parent["children"]) != before - 1:
        raise RuntimeError(f"删 {node_id} 失败")


def write_doc(doc: dict[str, Any]) -> None:
    # write_bytes 而非 write_text：Windows 后者会把整个文件转成 CRLF。
    WF.write_bytes(
        (json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )


def injected(mutate) -> dict[str, Any]:
    doc = json.loads(WF.read_text(encoding="utf-8"))
    doc["name"] = doc["name"] + SENTINEL
    mutate(doc)
    write_doc(doc)
    return doc


# --- 用例：期望失败集按推理补全，不照抄实际 ------------------------------------
def n1_no_enter(doc):
    find_node(doc["root"], "typeKeyword")["with"]["pressEnter"] = False


def n2_new_tab(doc):
    node = find_node(doc["root"], "fallbackNav")
    node["command"] = "browser.navigate"
    node["with"] = {
        "browserType": "${inputs.browserType}", "action": "goto",
        "url": "https://www.xiaohongshu.com/search_result?keyword=fixed",
        "timeoutMs": 20000,
    }


def n3_always_true(doc):
    find_node(doc["root"], "hasAiPanel")["condition"]["left"] = "1"


def n4_wrong_filter(doc):
    find_node(doc["root"], "clickOneDay")["with"]["args"] = ["最新"]


def n5_forward_ref(doc):
    find_node(doc["root"], "eachPost")["items"] = "${steps.grabItems.outputs.result}"


def n6_one_scroll(doc):
    loop = find_node(doc["root"], "eachPost")
    drop_child(loop, "scrollComments3")
    drop_child(loop, "sleepComments3")


def n7_empty_summary(doc):
    find_node(doc["root"], "grabItems")["with"]["script"] = (
        "return JSON.stringify([]);"
    )


def n8_iife(doc):
    find_node(doc["root"], "grabComments")["with"]["script"] = (
        "(() => { return JSON.stringify([]); })()"
    )


def n9_bad_session(doc):
    find_node(doc["root"], "close")["with"]["sessionId"] = (
        "${steps.fake.outputs.sessionId}"
    )


def n10_concat_path(doc):
    find_node(doc["root"], "shotResults")["with"]["savePath"] = (
        "${inputs.workspace}/results.png"
    )


def n11_no_reload_wait(doc):
    # 从 root 的 children 里摘掉；find_node 先确认锚点唯一
    find_node(doc["root"], "waitReload")
    drop_child(doc["root"], "waitReload")


def n12_no_persist(doc):
    find_node(doc["root"], "saveRaw")["with"]["text"] = "[]"


def n13_wrong_item_var(doc):
    find_node(doc["root"], "eachPost")["item_var"] = "item"


CASES: list[dict[str, Any]] = [
    dict(id="N1", desc="回车被改成不按（用户步骤 2 落空）", mutate=n1_no_enter,
         expect={"test_enter_search_has_fallback_when_url_does_not_change"}),
    dict(id="N2", desc="兜底导航改回 browser.navigate（新开 tab，session 全指错）",
         mutate=n2_new_tab,
         expect={"test_single_tab_session_is_used_and_closed",
                 "test_enter_search_has_fallback_when_url_does_not_change"}),
    dict(id="N3", desc="AI 框关闭条件改成恒真（不再看探测结果）", mutate=n3_always_true,
         expect={"test_ai_panel_close_is_conditional"}),
    dict(id="N4", desc="第二个筛选条件点错（最新点两次）", mutate=n4_wrong_filter,
         expect={"test_filter_clicks_latest_then_one_day"}),
    dict(id="N5", desc="forEach 引用了循环之后才产出的 grabItems", mutate=n5_forward_ref,
         expect={"test_step_refs_only_point_to_earlier_actions",
                 "test_each_post_opens_captures_and_scrolls_comments"}),
    dict(id="N6", desc="评论区只滚一次（懒加载评论抓不全）", mutate=n6_one_scroll,
         expect={"test_step_refs_only_point_to_earlier_actions",
                 "test_each_post_opens_captures_and_scrolls_comments"}),
    dict(id="N7", desc="grabItems 直接返回空数组（逐帖结果没汇总）", mutate=n7_empty_summary,
         expect={"test_extracted_result_is_actually_persisted"}),
    # 推理：形状判据必红；逐帖判据还钉死 grabComments 含 comment-item 选择器，
    # IIFE 版把整段采集逻辑换掉了，那条也会红（不是连带噪声，是第二个被破坏的契约）。
    dict(id="N8", desc="某个 executeScript 写成 IIFE（注入恒 null）", mutate=n8_iife,
         expect={"test_all_execute_scripts_are_function_bodies",
                 "test_each_post_opens_captures_and_scrolls_comments"}),
    dict(id="N9", desc="close 指向不存在的 session", mutate=n9_bad_session,
         expect={"test_step_refs_only_point_to_earlier_actions",
                 "test_single_tab_session_is_used_and_closed"}),
    # 推理：savePath 的整串引用判据必红；同时 ${inputs.shotPath} 这条引用从流程里
    # 消失，inputs 引用集合少一项，inputs 精确相等判据也会红。
    dict(id="N10", desc="截图路径用拼接引用（会落成字面量目录）", mutate=n10_concat_path,
         expect={"test_items_are_waited_for_before_scraping",
                 "test_inputs_referenced_are_exactly_the_declared_ones"}),
    dict(id="N11", desc="删掉「等结果重新载入」这一步", mutate=n11_no_reload_wait,
         expect={"test_step_refs_only_point_to_earlier_actions",
                 "test_seven_step_pipeline_order",
                 "test_items_are_waited_for_before_scraping",
                 "test_filter_clicks_latest_then_one_day"}),
    dict(id="N12", desc="saveRaw 不写抓到的结果", mutate=n12_no_persist,
         expect={"test_extracted_result_is_actually_persisted"}),
    dict(id="N13", desc="forEach 的 item_var 改名但循环体没跟", mutate=n13_wrong_item_var,
         expect={"test_each_post_opens_captures_and_scrolls_comments"}),
]

# 元验证：把 N3 命中的那条断言本身放宽，同一处注入必须变绿。
ANCHOR = '    assert node["condition"]["left"] == "${steps.probeAiPanel.outputs.result}"'
RELAXED = '    assert True  # ' + SENTINEL
META = dict(
    id="META", desc="放宽 N3 命中的判据后，同一注入应变绿（证明红是它报的）",
    mutate=n3_always_true, expect=set(), relax=True,
)


def verify_clean() -> list[str]:
    notes: list[str] = []
    if SENTINEL in WF.read_text(encoding="utf-8"):
        notes.append("流程文件里残留哨兵")
    if SENTINEL in TESTS.read_text(encoding="utf-8"):
        notes.append("契约测试里残留哨兵")
    return notes


def run_case(item: dict[str, Any], original_wf: bytes, original_tests: bytes) -> list[str]:
    problems: list[str] = []
    try:
        injected(item["mutate"])
        if item.get("relax"):
            text = TESTS.read_text(encoding="utf-8")
            count = text.count(ANCHOR)
            if count != 1:
                raise RuntimeError(f"元验证锚点命中 {count} 次，拒绝放宽")
            TESTS.write_bytes(text.replace(ANCHOR, RELAXED).encode("utf-8"))
        actual = run_contract()
        expected = set(item["expect"])
        if actual != expected:
            problems.append(f"  期望红={sorted(expected)}")
            problems.append(f"  实际红={sorted(actual)}")
            if expected - actual:
                problems.append(f"  漏报（假绿灯）：{sorted(expected - actual)}")
            if actual - expected:
                problems.append(f"  意外红：{sorted(actual - expected)}")
    finally:
        WF.write_bytes(original_wf)
        TESTS.write_bytes(original_tests)
    return problems


def main() -> int:
    if SENTINEL in WF.read_text(encoding="utf-8") or SENTINEL in TESTS.read_text(encoding="utf-8"):
        print("源码里已有哨兵，拒绝运行（上次注入没还原干净）")
        return 2
    original_wf = WF.read_bytes()
    original_tests = TESTS.read_bytes()
    before = {p: digest(p) for p in (WF, TESTS)}
    print("基线:", {p.name: v[:8] for p, v in before.items()})

    baseline = run_contract()
    if baseline:
        print(f"FAIL 基线自身不绿：{sorted(baseline)}")
        return 1

    failures = 0
    for item in [*CASES, META]:
        try:
            problems = run_case(item, original_wf, original_tests)
        except Exception as exc:  # noqa: BLE001 - 注入异常也要算失败并继续还原
            problems = [f"  注入/执行异常：{exc}"]
        if problems:
            failures += 1
            print(f"FAIL {item['id']} {item['desc']}")
            for line in problems:
                print(line)
        else:
            print(f"GREEN (negative verified) {item['id']} {item['desc']}")

    after = {p: digest(p) for p in before}
    if before != after:
        failures += 1
        print("FAIL 还原不彻底：")
        for p, value in before.items():
            if after[p] != value:
                print(f"  {p.name} {value[:8]} -> {after[p][:8]}")
    for note in verify_clean():
        failures += 1
        print(f"FAIL {note}")
    total = len(CASES) + 1
    print(f"NEGATIVE VERIFICATION {'FAILED' if failures else 'PASSED'} "
          f"({total - failures}/{total})")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
