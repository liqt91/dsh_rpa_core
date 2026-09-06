import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONNECTOR = ROOT / "workbuddy-connector"
CLI_JSON = json.loads((CONNECTOR / "cli.json").read_text(encoding="utf-8"))
META = json.loads((CONNECTOR / "connector-meta.json").read_text(encoding="utf-8"))


def _cli(*args: str) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "rpa_core.cli", *args],
        capture_output=True, text=True, encoding="utf-8",
    )
    return proc.returncode, proc.stdout


def test_connector_meta_schema():
    assert META["type"] == "cli"
    assert re.fullmatch(r"[a-z0-9-]+", META["source"]), "source must be kebab-case"
    assert META["source"] == "rpa-core"
    for field in ("name", "name_en", "description", "description_zh",
                  "description_en", "source", "type", "version"):
        assert META.get(field), f"missing {field}"
    assert META["examples_zh"] and META["examples_en"]
    assert len(META["examples_zh"]) >= 2 and len(META["examples_en"]) >= 2


def test_cli_json_required_fields():
    for platform in ("darwin", "linux", "win32"):
        assert CLI_JSON["init"].get(platform), f"init missing {platform}"
        assert CLI_JSON["status"].get(platform), f"status missing {platform}"
        assert CLI_JSON["unAuth"].get(platform), f"unAuth missing {platform}"
        assert CLI_JSON["auth"].get(platform), f"auth missing {platform}"
    assert CLI_JSON["runtime"]["type"] == "python"
    assert CLI_JSON.get("statusMatch")


def test_cli_json_win32_entries_do_not_reference_cmd_shims():
    """pip/distlib 在 Windows 只生成 .exe 入口脚本，不生成 .cmd（干净 venv 实测）。"""
    for section in ("init", "auth", "unAuth", "status"):
        win32_cmd = CLI_JSON[section]["win32"]
        assert ".cmd" not in win32_cmd, (
            f"{section}.win32 references nonexistent .cmd shim: {win32_cmd}"
        )


def test_skill_md_frontmatter_required_fields():
    text = (CONNECTOR / "skills" / "rpa-automation" / "SKILL.md").read_text(encoding="utf-8")
    match = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n", text, re.DOTALL)
    assert match, "SKILL.md missing YAML frontmatter"
    for field in ("name", "description", "description_zh", "description_en", "version", "author"):
        assert re.search(rf"^{field}: .+$", match.group(1), re.MULTILINE), (
            f"SKILL.md frontmatter missing required field: {field}"
        )


def test_icon_exists():
    assert (CONNECTOR / "icon.svg").is_file()


def test_status_matches_status_match():
    """status 输出必须匹配 cli.json 的 statusMatch（WorkBuddy 判定已连接的依据）。"""
    code, out = _cli("status")
    assert code == 0
    assert re.search(CLI_JSON["statusMatch"], out), (
        f"status output {out!r} does not match statusMatch {CLI_JSON['statusMatch']!r}"
    )


def test_auth_and_unauth_are_no_side_effect_ready():
    code, out = _cli("auth")
    assert code == 0
    assert json.loads(out)["status"] == "ready"
    code, out = _cli("unauth")
    assert code == 0
    assert json.loads(out)["status"] == "ok"


def test_status_is_idempotent_and_reports_version():
    for _ in range(2):
        code, out = _cli("status")
        assert code == 0
        payload = json.loads(out)
        assert payload["status"] == "ready"
        assert payload["version"]
