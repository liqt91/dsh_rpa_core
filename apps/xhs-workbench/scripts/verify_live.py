#!/usr/bin/env python3
"""小红书工作台真机 E2E 验证（显式 opt-in，不进默认门禁）。

默认只做**不占机**的静态预检；只有显式 ``--run`` 才会启动浏览器、打开小红书、
用当前登录态搜索一次。判据分四层，任一层不过都非零退出，绝不把「离线 /
空结果 / 没落盘」折叠成成功：

1. ``run`` 终态是 ``succeeded``；
2. ``executeScript`` 返回可解析且非空的 JSON 数组；
3. 数组里至少有带标题或链接的条目（不是空壳）；
4. ``data.writeText`` 落盘内容与 ``executeScript`` 返回值逐字一致；
5. ``waitResults`` 的 URL 确实是搜索结果页——否则「抓到了 30 条首页 feed」
   也会被前四条放过，属于采集没生效却报绿的假绿灯。

静态预检里的 ``env-status`` 只用来确认浏览器二进制与扩展安装记录：host 空闲
4 秒会自动退出（ADR 0015），扩展 service worker 也会休眠，所以此刻报 offline
不等于通道坏；通道是否真的可用，只有 ``--run`` 能证明。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parents[1]
CLI = REPO_ROOT / ".venv" / "Scripts" / "rpa-core.exe"
WORKFLOW = APP_ROOT / "workflows" / "xhs_product_search.json"
ELEMENT = APP_ROOT / "workflows" / "elements" / "xhs_search_box.json"
HOST_NAME = "com.rpa_core.ext_bridge"

sys.path.insert(0, str(APP_ROOT))
from src.collector import build_run_command, parse_result, step_output  # noqa: E402


def _cli_env() -> dict[str, str]:
    return dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")


def _cli(argv: list[str], timeout: float = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [str(CLI), *argv],
        cwd=REPO_ROOT,
        env=_cli_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def _json_from_stdout(text: str) -> dict[str, Any] | None:
    """CLI 输出是 indent=2 的多行 JSON，前面还可能夹环境噪声；扫第一个完整对象。"""
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            obj, _end = decoder.raw_decode(text, index)
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _host_manifest_file(browser: str) -> Path | None:
    """从 HKCU 注册表读 native host manifest 路径（Windows 事实源）。"""
    if sys.platform != "win32":
        return None
    import winreg  # noqa: PLC0415

    vendor = "Microsoft\\Edge" if browser == "msedge" else "Google\\Chrome"
    key_path = f"Software\\{vendor}\\NativeMessagingHosts\\{HOST_NAME}"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            return Path(str(winreg.QueryValue(key, "")))
    except OSError:
        return None


def _host_manifest_path(browser: str) -> tuple[bool, str]:
    """验 host manifest 及其指向的 exe 是否存在。"""
    manifest = _host_manifest_file(browser)
    if manifest is None:
        if sys.platform != "win32":
            return True, "非 Windows：跳过注册表检查"
        return False, f"HKCU 里没有 {browser} 的 {HOST_NAME} 注册表键"
    if not manifest.is_file():
        return False, f"host manifest 不存在：{manifest}"
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        host_exe = Path(str(payload["path"]))
    except (OSError, ValueError, KeyError) as exc:
        return False, f"host manifest 不可解析：{manifest}（{exc}）"
    if not host_exe.is_file():
        return False, f"host 可执行文件不存在：{host_exe}"
    return True, f"{manifest} -> {host_exe}"


def _extension_enabled_in_profile(browser: str) -> tuple[bool, str]:
    """只读扫浏览器 profile：host 白名单里的扩展 ID 必须有「未被禁用」的记录。

    `env-status` 传空 extension_id 时只按 unpacked 目录路径匹配，遇到装在别处
    （例如 uv tools 目录）的已启用扩展会误报 installed=false；这里按白名单 ID
    直接查 Secure Preferences，才是「这个浏览器的插件能不能连 host」的真判据。
    """
    manifest = _host_manifest_file(browser)
    if manifest is None or not manifest.is_file():
        if sys.platform != "win32":
            return True, "非 Windows：跳过 profile 探测"
        return False, "拿不到 host manifest，无法核对白名单 ID"
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        ids = {
            str(origin).removeprefix("chrome-extension://").rstrip("/")
            for origin in payload.get("allowed_origins") or []
        }
    except (OSError, ValueError) as exc:
        return False, f"host manifest 不可解析：{exc}"

    vendor = (
        Path("Google") / "Chrome" if browser == "chrome" else Path("Microsoft") / "Edge"
    )
    base = Path.home() / "AppData" / "Local" / vendor / "User Data"
    if not base.is_dir():
        return False, f"浏览器 profile 目录不存在：{base}"
    for profile in sorted(base.iterdir()):
        prefs = profile / "Secure Preferences"
        if not prefs.is_file():
            continue
        try:
            settings = (
                (json.loads(prefs.read_text(encoding="utf-8")).get("extensions") or {})
                .get("settings") or {}
            )
        except (OSError, ValueError):
            continue
        for eid in ids:
            record = settings.get(eid)
            if not isinstance(record, dict):
                continue
            disable = record.get("disable_reasons") or []
            if not disable:
                return True, (
                    f"profile={profile.name}, id={eid}, location={record.get('location')}, "
                    f"disable_reasons=[]"
                )
    return False, f"{base} 里没有「已启用且在 host 白名单中」的扩展记录；ids={sorted(ids)}"


def precheck(browser: str) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    add("cli", CLI.is_file(), str(CLI))
    add("workflow", WORKFLOW.is_file(), str(WORKFLOW))
    add("element", ELEMENT.is_file(), str(ELEMENT))

    if CLI.is_file() and WORKFLOW.is_file():
        try:
            proc = _cli(["validate", str(WORKFLOW)])
            payload = _json_from_stdout(proc.stdout) or {}
            add(
                "validate",
                proc.returncode == 0 and payload.get("valid") is True,
                f"rc={proc.returncode}, stdout={proc.stdout.strip()[:200]}",
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            add("validate", False, f"validate 调用失败：{exc}")

    if CLI.is_file():
        try:
            proc = _cli(["env-status"])
            payload = _json_from_stdout(proc.stdout) or {}
            browsers = payload.get("browsers") or {}
            # env-status 用的是 chrome/edge；executor 的 browserType 用 chrome/msedge。
            status_key = "edge" if browser == "msedge" else browser
            info = browsers.get(status_key) or {}
            add(
                "browser_binary",
                bool(info.get("binary")),
                f"browser={browser}, statusKey={status_key}, "
                f"binary={info.get('binary')}, running={info.get('running')}, "
                f"envStatusInstalled={info.get('installed')}（仅按 unpacked 路径匹配，不作判据）",
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            add("browser_binary", False, f"env-status 调用失败：{exc}")

    ok, detail = _extension_enabled_in_profile(browser)
    add("extension_enabled", ok, detail)
    ok, detail = _host_manifest_path(browser)
    add("native_host", ok, detail)
    return checks


def _write_evidence(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # write_text 在 Windows 会转 CRLF；证据文件用字节写，保持 LF。
    path.write_bytes(
        (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )


def _find_run_id(stdout: str) -> str:
    for line in stdout.splitlines():
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict) and obj.get("run_id"):
            return str(obj["run_id"])
    return ""


def evaluate_run(
    *,
    cli_returncode: int,
    status: str,
    group: str,
    raw_text: str,
    written_text: str | None,
    page_url: str = "",
    keyword: str = "",
) -> dict[str, Any]:
    """真机 E2E 的唯一判据函数：五条都绿才算跑通，坏输入必须各自报红。

    抽成纯函数是为了让 ``tests/test_live_verdict.py`` 能在不占机的情况下对每条
    判据做负向验证——脚本里内联判据只会得到「没测过但看起来绿」的假绿灯。
    """
    checks = {
        "run_succeeded": (
            cli_returncode == 0 and status == "succeeded" and group == "ok"
        ),
        "items_parsed": False,
        "items_have_content": False,
        "write_matches": bool(raw_text) and written_text == raw_text,
        "search_effective": "search_result" in page_url
        or bool(keyword and keyword in page_url),
    }
    items: Any = None
    reason = ""
    if raw_text:
        try:
            items = json.loads(raw_text)
        except ValueError as exc:
            reason = f"executeScript 返回值不是 JSON：{exc}"
        if isinstance(items, list):
            checks["items_parsed"] = len(items) > 0
            checks["items_have_content"] = any(
                str(row.get("title") or row.get("href") or "").strip()
                for row in items
                if isinstance(row, dict)
            )
        elif not reason:
            reason = "executeScript 返回值不是数组"
    return {"checks": checks, "items": items, "reason": reason}


def run_live(args: argparse.Namespace) -> int:
    checks = precheck(args.browser)
    failed = [c["name"] for c in checks if not c["ok"]]
    artifacts = (REPO_ROOT / args.artifacts).resolve()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    evidence_path = artifacts / f"e2e-verdict-{stamp}.json"
    base: dict[str, Any] = {
        "verdict": "FAILED",
        "checkedAt": datetime.now(UTC).isoformat(),
        "browserType": args.browser,
        "keyword": args.keyword,
        "artifacts": str(artifacts),
        "precheck": checks,
    }
    if failed:
        base["reason"] = f"预检未过：{', '.join(failed)}；未启动浏览器"
        _write_evidence(evidence_path, base)
        print(json.dumps(base, ensure_ascii=False, indent=2))
        return 2

    artifacts.mkdir(parents=True, exist_ok=True)
    cmd = build_run_command(
        WORKFLOW,
        args.keyword,
        artifacts,
        binary=str(CLI),
        extra_inputs={"browserType": args.browser, "workspace": str(artifacts)},
    )
    base["command"] = cmd
    try:
        proc = subprocess.run(  # noqa: S603
            cmd,
            cwd=REPO_ROOT,
            env=_cli_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=args.timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        base["reason"] = f"run 超过 {args.timeout}s 未结束"
        base["stdoutTail"] = (exc.stdout or "")[-1500:]
        _write_evidence(evidence_path, base)
        print(json.dumps(base, ensure_ascii=False, indent=2))
        return 1
    except OSError as exc:
        base["reason"] = f"run 启动失败：{exc}"
        _write_evidence(evidence_path, base)
        print(json.dumps(base, ensure_ascii=False, indent=2))
        return 1

    base["cliReturnCode"] = proc.returncode
    base["stdoutTail"] = proc.stdout[-1500:]
    base["stderrTail"] = proc.stderr[-800:]
    run_id = _find_run_id(proc.stdout)
    base["runId"] = run_id
    result_path = artifacts / run_id / "result.json" if run_id else Path("")
    base["resultPath"] = str(result_path)

    status = ""
    group = ""
    raw_text = ""
    page_url = ""
    if run_id and result_path.is_file():
        try:
            outcome = parse_result(result_path)
            status, group = outcome.status, outcome.group
            base["runStatus"] = outcome.status
            base["runGroup"] = outcome.group
            base["error"] = outcome.error
            raw = step_output(outcome, "grabItems", "result")
            raw_text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
        except (OSError, ValueError, KeyError) as exc:
            base["reason"] = f"result.json / 步骤输出不可解析：{exc}"
        try:
            page_url = str(step_output(outcome, "waitResults", "url") or "")
        except (KeyError, TypeError, UnboundLocalError):
            page_url = ""
    else:
        base["reason"] = "没拿到 run_id 或 result.json 不存在"
    base["pageUrl"] = page_url

    written = artifacts / "xhs-products.json"
    base["writtenPath"] = str(written)
    written_text = written.read_text(encoding="utf-8") if written.is_file() else None
    if written_text is not None:
        base["writtenBytes"] = len(written_text.encode("utf-8"))
    else:
        base["reason"] = base.get("reason") or "data.writeText 产物不存在"

    evaluation = evaluate_run(
        cli_returncode=proc.returncode,
        status=status,
        group=group,
        raw_text=raw_text,
        written_text=written_text,
        page_url=page_url,
        keyword=args.keyword,
    )
    verdict = evaluation["checks"]
    items = evaluation["items"]
    if isinstance(items, list):
        base["itemsCount"] = len(items)
        base["sample"] = items[:2]
    base["checks"] = verdict
    passed = all(verdict.values())
    base["verdict"] = "PASSED" if passed else "FAILED"
    if not passed and "reason" not in base:
        base["reason"] = evaluation["reason"] or (
            "判据未全过：" + ", ".join(k for k, v in verdict.items() if not v)
        )
    _write_evidence(evidence_path, base)
    base["evidencePath"] = str(evidence_path)
    print(json.dumps(base, ensure_ascii=False, indent=2))
    return 0 if passed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="小红书工作台真机 E2E 验证")
    parser.add_argument(
        "--run",
        action="store_true",
        help="真的占机跑一次采集（默认只做不占机的静态预检）",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="只做静态预检（与不带参数等价；显式写出来便于脚本调用）",
    )
    parser.add_argument("--browser", choices=("msedge", "chrome"), default="chrome")
    parser.add_argument("--keyword", default="AI效率工具")
    parser.add_argument("--artifacts", default="run_artifacts/xhs-e2e")
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()
    if args.run and args.check:
        parser.error("--run 与 --check 互斥")
    if not args.run:
        checks = precheck(args.browser)
        report = {
            "mode": "check",
            "browser": args.browser,
            "ok": all(c["ok"] for c in checks),
            "checks": checks,
            "note": "静态预检不证明通道在线；只有 --run 能证明",
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1
    return run_live(args)


if __name__ == "__main__":
    raise SystemExit(main())
