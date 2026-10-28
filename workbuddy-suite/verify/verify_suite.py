"""三件套的静态 + 协议自检（不需要窗口、不需要浏览器）。

它检查的是「上架前会被打回的那类问题」：
  · SKILL.md 的 frontmatter 字段是否齐全、命名是否合规、引用是否死链；
  · 扩展 manifest 里的每个文件是否真的存在、权限是否过宽；
  · 会话协议能不能真跑通（起服务 → 排队 → 取件 → 长轮询 → 关服务）；
  · 安全边界拦不拦得住（伪造 Origin、越界路径）。

跑法：python verify/verify_suite.py
编辑窗口（.harness/demo/ppx_probe）的真机探针在它自己的目录：launcher_probe / ui_probe /
exec_probe / run_probe 等；插件真机测试在 extension_test.mjs。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

SUITE = Path(__file__).resolve().parents[1]
SKILL_DIR = SUITE / "skills" / "workflow-studio"
SKILL_MD = SKILL_DIR / "SKILL.md"
STUDIO = SKILL_DIR / "scripts" / "studio.py"
EXT = SUITE / "extension"
PROBE_APP = SUITE.parent / ".harness" / "demo" / "ppx_probe" / "app"
PY = sys.executable
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# frontmatter：手写一个严格的小解析器（仓库里没有 pyyaml）。
# 严格 = 只认 `key: value` 与 `- item`，出现别的形状就报错，
# 免得「解析器太宽容，字段其实没写也对」。
# ---------------------------------------------------------------------------


def parse_frontmatter(text: str) -> tuple[dict[str, object], str]:
    if not text.startswith("---\n"):
        raise ValueError("SKILL.md 必须以 --- 开头")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError("frontmatter 没有闭合的 ---")
    body = text[end + 5 :]
    meta: dict[str, object] = {}
    current: str | None = None
    for raw in text[4:end].split("\n"):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw.startswith("  - ") or raw.startswith("- "):
            if current is None:
                raise ValueError(f"列表项没有归属的键：{raw!r}")
            item = raw.split("- ", 1)[1].strip()
            value = meta.get(current)
            if value is None or value == "":
                meta[current] = []
                value = meta[current]
            if not isinstance(value, list):
                raise ValueError(f"{current} 既有标量又有列表")
            value.append(item.strip('"').strip("'"))
            continue
        if raw.startswith(" "):
            continue  # 长描述换行续写
        key, sep, value = raw.partition(":")
        if not sep:
            raise ValueError(f"无法解析的行：{raw!r}")
        key = key.strip()
        current = key
        meta[key] = value.strip().strip('"').strip("'")
    return meta, body


def check_skill() -> None:
    text = SKILL_MD.read_text(encoding="utf-8")
    try:
        meta, body = parse_frontmatter(text)
        check("SKILL.md frontmatter 可解析", True, f"{len(meta)} 个字段")
    except ValueError as exc:
        check("SKILL.md frontmatter 可解析", False, str(exc))
        return

    required = [
        "name",
        "display_name",
        "display_name_en",
        "description",
        "description_zh",
        "description_en",
        "examples_zh",
        "examples_en",
        "version",
        "author",
    ]
    missing = [key for key in required if not meta.get(key)]
    check("frontmatter 必填字段齐全", not missing, "缺 " + ", ".join(missing) if missing else "")

    name = str(meta.get("name", ""))
    check(
        "name 是 kebab-case 且与目录同名",
        bool(re.fullmatch(r"[a-z][a-z0-9-]*", name)) and name == SKILL_DIR.name,
        name,
    )
    check(
        "version 是语义化版本",
        bool(re.fullmatch(r"\d+\.\d+\.\d+", str(meta.get("version", "")))),
        str(meta.get("version")),
    )
    display = str(meta.get("display_name", ""))
    check(
        "display_name 不含空格/特殊字符（付费资产硬规则）",
        bool(re.fullmatch(r"[\w\u4e00-\u9fff-]+", display)),
        display,
    )

    description = str(meta.get("description", ""))
    triggers = re.findall(r"[「]([^」]+)[」]", description)
    check(
        "description 明确写了触发词",
        "触发词" in description and len(triggers) >= 5,
        f"{len(triggers)} 个短语",
    )
    covered = all(word in description for word in ("编辑器", "确认", "拾取"))
    check("description 覆盖编辑/确认/拾取三个场景", covered)
    check("description 长度 <= 500", len(description) <= 500, f"{len(description)} 字")

    # 引用的文件必须存在（死链是上架材料最常见的低级错）
    refs = set(re.findall(r"(?:scripts|references|templates)/[\w./-]+", body))
    missing_refs = [ref for ref in refs if not (SKILL_DIR / ref).exists()]
    check(
        "正文引用的脚本/文档都存在",
        not missing_refs,
        "缺 " + ", ".join(missing_refs) if missing_refs else f"{len(refs)} 个引用",
    )

    examples = meta.get("examples_zh")
    check(
        "examples_zh 至少 3 条",
        isinstance(examples, list) and len(examples) >= 3,
        str(len(examples or [])),
    )
    check(
        "正文不硬编码 token/key",
        not re.search(r"(sk-[A-Za-z0-9]{8,}|token\s*=\s*['\"][^'\"]{8,})", text),
    )


def check_extension() -> None:
    manifest = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))
    version = str(manifest.get("manifest_version"))
    check("扩展是 MV3", manifest.get("manifest_version") == 3, version)
    check("扩展有名称/版本/描述", all(manifest.get(k) for k in ("name", "version", "description")))

    referenced = [
        manifest.get("background", {}).get("service_worker"),
        manifest.get("action", {}).get("default_popup"),
        *manifest.get("icons", {}).values(),
        *manifest.get("action", {}).get("default_icon", {}).values(),
    ]
    referenced += [entry.get("path") for entry in manifest.get("content_scripts", [])]
    missing = [item for item in referenced if item and not (EXT / item).is_file()]
    check(
        "manifest 引用的文件都存在",
        not missing,
        "缺 " + ", ".join(missing) if missing else f"{len([r for r in referenced if r])} 个文件",
    )

    permissions = set(manifest.get("permissions", []))
    check(
        "权限最小集（无 <all_urls> / tabs 全量）",
        permissions <= {"activeTab", "scripting", "storage", "contextMenus", "alarms"},
        str(sorted(permissions)),
    )
    # 用到的 chrome.* API 必须真的在 manifest 里声明过。
    # 这条是为 `chrome.alarms` 补的：代码里 create 了定时器，manifest 却没给 alarms 权限，
    # 结果徽标永远停在旧状态 —— 扩展「看起来能装」，功能是死的。
    strict = {
        "alarms": "alarms",
        "contextMenus": "contextMenus",
        "scripting": "scripting",
        "storage": "storage",
        "cookies": "cookies",
        "downloads": "downloads",
        "notifications": "notifications",
        "identity": "identity",
        "history": "history",
        "bookmarks": "bookmarks",
        "webRequest": "webRequest",
        "tabGroups": "tabGroups",
        "offscreen": "offscreen",
    }
    used: set[str] = set()
    for name in ("background.js", "popup.js", "content.js"):
        path = EXT / name
        if path.is_file():
            used |= set(re.findall(r"chrome\.(\w+)\.", path.read_text(encoding="utf-8")))
    missing_permissions = sorted({strict[api] for api in used if api in strict} - permissions)
    check(
        "用到的 chrome.* API 都声明了权限",
        not missing_permissions,
        "缺 " + ", ".join(missing_permissions) if missing_permissions else f"{len(used)} 个 API",
    )
    check(
        "商店描述长度 <= 132 字符",
        len(str(manifest.get("description", ""))) <= 132,
        f"{len(str(manifest.get('description', '')))} 字",
    )
    hosts = manifest.get("host_permissions", [])
    check(
        "host_permissions 仅回环",
        all(h.startswith(("http://127.0.0.1", "http://localhost")) for h in hosts),
        str(hosts),
    )

    for size in (16, 32, 48, 128):
        check(f"图标 {size}px 存在", (EXT / "icons" / f"icon-{size}.png").is_file())


def check_js() -> None:
    files = [EXT / "background.js", EXT / "popup.js", EXT / "content.js"]
    for path in files:
        result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
        check(f"{path.name} 语法正确", result.returncode == 0, result.stderr.strip()[:200])


def check_python() -> None:
    for path in (STUDIO, PROBE_APP / "api" / "api.py"):
        result = subprocess.run([PY, "-m", "py_compile", str(path)], capture_output=True, text=True)
        check(f"{path.name} 可编译", result.returncode == 0, result.stderr.strip()[:200])


# ---------------------------------------------------------------------------
# 协议：起真服务，走一遍 Agent 会走的路
# ---------------------------------------------------------------------------


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def http(
    method: str,
    port: int,
    route: str,
    payload: dict | None = None,
    origin: str | None = None,
) -> dict:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(f"http://127.0.0.1:{port}{route}", data=body, method=method)
    request.add_header("Content-Type", "application/json")
    if origin:
        request.add_header("Origin", origin)
    try:
        with OPENER.open(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read().decode("utf-8"))


def check_protocol() -> None:
    work = Path(tempfile.mkdtemp(prefix="studio-verify-"))
    home = work / "home"
    target = work / "flow.json"
    target.write_text('{"root": {"type": "sequence"}}\n', encoding="utf-8")
    port = free_port()
    env = dict(os.environ)
    env["STUDIO_HOME"] = str(home)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen(
        [PY, str(STUDIO), "serve", "--port", str(port), "--root", str(work)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
    )
    try:
        health = None
        for _ in range(40):
            try:
                health = http("GET", port, "/health")
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.15)
        check("会话服务能起来", bool(health and health.get("ok")), f"port {port}")
        if not health:
            return

        opened = http("POST", port, "/open", {"path": str(target), "reason": "自检"})
        detail = opened.get("path") or opened.get("error", "")
        check("open 接受合法路径", bool(opened.get("ok")), detail)

        inbox = http("GET", port, "/inbox?since=0&client=editor")
        queued = inbox.get("requests", [])
        found = any(item["path"] == str(target) for item in queued)
        check("收件箱能取到打开请求", found, f"{len(queued)} 条")
        again = http("GET", port, f"/inbox?since={inbox['cursor']}&client=editor")
        check("游标推进后不重复投递", again.get("requests") == [], str(again.get("requests")))

        check(
            "编辑窗口在线状态可见",
            (http("GET", port, "/health").get("editor") or {}).get("online") is True,
        )

        saved = http(
            "POST",
            port,
            "/events",
            {"kind": "save", "path": str(target), "sha": "deadbeef", "bytes": 12},
        )
        check("保存事件被接收", bool(saved.get("ok")), f"cursor={saved.get('cursor')}")

        waited = http("GET", port, "/events?since=0&kinds=save,capture&timeout=3")
        check(
            "长轮询立刻返回已有事件",
            [e["kind"] for e in waited.get("events", [])] == ["save"],
            str([e["kind"] for e in waited.get("events", [])]),
        )

        started = time.time()
        empty = http("GET", port, "/events?since=9999&timeout=1.0")
        elapsed = time.time() - started
        check(
            "没有事件时长轮询按时超时（不空转）",
            empty.get("timedOut") is True and 0.9 < elapsed < 3.5,
            f"{elapsed:.2f}s",
        )

        capture = http(
            "POST",
            port,
            "/capture",
            {"selector": "#ok", "tag": "button", "text": "确定"},
            origin="chrome-extension://abcdef",
        )
        check(
            "插件能上报拾取结果（带合法 Origin）",
            bool(capture.get("ok")),
            f"id={capture.get('id')}",
        )
        check(
            "拾取结果进了 captures",
            len(http("GET", port, "/captures?since=0").get("captures", [])) == 1,
        )

        forged = http(
            "POST", port, "/capture", {"selector": "#evil"}, origin="https://evil.example.com"
        )
        check(
            "伪造 Origin 被拒绝（安全边界）",
            forged.get("code") == "ORIGIN_DENIED",
            str(forged.get("code")),
        )

        outside = http("POST", port, "/open", {"path": str(Path.home() / "not-in-root.txt")})
        check(
            "越界路径被拒绝（--root 生效）",
            outside.get("code") == "BAD_PATH",
            str(outside.get("code")),
        )

        relative = http("POST", port, "/open", {"path": "flow.json"})
        check(
            "相对路径被拒绝（不猜 cwd）",
            relative.get("code") == "BAD_PATH",
            str(relative.get("code")),
        )

        stop = http("POST", port, "/shutdown", {})
        check("服务能受控停止", bool(stop.get("ok")))
        for _ in range(50):
            if proc.poll() is not None:
                break
            time.sleep(0.1)
        check("停止后进程真的退出", proc.poll() is not None, f"returncode={proc.poll()}")
        leftover = home / "endpoint.json"
        check("停止后端点文件被清理", not leftover.exists(), str(leftover))
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        if proc.stdout:
            proc.stdout.close()
        shutil.rmtree(work, ignore_errors=True)


def check_dist() -> None:
    """上架包的结构：平台按 `{skill-name}/SKILL.md` 找入口，套错层级会解析失败。"""
    import zipfile

    skill_zip = SUITE / "dist" / "workflow-studio-skill.zip"
    if not skill_zip.is_file():
        check("技能包已生成", False, "先跑 python tools/pack.py")
    else:
        with zipfile.ZipFile(skill_zip) as bundle:
            names = bundle.namelist()
        entry = f"{SKILL_DIR.name}/SKILL.md"
        check(
            f"技能包入口是 {entry}（平台目录结构）",
            entry in names and "SKILL.md" not in names,
            f"{len(names)} 个文件"
            + (
                ""
                if entry in names
                else f"，实际入口 {[n for n in names if n.endswith('SKILL.md')]}"
            ),
        )

    ext_zip = SUITE / "dist" / "workbuddy-studio-extension.zip"
    if ext_zip.is_file():
        with zipfile.ZipFile(ext_zip) as bundle:
            names = set(bundle.namelist())
        check(
            "扩展包根目录就是 manifest.json",
            "manifest.json" in names and not any(n.endswith("/manifest.json") for n in names),
        )
    else:
        check("扩展包已生成", False, "先跑 python tools/pack.py")

    connector_zip = SUITE / "dist" / "rpa-core-connector.zip"
    if connector_zip.is_file():
        with zipfile.ZipFile(connector_zip) as bundle:
            names = set(bundle.namelist())
        check(
            "连接器包含 meta/cli/icon + skills/{name}/SKILL.md",
            {"connector-meta.json", "cli.json", "icon.svg", "skills/rpa-automation/SKILL.md"}
            <= names,
            f"{len(names)} 个文件",
        )
    else:
        check("连接器包已生成", False, "先跑 tools/pack.py")


def main() -> int:
    check_skill()
    check_extension()
    check_js()
    check_python()
    check_dist()
    check_protocol()

    failed = [item for item in RESULTS if not item[1]]
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
