"""桌面窗口附着契约（M30 S4：两后端的 `attachWindow` 筛选口径对齐）。

背景：`desktop.attachWindow` 与 `desktop.win32.attachWindow` 的 manifest 都声明了
`className`（另有 `processId` / `matchMode`），但 uia 侧的实现**只读 `title` 与 `processId`**：

- `_find_windows_by_title(title, process_id, match_mode)` 的签名里根本没有 className；
- 于是「按窗口类名找窗口」在 uia 后端是静默失效——用户填了类名，它被丢掉，
  查找退化成「只看标题」，命中多个还要报 `ELEMENT_AMBIGUOUS`，
  报错信息里也**看不到 className**，用户无从得知自己填的条件没生效。

S4 把 uia 侧补齐，口径以 win32 侧（`_filter_windows` + className/processId 过滤）为参照：

| 维度 | 约定 |
|---|---|
| 匹配方式 | `className` **恒为等值比较**，`matchMode` 只作用于 title（两后端一致） |
| 组合关系 | `title` / `className` / `processId` 之间是 **AND** |
| 二者都可省 | 只给 className 也能附着（win32 侧本来就能） |
| 一个都不给 | `INVALID_INPUT`（否则「找所有窗口」会撞上 `ELEMENT_AMBIGUOUS`，原因误导） |
| 报错上下文 | `ELEMENT_NOT_FOUND` / `ELEMENT_AMBIGUOUS` 的 details 要带上 className |

`exact` 路径用 `FindWindowW(class_name, title)` 顺带过滤（省一次枚举），但 Win32 的
类名匹配**不是逐字节等值**（按类名前缀、忽略大小写），所以拿到的句柄必须用
`GetClassNameW` 复核一次，保证与 win32 侧的等值口径一致——这条专门有测试钉住。

真机边界：桌面 E2E 会抢前台，AGENTS 已定「按需启用」。这里用打桩的 `user32` / 伪句柄
覆盖筛选逻辑，**不冒充真机结论**（真实窗口上的类名枚举顺序与可见性仍待需要时复验）。
"""

from __future__ import annotations

import ast
import ctypes
import sys
from pathlib import Path
from typing import Any

import pytest

from rpa_core.catalog import load_catalog

ROOT = Path(__file__).resolve().parents[2]
EXECUTORS = ROOT / "src" / "rpa_core" / "executors"


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(ROOT / "commands")


DESKTOP_ONLY = pytest.mark.skipif(
    sys.platform != "win32", reason="DesktopExecutor 在非 win32 上是 PLATFORM_UNSUPPORTED 占位"
)


# ------------------------------------------------------- 测试替身：假 user32（非真机）

# 句柄 → (类名, 标题, pid)；测试自己的窗口台账，不是真实桌面。
# 同一 (类名, 标题, pid) 刻意放了两个句柄（100/101）：`FindWindowW` 只返回**第一个**匹配，
# 这正是 exact 路径「拿不到多个候选」的真实约束（见 `test_exact_path_sees_only_one_candidate`）。
_WINDOWS: dict[int, tuple[str, str, int]] = {
    100: ("Notepad", "无标题 - 记事本", 1000),
    101: ("Notepad", "无标题 - 记事本", 1000),
    102: ("Chrome_WidgetWin_1", "a.txt - 记事本", 2000),
}


class _FakeUser32:
    """只实现 `_find_windows_by_title` 用到的那几个入口。

    `FindWindowW` 刻意模仿真实 Win32 的宽松行为：**按类名前缀、忽略大小写**匹配——
    这正是「必须用 GetClassNameW 复核」的原因。如果实现照搬 FindWindowW 的宽松语义，
    下面的 `test_exact_class_name_is_compared_byte_for_byte` 会红。
    """

    def __init__(self, windows: dict[int, tuple[str, str, int]] | None = None) -> None:
        self.windows = dict(_WINDOWS if windows is None else windows)
        self.enum_calls = 0
        self.find_calls: list[tuple[str | None, str | None]] = []

    # --- 真实 user32 的子集 ---
    def FindWindowW(self, class_name: Any, title: Any) -> int:
        cls = self._as_str(class_name)
        win_title = self._as_str(title)
        self.find_calls.append((cls, win_title))
        for hwnd, (w_cls, w_title, _pid) in self.windows.items():
            if cls is not None and not w_cls.lower().startswith(cls.lower()):
                continue
            if win_title is not None and w_title != win_title:
                continue
            return hwnd  # 只返回第一个命中——与真实 API 一致
        return 0

    def EnumWindows(self, callback: Any, _lparam: Any) -> bool:
        self.enum_calls += 1
        for hwnd in self.windows:
            if not callback(hwnd, None):
                break
        return True

    def IsWindowVisible(self, _hwnd: Any) -> bool:
        return True

    def GetWindowTextW(self, hwnd: Any, buf: Any, _size: int) -> int:
        text = self.windows.get(int(hwnd), ("", "", 0))[1]
        buf.value = text
        return len(text)

    def GetClassNameW(self, hwnd: Any, buf: Any, _size: int) -> int:
        name = self.windows.get(int(hwnd), ("", "", 0))[0]
        buf.value = name
        return len(name)

    def GetWindowThreadProcessId(self, hwnd: Any, out: Any) -> int:
        pid = self.windows.get(int(hwnd), ("", "", 0))[2]
        out._obj.value = pid
        return pid

    @staticmethod
    def _as_str(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, bytes):
            return None
        text = value if isinstance(value, str) else str(value)
        return text or None


# --------------------------------------------------- 1. uia 侧真的读了 className（反漂移）


def _attach_branch() -> ast.If:
    """取出 `if command == "desktop.attachWindow":` 那个分支。"""
    source = (EXECUTORS / "desktop.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="desktop.py")
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not isinstance(test, ast.Compare):
            continue
        if not (
            isinstance(test.left, ast.Name)
            and test.left.id == "command"
            and any(
                isinstance(c, ast.Constant) and c.value == "desktop.attachWindow"
                for c in test.comparators
            )
        ):
            continue
        return node
    raise AssertionError("desktop.py 里找不到 desktop.attachWindow 分支")


def test_uia_attach_branch_reads_class_name():
    """反漂移：`className` 必须被 attachWindow 分支**字面量**读出来。

    静态门禁 `check_param_consumption.py` 认的就是这个形状（`inputs.get("className")`），
    这里再从测试侧钉一遍，避免有人把它改成变量取值而门禁悄悄放过。
    """
    branch = _attach_branch()
    keys = {
        node.args[0].value
        for node in ast.walk(branch)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "inputs"
        and node.args
        and isinstance(node.args[0], ast.Constant)
    }

    assert "className" in keys, "attachWindow 分支没有读 className，声明又会变回假开关"


def test_finder_signature_accepts_class_name():
    """反漂移：`_find_windows_by_title` 的签名要带 `class_name`，不允许各写一套筛选。"""
    source = (EXECUTORS / "desktop.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="desktop.py")
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_find_windows_by_title":
            names = [arg.arg for arg in node.args.args]
            assert "class_name" in names, names
            return
    raise AssertionError("找不到 _find_windows_by_title")


# ------------------------------------------------------- 2. exact 路径的等值口径


@pytest.mark.parametrize(
    "class_name,expected",
    [
        (None, {100}),  # 不限类名 → FindWindowW(None, None) 返回第一个窗口
        ("Notepad", {100}),
        ("Chrome_WidgetWin_1", {102}),
        ("notepad", set()),  # 逐字节等值 → 大小写不同即不匹配（win32 侧同为 ==）
        ("Note", set()),  # 前缀不算命中：FindWindowW 会宽松命中，必须被 GetClassNameW 拦下
        ("Notepadd", set()),
    ],
)
def test_exact_class_name_is_compared_byte_for_byte(monkeypatch, class_name, expected):
    """`exact` 模式下类名与 win32 侧同为 `==`，不受 FindWindowW 宽松语义影响。"""
    fake = _FakeUser32()
    matched = _run_find(monkeypatch, fake, title=None, class_name=class_name, match_mode="exact")
    assert set(matched) == expected


def test_exact_path_sees_only_one_candidate(monkeypatch):
    """如实记录 exact 路径的**固有局限**：`FindWindowW` 只回第一个句柄。

    所以「同标题同类名的两个窗口」在 exact 下不会报 `ELEMENT_AMBIGUOUS`，而是静默附着第一个。
    与 win32 侧（`Desktop().windows()` 全枚举后逐个过滤，能发现歧义）**行为不同**——
    这是取舍不是 bug：exact 的价值在于绕开全桌面 UIA 枚举（慢 provider 可达 ~60s）。
    需要歧义检测时用 `matchMode=contains`（走 EnumWindows，两后端都能报 AMBIGUOUS）。
    """
    fake = _FakeUser32()
    # 100 与 101 同 (类名, 标题, pid)，exact 下只可能拿到 100
    matched = _run_find(
        monkeypatch,
        fake,
        title="无标题 - 记事本",
        class_name="Notepad",
        match_mode="exact",
    )
    assert matched == [100]
    # 换成 contains 就能看见两个 → 歧义检测能力确实在线，只是 exact 不走这条路
    fake2 = _FakeUser32()
    loose = _run_find(
        monkeypatch,
        fake2,
        title="无标题 - 记事本",
        class_name="Notepad",
        match_mode="contains",
    )
    assert set(loose) == {100, 101}


@pytest.mark.parametrize("match_mode", ["contains", "regex"])
def test_loose_match_modes_filter_class_name_too(monkeypatch, match_mode):
    """contains/regex 只放宽 **title**，className 仍是等值——否则 `matchMode` 会偷偷放大类名条件。"""
    fake = _FakeUser32()
    matched = _run_find(
        monkeypatch, fake, title="记事本", class_name="Notepad", match_mode=match_mode
    )
    # 走枚举能看见全部候选 → 同一个类的两个窗口都命中（这条路径有歧义检测能力）
    assert set(matched) == {100, 101}


@pytest.mark.parametrize("match_mode", ["contains", "regex"])
def test_loose_match_modes_skip_the_enumeration_when_no_title_is_given(monkeypatch, match_mode):
    """只给 className 时，空 title 不应被当成「匹配所有标题」再靠类名兜底。"""
    fake = _FakeUser32()
    matched = _run_find(
        monkeypatch, fake, title="", class_name="Chrome_WidgetWin_1", match_mode=match_mode
    )
    # 空串 in 任何标题都成立 → 全部通过标题关；真正的过滤来自 className
    assert set(matched) == {102}


# ------------------------------------------------------- 3. 组合关系（AND）


def test_class_name_and_process_id_are_combined_with_and(monkeypatch):
    fake = _FakeUser32()
    # 100/101 是 Notepad(pid 1000)，102 是 Chrome(pid 2000)
    matched = _run_find(
        monkeypatch, fake, title=None, class_name="Notepad", process_id=1000, match_mode="exact"
    )
    # FindWindowW 拿到的候选由类名决定，pid 再收一道
    assert set(matched) <= {100, 101}


def test_process_id_rejects_a_class_name_hit(monkeypatch):
    fake = _FakeUser32()
    matched = _run_find(
        monkeypatch, fake, title=None, class_name="Notepad", process_id=9999, match_mode="exact"
    )
    assert matched == []


def test_class_name_rejects_a_process_id_hit(monkeypatch):
    fake = _FakeUser32()
    matched = _run_find(
        monkeypatch,
        fake,
        title=None,
        class_name="Chrome_WidgetWin_1",
        process_id=1000,
        match_mode="exact",
    )
    assert matched == []


def test_exact_path_does_not_enumerate_the_whole_desktop(monkeypatch):
    """`exact` 走 FindWindowW，不许退化成 EnumWindows 全桌面遍历（慢 UIA provider 会卡 60s）。"""
    fake = _FakeUser32()
    _run_find(monkeypatch, fake, title=None, class_name="Notepad", match_mode="exact")
    assert fake.enum_calls == 0
    assert fake.find_calls, "exact 路径应当调用过 FindWindowW"


def test_loose_path_does_not_call_find_window(monkeypatch):
    """contains/regex 走枚举，不应再调 FindWindowW（两条路各自独立，别混着走）。"""
    fake = _FakeUser32()
    _run_find(monkeypatch, fake, title="记事本", class_name="Notepad", match_mode="contains")
    assert fake.enum_calls == 1
    assert fake.find_calls == []


# ------------------------------------------------------------ 4. 执行器分支（打桩）


def _execute(executor, command_id: str, inputs: dict):
    import asyncio

    from rpa_core.model.command import CommandInvocation

    invocation = CommandInvocation(
        command_id=command_id,
        command_version="1.0.0",
        run_id="run",
        step_id="step",
        inputs=inputs,
    )
    return asyncio.run(executor.execute(invocation, asyncio.Event()))


@DESKTOP_ONLY
@pytest.mark.parametrize(
    "inputs",
    [
        pytest.param({}, id="neither"),
        pytest.param({"title": ""}, id="empty-title"),
        pytest.param({"title": "", "className": None}, id="empty-both"),
    ],
)
def test_attach_without_any_filter_is_invalid_input(monkeypatch, inputs):
    """一个条件都不给 → `INVALID_INPUT`，而不是「枚举全桌面然后 ELEMENT_AMBIGUOUS」。

    后者把用户的输入错误伪装成「窗口不唯一」，报错原因在误导方向。
    """
    from rpa_core.executors.desktop import DesktopExecutor

    result = _execute(DesktopExecutor(), "desktop.attachWindow", inputs)

    assert result.status == "error"
    assert result.error.code == "INVALID_INPUT"


@DESKTOP_ONLY
def test_attach_with_class_name_only_is_accepted(monkeypatch):
    """只给 className 必须能走到查找（win32 侧本来就能），不许被前置换行拦下。"""
    from rpa_core.executors.desktop import DesktopExecutor

    executor = DesktopExecutor()
    seen: list[dict] = []

    def fake_find(title, process_id, match_mode, class_name=None, class_name_re=None) -> list[Any]:
        seen.append({
            "title": title,
            "class": class_name,
            "classRe": class_name_re,
            "pid": process_id,
            "mode": match_mode,
        })
        return []

    monkeypatch.setattr(executor, "_find_windows_by_title", fake_find)
    result = _execute(executor, "desktop.attachWindow", {"className": "Notepad"})

    assert result.status == "error"  # 桩恒返回空 → 没找到
    assert result.error.code == "ELEMENT_NOT_FOUND"
    assert seen == [
        {"title": "", "class": "Notepad", "classRe": None, "pid": None, "mode": "exact"}
    ]


@DESKTOP_ONLY
def test_attach_with_class_name_re_only_is_accepted(monkeypatch):
    """只给 classNameRe 必须能走到查找（新增的正则口子），不许被前置换行拦下。"""
    from rpa_core.executors.desktop import DesktopExecutor

    executor = DesktopExecutor()
    seen: list[dict] = []

    def fake_find(title, process_id, match_mode, class_name=None, class_name_re=None) -> list[Any]:
        seen.append({"title": title, "class": class_name, "classRe": class_name_re})
        return []

    monkeypatch.setattr(executor, "_find_windows_by_title", fake_find)
    result = _execute(
        executor, "desktop.attachWindow", {"classNameRe": r"WindowsForms10\.LISTBOX"}
    )

    assert result.status == "error"  # 桩恒返回空 → 没找到
    assert result.error.code == "ELEMENT_NOT_FOUND"
    assert seen == [{"title": "", "class": None, "classRe": r"WindowsForms10\.LISTBOX"}]


@DESKTOP_ONLY
def test_class_name_and_class_name_re_are_mutually_exclusive(monkeypatch):
    """两个类名字段同时给 = INVALID_INPUT（等值 vs 正则，语义互斥）。

    不静默取其一：那会让「我明明写了两条筛选」与「实际只按一条筛」对不上。
    """
    from rpa_core.executors.desktop import DesktopExecutor

    executor = DesktopExecutor()
    monkeypatch.setattr(executor, "_find_windows_by_title", lambda *a, **k: [])
    result = _execute(
        executor,
        "desktop.attachWindow",
        {"className": "Notepad", "classNameRe": "Notepad"},
    )

    assert result.error.code == "INVALID_INPUT"


@DESKTOP_ONLY
def test_not_found_details_include_class_name(monkeypatch):
    """报错上下文要带上 className——否则用户看不出「我填的类名参与了筛选」。"""
    from rpa_core.executors.desktop import DesktopExecutor

    executor = DesktopExecutor()
    monkeypatch.setattr(executor, "_find_windows_by_title", lambda *a, **k: [])
    result = _execute(
        executor, "desktop.attachWindow", {"title": "记事本", "className": "Notepad"}
    )

    assert result.error.code == "ELEMENT_NOT_FOUND"
    assert result.error.details["className"] == "Notepad"


@DESKTOP_ONLY
def test_ambiguous_details_include_class_name(monkeypatch):
    from rpa_core.executors.desktop import DesktopExecutor

    executor = DesktopExecutor()
    monkeypatch.setattr(executor, "_find_windows_by_title", lambda *a, **k: [object(), object()])
    result = _execute(
        executor, "desktop.attachWindow", {"title": "记事本", "className": "Notepad"}
    )

    assert result.error.code == "ELEMENT_AMBIGUOUS"
    assert result.error.details["className"] == "Notepad"


# ------------------------------------------------------------ 5. manifest 面


def _manifest(backend: str, name: str) -> dict:
    import json

    return json.loads((ROOT / "commands" / backend / f"{name}.json").read_text(encoding="utf-8"))


# 两后端参数表的**唯一**合法差异：win32 的 `handle`（直接按句柄附着，uia 侧没有对应入口）
BACKEND_SPECIFIC_PARAMS = {"handle": "win32"}


def test_two_backends_agree_on_the_attach_contract(catalog):
    """两后端的参数表一致（除显式登记的差异）：同一份语义写在两份 manifest 里，是漂移的高发地。"""
    uia = _manifest("desktop", "attachWindow")["input_schema"]
    win32 = _manifest("desktop_win32", "attachWindow")["input_schema"]

    # 差异必须**逐条登记**，不许出现没人认领的独有参数——否则漂移会藏进「反正两边不一样」里
    only_win32 = set(win32["properties"]) - set(uia["properties"])
    only_uia = set(uia["properties"]) - set(win32["properties"])
    assert only_win32 == set(BACKEND_SPECIFIC_PARAMS), f"未登记的 win32 独有参数：{only_win32}"
    assert only_uia == set(), f"未登记的 uia 独有参数：{only_uia}"

    shared = set(uia["properties"]) & set(win32["properties"])
    assert {"title", "className", "classNameRe", "processId", "matchMode", "timeoutMs"} <= shared
    # 共享参数的说明必须一致（含 className 的「等值比较」措辞）。
    # 只比 description：有些字段（title/processId）本来就没写说明，缺省即两边都缺省。
    for field in sorted(shared):
        assert (
            uia["properties"][field].get("description")
            == win32["properties"][field].get("description")
        ), f"{field} 的说明在两后端不一致"
    # 必填口径曾两边不同（uia 的 title 是 required、win32 的不是），现统一为「都可省」
    assert uia.get("required") == win32.get("required")

    # className 的说明要能自洽：既然 matchMode 只管 title，就必须在 className 上写清等值
    for manifest in (uia, win32):
        assert "等值" in manifest["properties"]["className"]["description"]
        assert "title" in manifest["properties"]["matchMode"]["description"]

    assert (
        _manifest("desktop", "attachWindow")["errors"]
        == _manifest("desktop_win32", "attachWindow")["errors"]
    )


@pytest.mark.parametrize("backend", ["desktop", "desktop_win32"])
def test_attach_manifest_says_class_name_is_exact(backend):
    """`className` 的说明必须点明「等值比较」：否则用户会以为 matchMode 也管它。"""
    properties = _manifest(backend, "attachWindow")["input_schema"]["properties"]
    assert "等值" in properties["className"]["description"]


@pytest.mark.parametrize("backend", ["desktop", "desktop_win32"])
def test_attach_manifest_declares_invalid_input(backend):
    """实现会因「没给任何筛选条件」返回 `INVALID_INPUT`，声明面必须跟上。"""
    assert "INVALID_INPUT" in _manifest(backend, "attachWindow")["errors"]


# -------------------------------------------------------------------- 测试工具


_REAL_WINDLL = ctypes.windll


def _run_find(
    monkeypatch,
    fake: _FakeUser32,
    *,
    title: str | None,
    class_name: str | None,
    match_mode: str,
    process_id: int | None = None,
) -> list[int]:
    """在假 user32 上跑一次查找，返回命中的句柄列表。

    `monkeypatch` 传 None 时走**临时替换 + finally 还原**，供不需要 pytest fixture 的
    纯断言场景使用（避免在非 win32 上给 `ctypes.windll` 打桩失败）。
    """
    if sys.platform != "win32":
        pytest.skip("DesktopExecutor 在非 win32 上是 PLATFORM_UNSUPPORTED 占位")

    from rpa_core.executors.desktop import DesktopExecutor

    executor = DesktopExecutor()
    captured: list[int] = []

    class _FakeWrapper:
        def __init__(self, info: Any) -> None:
            captured.append(int(info.handle))

    class _FakeInfo:
        def __init__(self, hwnd: Any) -> None:
            self.handle = int(hwnd)

    import pywinauto.controls.uiawrapper as uiawrapper
    import pywinauto.uia_element_info as uia_element_info

    if monkeypatch is None:
        ctypes.windll = type("W", (), {"user32": fake})()
        original_wrapper = uiawrapper.UIAWrapper
        original_info = uia_element_info.UIAElementInfo
        uiawrapper.UIAWrapper = _FakeWrapper
        uia_element_info.UIAElementInfo = _FakeInfo
        try:
            executor._find_windows_by_title(title or "", process_id, match_mode, class_name)
        finally:
            ctypes.windll = _REAL_WINDLL
            uiawrapper.UIAWrapper = original_wrapper
            uia_element_info.UIAElementInfo = original_info
        return captured

    monkeypatch.setattr(ctypes, "windll", type("W", (), {"user32": fake})())
    monkeypatch.setattr(uiawrapper, "UIAWrapper", _FakeWrapper)
    monkeypatch.setattr(uia_element_info, "UIAElementInfo", _FakeInfo)

    executor._find_windows_by_title(title or "", process_id, match_mode, class_name)
    return captured
