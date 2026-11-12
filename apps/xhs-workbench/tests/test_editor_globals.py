"""编辑器底栏「全局参数」：后端 RPC 语义、前端接线、构建产物同步。

全局参数的真值源是 ``workflow.inputs``；写入 RPC 复用
``rpa_core.model.inputs`` 的权威校验，不另写一套标识符规则。
ADR 0022-R3 后 DOM 在 index.html、版式在 style.css、行为在 main.js；
三处是否真接上，靠 ``scripts/check_editor_bottom_dock.mjs`` 用最小 DOM shim 真跑一遍。
负向验证见 ``.harness/spike/negative_globals_dock.py``。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from src import ppx_bridge as pb

APP = Path(__file__).resolve().parents[1]
ROOT = APP.parents[1]
MAIN_JS = ROOT / ".harness" / "demo" / "ppx_probe" / "app" / "gui" / "src" / "main.js"
INDEX_HTML = ROOT / ".harness" / "demo" / "ppx_probe" / "app" / "gui" / "index.html"
STYLE_CSS = ROOT / ".harness" / "demo" / "ppx_probe" / "app" / "gui" / "src" / "style.css"
API_PY = ROOT / ".harness" / "demo" / "ppx_probe" / "app" / "api" / "api.py"
DIST_DIR = ROOT / ".harness" / "demo" / "ppx_probe" / "app" / "gui" / "dist" / "assets"
CHECK_ALL = ROOT / ".harness" / "scripts" / "check_all.py"
DOCK_SCRIPT = ROOT / "scripts" / "check_editor_bottom_dock.mjs"
BRANCH_SCOPE = ROOT / ".harness" / "branch-scope.json"


def _flow_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, Path]:
    """把新建流程落到临时工作区：_flow_path 认 api.WORKSPACE，_create_flow 认 XHS_WORKFLOWS。"""
    (tmp_path / "workflows").mkdir()
    monkeypatch.setattr(pb, "XHS_WORKFLOWS", tmp_path / "workflows")
    api = pb._load_api()
    monkeypatch.setattr(api, "WORKSPACE", tmp_path)
    created = pb._create_flow("全局参数")
    # 桥层是单例：上一条用例可能在 _flows 里留下同名 path 的 sha，乐观锁会误判
    api._flows.pop(created["path"], None)
    return created["path"], tmp_path / created["path"]


def _call(method: str, **params: Any) -> dict[str, Any]:
    env = pb.dispatch(method, params)
    assert env["ok"] is True, env
    return env["data"]


def _doc(fp: Path) -> dict[str, Any]:
    return json.loads(fp.read_text(encoding="utf-8"))


def test_bridge_registers_save_flow_inputs() -> None:
    """接线层：新 RPC 必须真被 @api_method 收进桥层注册表。"""
    assert "rpa.saveFlowInputs" in pb._registry()


def test_save_flow_inputs_round_trips_all_json_types(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """文本 / 数字 / 布尔 / 数组 / 对象 / 空串都要按 JSON 往返，且落盘与 openFlow 一致。"""
    path, fp = _flow_fixture(tmp_path, monkeypatch)
    opened = _call("rpa.openFlow", path=path)
    assert set(opened["inputs"]) == set(_doc(fp)["inputs"])

    values = {
        "keyword": '"AI效率工具"',
        "limit": "20",
        "enabled": "true",
        "rows": "[1, 2]",
        "meta": '{"a": 1}',
        "empty": '""',
    }
    data = _call("rpa.saveFlowInputs", path=path, values=values)
    expected = {
        "keyword": "AI效率工具",
        "limit": 20,
        "enabled": True,
        "rows": [1, 2],
        "meta": {"a": 1},
        "empty": "",
    }
    assert data["ok"] is True
    assert data["inputs"] == expected
    assert _doc(fp)["inputs"] == expected
    assert data["flow"]["inputs"] == expected
    assert data["compile"]["ok"] is True
    assert set(data["added"]) == set(expected) - set(opened["inputs"])
    assert set(data["removed"]) == set(opened["inputs"]) - set(expected)


@pytest.mark.parametrize(
    ("values", "code"),
    [
        ({"a.b": "1"}, "INVALID_DECLARATION"),
        ({"loop": "1"}, "INVALID_DECLARATION"),
        ({"": "1"}, "INVALID_DECLARATION"),
        ({"x": "[1,"}, "INVALID_DECLARATION"),
        (["not", "a", "dict"], "INVALID_SUBMIT"),
    ],
)
def test_save_flow_inputs_rejects_invalid_values_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, values: Any, code: str
) -> None:
    """名称格式 / 保留名 / 空名 / 坏 JSON / 形状错误一律拒绝，且磁盘逐字节不变。"""
    path, fp = _flow_fixture(tmp_path, monkeypatch)
    _call("rpa.openFlow", path=path)
    before = fp.read_bytes()
    data = _call("rpa.saveFlowInputs", path=path, values=values)
    assert data["ok"] is False
    assert data["code"] == code
    assert data["error"]
    assert fp.read_bytes() == before


def test_save_flow_inputs_honors_sha_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """文件在打开后被别处改过 ⇒ 拒绝覆盖，外部改动原样留在盘上。"""
    path, fp = _flow_fixture(tmp_path, monkeypatch)
    _call("rpa.openFlow", path=path)
    doc = _doc(fp)
    doc["name"] = "外部改过"
    external = (json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    fp.write_bytes(external)
    data = _call("rpa.saveFlowInputs", path=path, values={"only": '"x"'})
    assert data["ok"] is False
    assert data["code"] == "FLOW_CHANGED"
    assert fp.read_bytes() == external


def test_save_flow_inputs_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """重复保存同一份声明不该改动文件（已保存状态才不会被误标脏）。"""
    path, fp = _flow_fixture(tmp_path, monkeypatch)
    values = {"keyword": '"AI效率工具"'}
    first = _call("rpa.saveFlowInputs", path=path, values=values)
    assert first["saved"] is True
    before = fp.read_bytes()
    second = _call("rpa.saveFlowInputs", path=path, values=values)
    assert second["saved"] is False
    assert fp.read_bytes() == before


def test_frontend_wires_globals_panel_to_rpc() -> None:
    """源码接线：RPC、HTML 骨架、样式归属、打开流程时刷新、运行时切日志 tab。"""
    main = MAIN_JS.read_text(encoding="utf-8")
    api = API_PY.read_text(encoding="utf-8")
    html = INDEX_HTML.read_text(encoding="utf-8")
    css = STYLE_CSS.read_text(encoding="utf-8")
    for needle in (
        "ppx.call('rpa.saveFlowInputs'",  # 钉实际调用，注释里出现名字不算接线
        "bindBottomDock()",
        "globalsSaved = flow.inputs",
        "activateDockTab('log')",
    ):
        assert needle in main, f"main.js 缺 {needle}"
    for banned in ("DOCK_CSS", "DND_CSS", "injectDockStyles", "injectDndStyles"):
        assert banned not in main, f"main.js 里还有运行时注入残留：{banned}"
    for needle in (
        '@api_method("rpa.saveFlowInputs")',
        "declaration_from_entries(entries)",
        "Workflow.model_validate(document)",
    ):
        assert needle in api, f"api.py 缺 {needle}"
    for needle in (
        'id="bottomDock"',
        'data-tab="globals"',
        'data-panel="globals"',
        'id="globalsRows"',
        'id="globalsSave"',
    ):
        assert needle in html, f"index.html 缺 {needle}"
    for needle in (
        "grid-template-rows: minmax(0, 1fr) auto",
        "grid-column: 1 / -1",
        ".dockTab.active",
        ".globalRow",
    ):
        assert needle in css, f"style.css 缺 {needle}"


def test_bottom_dock_check_is_wired_into_full_gate() -> None:
    """接线哨兵：摘掉 check_all.py 里那一行，真跑判据就不再进全门禁。"""
    assert DOCK_SCRIPT.is_file()
    hits = CHECK_ALL.read_text(encoding="utf-8").count('"check_editor_bottom_dock.mjs"')
    assert hits == 1, f"check_all.py 里接线命中 {hits} 次（须恰好 1）"


def test_dist_bundle_is_rebuilt_from_source() -> None:
    """改了 src 忘了 npm run build ⇒ 工作台托管的还是旧编辑器（JS 与 CSS 都要查）。"""
    bundles = sorted(DIST_DIR.glob("*.js"))
    assert bundles, "gui/dist 未构建：在 app/gui 下跑 npm run build"
    blob = "\n".join(p.read_text(encoding="utf-8") for p in bundles)
    for needle in ("rpa.saveFlowInputs", "bottomDock", "globalsCount"):
        assert needle in blob, f"dist JS 产物缺 {needle}"
    # 双向：源码里出现的字面量 RPC 名，产物里一个都不能少（防「产物还是旧的那份」）
    main_src = MAIN_JS.read_text(encoding="utf-8")
    rpc_names = set(re.findall(r"ppx\.call\('(rpa\.[A-Za-z]+)'", main_src))
    assert "rpa.saveFlowInputs" in rpc_names, "源码里没找到新 RPC 的字面量调用"
    for name in rpc_names:
        assert name in blob, f"dist JS 产物缺源码里的 {name}"
    styles = sorted(DIST_DIR.glob("*.css"))
    assert styles, "gui/dist 未构建 CSS"
    built_css = "\n".join(p.read_text(encoding="utf-8") for p in styles)
    for needle in ("#bottomDock", ".dockTab.active", ".globalRow", ".card.drop-before"):
        assert needle in built_css, f"dist CSS 产物缺 {needle}"


def test_prototype_dir_is_shared_runtime() -> None:
    """ADR 0022-R3：原型目录整体共享，客户端打包配置仍精确留给 ppx-only。"""
    scope = json.loads(BRANCH_SCOPE.read_text(encoding="utf-8"))
    assert ".harness/demo/ppx_probe/" in scope["shared_runtime"]
    sys.path.insert(0, str(ROOT / ".harness" / "scripts"))
    import check_branch_scope as gate

    for rel in (
        "app/gui/index.html",
        "app/gui/src/style.css",
        "app/api/api.py",
        "app/gui/src/main.js",
    ):
        path = ".harness/demo/ppx_probe/" + rel
        assert gate.owner_of(path, scope) is None, f"{path} 仍被独占"
    assert gate.owner_of(".harness/demo/ppx_probe/app/ppx.toml", scope) == "ppx-only"


def test_bottom_dock_check_script_passes() -> None:
    """应用层测试的独立通道也真跑一次 DOM 判据，不等 check_all.py。"""
    node = shutil.which("node")
    if not node:
        pytest.skip("本机没有 node；check_all.py 同样会跳过 JS 判据")
    done = subprocess.run(
        [node, str(DOCK_SCRIPT)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
    )
    assert done.returncode == 0, (done.stdout + done.stderr)[-3000:]
    assert "EDITOR BOTTOM DOCK CHECK PASSED" in done.stdout
