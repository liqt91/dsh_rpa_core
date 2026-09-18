"""pytest 全局约定（跨平台门禁自足性的前提）。

## 0. GUI 用例一律离屏，绝不在开发者桌面弹窗

所有 GUI 用例走 `QT_QPA_PLATFORM=offscreen`（各模块在导入 Qt 前 setdefault），
这里再做一次全局兜底，避免将来新增用例漏设而真的弹出窗口。平台插件在
`QApplication` 创建时定型，因此必须在**任何 Qt 导入之前**设置。

真实桌面 E2E（启动记事本 / WinForms 演示程序 / 捕获悬浮框，会弹窗并抢前台）
为显式开关：缺省跳过，设 `RPA_DESKTOP_E2E=1` 启用——门禁脚本
`.harness/scripts/check_all.py` 默认**不**启用（弹窗会打断维护者操作），
需要完整覆盖时跑 `check_all.py --with-desktop-e2e`；日常 `uv run pytest`
同样不弹窗。

## 1. 默认执行通道必须是离线的

扩展执行通道经**本地端点**（`rpa_core_ext_<browser>_<实例 token>`，见 ADR 0015）发现
bridge host。开发者本机常驻浏览器扩展时，那些没有显式注入 client 的用例
（如 `tests/contract/test_browser_contract.py` 里直接构造的执行器）会真的连上
**开发者本人浏览器**——门禁结论随本机环境漂移，同一份代码在不同机器上跑出不同结果，
甚至产生真实副作用。

这里在收集测试前把端点前缀改成一个测试专用值（`RPA_EXT_ENDPOINT_PREFIX`），
本机真实端点（`rpa_core_ext_…`）因此不可见，「默认通道」成为确定性的离线状态。
需要真实通道的用例一律显式构造 client 并指向测试端点
（见 `test_ext_bridge.py` 的 host 子进程夹具）。

## 2. 受限执行环境下临时根要能真正建出来

pytest 的 `tmp_path` 基目录默认取 `tempfile.gettempdir()`，并在其下建
`pytest-of-<user>`（已存在则 `mkdir(mode=0o700, exist_ok=True)`）。受限执行环境
（把 host 文件操作代理给宿主的沙箱/容器）会拒绝「对已存在路径再次带 mode 的
mkdir」，而抛出的 `PermissionError` 不是 `FileExistsError`，`exist_ok=True`
**兜不住**——于是所有依赖 `tmp_path` 的用例在 setup 阶段整片 ERROR，表象是
`PermissionError: EEXIST .../pytest-of-unknown`，真实原因既不在用例也不在产品代码。

注意这个故障是**有状态**的：目录不存在时首次创建会成功，第二次运行才炸。

这里只在默认临时根确实不可用时，改用一个**本次运行专用的全新 basetemp**：

- 全新 → 绕开「对已存在路径 mkdir」；
- 全新且为空 → pytest 拿到 `--basetemp` 时会先 `rm_rf` 再 `mkdir`，目标为空时
  这次清理不构成任何批量删除。受限环境最不该在**启动阶段**做批量删除（失败即
  整个 session 崩），所以刻意挑一个要么不存在、要么为空的路径。
- 目录建完后 best-effort 回收，回收失败不影响结论。

正常机器上探测通过、一切保持 pytest 原生目录布局；显式 `--basetemp` 优先级更高。
"""

import getpass
import os
import shutil
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# 0. GUI 离屏兜底（必须在任何 Qt 导入前生效）。用「空值也视为未设置」的写法：
# 环境变量若存在但为空串，setdefault 不会覆盖，Qt 会退回真实平台而弹窗。
if not os.environ.get("QT_QPA_PLATFORM"):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"

# 真实桌面 E2E 的显式开关（会弹窗抢前台，缺省跳过；门禁默认不启用，
# `check_all.py --with-desktop-e2e` 或显式设该环境变量时才跑）。
# 各 e2e 用例文件直接读该环境变量做 skipif，reason 里必须含下面的关键子串，
# 供 pytest_terminal_summary 识别并提示启用方式。
DESKTOP_E2E_ENABLED = os.environ.get("RPA_DESKTOP_E2E") == "1"
DESKTOP_E2E_SKIP_MARKER = "RPA_DESKTOP_E2E=1"

# 测试专用端点前缀：本机真实端点（rpa_core_ext_…）因此对默认 client 不可见，
# 用例走「扩展离线」分支（需要真实通道的用例显式指向测试端点）。
#
# 前缀**必须与默认前缀 `rpa_core_ext_` 互不包含**：`list_endpoints` 用
# `startswith` 过滤，若隔离前缀以默认前缀开头（如早先的 `rpa_core_ext_test_isolated_`），
# 则**真实进程会枚举到测试端点** —— 开发者跑门禁期间用 GUI/CLI 捕获会 arm 到测试的
# 假 bridge 端点、拿到测试描述符并把它当成真实元素落库（2026-09-18 跨进程实测取证）。
# 回归守门：tests/unit/test_local_transport.py::test_isolated_endpoint_prefix_...
os.environ["RPA_EXT_ENDPOINT_PREFIX"] = "rpacore-iso_"

# 默认临时根不可用时的候选 basetemp 父目录：先系统临时区（不污染工作区），
# 最后一个兜底放工作区内（工作区几乎总是可写的），已被 .gitignore 排除。
_FALLBACK_PARENTS = (
    Path(tempfile.gettempdir()) / "rpa-core-pytest",
    Path("/tmp") / "rpa-core-pytest",
    REPO_ROOT / ".pytest_tmp",
)

_provisioned: str | None = None


def _current_user() -> str:
    try:
        return getpass.getuser()
    except (ImportError, OSError, KeyError):  # 部分环境根本没有 passwd 条目
        return "unknown"


def _directory_is_writable(directory: Path) -> bool:
    probe = directory / f"pytest-probe-{os.getpid()}"
    try:
        probe.mkdir()
    except OSError:
        return False
    try:
        probe.rmdir()
    except OSError:
        pass
    return True


def _default_basetemp_is_usable() -> bool:
    """复刻 pytest 建默认基目录的动作，判断它在这个环境里能否成立。

    pytest 只对 `pytest-of-<user>` 与 `pytest-of-unknown` 这两个名字操作，任何一个
    成功就继续；两个都失败整个 session 就崩。因此这两个名字都被拒时，即使临时根
    本身可写，也判为不可用。
    """
    temproot = Path(tempfile.gettempdir()).resolve()
    for name in (f"pytest-of-{_current_user()}", "pytest-of-unknown"):
        rootdir = temproot / name
        if not rootdir.exists():
            # 路径还不存在时 pytest 会新建它，父目录可写即可成功。这里刻意不去建它：
            # 一旦被探测行为抢先建出来，就把「新建」变成了「已存在」，反而触发故障。
            return _directory_is_writable(temproot)
        try:
            # 已存在时 pytest 会再 mkdir 一次（带 mode）——受限环境正是卡在这一步。
            rootdir.mkdir(mode=0o700, exist_ok=True)
        except OSError:
            continue
        return True
    return False


def _provision_basetemp() -> str | None:
    """在可写的父目录下开一个本次运行专用的空目录，作为 basetemp。"""
    for parent in _FALLBACK_PARENTS:
        try:
            parent.mkdir(parents=True, exist_ok=True)
            return tempfile.mkdtemp(dir=parent, prefix="run-")
        except OSError:
            continue
    return None


def pytest_configure(config):
    global _provisioned
    if config.option.basetemp:
        return  # 显式 --basetemp 优先，不覆盖
    if _default_basetemp_is_usable():
        return  # 正常环境：保持 pytest 原生目录布局
    _provisioned = _provision_basetemp()
    if _provisioned is not None:
        config.option.basetemp = _provisioned


def pytest_unconfigure(config):
    # 回收本次运行专用的 basetemp；受限环境可能拒绝这次删除，失败不影响任何结论。
    if _provisioned is not None:
        shutil.rmtree(_provisioned, ignore_errors=True)


def pytest_terminal_summary(terminalreporter) -> None:
    """桌面 E2E 被跳过时明确告知启用方式，避免误以为覆盖丢了。"""
    if DESKTOP_E2E_ENABLED:
        return
    skipped = terminalreporter.stats.get("skipped") or []
    # 跳过原因在 report.longrepr 的 (path, lineno, reason) 三元组里（无 .reason 属性）
    texts = []
    for item in skipped:
        longrepr = getattr(item, "longrepr", None)
        if isinstance(longrepr, tuple) and len(longrepr) == 3:
            texts.append(str(longrepr[2]))
        else:
            texts.append(str(longrepr))
    if not any(DESKTOP_E2E_SKIP_MARKER in text for text in texts):
        return
    terminalreporter.write_sep("-", "desktop e2e skipped")
    terminalreporter.write_line(
        "真实桌面 E2E（记事本 / WinForms 演示程序 / 捕获悬浮框）已跳过，"
        "以免弹窗抢焦点。"
    )
    terminalreporter.write_line(
        "需要时：RPA_DESKTOP_E2E=1 uv run pytest（PowerShell: "
        "$env:RPA_DESKTOP_E2E=1; uv run pytest）"
    )
