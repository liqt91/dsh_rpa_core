"""真机 E2E 判据的负向验证（全程不占机）。

只测脚本里的判据函数不够：还要证明 ``run_live`` 真的调用了它（纯函数层绿
≠ 接线层绿）。每条用例都断言「实际红的集合 == 推理出的期望红的集合」，
用等值而不是阈值，避免判据容得下坏输入。
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import verify_live  # noqa: E402

GOOD = json.dumps(
    [{"title": "AI效率工具", "href": "https://www.xiaohongshu.com/goods/1", "text": ""}],
    ensure_ascii=False,
)
KEYWORD = "AI效率工具"
SEARCH_URL = "https://www.xiaohongshu.com/search_result?keyword=AI"
HOME_URL = "https://www.xiaohongshu.com/explore"
ALL_GREEN = {
    "run_succeeded": True,
    "items_parsed": True,
    "items_have_content": True,
    "write_matches": True,
    "search_effective": True,
}


def _checks(**overrides: object) -> dict[str, bool]:
    params: dict[str, object] = {
        "cli_returncode": 0,
        "status": "succeeded",
        "group": "ok",
        "raw_text": GOOD,
        "written_text": GOOD,
        "page_url": SEARCH_URL,
        "keyword": KEYWORD,
    }
    params.update(overrides)
    return verify_live.evaluate_run(**params)["checks"]


def _red(*names: str) -> dict[str, bool]:
    return {**ALL_GREEN, **dict.fromkeys(names, False)}


def test_baseline_is_all_green() -> None:
    assert _checks() == ALL_GREEN


def test_failed_run_only_breaks_run_succeeded() -> None:
    assert _checks(status="failed") == _red("run_succeeded")
    assert _checks(cli_returncode=1) == _red("run_succeeded")
    assert _checks(group="needs_attention") == _red("run_succeeded")


def test_empty_array_breaks_two_item_checks() -> None:
    assert _checks(raw_text="[]", written_text="[]") == _red(
        "items_parsed", "items_have_content"
    )


def test_blank_rows_break_content_check() -> None:
    blank = json.dumps([{"title": "", "href": "", "text": ""}], ensure_ascii=False)
    assert _checks(raw_text=blank, written_text=blank) == _red("items_have_content")


def test_wrong_shape_breaks_two_item_checks() -> None:
    result = verify_live.evaluate_run(
        cli_returncode=0,
        status="succeeded",
        group="ok",
        raw_text='{"a": 1}',
        written_text='{"a": 1}',
        page_url=SEARCH_URL,
        keyword=KEYWORD,
    )
    assert result["checks"] == _red("items_parsed", "items_have_content")
    assert result["reason"] == "executeScript 返回值不是数组"


def test_non_json_result_is_reported_not_swallowed() -> None:
    result = verify_live.evaluate_run(
        cli_returncode=0,
        status="succeeded",
        group="ok",
        raw_text="NOT JSON",
        written_text="NOT JSON",
        page_url=SEARCH_URL,
        keyword=KEYWORD,
    )
    assert result["checks"] == _red("items_parsed", "items_have_content")
    assert "不是 JSON" in result["reason"]


def test_write_mismatch_and_missing_file_both_break_write_check() -> None:
    assert _checks(written_text=GOOD + "x") == _red("write_matches")
    assert _checks(written_text=None) == _red("write_matches")


def test_empty_step_output_breaks_three_checks() -> None:
    assert _checks(raw_text="", written_text="") == _red(
        "items_parsed", "items_have_content", "write_matches"
    )


def test_home_feed_is_not_a_search_result() -> None:
    """真机踩到：抓到 30 条 /explore 首页笔记，前四条判据全绿却没搜到目标。"""
    assert _checks(page_url=HOME_URL) == _red("search_effective")


def test_run_live_actually_calls_the_verdict_function() -> None:
    """接线哨兵：判据函数改了却没被真机路径消费 = 恒绿假判据。"""
    tree = ast.parse((SCRIPTS / "verify_live.py").read_text(encoding="utf-8"))
    fn = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "run_live"
    )
    called = {
        node.func.id
        for node in ast.walk(fn)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "evaluate_run" in called
