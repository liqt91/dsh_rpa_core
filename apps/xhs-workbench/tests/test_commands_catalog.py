"""指令目录单测：i18n.js 文本解析、manifest 摊平、分组排序。"""

from __future__ import annotations

from pathlib import Path

from src import commands_catalog as cc

REPO = Path(__file__).resolve().parents[3]
I18N = REPO / "src" / "rpa_core" / "gui" / "i18n.js"
COMMANDS = REPO / "commands"


def test_labels_parse_only_the_commands_block(tmp_path: Path) -> None:
    fixture = tmp_path / "i18n.js"
    fixture.write_text(
        "window.RPA_I18N = {\n"
        "  commands: {\n"
        '    "browser.navigate": "打开网页",\n'
        '    "data.log": "打印日志",\n'
        "  },\n"
        "  fields: {\n"
        '    "browser.navigate.url": "目标网址",\n'
        "  },\n"
        "};\n",
        encoding="utf-8",
    )
    labels = cc.load_command_labels(fixture)
    assert labels == {"browser.navigate": "打开网页", "data.log": "打印日志"}
    # fields 段的「命令.字段」键不能被混进命令表（同一 id 形态，靠缩进切分段）
    assert "browser.navigate.url" not in labels


def test_labels_real_i18n_covers_browser_commands() -> None:
    labels = cc.load_command_labels(I18N)
    assert labels["browser.navigate"] == "打开网页"
    assert labels["browser.screenshot"] == "网页截图"
    assert len(labels) >= 60


def test_load_commands_real_dir_groups_and_sorts() -> None:
    labels = cc.load_command_labels(I18N)
    commands = cc.load_commands(COMMANDS, labels)
    assert len(commands) >= 80
    keys = [(c["group"], c["order"], c["id"]) for c in commands]
    assert keys == sorted(keys)  # 排序本身就是契约：指令栏按组聚合、组内按 palette 顺序
    nav = next(c for c in commands if c["id"] == "browser.navigate")
    assert nav["label"] == "打开网页"
    assert nav["groupLabel"] == "浏览器"
    url_field = next(f for f in nav["fields"] if f["key"] == "url")
    bt_field = next(f for f in nav["fields"] if f["key"] == "browserType")
    assert bt_field["required"] is True
    assert url_field["required"] is False  # navigate 的 required 只有 browserType
    assert url_field["fx"] is True  # manifest 的 x-fx 必须透给表单（可切表达式）


def test_unknown_group_falls_back_to_group_key() -> None:
    labels = cc.load_command_labels(I18N)
    commands = cc.load_commands(COMMANDS, labels)
    groups = {c["group"] for c in commands}
    assert {"browser", "data", "workflow"} <= groups
    for c in commands:
        assert c["groupLabel"]  # 永不空
