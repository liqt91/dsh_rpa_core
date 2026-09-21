import asyncio
import base64
import json
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from rpa_core.extension_exec import ChannelMetrics, ExtensionChannelError
from rpa_core.extension_launch import (
    BrowserLaunchError,
    find_extension_dir,
    launch_browser,
)
from rpa_core.model.command import (
    CommandInvocation,
    CommandResult,
    EffectKind,
    EffectRecord,
)
from rpa_core.model.errors import ErrorCode

from .base import CommandExecutor, resolve_session_id
from .browser_ext import ExtensionExecSession

# 滚动 JS：window 全页滚动 / 元素内部滚动（position: top|bottom|point|page）
_SCROLL_WINDOW_JS = """([position, behavior, x, y]) => {
    if (position === 'top') { window.scrollTo({top: 0, behavior}); }
    else if (position === 'bottom') {
        window.scrollTo({top: document.documentElement.scrollHeight, behavior});
    }
    else if (position === 'point') { window.scrollTo({top: y, left: x, behavior}); }
    else { window.scrollBy({top: window.innerHeight, behavior}); }
    return window.scrollY;
}"""

_SCROLL_ELEMENT_JS = """(el, [position, behavior, x, y]) => {
    if (position === 'top') { el.scrollTo({top: 0, behavior}); }
    else if (position === 'bottom') { el.scrollTo({top: el.scrollHeight, behavior}); }
    else if (position === 'point') { el.scrollTo({top: y, left: x, behavior}); }
    else { el.scrollBy({top: el.clientHeight, behavior}); }
    return el.scrollTop;
}"""

# 不依赖已有会话的命令（与 desktop 的 attachWindow/getWindowList 同口径）：
# attach 自己新建会话（绑定已有标签页），listPages 只列全部标签页——
# 二者在「还没有任何会话」时也必须可用，否则只能先开网页才能列举/附着。
#
# closeBrowser（M32 S1）也在列：它是**进程级**操作（按进程名终止浏览器），
# 与扩展通道和会话都无关——恰恰在扩展已经掉线（浏览器卡死、就是它该被杀的场景）
# 时最需要它可用，因此绝不能先被会话门拦下。
_NO_SESSION_COMMANDS = ("browser.attach", "browser.listPages", "browser.closeBrowser")

# 扩展通道：命令 → page.call 原语（DOM 操作走扩展注入函数，规避页面 CSP 对 eval 的限制）
_EXT_PAGE_METHODS = {
    "browser.click": "click",
    "browser.hover": "hover",
    "browser.input": "input",
    "browser.scroll": "scroll",
    "browser.select": "select",
    "browser.check": "check",
}


# 已带 scheme（http(s)/file/data/about 等）则原样保留；否则默认补 https（对用户友好）
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")

# 首次创建标签页时的等待下限：待覆盖插件 MV3 service worker 的唤醒周期（约 30s），
# 否则刚进入休眠的插件会在等待窗口内无人领取，被误判「离线」而去拉起新浏览器。
_EXT_SW_WAKE_SECONDS = 45.0


_SESSION_RESOURCE_PREFIX = "browser.session:"


# 浏览器进程名（按操作系统）。终止浏览器（browser.closeBrowser）用它做进程匹配。
# chromium 系在同一 exe 下会派生多个 helper 进程（renderer/gpu/utility），
# 按名字匹配会**一起**命中——这是想要的：只杀主进程会留下一堆孤儿 renderer，
# 它们仍占着用户数据目录，重启时浏览器会报「配置目录已被使用」。
_BROWSER_PROCESS_NAMES: dict[str, tuple[str, ...]] = {
    "msedge": ("msedge.exe", "msedge"),
    "chrome": ("chrome.exe", "chrome"),
}

# 控制台命令输出的解码方式：Windows 上 `tasklist` 走的是控制台代码页，
# 中文系统是 GBK/CP936——不显式给编码，subprocess 会按 UTF-8 解并抛错。
_CONSOLE_ENCODING = "mbcs" if sys.platform == "win32" else "utf-8"


def _list_browser_processes(names: tuple[str, ...]) -> list[dict[str, Any]]:
    """列出进程名命中 `names` 的进程（`[{"pid": int, "name": str}]`）。

    跨平台：Windows 用 `tasklist`，POSIX 用 `ps`。都不引入第三方依赖——`psutil`
    不在依赖里，为了一个「关闭浏览器」去加一个二进制依赖不划算。

    M35 起不再带 `startedAt`（启动时刻）：它唯一的消费者是 closeBrowser 的
    `launchedByUs` 水位筛选，该语义已删（维护者定案：直接杀指定浏览器的全部
    进程）。Windows 的 tasklist 本来就拿不到启动时间（恒记 0），POSIX 的
    etimes 换算随之删除。
    """
    if sys.platform == "win32":
        # 编码必须显式给：`tasklist` 在中文 Windows 上输出 GBK，按 UTF-8 解会抛
        # UnicodeDecodeError（且异常发生在 subprocess 的读线程里，表现为 stdout 为 None）。
        completed = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=20, check=False,
            encoding=_CONSOLE_ENCODING, errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        rows: list[dict[str, Any]] = []
        wanted = {name.lower() for name in names}
        for line in completed.stdout.splitlines():
            cells = [cell.strip().strip('"') for cell in line.split('","')]
            if len(cells) < 2:
                continue
            image = cells[0].strip('"').lower()
            if image not in wanted:
                continue
            try:
                pid = int(cells[1])
            except ValueError:
                continue
            rows.append({"pid": pid, "name": image})
        return rows
    completed = subprocess.run(
        ["ps", "-eo", "pid=,comm="],
        capture_output=True, text=True, timeout=20, check=False,
    )
    rows = []
    wanted = {name.lower() for name in names}
    for line in completed.stdout.splitlines():
        parts = line.split(None, 1)
        if len(parts) < 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        command = parts[1].strip()
        base = command.rsplit("/", 1)[-1].lower()
        if base not in wanted and command.lower() not in wanted:
            continue
        rows.append({"pid": pid, "name": base})
    return rows


def _terminate_process(pid: int, force: bool) -> None:
    """终止一个进程：force=True 立即强杀；否则先请它正常退出。"""
    if sys.platform == "win32":
        command = ["taskkill", "/PID", str(pid), "/T"]
        if force:
            command.append("/F")
        subprocess.run(
            command, capture_output=True, timeout=30, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return
    signal_number = signal.SIGKILL if force else signal.SIGTERM
    os.kill(pid, signal_number)


def _wait_processes_exit(pids: list[int], timeout_s: float) -> bool:
    """轮询等待 `pids` 全部退出；超时返回 False。

    存活探测**不能**用 `os.kill(pid, 0)`：Windows 上它会调 `TerminateProcess`，
    传信号 0 直接抛 `OSError [WinError 87] 参数错误`（POSIX 才有「信号 0 = 只探测」
    的语义）。Windows 侧改用 `OpenProcess` 拿句柄——拿不到句柄即进程已不存在。
    """
    deadline = time.monotonic() + max(timeout_s, 0.0)
    pending = {int(pid) for pid in pids if int(pid) > 0}
    while pending:
        pending = {pid for pid in pending if _process_alive(pid)}
        if not pending:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.15)
    return True


def _process_alive(pid: int) -> bool:
    """进程是否仍存在（跨平台存活探测，不发送任何信号）。"""
    if sys.platform == "win32":
        import ctypes

        # PROCESS_QUERY_LIMITED_INFORMATION(0x1000)：权限要求最低的查询权限，
        # 足以判断「还在不在」，且对受保护进程也能拿到句柄。
        process_query_limited_information = 0x1000
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(process_query_limited_information, False, int(pid))
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # 存在但不属于我们 → 仍在
        return True
    return True


def session_bindings_from_scopes(
    scopes: Any,
) -> tuple[dict[str, str], dict[str, str], str | None]:
    """从 checkpoint 快照还原浏览器会话绑定（M21 跨进程续接）。

    返回 `(sessionId → tabId, sessionId → 浏览器实例, 最后活跃的 sessionId)`。

    快照里两处信息互补（写入点见 `_open_extension` 与 `browser.attach`）：
    - `effects`：`resource = "browser.session:<sid>"`；`details.tabId` 非空即绑定；
      `details.operation = "detach"` 表示会话已解绑（`browser.close`），不再还原；
    - `outputs.browserInstance`：会话所属浏览器实例，多实例并存时用于路由。
    """
    if not isinstance(scopes, dict):
        return {}, {}, None
    steps = scopes.get("steps")
    if not isinstance(steps, dict):
        return {}, {}, None

    tabs: dict[str, str] = {}
    hosts: dict[str, str] = {}
    last_sid: str | None = None
    for step in steps.values():
        if not isinstance(step, dict):
            continue
        outputs = step.get("outputs")
        if isinstance(outputs, dict):
            sid = outputs.get("sessionId")
            if isinstance(sid, str) and sid:
                last_sid = sid
                instance = outputs.get("browserInstance")
                if isinstance(instance, str) and instance:
                    hosts[sid] = instance
        for effect in step.get("effects") or []:
            if not isinstance(effect, dict):
                continue
            resource = effect.get("resource")
            if not isinstance(resource, str) or not resource.startswith(_SESSION_RESOURCE_PREFIX):
                continue
            sid = resource[len(_SESSION_RESOURCE_PREFIX):]
            if not sid:
                continue
            details = effect.get("details")
            details = details if isinstance(details, dict) else {}
            if str(details.get("operation") or "") == "detach":
                tabs.pop(sid, None)
                hosts.pop(sid, None)
                continue
            tab_id = details.get("tabId")
            if isinstance(tab_id, str) and tab_id:
                tabs[sid] = tab_id
    if last_sid is not None and last_sid not in tabs:
        last_sid = None
    return tabs, hosts, last_sid


def _ensure_scheme(url: str) -> str:
    stripped = url.strip()
    if not stripped:
        return url
    if _SCHEME_RE.match(stripped):
        return stripped
    return f"https://{stripped}"


def _fallback_details(fallback: dict[str, Any] | None) -> dict[str, Any]:
    """元素自愈命中时，把「哪条候选救回来的」并进执行证据（便于事后归因）。"""
    return {"fallback": fallback} if fallback else {}


# 执行前预检（M28 S2）的稳定错误码：扩展侧 domOp 在返回值里带 `precheck`
_PRECHECK_CODES = (
    ErrorCode.ELEMENT_COVERED,
    ErrorCode.ELEMENT_DISABLED,
    ErrorCode.ELEMENT_NOT_VISIBLE,
)


def _describe_blocker(blocked_by: Any) -> str:
    """把扩展回的遮挡者描述拼成可读标签（tag#id.class，便于在 DevTools 里比对）。"""
    if not isinstance(blocked_by, dict):
        return str(blocked_by or "")
    return "".join(
        [
            str(blocked_by.get("tag") or ""),
            f"#{blocked_by['id']}" if blocked_by.get("id") else "",
            "." + ".".join(str(blocked_by.get("className") or "").split())
            if blocked_by.get("className")
            else "",
        ]
    )


def _precheck_result(
    payload: dict[str, Any], *, candidates_tried: int = 0
) -> CommandResult | None:
    """把扩展侧的执行前预检失败翻成 `CommandResult`；通过（或无 precheck）返回 None。

    预检失败的 `blockedBy`（谁挡住了）必须原样进入 details——「报错可定位」是这一
    切片的验收标准之一，只给一句「被遮挡」等于没报。
    """
    precheck = payload.get("precheck")
    if not isinstance(precheck, dict):
        return None
    code = str(precheck.get("code") or "")
    try:
        error_code = ErrorCode(code)
    except ValueError:
        return None
    details: dict[str, Any] = {"channel": "extension", "precheck": True}
    if payload.get("matchedCount") is not None:
        details["matchedCount"] = payload.get("matchedCount")
    details.update({k: v for k, v in precheck.items() if k not in ("code", "message")})
    if details.get("blockedBy") and not details.get("blockedByLabel"):
        details["blockedByLabel"] = _describe_blocker(details["blockedBy"])
    if candidates_tried:
        details["candidatesTried"] = candidates_tried
    return CommandResult.failure(
        error_code,
        str(precheck.get("message") or code),
        details=details,
    )


def _session_metrics(session: Any) -> ChannelMetrics | None:
    """取扩展会话上的通道度量器；测试桩没有该属性时返回 None（度量静默跳过）。"""
    metrics = getattr(session, "metrics", None)
    return metrics if isinstance(metrics, ChannelMetrics) else None


class PlaywrightExecutor(CommandExecutor):
    def __init__(self, ext_session=None, flow_dir: Path | None = None):
        # 自研扩展单通道：会话 = 用户真实浏览器里的一个标签页句柄
        self._ext = ext_session or ExtensionExecSession()
        self._ext_sessions: dict[str, str] = {}  # sessionId -> tabId
        # sessionId -> 宿主浏览器（创建会话时绑定），后续元素操作据此路由到正确浏览器实例
        self._ext_session_hosts: dict[str, str] = {}
        # 最近激活的会话：sessionId 可省略时按「最近激活 > 唯一会话」回退（同 desktop）
        self._last_session_id: str | None = None
        # 流程目录：元素自愈（M28 S1）据此反查本流程的元素资产（<flowDir>/elements/*.json）
        self._flow_dir = Path(flow_dir) if flow_dir else None
        self._element_assets_cache: tuple[float, dict[str, list[dict[str, Any]]]] | None = None

    # ---- 元素自愈：运行期按失败的 selector 反查元素资产候选（M28 S1） ----------
    @staticmethod
    def _normalize_selector(selector: str) -> str:
        """比较用归一化：折叠空白（资产里的 selector 与节点参数可能只差空格）。"""
        return " ".join(str(selector or "").split())

    def _element_candidates(self, selector: str) -> list[dict[str, Any]]:
        """按 selector 反查元素资产的备选候选（稳定性顺序）。

        契约（M28 S1 定案）：**不改命令参数、不写工作流文件**——候选仍以元素资产
        （`<flowDir>/elements/*.json`，M10 捕获时落盘）为单一事实来源，运行期用失败的
        selector 去反查取用；资产缺失/不匹配时行为与今天完全一致（不做任何猜测）。
        """
        if self._flow_dir is None:
            return []
        elements_dir = self._flow_dir / "elements"
        try:
            files = sorted(elements_dir.glob("*.json"))
        except OSError:
            return []
        if not files:
            return []
        try:
            stamp = max(path.stat().st_mtime for path in files)
        except OSError:
            return []
        cached = self._element_assets_cache
        if cached is None or cached[0] != stamp:
            index: dict[str, list[dict[str, Any]]] = {}
            for path in files:
                try:
                    document = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if not isinstance(document, dict):
                    continue
                raw_selector = document.get("selector")
                if not isinstance(raw_selector, dict):
                    continue
                css = raw_selector.get("css")
                candidates = raw_selector.get("candidates")
                if not isinstance(css, str) or not css:
                    continue
                if not isinstance(candidates, list):
                    continue
                usable = [
                    candidate
                    for candidate in candidates
                    if isinstance(candidate, dict)
                    and isinstance(candidate.get("selector"), str)
                    and candidate.get("selector")
                ]
                if usable:
                    index[self._normalize_selector(css)] = usable
            self._element_assets_cache = (stamp, index)
        return self._element_assets_cache[1].get(self._normalize_selector(selector), [])

    async def _page_call_with_fallback(
        self,
        tab_id: str,
        selector: str,
        method: str,
        *,
        args: dict[str, Any],
        timeout_s: float,
        target_host: str,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """先按 selector 定位；未命中时按元素资产候选依次重试（元素自愈）。

        返回 `(payload, fallback)`；`fallback` 非空表示靠候选救回，含所用候选与顺位
        （写进执行证据，便于事后判断「这次是哪条候选生效」）。

        两处「不重试」的判定（M28 S2）：
        - 主选择器**命中但预检不过**（被遮挡/隐藏/禁用）→ 直接返回该 payload：元素找到了，
          按候选重试只会把「点错地方」换成「点到另一个元素」，比失败更危险。
        - 候选**命中但预检不过** → 继续试下一条候选（候选本就是为了绕开改版失效的定位）。
        候选全部未命中时返回最后一次 payload（调用方照旧报 ELEMENT_NOT_FOUND，并带上
        尝试过的候选数）。
        """
        payload = await asyncio.to_thread(
            self._ext.page_call, tab_id, selector, method,
            args=args, timeout_seconds=timeout_s, target_host=target_host,
        )
        if int(payload.get("matchedCount") or 0) > 0:
            return payload, None
        candidates = self._element_candidates(selector)
        last_precheck = payload
        for index, candidate in enumerate(candidates, start=1):
            candidate_selector = str(candidate["selector"])
            try:
                retry = await asyncio.to_thread(
                    self._ext.page_call, tab_id, candidate_selector, method,
                    args=args, timeout_seconds=timeout_s, target_host=target_host,
                )
            except ExtensionChannelError as exc:
                # 候选命中了但预检失败：换下一条候选（「不唯一」时换个更稳的定位）
                if exc.code in _PRECHECK_CODES:
                    last_precheck = {
                        "matchedCount": 1,
                        "precheck": {"code": exc.code, "message": str(exc), **exc.details},
                    }
                    continue
                raise
            if int(retry.get("matchedCount") or 0) > 0:
                if isinstance(retry.get("precheck"), dict):
                    last_precheck = retry
                    continue
                return retry, {
                    "mainSelector": selector,
                    "usedSelector": candidate_selector,
                    "kind": candidate.get("kind"),
                    "index": index,
                    "candidateCount": len(candidates),
                }
        return last_precheck, None

    async def execute(
        self, invocation: CommandInvocation, cancellation: asyncio.Event
    ) -> CommandResult:
        """命令边界（M28 S4）：把本步的扩展通道往返数与耗时并进执行证据。

        度量在**这一步**内累计（进入即 `reset()`），随 `diagnostics` 落进
        `scopes.steps.<node>.diagnostics` 与 checkpoint，形成「每步往返数 + 耗时」
        基线；将来任何改动让某条命令的往返数悄悄上升（例如无意中多打一次探测、
        自愈候选退化成逐条重试），都能从运行证据里直接看出来，不必靠肉眼比对代码。

        只统计**真走过通道**的命令：一步里往返数为 0（纯参数校验失败、关闭会话等
        不需要扩展的路径）就不写这段，免得证据里全是空壳。
        """
        metrics = _session_metrics(self._ext)
        if metrics is not None:
            metrics.reset()
        # perf_counter（非 monotonic）：Windows 上 monotonic 的粒度约 15.6ms，比一步
        # 命令还粗——用它计时会把常见命令记成 0ms，度量直接失去意义。
        started = time.perf_counter()
        result = await self._execute_command(invocation, cancellation)
        if metrics is None or not metrics.round_trips:
            return result
        step_ms = (time.perf_counter() - started) * 1000.0
        # 合并而不是覆盖：navigate 自己已经写了 durationMs
        return result.model_copy(
            update={
                "diagnostics": {
                    **result.diagnostics,
                    "extension": metrics.as_diagnostics(step_ms=step_ms),
                }
            }
        )

    async def _execute_command(
        self, invocation: CommandInvocation, cancellation: asyncio.Event
    ) -> CommandResult:
        if cancellation.is_set():
            return CommandResult(status="cancelled")
        # 与 execute() 的度量同源：perf_counter 才有 Windows 上的亚毫秒分辨率
        started = time.perf_counter()
        command = invocation.command_id
        inputs = invocation.inputs
        try:
            explicit = str(inputs.get("sessionId") or "")
            # attach/listPages 不依赖已有会话：直接进扩展通道（会话缺失时不得被会话门拦下）
            if command in _NO_SESSION_COMMANDS:
                return await self._execute_extension(
                    command, invocation, inputs, "", started, cancellation
                )
            # 打开网页（action=goto）且尚无会话 → 在用户真实浏览器里新建标签页会话
            if (
                command == "browser.navigate"
                and str(inputs.get("action") or "goto") == "goto"
                and (not explicit or explicit not in self._ext_sessions)
            ):
                if not inputs.get("url"):
                    return CommandResult.failure(
                        ErrorCode.INVALID_INPUT, "url is required when action=goto"
                    )
                return await self._open_extension(invocation, inputs, started)
            # 其余命令（含既有会话上的 navigate goto/back/forward/reload）走扩展会话；
            # sessionId 可省略：显式指定 > 最近激活 > 唯一会话（缺省会话解析，与 desktop 同口径）
            ext_session_id = resolve_session_id(
                explicit, self._ext_sessions, self._last_session_id
            )
            if ext_session_id and ext_session_id in self._ext_sessions:
                self._last_session_id = ext_session_id
                return await self._execute_extension(
                    command, invocation, inputs, ext_session_id, started, cancellation
                )
            # 无有效扩展会话：优先给「扩展离线」的可操作错误，其次报「缺少会话」
            offline = await self._extension_preflight(inputs)
            if offline is not None:
                return offline
            return CommandResult.failure(
                ErrorCode.EXECUTOR_FAILED,
                f"browser 命令 {command} 缺少有效会话：请先执行 browser.navigate 打开网页，"
                "并确保自研扩展在线。",
                details={
                    "requiredField": "sessionId",
                    "sessionId": explicit or None,
                },
            )
        except ExtensionChannelError as exc:
            return self._ext_channel_failure(exc)
    def _ext_offline_result(self) -> CommandResult:
        """扩展通道离线：给出可照着做的排查步骤（不白等 timeoutMs）。"""
        hint = (
            "请依次确认 ① 目标浏览器（Chrome/Edge）已打开；② 扩展已加载并启用"
            "（edge://extensions 或 chrome://extensions 里点「重新加载」）；"
            "③ devserver 正在运行且扩展指向的端口一致"
        )
        return CommandResult.failure(
            ErrorCode.EXECUTOR_FAILED,
            f"浏览器执行通道当前离线：没有检测到浏览器里的自研插件在轮询命令。{hint}。"
            "浏览器执行已统一收敛到自研扩展单通道，请确认扩展已安装并启用。",
            details={"reason": "channel_offline"},
        )

    async def _extension_preflight(self, inputs: dict[str, Any]) -> CommandResult | None:
        """扩展通道前置检查：扩展必须在线（离线立即失败，不白等整段命令超时）。

        为什么必须前置：扩展通道的命令是「入队等扩展来领」，扩展不在线时命令会在队列里
        躺满 timeoutMs（默认 30s）才以 TIMEOUT 返回。用户看到的现象只是「执行后没有打开
        浏览器」，完全无从判断是插件没装、浏览器没开。这里提前 <1s 给出可操作的失败原因。
        """
        try:
            status = await asyncio.to_thread(self._ext.status)
        except Exception:  # noqa: BLE001 - 探测失败一律按离线处理
            status = {"online": False}
        if status.get("online"):
            return None
        return self._ext_offline_result()

    async def _fresh_hosts(self) -> list[str]:
        """获取当前在线浏览器清单（非缓存，供自启后轮询）。"""
        try:
            status = await asyncio.to_thread(self._ext.client.status)
        except Exception:  # noqa: BLE001 - 探测失败一律按离线处理
            status = {}
        hosts = status.get("hosts")
        return [str(h) for h in hosts] if isinstance(hosts, list) else []

    async def _is_any_extension_active(self) -> bool:
        """近窗口内是否有任意浏览器插件在轮询（含 SW 休眠中的活跃宿主）。"""
        try:
            status = await asyncio.to_thread(self._ext.client.status)
        except Exception:  # noqa: BLE001 - 探测失败一律视为无活跃宿主
            return False
        if status.get("online"):
            return True
        hosts = status.get("hosts")
        return isinstance(hosts, list) and bool(hosts)

    async def _create(
        self, url: str, timeout_s: float, target_host: str | None
    ) -> dict[str, Any] | CommandResult:
        """发一次 tabs.create；通道错误收敛为可读 CommandResult（含失败）。"""
        try:
            return await asyncio.to_thread(
                self._ext.tabs_create, url,
                timeout_seconds=timeout_s, target_host=target_host,
            )
        except ExtensionChannelError as exc:
            return self._ext_channel_failure(exc)

    async def _launch_then_create(
        self, target_host: str, inputs: dict[str, Any], url: str, timeout_s: float
    ) -> dict[str, Any] | CommandResult:
        """裸拉起目标浏览器 → 等待插件上线 → tabs.create 创建标签页。

        设计（维护者定案）：拉起**不带 URL**，目标页一律由 tabs.create 创建——
        创建即返回 tabId，程序创建的标签页地址栏不聚焦，也不需要任何「探测哪个
        标签页是我们的」逻辑。冷启动时浏览器自己打开的默认启动页（空白 NTP 或
        恢复的上次会话）**原样保留、不导航也不关闭**（与影刀一致：那是「指令
        以外的操作」，发生在用户眼前观感差；且绝不动用户已有页面）。
        """
        launched = await self._launch_target(target_host, inputs)
        if isinstance(launched, CommandResult):
            return launched
        return await self._create(url, timeout_s, target_host)

    async def _open_with_launch(
        self, target_host: str | None, inputs: dict[str, Any], url: str, timeout_s: float
    ) -> dict[str, Any] | CommandResult:
        """命令级驱动地创建标签页：直接把 tabs.create 入队由目标插件领取。

        不用 hosts「在线窗口」预判放弃（插件 MV3 SW 休眠约 30s，窗口判在线/离线会抖动）。
        分两条路：
        - 完全无插件在跑（online False）→ 无对象可唤醒，直接离线失败或拉起浏览器（不空等）。
        - 有活跃宿主（其中可能有闲置到休眠的目标插件）→ 命令入队等其领取，等待覆盖 SW
          唤醒周期；只有确实无人领取（TIMEOUT）且指定了 browserType 才拉起后重试一次。
        """
        if not await self._is_any_extension_active():
            if not target_host:
                return self._ext_offline_result()
            return await self._launch_then_create(target_host, inputs, url, timeout_s)
        first_wait = max(timeout_s, _EXT_SW_WAKE_SECONDS)
        try:
            return await asyncio.to_thread(
                self._ext.tabs_create, url,
                timeout_seconds=first_wait, target_host=target_host,
            )
        except ExtensionChannelError as exc:
            # 仅「无人领取（超时）或目标不在线」才值得尝试拉起；其它错误原样上抛
            if exc.code not in ("TIMEOUT", "TARGET_HOST_OFFLINE"):
                return self._ext_channel_failure(exc)
        if not target_host:
            return self._ext_offline_result()
        return await self._launch_then_create(target_host, inputs, url, timeout_s)

    async def _launch_target(
        self, target_host: str, inputs: dict[str, Any]
    ) -> CommandResult | None:
        """裸拉起目标浏览器（不带 URL）并等待其插件上线；成功返回 None，否则返回可操作报错。

        不带 URL 的原因：目标页统一由拉起后的 tabs.create 创建（创建即返回 tabId、
        地址栏不聚焦）；浏览器冷启动自己打开的启动页原样保留（与影刀一致），
        不做任何「指令以外的操作」。
        """
        browser = target_host
        command_line_args = inputs.get("commandLineArgs")
        if not isinstance(command_line_args, list):
            command_line_args = []
        argv_extra = [str(a) for a in command_line_args if isinstance(a, str)]
        # 是否显式走了独立用户数据目录（临时注入插件），决定拉起失败后如何引导
        isolated_dir = any(a.startswith("--user-data-dir") for a in argv_extra)
        extension_dir = find_extension_dir()
        try:
            await asyncio.to_thread(
                launch_browser, browser, None,
                extension_dir=extension_dir, argv_extra=argv_extra,
            )
        except BrowserLaunchError as exc:
            return CommandResult.failure(
                ErrorCode.EXECUTOR_FAILED,
                f"目标浏览器 {browser} 离线且自动拉起失败：{exc}",
                details={"reason": "browser_launch_failed", "browser": browser},
            )
        # 拉起后轮询目标浏览器上线。窗口必须覆盖 MV3 最慢唤醒路径：浏览器
        # 已在运行时，扩展 service worker 处于休眠，靠 chrome.alarms 兜底
        # 重拉（平台最小间隔 30s）——15s 的窗口会系统性错过这种场景。
        if await self._wait_target_online(browser, _EXT_SW_WAKE_SECONDS):
            return None
        if isolated_dir:
            msg = (
                f"已用独立目录临时拉起 {browser}，但在限时内仍未检测到其自研插件上线。"
                "请确认 ①--user-data-dir=<目录> 可写；②自研扩展已被注入（--load-extension 生效）；"
                "③扩展通道宿主端口与插件一致（默认 127.0.0.1:8765）。"
            )
        else:
            msg = (
                f"已尝试自动拉起 {browser}（默认配置），但在限时内未检测到其自研插件在线。"
                "若该浏览器已装自研插件，多为扩展后台休眠唤醒慢——直接重试一次通常即可；"
                "否则需在目标浏览器默认配置里预装并启用自研插件："
                f"打开 edge://extensions 或 chrome://extensions，开启「开发者模式」加载扩展目录；"
                "或在本指令「命令行参数」里传 --user-data-dir=<独立目录> 以独立目录临时加载插件。"
            )
        return CommandResult.failure(
            ErrorCode.EXECUTOR_FAILED,
            msg,
            details={
                "reason": "browser_launch_no_host",
                "browser": browser,
                "isolatedDir": isolated_dir,
            },
        )

    async def _wait_target_online(self, target: str, timeout_s: float) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if target in await self._fresh_hosts():
                return True
            await asyncio.sleep(0.4)
        return False

    async def _open_extension(
        self, invocation: CommandInvocation, inputs: dict[str, Any], started: float
    ) -> CommandResult:
        """打开网页（扩展通道）= 在目标浏览器里新建标签页；目标浏览器离线时先自动拉起。"""
        url = _ensure_scheme(str(inputs["url"]))
        timeout_s = int(inputs.get("timeoutMs", 30_000)) / 1000.0
        target_host = str(inputs.get("browserType") or "").strip().lower() or None
        # 命令级驱动：把 tabs.create 入队由目标插件领取（含等待 SW 唤醒），
        # 命令确实无人领取且指定了 browserType 时才自动拉起新浏览器。
        opened = await self._open_with_launch(target_host, inputs, url, timeout_s)
        if isinstance(opened, CommandResult):
            return opened
        on_timeout = str(inputs.get("onTimeout") or "error").strip().lower()
        # 页面未在 timeoutMs 内加载完成时，按影刀「加载超时后执行」策略处理：
        # stop=停止网页加载并继续；error（默认）=视为加载超时失败，走引擎错误处理。
        if bool(opened.get("timedOut")):
            if on_timeout == "stop":
                await asyncio.to_thread(
                    self._ext.stop_loading, str(opened.get("tabId") or ""),
                    target_host=target_host,
                )
            else:
                return CommandResult.failure(
                    ErrorCode.TIMEOUT,
                    f"页面加载超时（timeoutMs={int(inputs.get('timeoutMs', 30_000))}ms）："
                    "网页未在限时内加载完成。可设「加载超时后」为停止网页加载，或调大 timeoutMs。",
                    details={"reason": "navigate_load_timeout", "url": url},
                )
        session_id = str(uuid.uuid4())
        tab_id = str(opened.get("tabId") or "")
        self._ext_sessions[session_id] = tab_id
        self._last_session_id = session_id  # 新建即激活：后续省略 sessionId 的命令默认作用于此
        # 绑定会话所属实例：优先取「实际创建该标签页的浏览器实例」的 instanceId（由 Hub 补带），
        # 拿不到才回退浏览器名（browserType / 自动）；后续元素操作按实例精确路由。
        instance_id = str(opened.get("instanceId") or "") or ""
        self._ext_session_hosts[session_id] = instance_id or (target_host or "")
        browser_type = str(inputs.get("browserType") or "").strip().lower() or "msedge"
        final_url = str(opened.get("url") or url)
        return CommandResult.success(
            outputs={
                "sessionId": session_id,
                "url": final_url,
                "resourceType": "webPage",
                "browserInstance": instance_id or None,
                "browserType": browser_type,
                "tabId": tab_id,
            },
            effects=[
                EffectRecord.committed(
                    invocation,
                    kind=EffectKind.SESSION,
                    resource=f"browser.session:{session_id}",
                    details={
                        "operation": "navigate",
                        "transport": "extension",
                        "tabId": tab_id,
                        "url": final_url,
                    },
                )
            ],
            diagnostics={"durationMs": int((time.perf_counter() - started) * 1000)},
        )

    async def _execute_extension(
        self,
        command: str,
        invocation: CommandInvocation,
        inputs: dict[str, Any],
        session_id: str,
        started: float,
        cancellation: asyncio.Event,
    ) -> CommandResult:
        """扩展会话内的命令执行（Phase 1 命令集）。"""
        tab_id = self._ext_sessions.get(session_id, "")
        selector = str(inputs.get("selector") or "")
        timeout_s = int(inputs.get("timeoutMs", 30_000)) / 1000.0
        # 无会话命令（attach/listPages）不带会话：resource 退化到浏览器级标签页
        resource = f"browser.session:{session_id}" if session_id else "browser.tabs"
        # 会话所属浏览器：后续每个 tab/浏览器级操作都路由到它，多浏览器并存时不串台
        host = self._ext_session_hosts.get(session_id, "")
        try:
            if command == "browser.close":
                # 扩展会话的「关闭」= 解绑：用户浏览器里的标签页留给用户，不代关
                # M29 S3：manifest 不再声明 `forceKill`/`ignoreUnload`——我们不拥有用户的浏览器
                # 进程（也没有它的句柄），`chrome.tabs.remove` 本身也不弹 beforeunload 对话框，
                # 两个参数在单通道下没有对应物。缺口（关标签页/终止进程）见 BACKLOG，属独立命令。
                self._ext_sessions.pop(session_id, None)
                self._ext_session_hosts.pop(session_id, None)
                if self._last_session_id == session_id:
                    self._last_session_id = None
                return self._ext_success(
                    invocation, EffectKind.SESSION, resource, {"operation": "detach"},
                    outputs={},
                )
            if command == "browser.closeTabs":
                # 关标签页（M32 S1）：tabIds 显式列表 / all=当前窗口全部，二者互斥。
                # 为什么是独立命令而不是 browser.close 的参数：`close` 是**会话生命周期**
                # 命令（解绑，资源语义是 session），关标签是**对用户浏览器的破坏性操作**
                # （会动到用户自己开的页面）——两者的风险等级与 effect 种类都不同，
                # 混在一个命令里会让「关闭会话」这种无害操作带上关页面的杀伤力。
                tab_ids = inputs.get("tabIds")
                close_all = bool(inputs.get("all"))
                # M37：None = 未指定，不进 args——缺省语义由扩展兜底（!== false 视为
                # true），与 all/windowId 同款「显式给才转发」风格，避免双份默认值漂移。
                ignore_before_unload = inputs.get("ignoreBeforeUnload")
                if tab_ids is not None and not isinstance(tab_ids, list):
                    return CommandResult.failure(
                        ErrorCode.INVALID_INPUT,
                        "tabIds 必须是整数数组（取 browser.listPages 返回的 index 对应 tabId）。",
                        details={"field": "tabIds", "receivedType": type(tab_ids).__name__},
                    )
                if bool(tab_ids) == close_all:
                    return CommandResult.failure(
                        ErrorCode.INVALID_INPUT,
                        "tabIds 与 all 必须二选一：要么给 tabIds 列表指定要关的标签页，"
                        "要么 all=true 关闭当前窗口所有标签页（含用户自己打开的）。",
                        details={
                            "field": "tabIds/all",
                            "tabIdsProvided": bool(tab_ids),
                            "all": close_all,
                        },
                    )
                payload = await asyncio.to_thread(
                    self._ext.tabs_close_many,
                    tab_ids=[int(tab_id) for tab_id in tab_ids] if tab_ids else None,
                    close_all=close_all,
                    ignore_before_unload=ignore_before_unload,
                    timeout_seconds=timeout_s,
                    target_host=host,
                )
                closed = [int(t) for t in (payload.get("closedTabIds") or [])]
                failed = [int(t) for t in (payload.get("failedTabIds") or [])]
                # 关掉的就是当前会话所属标签页 → 会话随之失效，主动解绑免得后续步骤
                # 拿着一个死 tabId 去操作（报 TIMEOUT 而不是「页面已关闭」）。
                if tab_id and int(tab_id) in closed:
                    self._ext_sessions.pop(session_id, None)
                    self._ext_session_hosts.pop(session_id, None)
                    if self._last_session_id == session_id:
                        self._last_session_id = None
                return self._ext_success(
                    invocation, EffectKind.UNSAFE_WRITE,
                    f"browser.session:{session_id}" if session_id else "browser.tabs",
                    {
                        "operation": "closeTabs",
                        "transport": "extension",
                        "scope": "all" if close_all else "tabIds",
                        "ignoreBeforeUnload": (
                            True if ignore_before_unload is None else bool(ignore_before_unload)
                        ),
                        "closedCount": len(closed),
                        "failedCount": len(failed),
                    },
                    outputs={
                        "closedCount": len(closed),
                        "closedTabIds": closed,
                        "failedTabIds": failed,
                    },
                )
            if command == "browser.closeBrowser":
                return await self._close_browser(
                    invocation, inputs, timeout_s, host
                )
            # 关闭（解绑）不依赖扩展在线；其余命令前先确认扩展还在轮询，
            # 否则会白等 timeoutMs（默认 30s）才报 TIMEOUT。
            offline = await self._extension_preflight(inputs)
            if offline is not None:
                return offline
            if command == "browser.navigate":
                action = str(inputs.get("action") or "goto")
                if action == "goto":
                    result = await asyncio.to_thread(
                        self._ext.tabs_navigate, tab_id, str(inputs["url"]),
                        timeout_seconds=timeout_s, target_host=host,
                    )
                else:
                    result = await asyncio.to_thread(
                        self._ext.tabs_history, tab_id, action,
                        timeout_seconds=timeout_s, target_host=host,
                    )
                final_url = str(result.get("url") or "")
                return self._ext_success(
                    invocation, EffectKind.SESSION, resource,
                    {"operation": action, "transport": "extension", "url": final_url},
                    outputs={
                        "sessionId": session_id, "url": final_url, "resourceType": "webPage"
                    },
                )
            if command == "browser.listPages":
                tabs = await asyncio.to_thread(self._ext.tabs_list, timeout_seconds=timeout_s)
                pages = [
                    {
                        "index": index,
                        "url": str(tab.get("url") or ""),
                        "title": str(tab.get("title") or ""),
                    }
                    for index, tab in enumerate(tabs)
                ]
                return self._ext_success(
                    invocation, EffectKind.READ, resource,
                    {"operation": "listPages", "count": len(pages)},
                    outputs={"pages": pages, "count": len(pages)},
                )
            if command == "browser.attach":
                matched = await asyncio.to_thread(self._match_ext_tab, inputs, timeout_s)
                if matched is None:
                    return self._ext_not_found(inputs)
                new_session = str(uuid.uuid4())
                new_tab = str(matched.get("id") or "")
                self._ext_sessions[new_session] = new_tab
                self._ext_session_hosts[new_session] = ""  # attach 页签所在浏览器未知，按任意路由
                self._last_session_id = new_session  # 附着即激活
                matched_url = str(matched.get("url") or "")
                return self._ext_success(
                    invocation, EffectKind.SESSION, f"browser.session:{new_session}",
                    {"operation": "attach", "tabId": new_tab, "url": matched_url},
                    outputs={
                        "sessionId": new_session,
                        "url": matched_url,
                        "resourceType": "webPage",
                    },
                )
            if command == "browser.executeScript":
                payload = await asyncio.to_thread(
                    self._ext.page_eval, tab_id, str(inputs["script"]),
                    args=list(inputs.get("args") or []),
                    timeout_seconds=timeout_s, target_host=host,
                )
                result_value = payload.get("result")
                return self._ext_success(
                    invocation, EffectKind.UNSAFE_WRITE, resource + ":script",
                    {"operation": "executeScript"},
                    outputs={"result": result_value, "sessionId": session_id},
                    value=result_value,
                )
            if command == "browser.getText":
                info_type = str(inputs.get("infoType") or "text")
                payload, fallback = await self._page_call_with_fallback(
                    tab_id, selector, "getText",
                    args={"infoType": info_type},
                    timeout_s=timeout_s, target_host=host,
                )
                precheck = _precheck_result(payload)
                if precheck is not None:
                    return precheck
                count = int(payload.get("matchedCount") or 0)
                if count == 0:
                    return self._ext_not_found(
                        inputs,
                        candidates_tried=len(self._element_candidates(selector)),
                    )
                value = payload.get("result")
                return self._ext_success(
                    invocation, EffectKind.READ, resource + f":selector:{selector}",
                    {
                        "operation": "getText",
                        "infoType": info_type,
                        "matchedCount": count,
                        **_fallback_details(fallback),
                    },
                    outputs={"value": "" if value is None else str(value)},
                    value=value,
                )
            if command == "browser.waitFor":
                return await self._ext_wait_for(
                    invocation, inputs, session_id, tab_id, selector,
                    timeout_s, host, cancellation,
                )
            return await self._ext_phase_d(
                command, invocation, inputs, session_id, tab_id, selector, timeout_s, host
            )
        except ExtensionChannelError as exc:
            return self._ext_channel_failure(exc)

    async def _ext_page_command(
        self,
        command: str,
        invocation: CommandInvocation,
        inputs: dict[str, Any],
        session_id: str,
        tab_id: str,
        selector: str,
        timeout_s: float,
        target_host: str,
    ) -> CommandResult:
        method = _EXT_PAGE_METHODS[command]
        resource = f"browser.session:{session_id}:selector:{selector}"
        payload, fallback = await self._page_call_with_fallback(
            tab_id, selector, method,
            args=self._ext_method_args(command, inputs),
            timeout_s=timeout_s, target_host=target_host,
        )
        count = int(payload.get("matchedCount") or 0)
        # 预检失败优先于「0 命中」判定：元素被遮挡/隐藏/禁用时扩展回的是带 precheck 的
        # payload（matchedCount 仍 > 0），必须按具体原因报错而不是静默继续。
        precheck = _precheck_result(
            payload, candidates_tried=len(self._element_candidates(selector))
        )
        if precheck is not None:
            return precheck
        if command != "browser.scroll" and count == 0:
            return self._ext_not_found(
                inputs,
                candidates_tried=len(self._element_candidates(selector)),
            )
        # 扩展显式拒绝（如 clipboard 粘贴注入未被接受）：如实失败，不退化成别的模式
        if payload.get("inputRejected"):
            return CommandResult.failure(
                ErrorCode.EXECUTOR_FAILED,
                str(payload.get("message") or "输入未被元素接受"),
                details={"reason": "input_mode_not_accepted"},
            )
        await self._post_delay(inputs)
        if command == "browser.scroll":
            scroll_y = payload.get("result")
            return self._ext_success(
                invocation, EffectKind.UNSAFE_WRITE, resource,
                {"operation": "scroll", "scrollY": scroll_y},
                outputs={"scrollY": float(scroll_y or 0)},
            )
        if command == "browser.check":
            return self._ext_success(
                invocation, EffectKind.UNSAFE_WRITE, resource,
                {"operation": "check", "matchedCount": count, **_fallback_details(fallback)},
                outputs={"checked": bool(payload.get("result")), "matchedCount": count},
            )
        # 指针/键盘类原语（click/input/select/hover）统一 unsafe-write：hover 会触发页面
        # mouseover 处理器，同样不可安全重放——必须与各自 manifest 的 effect.kind 严格一致
        return self._ext_success(
            invocation, EffectKind.UNSAFE_WRITE, resource,
            {
                "operation": command.rsplit(".", 1)[-1],
                "matchedCount": count,
                **_fallback_details(fallback),
            },
            outputs={"matchedCount": count, "sessionId": session_id},
        )

    async def _ext_phase_d(
        self,
        command: str,
        invocation: CommandInvocation,
        inputs: dict[str, Any],
        session_id: str,
        tab_id: str,
        selector: str,
        timeout_s: float,
        target_host: str,
    ) -> CommandResult:
        """阶段 D：扩展补齐命令路由（DOM 读取/写入原语 + cookie + 浏览器级原语）。

        原 13 条（_EXT_PAGE_METHODS）仍复用 _ext_page_command；未实现的 upload/download/
        handleDialog 落「该能力扩展暂未实现」的显式错误（保留 manifest 字段，不硬失败）。
        """
        if command in _EXT_PAGE_METHODS:
            return await self._ext_page_command(
                command, invocation, inputs, session_id, tab_id, selector,
                timeout_s, target_host,
            )
        resource = f"browser.session:{session_id}"
        # -- DOM 读取/写入原语（page.call） -----------------------------------
        page_methods = {
            "browser.queryAll": ("queryAll", EffectKind.READ),
            "browser.getPosition": ("getPosition", EffectKind.READ),
            "browser.getScrollPosition": ("getScrollPosition", EffectKind.READ),
            "browser.getSelectOptions": ("getSelectOptions", EffectKind.READ),
            "browser.setValue": ("setValue", EffectKind.UNSAFE_WRITE),
            "browser.setAttribute": ("setAttribute", EffectKind.UNSAFE_WRITE),
            "browser.drag": ("drag", EffectKind.UNSAFE_WRITE),
        }
        if command in page_methods:
            method, effect = page_methods[command]
            # M28 S2：drag 也走自愈 + 预检那条路（S1 遗留的「drag 定位另起 page.call」）。
            # drag 的源元素同样会「改版失效」和「被遮挡」，与 click/input 同理；
            # 目标元素（targetSelector）由扩展侧按参数直接取，不在这一层的自愈范围内。
            if command == "browser.drag":
                payload, fallback = await self._page_call_with_fallback(
                    tab_id, selector, method,
                    args=self._phase_d_method_args(command, inputs),
                    timeout_s=timeout_s, target_host=target_host,
                )
            else:
                fallback = None
                payload = await asyncio.to_thread(
                    self._ext.page_call, tab_id, selector, method,
                    args=self._phase_d_method_args(command, inputs),
                    timeout_seconds=timeout_s, target_host=target_host,
                )
            precheck = _precheck_result(
                payload, candidates_tried=len(self._element_candidates(selector))
            )
            if precheck is not None:
                return precheck
            count = int(payload.get("matchedCount") or 0)
            if count == 0:
                return self._ext_not_found(inputs)
            result = payload.get("result")
            op = command.rsplit(".", 1)[-1]
            if command == "browser.queryAll":
                items = [str(v) for v in result] if isinstance(result, list) else []
                return self._ext_success(
                    invocation, effect, resource + f":selector:{selector}",
                    {"operation": op, "matchedCount": count},
                    outputs={"items": items, "count": count},
                )
            if command == "browser.getPosition":
                box = dict(result) if isinstance(result, dict) else {}
                return self._ext_success(
                    invocation, effect, resource + f":selector:{selector}",
                    {"operation": op, "matchedCount": count},
                    outputs=box,
                )
            if command == "browser.getScrollPosition":
                pos = dict(result) if isinstance(result, dict) else {}
                return self._ext_success(
                    invocation, effect, resource + f":selector:{selector}",
                    {"operation": op, "matchedCount": count},
                    outputs={"scrollX": pos.get("scrollX", 0), "scrollY": pos.get("scrollY", 0)},
                )
            if command == "browser.getSelectOptions":
                data = dict(result) if isinstance(result, dict) else {}
                options = data.get("options") if isinstance(data.get("options"), list) else []
                return self._ext_success(
                    invocation, effect, resource + f":selector:{selector}",
                    {"operation": op, "matchedCount": count},
                    outputs={"options": options, "count": int(data.get("count") or len(options))},
                )
            # outputs 与 manifest 严格对齐：setValue/setAttribute 只声明 matchedCount；
            # drag 与 click/input/select 同属指针类原语，manifest 额外声明回带 sessionId。
            outputs: dict[str, Any] = {"matchedCount": count}
            if command == "browser.drag":
                outputs["sessionId"] = session_id
            return self._ext_success(
                invocation, effect, resource + f":selector:{selector}",
                {"operation": op, "matchedCount": count, **_fallback_details(fallback)},
                outputs=outputs,
            )
        # -- Cookie（chrome.cookies，作用域 url 由扩展按 tab 当前页推导） -------
        # M29 S2：这一段此前有两个「声明了不生效」——
        # (1) `cookieGetAll` 的 `name`/`domain`/`path` 过滤器一个都没转发（浏览器作用域全量返回）；
        # (2) 四个命令都没把会话绑定的 `tabId` 传给扩展，于是 `tabUrl(undefined)` → `""`，
        #     `chrome.cookies.get/set/remove` 拿着空 url 调 API（拿不到作用域 URL）。
        if command == "browser.cookieGetAll":
            # 逐个显式取值（而不是循环一个 tuple）：静态门禁 `check_param_consumption.py` 按
            # 字面量判定「声明的参数是否真被消费」，用循环变量会让它看不见——那就等于把漂移藏回去。
            filters: dict[str, str] = {}
            if inputs.get("name"):
                filters["name"] = str(inputs["name"])
            if inputs.get("domain"):
                filters["domain"] = str(inputs["domain"])
            if inputs.get("path"):
                filters["path"] = str(inputs["path"])
            cookies = await asyncio.to_thread(
                self._ext.cookies_get_all, filters=filters or None, tab_id=tab_id,
                timeout_seconds=timeout_s, target_host=target_host,
            )
            return self._ext_success(
                invocation, EffectKind.READ, resource,
                {"operation": "cookieGetAll", "count": len(cookies), "filters": sorted(filters)},
                outputs={"cookies": cookies, "count": len(cookies)},
            )
        if command == "browser.cookieGet":
            value = await asyncio.to_thread(
                self._ext.cookies_get, str(inputs["name"]), tab_id=tab_id,
                timeout_seconds=timeout_s, target_host=target_host,
            )
            return self._ext_success(
                invocation, EffectKind.READ, resource,
                {"operation": "cookieGet", "name": inputs.get("name")},
                outputs={"value": value if value is not None else ""},
            )
        if command == "browser.cookieSet":
            count = await asyncio.to_thread(
                self._ext.cookies_set, list(inputs.get("cookies") or []), tab_id=tab_id,
                timeout_seconds=timeout_s, target_host=target_host,
            )
            return self._ext_success(
                invocation, EffectKind.IDEMPOTENT_WRITE, resource,
                {"operation": "cookieSet", "count": count},
                outputs={"count": count},
            )
        if command == "browser.cookieRemove":
            await asyncio.to_thread(
                self._ext.cookies_remove, str(inputs.get("name") or ""), tab_id=tab_id,
                timeout_seconds=timeout_s, target_host=target_host,
            )
            return self._ext_success(
                invocation, EffectKind.UNSAFE_WRITE, resource,
                {"operation": "cookieRemove"},
                outputs={"sessionId": session_id},
            )
        # -- 浏览器级原语 ------------------------------------------------------
        if command == "browser.stopLoading":
            result = await asyncio.to_thread(
                self._ext.stop_loading, tab_id,
                timeout_seconds=timeout_s, target_host=target_host,
            )
            return self._ext_success(
                invocation, EffectKind.UNSAFE_WRITE, resource,
                {"operation": "stopLoading"},
                outputs={"url": str(result.get("url") or "")},
            )
        if command == "browser.waitLoad":
            result = await asyncio.to_thread(
                self._ext.wait_load, tab_id,
                timeout_ms=int(inputs.get("timeoutMs", 30_000)),
                timeout_seconds=timeout_s, target_host=target_host,
            )
            return self._ext_success(
                invocation, EffectKind.READ, resource,
                {"operation": "waitLoad"},
                outputs={"url": str(result.get("url") or "")},
            )
        if command == "browser.screenshot":
            # M29 S3：manifest 不再声明 `fullPage`/`selector`——`chrome.tabs.captureVisibleTab`
            # 只能截「窗口当前可见标签页的可见区」；元素裁剪与整页拼接都必须先在扩展里解码图像
            # （MV3 service worker 没有 `Image`/`FileReader`），得走 offscreen document 或 CDP。
            # 与其留两个「勾了就静默截错」的开关，不如删掉（缺口见 BACKLOG「整页/元素截图」）。
            save_path = str(inputs["savePath"])
            result = await asyncio.to_thread(
                self._ext.screenshot, tab_id,
                timeout_seconds=timeout_s, target_host=target_host,
            )
            data_url = str(result.get("dataUrl") or "")
            try:
                b64 = data_url.split(",", 1)[1] if "," in data_url else ""
                raw = base64.b64decode(b64) if b64 else b""
            except Exception:  # noqa: BLE001 - 扩展截图失败按落盘失败处理
                raw = b""
            if not raw:
                return CommandResult.failure(
                    ErrorCode.EXECUTOR_FAILED,
                    "screenshot: 扩展未返回有效截图数据",
                    details={"channel": "extension"},
                )
            file_path = await asyncio.to_thread(self._write_screenshot, save_path, raw)
            # effect 必须与 manifest 声明严格一致（orchestrator 校验 kinds 相等）：
            # screenshot 的 effect.kind = idempotent-write（同 savePath 重放覆盖同一文件）
            return self._ext_success(
                invocation, EffectKind.IDEMPOTENT_WRITE, resource + ":screenshot",
                {"operation": "screenshot", "filePath": file_path},
                outputs={"filePath": file_path},
            )
        # -- 能力暂未实现（保留 manifest 字段，给出可操作错误） ----------------
        if command in ("browser.upload", "browser.download", "browser.handleDialog"):
            return CommandResult.failure(
                ErrorCode.COMMAND_NOT_FOUND,
                f"{command}：该能力扩展暂未实现（自研扩展单通道下本期未覆盖）。",
                details={"command": command},
            )
        return CommandResult.failure(
            ErrorCode.COMMAND_NOT_FOUND,
            f"Unsupported command on extension channel: {command}",
        )

    def _write_screenshot(self, save_path: str, raw: bytes) -> str:
        """截图落盘（同步，经 asyncio.to_thread 调用；返回绝对路径字符串）。"""
        target = Path(save_path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        return str(target)

    def _phase_d_method_args(self, command: str, inputs: dict[str, Any]) -> dict[str, Any]:
        """阶段 D DOM 命令参数 → 扩展 page.call 参数。"""
        if command == "browser.setValue":
            return {"setWay": inputs.get("setWay") or "value",
                    "value": "" if inputs.get("value") is None else str(inputs["value"])}
        if command == "browser.setAttribute":
            return {"name": str(inputs.get("name") or ""),
                    "value": "" if inputs.get("value") is None else str(inputs["value"])}
        if command == "browser.drag":
            return {"targetSelector": str(inputs.get("targetSelector") or "")}
        return {}

    async def _ext_wait_for(
        self,
        invocation: CommandInvocation,
        inputs: dict[str, Any],
        session_id: str,
        tab_id: str,
        selector: str,
        timeout_s: float,
        target_host: str,
        cancellation: asyncio.Event,
    ) -> CommandResult:
        """元素状态等待：扩展侧按「命中数 ± 可见性」轮询（visible/hidden/attached/detached）。"""
        state = str(inputs.get("state") or "visible")
        want_visible = state in ("visible", "hidden")
        want_present = state in ("visible", "attached")
        deadline = time.monotonic() + timeout_s
        last_count = 0
        while True:
            payload = await asyncio.to_thread(
                self._ext.page_call, tab_id, selector, "count",
                args={"visible": want_visible},
                timeout_seconds=min(10.0, timeout_s), target_host=target_host,
            )
            last_count = int(payload.get("matchedCount") or 0)
            hit = last_count > 0 if want_present else last_count == 0
            if hit:
                return self._ext_success(
                    invocation, EffectKind.READ,
                    f"browser.session:{session_id}:selector:{selector}",
                    {"operation": "waitFor", "state": state, "matchedCount": last_count},
                    outputs={"matchedCount": last_count},
                )
            if cancellation.is_set():
                return CommandResult(status="cancelled")
            if time.monotonic() >= deadline:
                return CommandResult.failure(
                    ErrorCode.TIMEOUT,
                    f"waitFor({state}) timed out on extension channel",
                    details={"selector": selector, "state": state},
                )
            await asyncio.sleep(0.3)

    @staticmethod
    async def _post_delay(inputs: dict[str, Any]) -> None:
        """`postDelayMs`（manifest 声明）：动作后等待指定毫秒。

        放在执行器侧而不是扩展里：纯等待不必占着扩展通道，且与桌面执行器同口径
        （桌面侧一直支持该参数，浏览器侧此前声明了却没生效）。
        """
        raw = inputs.get("postDelayMs")
        try:
            delay_ms = int(raw or 0)
        except (TypeError, ValueError):
            return
        if delay_ms > 0:
            await asyncio.sleep(min(delay_ms, 60_000) / 1000.0)

    def _ext_method_args(self, command: str, inputs: dict[str, Any]) -> dict[str, Any]:
        """命令参数 → 扩展 page.call 参数（未支持的字段在扩展侧忽略）。"""
        if command == "browser.click":
            return {
                "button": inputs.get("button") or "left",
                "clickType": inputs.get("clickType") or "single",
                "modifiers": inputs.get("modifiers") or [],
                # M29：这两个此前**声明了却没转发**——点击事件连坐标都没有（clientX/clientY 恒 0），
                # simulateHuman 则完全是摆设。默认值与 manifest 严格一致（true / center）。
                "simulateHuman": bool(inputs.get("simulateHuman", True)),
                "clickPosition": inputs.get("clickPosition") or "center",
            }
        if command == "browser.input":
            return {
                "text": "" if inputs.get("text") is None else str(inputs["text"]),
                # 默认值与 manifest 一致（fill）——此前默认 type 且扩展只认 "set"，
                # 导致 fill/type/clipboard 三种模式实际落到同一条逐字分支（漂移）
                "mode": inputs.get("mode") or "fill",
                "append": bool(inputs.get("append", False)),
                "pressEnter": bool(inputs.get("pressEnter", False)),
                # 以下两个此前**声明了但没转发**：逐字间隔（风控场景核心参数）与输入前点击
                "keyIntervalMs": inputs.get("keyIntervalMs"),
                "clickBeforeInput": bool(inputs.get("clickBeforeInput", False)),
            }
        if command == "browser.scroll":
            return {
                "position": inputs.get("position") or "bottom",
                "x": inputs.get("x"),
                "y": inputs.get("y"),
                "smooth": bool(inputs.get("smooth", False)),
            }
        if command == "browser.select":
            return {
                "value": "" if inputs.get("value") is None else str(inputs["value"]),
                "selectBy": inputs.get("selectBy") or "value",
            }
        if command == "browser.check":
            return {"operation": inputs.get("operation") or "check"}
        return {}

    def _match_ext_tab(self, inputs: dict[str, Any], timeout_s: float) -> dict[str, Any] | None:
        """attach：在用户浏览器全部标签页里按 url/title 子串或正则匹配。"""
        import re

        pattern = str(inputs.get("pattern") or "")
        match_by = str(inputs.get("matchBy") or "url")
        use_regex = bool(inputs.get("useRegex", False))
        tabs = self._ext.tabs_list(timeout_seconds=timeout_s)
        for tab in tabs:
            field = str(tab.get("title") if match_by == "title" else tab.get("url") or "")
            if use_regex:
                try:
                    hit = re.search(pattern, field) is not None
                except re.error:
                    hit = False
            else:
                hit = pattern in field
            if hit:
                return tab
        return None

    def _ext_success(
        self,
        invocation: CommandInvocation,
        kind: EffectKind,
        resource: str,
        details: dict[str, Any],
        *,
        outputs: dict[str, Any] | None = None,
        value: Any = None,
    ) -> CommandResult:
        return CommandResult.success(
            value=value,
            outputs=outputs or {},
            effects=[
                EffectRecord.committed(
                    invocation, kind=kind, resource=resource, details=details
                )
            ],
        )

    async def _close_browser(
        self,
        invocation: CommandInvocation,
        inputs: dict[str, Any],
        timeout_s: float,
        host: str,
    ) -> CommandResult:
        """终止指定类型浏览器的全部进程（M35 定案，取代 M32 的 scope 两档）。

        维护者定案（2026-09-21）：**不做范围区分，语义就是「直接杀某个浏览器的
        所有进程」**——按进程名匹配该类型的全部进程并终止，包含用户自己打开的
        窗口；执行这条命令本身就是显式的破坏性授权，不需要再用第二个参数确认。

        为什么删掉 M32 的 `launchedByUs`/`byProcessName` 两档：保守默认的判据
        「哪些是我们拉起的」依赖本进程内存里的启动时间水位（`_launched_marks`），
        流程跨进程执行（GUI 重启 / resume 换进程）后水位必然丢失，随即把明明该关
        的浏览器报成 `no_launched_process`——保护没兑现几次，误报先来了；而「只
        解绑会话、不碰进程」的正确工具本来就是 `browser.close`，不在本命令职责里。

        保留（M32 的有效部分）：`force` 决定终止方式；逐进程记账
        （`killedProcessIds`/`failedProcessIds`）；终止后等进程真正退出——不然
        上层紧接着的「重新拉起」会拿旧进程的插件端点当在线（同一浏览器被判定为
        「还在」）。
        """
        browser = str(inputs.get("browserType") or "msedge").strip().lower()
        if browser not in _BROWSER_PROCESS_NAMES:
            return CommandResult.failure(
                ErrorCode.INVALID_INPUT,
                f"不支持的浏览器类型：{browser!r}（只支持 msedge / chrome）",
                details={"field": "browserType", "received": browser},
            )
        force = bool(inputs.get("force"))
        process_names = _BROWSER_PROCESS_NAMES[browser]
        try:
            matched = await asyncio.to_thread(_list_browser_processes, process_names)
        except OSError as exc:
            return CommandResult.failure(
                ErrorCode.PLATFORM_UNSUPPORTED,
                f"无法枚举浏览器进程（{exc}）——终止浏览器依赖操作系统的进程列表接口，"
                "当前平台或权限下不可用。",
                details={"browser": browser, "platform": sys.platform},
            )
        killed: list[int] = []
        failed: list[int] = []
        for info in matched:
            pid = int(info.get("pid") or 0)
            if pid <= 0:
                continue
            try:
                await asyncio.to_thread(_terminate_process, pid, force)
                killed.append(pid)
            except OSError:
                failed.append(pid)
        # 进程终止不是瞬时的：等它们真正退出，不然上层紧接着的「重新拉起」会
        # 拿旧进程的插件端点当在线（同一浏览器被判定为「还在」）。
        exited = True
        if matched:
            exited = await asyncio.to_thread(
                _wait_processes_exit,
                [info["pid"] for info in matched],
                min(timeout_s, 10.0),
            )
        return self._ext_success(
            invocation, EffectKind.SESSION, f"browser.process:{browser}",
            {
                "operation": "closeBrowser",
                "browser": browser,
                "force": force,
                "matchedCount": len(matched),
                "killedCount": len(killed),
                "failedCount": len(failed),
                "allExited": exited,
            },
            outputs={
                # 「已终止」= 目标状态已达成：没有任何匹配进程（本来就不存在）也算达成——
                # 收尾步骤的语义是「确保它不在跑」，而不是「我刚杀了几个」。
                # 有匹配却没能全杀掉 → 不算是（failedProcessIds 里能看出是哪些）。
                "terminated": not failed,
                "matchedCount": len(matched),
                "killedProcessIds": killed,
                "failedProcessIds": failed,
            },
        )

    @staticmethod
    def _ext_not_found(
        inputs: dict[str, Any], *, candidates_tried: int = 0
    ) -> CommandResult:
        details: dict[str, Any] = {
            "selector": inputs.get("selector"),
            "matchedCount": 0,
        }
        if candidates_tried:
            # 自愈失败也要可诊断：告诉用户「主选择器 + N 条候选全都没命中」
            details["candidatesTried"] = candidates_tried
        return CommandResult.failure(
            ErrorCode.ELEMENT_NOT_FOUND,
            "Target element did not match"
            + (f"（含 {candidates_tried} 条备选候选）" if candidates_tried else ""),
            details=details,
        )

    def _ext_channel_failure(self, exc: ExtensionChannelError) -> CommandResult:
        """扩展通道错误整形：把协议码翻译成用户能照着做的说明。"""
        # 执行前预检（M28 S2）：遮挡/隐藏/禁用是「找到但不可安全操作」，必须显式失败，
        # 并把「谁挡住了」带出来（只给一句「被遮挡」等于没报）。
        if exc.code in _PRECHECK_CODES:
            try:
                error_code = ErrorCode(exc.code)
            except ValueError:  # pragma: no cover - 白名单与枚举应当一致
                error_code = ErrorCode.EXECUTOR_FAILED
            details: dict[str, Any] = {"channel": "extension", "code": exc.code}
            if isinstance(exc.details, dict):
                details.update(exc.details)
                blocked_by = exc.details.get("blockedBy")
                label = exc.details.get("blockedByLabel")
                if blocked_by and not label:
                    details["blockedByLabel"] = _describe_blocker(blocked_by)
            return CommandResult.failure(
                error_code,
                str(exc).split(": ", 1)[-1] or exc.code,
                details=details,
            )
        if exc.code == "TIMEOUT":
            return CommandResult.failure(
                ErrorCode.TIMEOUT,
                "extension channel: 命令已下发但扩展未在时限内回应——浏览器可能已关闭、"
                "扩展被禁用，或浏览器被挂起（MV3 service worker 休眠）。"
                "请确认目标浏览器仍在运行且扩展已启用后重试。",
                details={"channel": "extension", "code": exc.code, "reason": "no_response"},
            )
        if exc.code == "CHANNEL_OFFLINE":
            return CommandResult.failure(
                ErrorCode.EXECUTOR_FAILED,
                "extension channel: 连不上本机 devserver 的命令端点（RPA_EXT_HUB_URL）——"
                "请确认 devserver 正在运行；重启过端口变化时需一并重启 devserver，"
                "让 run 子进程拿到正确端口。",
                details={"channel": "extension", "code": exc.code, "reason": "hub_unreachable"},
            )
        if exc.code == "TARGET_HOST_OFFLINE":
            return CommandResult.failure(
                ErrorCode.EXECUTOR_FAILED,
                f"目标浏览器的自研插件未在线（{exc}）——请在浏览器类型指定的 Chrome/Edge 里"
                "安装并启用插件后重试，或在指令里改选已装插件的浏览器。",
                details={"channel": "extension", "code": exc.code, "reason": "target_host_offline"},
            )
        return CommandResult.failure(
            ErrorCode.EXECUTOR_FAILED,
            f"extension channel: {exc}",
            details={"channel": "extension", "code": exc.code},
        )

    def restore_from_scopes(self, scopes: Any) -> None:
        """resume 时按快照重建会话绑定（M21 跨进程续接，由 `ExecutorRegistry` 调用）。

        扩展通道的会话 = 用户浏览器里的标签页句柄，**不随 run 进程退出而消失**
        （`close()` 只解绑、不代关用户标签页），所以 resume 起来的新进程只要把
        `sessionId → tabId` 接回来，就能接着操作同一批标签页——不然「暂停后继续」
        会以「缺少有效会话」失败（暂停 = 干净收口 + 进程退出，见 ADR 0005）。

        不在这里校验标签页是否仍存在：那要一次扩展往返，而「跑起来才发现页面被
        用户关了」和恢复期判定是同一种错误，交给真去用的那条命令报 not found 更直接。
        """
        tabs, hosts, last_sid = session_bindings_from_scopes(scopes)
        self._ext_sessions.update(tabs)
        self._ext_session_hosts.update(hosts)
        if last_sid is not None:
            self._last_session_id = last_sid

    async def close(self) -> None:
        # 扩展单通道（M15）：只解绑，不动用户浏览器里的标签页（默认整浏览器权限≠代管生命周期）
        self._ext_sessions.clear()
