"""第一批影刀对齐指令（data.readText/appendText/fileExists/deletePath/datetimeNow、workflow.sleep）契约测试。

直接调用 python_worker.execute()（同步），workspace 由调用方传入（与 data.writeJson 同款隔离模型）。
"""

from pathlib import Path

from rpa_core.model.command import CommandInvocation
from rpa_core.workers import python_worker as pw


def _inv(cid: str, workspace, **inputs) -> CommandInvocation:
    return CommandInvocation(
        command_id=cid,
        command_version="1.0.0",
        run_id="run",
        step_id="t",
        inputs={"workspace": str(workspace), **inputs},
    )


def _make_file(workspace: Path, name: str, content: str) -> Path:
    target = workspace / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def test_read_text_returns_content_and_read_effect(tmp_path):
    workspace = tmp_path / "ws"
    a = _make_file(workspace, "a.txt", "你好 rpa\n第二行")
    result = pw.execute(_inv("data.readText", workspace, path="a.txt"))
    assert result.status == "success"
    assert result.outputs["text"] == "你好 rpa\n第二行"
    assert result.outputs["path"] == str(a.resolve())
    effect = result.effects[0]
    assert effect.kind.value == "read"
    assert effect.status.value == "committed"


def _assert_failed(result, code):
    assert result.status != "success"
    assert result.error.code == code


def test_read_text_missing_file_fails(tmp_path):
    workspace = tmp_path / "ws"
    result = pw.execute(_inv("data.readText", workspace, path="nope.txt"))
    _assert_failed(result, "INVALID_INPUT")


def test_read_text_outside_workspace_denied(tmp_path):
    workspace = tmp_path / "ws"
    outside = tmp_path / "outer.txt"
    outside.write_text("x", encoding="utf-8")
    result = pw.execute(_inv("data.readText", workspace, path=str(outside)))
    _assert_failed(result, "CAPABILITY_DENIED")


def test_append_text_cumulates(tmp_path):
    workspace = tmp_path / "ws"
    _make_file(workspace, "log.txt", "ini")
    r1 = pw.execute(_inv("data.appendText", workspace, path="log.txt", text="+1"))
    assert r1.status == "success"
    assert r1.effects[0].kind.value == "unsafe-write"
    pw.execute(_inv("data.appendText", workspace, path="log.txt", lines=["-2"]))
    assert (workspace / "log.txt").read_text(encoding="utf-8") == "ini+1-2"


def test_append_text_creates_missing_file(tmp_path):
    workspace = tmp_path / "ws"
    pw.execute(_inv("data.appendText", workspace, path="new.txt", text="段"))
    assert (workspace / "new.txt").read_text(encoding="utf-8") == "段"


def test_file_exists_true_and_false(tmp_path):
    workspace = tmp_path / "ws"
    _make_file(workspace, "in.txt", "x")
    t = pw.execute(_inv("data.fileExists", workspace, path="in.txt"))
    assert t.status == "success" and t.outputs["exists"] is True
    f = pw.execute(_inv("data.fileExists", workspace, path="missing.txt"))
    assert f.status == "success" and f.outputs["exists"] is False


def test_delete_path_file(tmp_path):
    workspace = tmp_path / "ws"
    target = _make_file(workspace, "del.txt", "x")
    result = pw.execute(_inv("data.deletePath", workspace, path="del.txt"))
    assert result.status == "success" and result.outputs["deleted"] is True
    assert not target.exists()
    assert result.effects[0].kind.value == "unsafe-write"


def test_delete_path_missing_is_noop_success(tmp_path):
    workspace = tmp_path / "ws"
    result = pw.execute(_inv("data.deletePath", workspace, path="ghost.txt"))
    assert result.status == "success" and result.outputs["deleted"] is False


def test_delete_nonempty_dir_without_recursive_fails(tmp_path):
    workspace = tmp_path / "ws"
    _make_file(workspace, "d/f.txt", "x")
    result = pw.execute(_inv("data.deletePath", workspace, path="d"))
    _assert_failed(result, "INVALID_INPUT")


def test_delete_dir_recursive(tmp_path):
    workspace = tmp_path / "ws"
    _make_file(workspace, "d/f.txt", "x")
    result = pw.execute(_inv("data.deletePath", workspace, path="d", recursive=True))
    assert result.status == "success" and result.outputs["deleted"] is True
    assert not (workspace / "d").exists()


def test_datetime_now_reports_value_iso_timestamp(tmp_path):
    workspace = tmp_path / "ws"
    result = pw.execute(_inv("data.datetimeNow", workspace))
    assert result.status == "success"
    assert result.outputs["timestamp"] > 0
    assert str(result.outputs["timestamp"]).isdigit()


def test_datetime_now_custom_format(tmp_path):
    workspace = tmp_path / "ws"
    result = pw.execute(_inv("data.datetimeNow", workspace, format="%Y-%m-%d"))
    assert result.status == "success"
    # 输出形如 2026-09-14
    parts = result.outputs["value"].split("-")
    assert len(parts) == 3 and all(len(p) == 2 or len(p) == 4 for p in parts)


def test_sleep_waits_and_reports_ms(tmp_path):
    import time

    workspace = tmp_path / "ws"
    start = time.perf_counter()
    result = pw.execute(_inv("workflow.sleep", workspace, seconds=0.05))
    elapsed = time.perf_counter() - start
    assert result.status == "success"
    assert result.outputs["sleptMs"] == 50
    assert elapsed >= 0.04


def test_sleep_rejects_negative(tmp_path):
    workspace = tmp_path / "ws"
    result = pw.execute(_inv("workflow.sleep", workspace, seconds=-1))
    _assert_failed(result, "INVALID_INPUT")


# ---------------------------------------------------------------------------
# data.setVar 按 varType 类型格式化（对标影刀「设置变量」）
# ---------------------------------------------------------------------------


def test_setvar_string_default_and_explicit(tmp_path):
    workspace = tmp_path / "ws"
    # 缺省（无 varType）/ 显式 string：统一按字符串格式化（对标影刀默认字符串）
    r1 = pw.execute(_inv("data.setVar", workspace, varName="a", value=42))
    assert r1.status == "success" and r1.outputs["value"] == "42"
    r2 = pw.execute(_inv("data.setVar", workspace, varName="a", varType="string", value=42))
    assert r2.outputs["value"] == "42"
    r3 = pw.execute(_inv("data.setVar", workspace, varName="a", value="已存文本"))
    assert r3.outputs["value"] == "已存文本"


def test_setvar_string_type(tmp_path):
    workspace = tmp_path / "ws"
    r = pw.execute(_inv("data.setVar", workspace, varName="s", varType="string", value=123))
    assert r.outputs["value"] == "123"
    none_r = pw.execute(_inv("data.setVar", workspace, varName="s", varType="string", value=None))
    assert none_r.outputs["value"] == ""


def test_setvar_number_type(tmp_path):
    workspace = tmp_path / "ws"
    r = pw.execute(_inv("data.setVar", workspace, varName="n", varType="number", value="3.14"))
    assert r.outputs["value"] == 3.14
    int_r = pw.execute(_inv("data.setVar", workspace, varName="n", varType="number", value="7"))
    assert int_r.outputs["value"] == 7
    bool_r = pw.execute(_inv("data.setVar", workspace, varName="n", varType="number", value=True))
    assert bool_r.outputs["value"] == 1
    empty_r = pw.execute(_inv("data.setVar", workspace, varName="n", varType="number", value=""))
    assert empty_r.outputs["value"] == 0
    bad = pw.execute(_inv("data.setVar", workspace, varName="n", varType="number", value="abc"))
    _assert_failed(bad, "INVALID_INPUT")


def test_setvar_boolean_type(tmp_path):
    workspace = tmp_path / "ws"
    t = pw.execute(_inv("data.setVar", workspace, varName="b", varType="boolean", value="true"))
    assert t.outputs["value"] is True
    one = pw.execute(_inv("data.setVar", workspace, varName="b", varType="boolean", value=1))
    assert one.outputs["value"] is True
    zero = pw.execute(_inv("data.setVar", workspace, varName="b", varType="boolean", value=0))
    assert zero.outputs["value"] is False
    empty = pw.execute(_inv("data.setVar", workspace, varName="b", varType="boolean", value=None))
    assert empty.outputs["value"] is False


def test_setvar_object_type(tmp_path):
    workspace = tmp_path / "ws"
    keep = pw.execute(_inv("data.setVar", workspace, varName="o", varType="object", value={"k": 1}))
    assert keep.outputs["value"] == {"k": 1}
    parsed = pw.execute(
        _inv("data.setVar", workspace, varName="o", varType="object", value='{"a": 2}')
    )
    assert parsed.outputs["value"] == {"a": 2}
    empty = pw.execute(_inv("data.setVar", workspace, varName="o", varType="object", value=""))
    assert empty.outputs["value"] == {}
    bad = pw.execute(_inv("data.setVar", workspace, varName="o", varType="object", value="{nope"))
    _assert_failed(bad, "INVALID_INPUT")


def test_setvar_array_type(tmp_path):
    workspace = tmp_path / "ws"
    keep = pw.execute(_inv("data.setVar", workspace, varName="arr", varType="array", value=[1, 2]))
    assert keep.outputs["value"] == [1, 2]
    parsed = pw.execute(
        _inv("data.setVar", workspace, varName="arr", varType="array", value='["x"]')
    )
    assert parsed.outputs["value"] == ["x"]
    bad = pw.execute(_inv("data.setVar", workspace, varName="arr", varType="array", value="[1,2"))
    _assert_failed(bad, "INVALID_INPUT")

# ---- data.log（打印日志，影刀对齐） -----------------------------------------


def test_log_echoes_message_and_level(tmp_path):
    """打印日志：原样回传 message/level（变量引用由 orchestrator 解析后传入）。"""
    workspace = tmp_path / "ws"
    result = pw.execute(_inv("data.log", workspace, message="抓到的标题是 百度一下"))
    assert result.status == "success"
    assert result.outputs["message"] == "抓到的标题是 百度一下"
    assert result.outputs["level"] == "info"
    assert result.value == "抓到的标题是 百度一下"


def test_log_accepts_level_and_empty_message(tmp_path):
    """级别可选（默认 info）；空文本也允许（仍产生一条日志行）。"""
    workspace = tmp_path / "ws"
    result = pw.execute(_inv("data.log", workspace, message="", level="warn"))
    assert result.status == "success"
    assert result.outputs["message"] == ""
    assert result.outputs["level"] == "warn"
