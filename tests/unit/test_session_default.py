"""会话默认解析（sessionId 可省略，默认作用于最近激活会话）。"""

from rpa_core.executors.base import resolve_session_id


def test_explicit_session_id_wins():
    assert resolve_session_id("s-2", {"s-1": 1, "s-2": 2}, "s-1") == "s-2"


def test_empty_falls_back_to_last_active():
    assert resolve_session_id(None, {"s-1": 1, "s-2": 2}, "s-2") == "s-2"


def test_empty_falls_back_to_single_session():
    assert resolve_session_id("", {"only": 1}, None) == "only"


def test_ambiguous_without_last_active_returns_empty():
    """多个会话且无激活记录时不猜测，由调用方报 SESSION_NOT_FOUND。"""
    assert resolve_session_id(None, {"s-1": 1, "s-2": 2}, None) == ""


def test_stale_last_active_falls_back_to_single_session():
    """最近会话已被关闭时退回唯一会话。"""
    assert resolve_session_id(None, {"only": 1}, "gone") == "only"


def test_no_sessions_returns_empty():
    assert resolve_session_id(None, {}, None) == ""
    assert resolve_session_id("s-1", {}, None) == "s-1"
