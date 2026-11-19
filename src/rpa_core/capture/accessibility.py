"""目标应用「无障碍树休眠」的检测与唤醒。

**为什么需要**：微信 4.x 是 Qt 5.15 静态链接（无 ``platforms`` 目录，
``QAccessible`` 编在 ``Weixin.dll`` 里）。它的 ``QAccessible`` active gate
冷启动为 0，此时标准 UIA / MSAA 只拿得到 Qt 外壳——主窗 ``Descendants``
恰好 1 个 ``MMUIRenderSubWindowHW``，MSAA 侧 ``accChild`` 全 ``E_INVALIDARG``，
``QueryInterface(IAccessible2)`` 直接 ``E_NOINTERFACE``。捕获 UI 上表现成
「这个窗口里没有控件」，但**树并不是不存在**。

**做法**（对齐影刀 ``activity_weixin_v4/wechat.py``，明文可读）：
在目标 dll 里定位那个 active byte，热写为 1，再发 ``WM_GETOBJECT``——
当前进程立即开始返回 ``mmui::MainWindow`` provider，**无需重启**。
实测（微信 4.2.0.13）：写 1 字节后主窗 ``Descendants`` 1 → 118，
出现 ``mmui::MainView`` / ``mmui::MainTabBar`` / ``mmui::XTabBarButton``。

**边界**（不做的）：不注入 dll、不建 provider、不做图像匹配；只读/写
**一个字节**，写前记原值，可还原。写内存由调用方显式开启。
"""

from __future__ import annotations

import ctypes
import re
import struct
import threading
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

# ---- PE / 扫描常量 ---------------------------------------------------------

IMAGE_SCN_MEM_EXECUTE = 0x20000000
IMAGE_SCN_MEM_WRITE = 0x80000000

# Qt 的无障碍日志类别。用它锚定「本模块确实是个 Qt 二进制」，并给
# gate 候选做距离过滤（真正的 gate 与它在同一段无障碍代码里）。
CORE_STRING = b"qt.accessibility.core"

# `test rcx,rcx; jz ...; cmp byte ptr [rip+disp32], 0; jz ...`
GATE_PATTERN = re.compile(
    rb"\x48\x85\xc9\x0f\x84....\x80\x3d(?P<disp>.{4})\x00\x0f\x84", re.DOTALL
)

# `lea r, [rip+disp32]` 的操作码。xref 扫描先靠它做 memchr 定位，避免逐字节 Python 循环。
_LEA_OPCODE = b"\x8d"

# gate 与 core 字符串引用点必须落在同一段代码里；影刀取 0x20000，实测
# 4.2.0.13 的距离是 0x925（2026-11-18 用新旧两版实现逐项比对 refs 后确认）。
_MAX_CORE_DISTANCE = 0x20000


_ACCESS = 0x0008 | 0x0010 | 0x0020 | 0x0400  # VM_OPERATION | VM_READ | VM_WRITE | QUERY
_WM_GETOBJECT = 0x003D
_OBJID_CLIENT = -4
_SMTO_ABORTIFHUNG = 0x0002


@dataclass(frozen=True)
class PeSection:
    name: str
    rva: int
    vsize: int
    raw_ptr: int
    raw_size: int
    chars: int

    @property
    def executable(self) -> bool:
        return bool(self.chars & IMAGE_SCN_MEM_EXECUTE)

    @property
    def writable(self) -> bool:
        return bool(self.chars & IMAGE_SCN_MEM_WRITE)


@dataclass(frozen=True)
class GateScan:
    """一次 gate 定位的结果（纯数据，便于判据与日志）。"""

    rva: int | None
    candidate_count: int
    core_xref_count: int
    distance: int | None


@dataclass(frozen=True)
class WakeResult:
    """唤醒尝试的结构化结果。``reason`` 永远是封闭枚举，便于判据。"""

    ok: bool
    reason: str
    pid: int = 0
    dll: str = ""
    rva: int | None = None
    before: int | None = None
    after: int | None = None
    # 是否**尝试过** poke，以及 poke 有没有送达（``None`` = 没试，例如 ``poke=False``）。
    # ★ 单列一个字段而不是折进 ``ok``：gate 写成 1 是既成事实，poke 失败只说明
    #   「provider 没被叫醒」，两者要分开报（M98 实测微信回「拒绝访问」，日志却记 ok）。
    poked: bool | None = None

    REASONS = (
        "already_active",  # 读到的就是 1，无需写
        "activated",  # 写成功且读回校验通过
        "no_window",  # hwnd 无效
        "no_process",  # 取不到 pid / 打不开进程
        "no_dll",  # 进程里没有目标 dll
        "no_gate",  # 扫描不到 gate
        "read_failed",
        "write_failed",
    )


# ---- 纯函数：PE 解析与 gate 定位 -------------------------------------------

def pe_sections(data: bytes) -> list[PeSection]:
    """解析 PE 节表。非 PE / 截断一律返回空列表（调用方视为扫描失败）。"""
    if len(data) < 0x40 or data[:2] != b"MZ":
        return []
    e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    if e_lfanew + 0x18 > len(data) or data[e_lfanew:e_lfanew + 4] != b"PE\0\0":
        return []
    coff = e_lfanew + 4
    nsec = struct.unpack_from("<H", data, coff + 2)[0]
    opt_size = struct.unpack_from("<H", data, coff + 16)[0]
    sect_off = coff + 20 + opt_size
    out: list[PeSection] = []
    for i in range(nsec):
        base = sect_off + i * 40
        if base + 40 > len(data):
            break
        name = data[base:base + 8].rstrip(b"\0").decode("ascii", "ignore")
        vsize, vaddr, rawsize, rawptr = struct.unpack_from("<IIII", data, base + 8)
        chars = struct.unpack_from("<I", data, base + 36)[0]
        out.append(PeSection(name, vaddr, vsize, rawptr, rawsize, chars))
    return out


def section_for_rva(sections: list[PeSection], rva: int) -> PeSection | None:
    for sec in sections:
        span = max(sec.vsize, sec.raw_size)
        if sec.rva <= rva < sec.rva + span:
            return sec
    return None


def offset_to_rva(sections: list[PeSection], offset: int) -> int | None:
    for sec in sections:
        if sec.raw_ptr <= offset < sec.raw_ptr + sec.raw_size:
            return sec.rva + offset - sec.raw_ptr
    return None


def rip_xrefs_to(
    data: bytes,
    sections: list[PeSection],
    target_rva: int,
    *,
    rva_windows: list[tuple[int, int]] | None = None,
) -> list[int]:
    """可执行段里指向 ``target_rva`` 的 RIP 相对 LEA 引用点（RVA 列表）。

    ★ 用 ``bytes.find`` 定位 ``lea`` 操作码（C 层 memchr），只在命中处解析 disp。
      逐字节 Python 循环在 196 MB 的 Weixin.dll 上要 **14.5 秒**（2026-11-18 实测
      分项计时：读文件 0.11s / 本函数 14.58s / gate 正则 0.25s），而这一步正卡在
      hover 的第一帧上——用户侧看到的就是「点捕获后假死十几秒」。改后同一文件
      <1 秒，判定语义不变（同一批指令、同一起始 RVA）。

    ``rva_windows`` 把扫描限制在若干 RVA 区间里：调用方已经知道只需要「离某个
    位置不超过 D」的引用点时（``scan_gate_rva`` 的距离过滤），全段扫是在算一份
    绝大部分会被丢掉的结果——实测 196 MB 的 Weixin.dll 里 ``\\x8d`` 命中 **314 万**
    次、全扫 892 ms，而 gate 候选附近真正需要的窗口只有几百 KB。

    ★ 契约：区间只需是「所有可能被采用的引用点」的**超集**，多收不漏收即可——
      距离过滤仍由调用方做。区间按 RVA 给出（不是文件偏移），跨段区间由本函数
      与各可执行段求交；重叠区间命中的同一指令只记一次（结果去重排序）。
    """
    out: list[int] = []
    for sec in sections:
        if not sec.executable:
            continue
        seg_start = sec.raw_ptr
        seg_end = min(len(data), seg_start + sec.raw_size)
        if rva_windows is None:
            spans = [(seg_start, seg_end)]
        else:
            spans = []
            for lo_rva, hi_rva in rva_windows:
                lo = max(seg_start, seg_start + (lo_rva - sec.rva))
                hi = min(seg_end, seg_start + (hi_rva - sec.rva))
                if hi > lo:
                    spans.append((lo, hi))
        for raw_start, raw_end in spans:
            raw = data[raw_start:raw_end]
            # raw 里的偏移要换回 RVA：窗口起点可能不在段首，基准 RVA 随 raw_start 平移。
            base_rva = sec.rva + (raw_start - seg_start)
            pos = raw.find(_LEA_OPCODE)
            while pos >= 0:
                # ModRM mod=00, rm=101 ⇒ RIP 相对寻址（reg 字段任意）
                if pos + 2 <= len(raw) and (raw[pos + 1] & 0xC7) == 0x05:
                    rex = pos - 1
                    has_rex = rex >= 0 and 0x40 <= raw[rex] <= 0x4F
                    if has_rex:
                        # REX.W + lea r64, [rip+disp32]：指令从 REX 起算，共 7 字节
                        if rex + 7 <= len(raw):
                            disp = struct.unpack_from("<i", raw, rex + 3)[0]
                            if base_rva + rex + 7 + disp == target_rva:
                                out.append(base_rva + rex)
                    elif pos + 6 <= len(raw):
                        # lea r32, [rip+disp32]（不带 REX）共 6 字节。带 REX 的已在上面
                        # 算过，不能按 6 字节再算一次——否则同一指令的 xref 数翻倍。
                        disp = struct.unpack_from("<i", raw, pos + 2)[0]
                        if base_rva + pos + 6 + disp == target_rva:
                            out.append(base_rva + pos)
                pos = raw.find(_LEA_OPCODE, pos + 1)
    if rva_windows is not None:
        # 相邻/重叠的候选窗口会重复命中同一条指令，去重后才对得上「引用点个数」。
        return sorted(set(out))
    return out


def scan_gate_rva(data: bytes) -> GateScan:
    """在 dll 镜像里定位 ``QAccessible`` active byte 的 RVA。

    判据三层（缺一不可）：指令落在可执行段、disp 目标落在可写段、
    与该 dll 里 ``qt.accessibility.core`` 引用点同段且距离不超过阈值。
    多候选时取距离最近者——gate 和这个日志类别在源码上就是邻居。

    ★ 两遍扫描：第一遍只做**结构**粗筛（不需要 core 引用点），第二遍才在候选
      ± ``_MAX_CORE_DISTANCE`` 的窗口里找 core 引用点。原因是距离过滤的上限本身
      就决定了「窗口外的引用点不可能赢」，而全段找引用点在 196 MB 的 Weixin.dll
      上要 892 ms（``\\x8d`` 命中 314 万次）——那正是用户说的「首次校验约 2 秒」
      里最粗的一段。改成按候选开窗后，同一文件整体降到 ~200 ms。
    """
    sections = pe_sections(data)
    if not sections:
        return GateScan(None, 0, 0, None)

    core_off = data.find(CORE_STRING)
    core_rva = offset_to_rva(sections, core_off) if core_off >= 0 else None
    if core_rva is None:
        return GateScan(None, 0, 0, None)

    # 第一遍：结构粗筛（指令可执行 + disp 指向可写段），此时还不用 core 引用点。
    rough: list[tuple[int, int]] = []  # (指令 RVA, gate 目标 RVA)
    for match in GATE_PATTERN.finditer(data):
        match_rva = offset_to_rva(sections, match.start())
        disp_rva = offset_to_rva(sections, match.start("disp"))
        if match_rva is None or disp_rva is None:
            continue
        msec = section_for_rva(sections, match_rva)
        if msec is None or not msec.executable:
            continue
        cmp_rva = disp_rva - 2  # `80 3D <disp32>` 的起始
        disp = struct.unpack("<i", match.group("disp"))[0]
        target_rva = cmp_rva + 7 + disp
        tsec = section_for_rva(sections, target_rva)
        if tsec is None or not tsec.writable:
            continue
        rough.append((match_rva, target_rva))

    # 没有候选就没有必要去找引用点。
    if not rough:
        return GateScan(None, 0, 0, None)

    # 第二遍：只在候选附近找 core 引用点。窗口按 RVA 给，跨段由 rip_xrefs_to 求交。
    # 窗口边界就是阈值本身：距离 > 阈值的引用点会被下面过滤掉，不需要额外余量。
    radius = _MAX_CORE_DISTANCE
    core_xrefs = rip_xrefs_to(
        data, sections, core_rva,
        rva_windows=[(rva - radius, rva + radius) for rva, _ in rough],
    )

    # 没有 ``qt.accessibility.core`` 的引用点，就不做猜测。
    # 这条是安全取向：写错一个字节会打进别人的进程，宁可「不唤醒」也不能蒙。
    if not core_xrefs:
        return GateScan(None, 0, 0, None)

    best: tuple[int, int] | None = None
    count = 0
    for match_rva, target_rva in rough:
        distance = min(abs(match_rva - x) for x in core_xrefs)
        if distance > _MAX_CORE_DISTANCE:
            continue
        count += 1
        if best is None or distance < best[0]:
            best = (distance, target_rva)

    if best is None:
        return GateScan(None, count, len(core_xrefs), None)
    return GateScan(best[1], count, len(core_xrefs), best[0])


_SCAN_CACHE: dict[tuple[str, int, int], GateScan] = {}
_SCAN_LOCK = threading.Lock()


def gate_for_dll(dll_path: str | Path) -> GateScan:
    """按 (路径, 大小, mtime) 缓存扫描结果——dll 升级后自动失效。"""
    path = Path(dll_path)
    try:
        st = path.stat()
    except OSError:
        return GateScan(None, 0, 0, None)
    key = (str(path).lower(), st.st_size, int(st.st_mtime))
    with _SCAN_LOCK:
        hit = _SCAN_CACHE.get(key)
    if hit is not None:
        return hit
    try:
        data = path.read_bytes()
    except OSError:
        return GateScan(None, 0, 0, None)
    result = scan_gate_rva(data)
    with _SCAN_LOCK:
        _SCAN_CACHE[key] = result
    return result


# ---- 进程侧：模块、读写、唤醒 ---------------------------------------------

class _MODULEINFO(ctypes.Structure):
    _fields_ = [
        ("lpBaseOfDll", ctypes.c_void_p),
        ("SizeOfImage", wintypes.DWORD),
        ("EntryPoint", ctypes.c_void_p),
    ]


def _kernel32():
    return ctypes.windll.kernel32


def _psapi():
    """psapi 句柄/指针一律显式声明——64 位下不声明会按 32 位截断（OverflowError）。"""
    psapi = ctypes.windll.psapi
    psapi.EnumProcessModules.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p),
        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
    ]
    psapi.EnumProcessModules.restype = wintypes.BOOL
    psapi.GetModuleFileNameExW.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, wintypes.LPWSTR, wintypes.DWORD,
    ]
    psapi.GetModuleFileNameExW.restype = wintypes.DWORD
    psapi.GetModuleInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(_MODULEINFO), wintypes.DWORD,
    ]
    psapi.GetModuleInformation.restype = wintypes.BOOL
    return psapi


def open_process(pid: int) -> int:
    """打开目标进程（读写 + 查询）。失败返回 0。"""
    k32 = _kernel32()
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.OpenProcess.restype = wintypes.HANDLE
    handle = k32.OpenProcess(_ACCESS, False, int(pid))
    return int(handle or 0)


def close_handle(handle: int) -> None:
    if handle:
        _kernel32().CloseHandle(wintypes.HANDLE(handle))


def module_base_and_path(pid: int, dll_name: str) -> tuple[int, str] | None:
    """在目标进程里找 ``dll_name`` 的 (基址, 全路径)。找不到返回 None。"""
    psapi = _psapi()
    handle = open_process(pid)
    if not handle:
        return None
    try:
        mods = (ctypes.c_void_p * 1024)()
        needed = wintypes.DWORD()
        if not psapi.EnumProcessModules(
            wintypes.HANDLE(handle), mods, ctypes.sizeof(mods), ctypes.byref(needed)
        ):
            return None
        count = needed.value // ctypes.sizeof(ctypes.c_void_p)
        buf = ctypes.create_unicode_buffer(1024)
        for i in range(count):
            if not psapi.GetModuleFileNameExW(
                wintypes.HANDLE(handle), mods[i], buf, 1024
            ):
                continue
            path = buf.value
            if not path.lower().endswith(dll_name.lower()):
                continue
            info = _MODULEINFO()
            if not psapi.GetModuleInformation(
                wintypes.HANDLE(handle), mods[i], ctypes.byref(info),
                ctypes.sizeof(info),
            ):
                continue
            return int(info.lpBaseOfDll or 0), path
        return None
    finally:
        close_handle(handle)


def read_byte(pid: int, address: int) -> int | None:
    handle = open_process(pid)
    if not handle:
        return None
    try:
        buf = (ctypes.c_ubyte * 1)()
        got = ctypes.c_size_t(0)
        ok = _kernel32().ReadProcessMemory(
            wintypes.HANDLE(handle), ctypes.c_void_p(address), buf, 1,
            ctypes.byref(got),
        )
        return int(buf[0]) if ok and got.value == 1 else None
    finally:
        close_handle(handle)


def write_byte(pid: int, address: int, value: int) -> bool:
    handle = open_process(pid)
    if not handle:
        return False
    try:
        buf = (ctypes.c_ubyte * 1)(value & 0xFF)
        wrote = ctypes.c_size_t(0)
        ok = _kernel32().WriteProcessMemory(
            wintypes.HANDLE(handle), ctypes.c_void_p(address), buf, 1,
            ctypes.byref(wrote),
        )
        return bool(ok and wrote.value == 1)
    finally:
        close_handle(handle)


def poke_provider(hwnd: int) -> bool:
    """发 WM_GETOBJECT(OBJID_CLIENT) 让目标重建 provider。

    gate 已置 1 时它立刻返回 ``mmui::MainWindow``；为 0 时只回 Qt 外壳。

    返回**是否送达**（``SendMessageTimeoutW`` 非零）。

    ★ 以前不看返回值：对微信实测返回 0（``GetLastError=5`` 拒绝访问），日志里
      仍写 ``ok=true``，排查时把「provider 根本没响应」误读成「provider 建好了但
      没有节点」（M98 走了半天弯路）。现在失败与否由调用方记进 ``WakeResult.poked``。
    """
    user32 = ctypes.windll.user32
    user32.SendMessageTimeoutW.restype = ctypes.c_size_t
    user32.SendMessageTimeoutW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
        wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t),
    ]
    result = ctypes.c_size_t(0)
    sent = user32.SendMessageTimeoutW(
        wintypes.HWND(hwnd), _WM_GETOBJECT, wintypes.WPARAM(0),
        wintypes.LPARAM(_OBJID_CLIENT), _SMTO_ABORTIFHUNG, 2000,
        ctypes.byref(result),
    )
    return bool(sent)


def window_class_name(hwnd: int) -> str:
    """取窗口类名（取不到给空串）。唤醒前的「像不像 Qt 壳」判断用它。"""
    buf = ctypes.create_unicode_buffer(256)
    ctypes.windll.user32.GetClassNameW(wintypes.HWND(int(hwnd)), buf, 256)
    return buf.value


def wake_if_dormant(
    hwnd: int, *, dll_name: str = "Weixin.dll", poke: bool = True
) -> WakeResult | None:
    """若 ``hwnd`` 是「Qt 窗口壳」，尝试唤醒它的无障碍树。

    与 ``desktop_agent._maybe_wake_accessibility`` 的分工：那边管**捕获**（带节流、
    hopeless 缓存、状态翻转 trace，每秒都可能被调），这里给**其它一次性入口**
    （如元素校验）用——只看类名决定要不要动手，然后交给同一个 :func:`activate`。

    返回 ``None`` 表示「不是休眠候选，什么都没动」；返回 ``WakeResult`` 表示真试过，
    调用方据 ``ok``/``reason``/``poked`` 决定日志与提示。

    ★ 顺序很重要：必须在**第一次 UIA 查询之前**调用。微信 4.x 的 QAccessible 只在
      进程首次被 UIA 查询时决定建不建树，查过之后再写 gate 就晚了（M98 实测
      「写 gate + poke + 重建窗口」全部无效）。
    """
    if not hwnd:
        return None
    try:
        class_name = window_class_name(hwnd)
    except Exception:  # noqa: BLE001 - 唤醒是增强路径，取类名失败就当不是候选
        return None
    if not looks_dormant(class_name, 1):
        return None
    return activate(hwnd, dll_name=dll_name, poke=poke)


def _pid_of_window(hwnd: int) -> int:
    pid = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
    return int(pid.value)


def looks_dormant(class_name: str, child_count: int) -> bool:
    """「Qt 外壳 + 没有更细的节点」= 无障碍树疑似休眠。

    只作**候选判定**：真正要不要写，还要看 gate 当前字节是不是 0。
    非 Qt 窗口（很多原生应用本来就只有一层）不算，避免误伤。
    """
    if child_count > 1:
        return False
    name = class_name or ""
    return name.startswith("Qt") and name.endswith("WindowIcon")


def activate(hwnd: int, *, dll_name: str = "Weixin.dll", poke: bool = True) -> WakeResult:
    """把目标窗口所在进程的 QAccessible gate 打开（写 1 + 唤醒 provider）。

    幂等：已经是 1 就只唤醒、不再写。任何一步失败都带封闭的 ``reason`` 返回，
    调用方据此决定提示文案——**不允许静默**。

    ``poke=False`` 只跳过「gate 已经是 1」时的 ``WM_GETOBJECT``：保活路径每隔一两秒确认
    一次，若每次都让目标重建 provider 树（微信一百多个节点）会平白增加对方开销。
    **真正写过字节时永远会 poke**——否则 provider 不会重建，写了等于没写。
    """
    if not hwnd:
        return WakeResult(False, "no_window")
    pid = _pid_of_window(hwnd)
    if not pid:
        return WakeResult(False, "no_process")
    found = module_base_and_path(pid, dll_name)
    if found is None:
        return WakeResult(False, "no_dll", pid=pid)
    base, dll_path = found
    scan = gate_for_dll(dll_path)
    if scan.rva is None:
        return WakeResult(False, "no_gate", pid=pid, dll=dll_path)
    address = base + scan.rva
    before = read_byte(pid, address)
    if before is None:
        return WakeResult(False, "read_failed", pid, dll_path, scan.rva)
    if before == 1:
        poked = bool(poke_provider(hwnd)) if poke else None
        return WakeResult(True, "already_active", pid, dll_path, scan.rva, 1, 1, poked)
    if not write_byte(pid, address, 1):
        return WakeResult(False, "write_failed", pid, dll_path, scan.rva, before)
    after = read_byte(pid, address)
    if after != 1:
        return WakeResult(False, "write_failed", pid, dll_path, scan.rva, before, after)
    # 写过字节就必须唤醒 provider——即使调用方说「稳态别打扰」。
    poked = bool(poke_provider(hwnd))
    return WakeResult(True, "activated", pid, dll_path, scan.rva, before, after, poked)


def restore(result: WakeResult) -> bool:
    """把 gate 写回唤醒前的值（``activated`` 才有意义）。"""
    if not result.ok or result.reason != "activated" or result.before is None:
        return False
    if not result.pid or result.rva is None:
        return False
    found = module_base_and_path(result.pid, "Weixin.dll")
    if found is None:
        return False
    return write_byte(result.pid, found[0] + result.rva, result.before)
