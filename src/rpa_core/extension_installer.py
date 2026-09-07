"""浏览器扩展静默安装器（方案与竞品调证见 docs/extension-install.md）。

两条 Windows 安装通道：
- 外部扩展注册表（``Software\\<vendor>\\<browser>\\Extensions\\<id>`` 写
  path+version，指向本地 CRX）。浏览器启动时由 external_registry_loader 消费，
  静默安装本地 CRX。免管理员、免商店上架、免 devserver 在线，不受未加域
  forcelist 门控（[BLOCKED]）影响。影刀/K-RPA 即此通道。
  **注意**：Edge 152+ 消费版将此类安装按"未知来源"硬禁用（启用开关灰色），
  零点击启用需扩展上架商店（实验矩阵见 docs §6.4）。
- ``--policy``：ExtensionInstallForcelist 策略（受管环境强制安装、不可卸载）。
  未加域机器仅接受商店来源；HKCU 被加固时单次 UAC 提权写 HKLM 自动降级。

打包：用本机 Chromium 浏览器把 extension/ 打成 CRX（签名私钥持久化在 build
目录，重打包 ID 稳定）。devserver 另行托管 update manifest XML + CRX（官方
策略允许 http scheme，CRX 必须以 application/x-chrome-extension 返回），
供策略通道的 update_url 与运行期诊断使用。
"""

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DEVSERVER_ORIGIN = "http://127.0.0.1:8765"
CRX_MIME = "application/x-chrome-extension"
UPDATE_MANIFEST_PATH = "/api/extension/update-manifest"
CRX_PATH = "/api/extension/crx"
FORCELIST_KEY = "ExtensionInstallForcelist"

_PACK_TIMEOUT_SECONDS = 60.0
_TAG_INTEGER = 0x02
_TAG_BIT_STRING = 0x03
_TAG_OCTET_STRING = 0x04
_TAG_NULL = 0x05
_TAG_SEQUENCE = 0x30
_RSA_OID = bytes.fromhex("06092A864886F70D010101")


class ExtensionInstallError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PackedExtension:
    crx_path: Path
    pem_path: Path
    extension_id: str
    version: str


# -- 路径解析 -----------------------------------------------------------------


def extension_root() -> Path:
    """扩展源码目录：pip 安装后读包内打包的 extension/，开发态读仓库根。"""
    packaged = Path(__file__).resolve().parent / "extension"
    if packaged.is_dir():
        return packaged
    return Path(__file__).resolve().parents[2] / "extension"


def default_build_dir() -> Path:
    """打包产物目录（与 cwd 无关）：~/.rpa-core/extension-build。"""
    return Path.home() / ".rpa-core" / "extension-build"


def _browser_binary_candidates(browser: str) -> list[Path]:
    if browser == "chrome":
        relative = Path("Google") / "Chrome" / "Application" / "chrome.exe"
    else:
        relative = Path("Microsoft") / "Edge" / "Application" / "msedge.exe"
    candidates = []
    for env in ("ProgramFiles", "ProgramFiles(x86)", "LocalAppData"):
        base = os.environ.get(env)
        if base:
            candidates.append(Path(base) / relative)
    return candidates


def find_browser_binary() -> Path | None:
    for browser in ("chrome", "edge"):
        for path in _browser_binary_candidates(browser):
            if path.is_file():
                return path
    return None


def detect_browsers() -> dict[str, bool]:
    """报告 Chrome/Edge 是否存在可执行文件（仅展示用，策略写入不受其影响）。"""
    return {
        browser: any(path.is_file() for path in _browser_binary_candidates(browser))
        for browser in ("chrome", "edge")
    }


# -- 扩展 ID：SHA-256(SPKI DER) 前 32 个 hex 字符映射到 a-p -------------------


def extension_id_from_key_der(spki_der: bytes) -> str:
    digest = hashlib.sha256(spki_der).hexdigest()
    return "".join(chr(ord("a") + int(char, 16)) for char in digest[:32])


def _read_der_length(data: bytes, offset: int) -> tuple[int, int]:
    first = data[offset]
    offset += 1
    if first < 0x80:
        return first, offset
    count = first & 0x7F
    return int.from_bytes(data[offset:offset + count], "big"), offset + count


def _der_children(data: bytes) -> list[tuple[int, bytes]]:
    children = []
    offset = 0
    while offset < len(data):
        tag = data[offset]
        offset += 1
        length, offset = _read_der_length(data, offset)
        children.append((tag, data[offset:offset + length]))
        offset += length
    return children


def _der_encode(tag: int, content: bytes) -> bytes:
    length = len(content)
    if length < 0x80:
        encoded_length = bytes([length])
    else:
        raw = length.to_bytes((length.bit_length() + 7) // 8, "big")
        encoded_length = bytes([0x80 | len(raw)]) + raw
    return bytes([tag]) + encoded_length + content


def _der_integer(raw: bytes) -> bytes:
    value = raw.lstrip(b"\x00") or b"\x00"
    if value[0] & 0x80:
        value = b"\x00" + value
    return _der_encode(_TAG_INTEGER, value)


def public_key_der_from_pem(pem_text: str) -> bytes:
    """从 RSA 私钥 PEM（PKCS#1 或 PKCS#8）重建 SubjectPublicKeyInfo DER。"""
    payload = "".join(
        line.strip()
        for line in pem_text.splitlines()
        if line.strip() and not line.strip().startswith("-----")
    )
    der = base64.b64decode(payload)
    top = _der_children(der)[0][1]
    children = _der_children(top)
    if children[1][0] == _TAG_SEQUENCE:
        # PKCS#8 PrivateKeyInfo：第三个元素 OCTET STRING 内含 PKCS#1 RSAPrivateKey
        pkcs1 = _der_children(children[2][1])[0][1]
        children = _der_children(pkcs1)
    modulus, exponent = children[1][1], children[2][1]
    rsa_public_key = _der_encode(_TAG_SEQUENCE, _der_integer(modulus) + _der_integer(exponent))
    algorithm = _der_encode(_TAG_SEQUENCE, _RSA_OID + _der_encode(_TAG_NULL, b""))
    subject_public_key = _der_encode(_TAG_BIT_STRING, b"\x00" + rsa_public_key)
    return _der_encode(_TAG_SEQUENCE, algorithm + subject_public_key)


def extension_id_from_pem(pem_text: str | bytes) -> str:
    if isinstance(pem_text, bytes):
        pem_text = pem_text.decode("ascii")
    return extension_id_from_key_der(public_key_der_from_pem(pem_text))


# -- CRX 打包 -----------------------------------------------------------------


def _extension_version(extension_dir: Path) -> str:
    manifest = json.loads((extension_dir / "manifest.json").read_text(encoding="utf-8"))
    return str(manifest["version"])


def pack_extension(extension_dir: Path, build_dir: Path) -> PackedExtension:
    """打包 CRX；私钥持久化到 build 目录保证重打包 ID 稳定。需要本机 Chromium。"""
    binary = find_browser_binary()
    if binary is None:
        existing = load_packed_extension(build_dir)
        if existing is not None:
            return existing
        raise ExtensionInstallError("NO_BROWSER", "未找到 Chrome/Edge，无法打包扩展")
    build_dir.mkdir(parents=True, exist_ok=True)
    pem_path = build_dir / "extension.pem"
    staging_root = build_dir / ".pack"
    staging = staging_root / "extension"
    shutil.rmtree(staging_root, ignore_errors=True)
    staging.mkdir(parents=True)
    for source in extension_dir.rglob("*"):
        if source.is_file():
            target = staging / source.relative_to(extension_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    command = [str(binary), f"--pack-extension={staging}"]
    if pem_path.exists():
        command.append(f"--pack-extension-key={pem_path}")
    try:
        completed = subprocess.run(
            command, capture_output=True, timeout=_PACK_TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired as exc:
        raise ExtensionInstallError("PACK_FAILED", f"打包超时: {binary}") from exc
    produced_crx = staging_root / "extension.crx"
    if completed.returncode != 0 or not produced_crx.exists():
        shutil.rmtree(staging_root, ignore_errors=True)
        raise ExtensionInstallError("PACK_FAILED", f"打包失败: {binary}")
    crx_path = build_dir / "extension.crx"
    shutil.move(str(produced_crx), str(crx_path))
    if not pem_path.exists():
        produced_pem = staging_root / "extension.pem"
        if not produced_pem.exists():
            shutil.rmtree(staging_root, ignore_errors=True)
            raise ExtensionInstallError("PACK_FAILED", "打包未产出 .pem 私钥")
        shutil.move(str(produced_pem), str(pem_path))
    shutil.rmtree(staging_root, ignore_errors=True)
    return PackedExtension(
        crx_path, pem_path, extension_id_from_pem(pem_path.read_text(encoding="utf-8")),
        _extension_version(extension_dir),
    )


def load_packed_extension(build_dir: Path) -> PackedExtension | None:
    crx_path = build_dir / "extension.crx"
    pem_path = build_dir / "extension.pem"
    if not (crx_path.is_file() and pem_path.is_file()):
        return None
    return PackedExtension(
        crx_path, pem_path, extension_id_from_pem(pem_path.read_text(encoding="utf-8")),
        _extension_version(extension_root()),
    )


# -- update manifest XML（devserver 托管）-------------------------------------


def update_manifest_xml(packed: PackedExtension, codebase_url: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gupdate xmlns="http://www.google.com/update2/response" protocol="2.0">\n'
        f'  <app appid="{packed.extension_id}">\n'
        f'    <updatecheck codebase="{codebase_url}" version="{packed.version}" />\n'
        "  </app>\n"
        "</gupdate>\n"
    )


# -- HKCU ExtensionInstallForcelist 策略 --------------------------------------

BROWSER_POLICY_ROOTS = {
    "chrome": r"Software\Policies\Google\Chrome",
    "edge": r"Software\Policies\Microsoft\Edge",
}


def _enum_values(winreg, key) -> list[tuple[str, str]]:
    entries = []
    index = 0
    while True:
        try:
            name, data, _ = winreg.EnumValue(key, index)
        except OSError:
            return entries
        entries.append((str(name), str(data)))
        index += 1


def write_forcelist_entry(winreg, policy_root: str, value: str, hive=None) -> str:
    """追加一条强制安装策略；同值已存在时幂等跳过。返回 added / already。"""
    if hive is None:
        hive = winreg.HKEY_CURRENT_USER
    key = winreg.CreateKeyEx(hive, f"{policy_root}\\{FORCELIST_KEY}", 0,
                             winreg.KEY_READ | winreg.KEY_WRITE)
    try:
        entries = _enum_values(winreg, key)
        if value in (data for _, data in entries):
            return "already"
        used = {name for name, _ in entries}
        index = 1
        while str(index) in used:
            index += 1
        winreg.SetValueEx(key, str(index), 0, winreg.REG_SZ, value)
        return "added"
    finally:
        key.Close()


def remove_forcelist_entries(
    winreg,
    policy_root: str,
    extension_id: str | None = None,
    update_url: str | None = None,
    hive=None,
) -> int:
    """删除匹配的策略条目（按扩展 ID 前缀或 update URL 后缀），返回删除数。"""
    if hive is None:
        hive = winreg.HKEY_CURRENT_USER
    try:
        key = winreg.OpenKey(hive, f"{policy_root}\\{FORCELIST_KEY}", 0,
                             winreg.KEY_READ | winreg.KEY_WRITE)
    except OSError:
        return 0
    try:
        removed = 0
        for name, data in _enum_values(winreg, key):
            if extension_id is not None and data.split(";", 1)[0].strip() == extension_id:
                winreg.DeleteValue(key, name)
                removed += 1
            elif extension_id is None and update_url is not None and data.endswith(update_url):
                winreg.DeleteValue(key, name)
                removed += 1
        return removed
    finally:
        key.Close()


def _winreg_module():
    import winreg

    return winreg


def _read_external_registry_entry_status(browser: str, extension_id: str) -> dict | None:
    """只读外部扩展注册表条目（path/version）；非 win32 或无条目返回 None。"""
    external_root = BROWSER_EXTERNAL_ROOTS.get(browser)
    if external_root is None or sys.platform != "win32":
        return None
    try:
        winreg = _winreg_module()
    except ImportError:
        return None
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, f"{external_root}\\{extension_id}", 0, winreg.KEY_READ
        )
    except OSError:
        return None
    try:
        values = dict(_enum_values(winreg, key))
    finally:
        key.Close()
    if not values:
        return None
    return {"path": values.get("path"), "version": values.get("version")}


def install_policy_entry(
    extension_id: str, update_url: str, *, elevated: bool = False
) -> dict[str, str]:
    """对 Chrome/Edge 写入强制安装策略（默认 HKCU；elevated=True 写 HKLM，需管理员）。"""
    if sys.platform != "win32":
        raise ExtensionInstallError(
            "PLATFORM_UNSUPPORTED", "静默安装当前仅支持 Windows（macOS/Linux 需 root/MDM，暂缓）"
        )
    winreg = _winreg_module()
    hive = winreg.HKEY_LOCAL_MACHINE if elevated else winreg.HKEY_CURRENT_USER
    value = f"{extension_id};{update_url}"
    results = {}
    try:
        for browser, policy_root in BROWSER_POLICY_ROOTS.items():
            results[browser] = write_forcelist_entry(winreg, policy_root, value, hive=hive)
    except PermissionError as exc:
        raise ExtensionInstallError("REGISTRY_DENIED", f"写注册表失败: {exc}") from exc
    return results


def remove_policy_entries(
    extension_id: str | None = None, update_url: str | None = None
) -> dict[str, int]:
    if sys.platform != "win32":
        raise ExtensionInstallError(
            "PLATFORM_UNSUPPORTED", "静默安装当前仅支持 Windows（macOS/Linux 需 root/MDM，暂缓）"
        )
    winreg = _winreg_module()
    removed = {}
    for browser, policy_root in BROWSER_POLICY_ROOTS.items():
        count = 0
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                count += remove_forcelist_entries(
                    winreg, policy_root, extension_id, update_url, hive=hive
                )
            except PermissionError:
                pass
        removed[browser] = count
    return removed


# -- HKCU 外部扩展注册表（external_registry_loader 通道；默认路线）-------------
# 影刀/K-RPA 同款机制：HKCU\Software\<vendor>\<browser>\Extensions\<id> 写
# path（本地 CRX 绝对路径）+ version，浏览器启动时静默安装，无需网络/商店/管理员。

BROWSER_EXTERNAL_ROOTS = {
    "chrome": r"Software\Google\Chrome\Extensions",
    "edge": r"Software\Microsoft\Edge\Extensions",
}


def write_external_registry_entry(
    winreg, external_root: str, extension_id: str, crx_path: Path, version: str, hive=None
) -> str:
    """写 ``<root>\\<id>`` 子键的 path/version；内容相同幂等。返回 added/updated/already。"""
    if hive is None:
        hive = winreg.HKEY_CURRENT_USER
    key = winreg.CreateKeyEx(
        hive, f"{external_root}\\{extension_id}", 0, winreg.KEY_READ | winreg.KEY_WRITE
    )
    try:
        existing = dict(_enum_values(winreg, key))
        if existing.get("path") == str(crx_path) and existing.get("version") == version:
            return "already"
        winreg.SetValueEx(key, "path", 0, winreg.REG_SZ, str(crx_path))
        winreg.SetValueEx(key, "version", 0, winreg.REG_SZ, version)
        return "updated" if existing else "added"
    finally:
        key.Close()


def remove_external_registry_entry(
    winreg, external_root: str, extension_id: str, hive=None
) -> int:
    """删除 ``<root>\\<id>`` 子键；不存在时 no-op。返回 0/1。"""
    if hive is None:
        hive = winreg.HKEY_CURRENT_USER
    try:
        winreg.DeleteKey(hive, f"{external_root}\\{extension_id}")
        return 1
    except OSError:
        return 0


def install_external_registry_entry(
    extension_id: str,
    crx_path: Path,
    version: str,
    browsers: tuple[str, ...] = ("chrome", "edge"),
) -> dict[str, str]:
    """对指定浏览器（默认 Chrome+Edge）各写一条 HKCU 外部扩展注册表；
    浏览器下次启动即静默安装本地 CRX。返回 {browser: added/updated/already}。"""
    if sys.platform != "win32":
        raise ExtensionInstallError(
            "PLATFORM_UNSUPPORTED", "静默安装当前仅支持 Windows（macOS/Linux 需 root/MDM，暂缓）"
        )
    winreg = _winreg_module()
    results = {}
    try:
        for browser, external_root in BROWSER_EXTERNAL_ROOTS.items():
            if browser not in browsers:
                continue
            results[browser] = write_external_registry_entry(
                winreg, external_root, extension_id, crx_path, version
            )
    except PermissionError as exc:
        raise ExtensionInstallError("REGISTRY_DENIED", f"写注册表失败: {exc}") from exc
    return results


def remove_external_registry_entries(extension_id: str) -> dict[str, int]:
    """双浏览器、HKCU/HKLM 双 hive 清理外部扩展注册表子键，返回各浏览器删除数。"""
    if sys.platform != "win32":
        raise ExtensionInstallError(
            "PLATFORM_UNSUPPORTED", "静默安装当前仅支持 Windows（macOS/Linux 需 root/MDM，暂缓）"
        )
    winreg = _winreg_module()
    removed = {}
    for browser, external_root in BROWSER_EXTERNAL_ROOTS.items():
        count = 0
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                count += remove_external_registry_entry(
                    winreg, external_root, extension_id, hive=hive
                )
            except PermissionError:
                pass
        removed[browser] = count
    return removed


# -- 状态检测（extension-status：注册表 + 浏览器 profile 只读探针）-------------

# profile 的 Secure Preferences 里，扩展记录通常在 extensions.settings.<id>；
# enabled 判定：该记录无 disable_reasons 且（有 ack_external 或 state==1）——
# 对应实测「启用后」终态（Chrome/Edge 152，docs §6.5）。


def browser_user_data_dirs(browser: str) -> list[Path]:
    """返回某浏览器 User Data 目录候选（本地用户目录；不存在的目录剔除）。"""
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if not local:
            return []
        relative = {
            "chrome": Path("Google") / "Chrome" / "User Data",
            "edge": Path("Microsoft") / "Edge" / "User Data",
        }.get(browser)
        if relative is None:
            return []
        return [Path(local) / relative]
    home = Path.home()
    relative = {
        "chrome": Path(".config/google-chrome"),
        "edge": Path(".config/microsoft-edge"),
    }.get(browser)
    return [home / relative] if relative else []


def _profile_prefs_json(profile_dir: Path, filename: str) -> dict:
    """读 profile 下的 JSON 偏好文件；缺失/损坏返回空 dict。"""
    path = profile_dir / filename
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def read_profile_extension_state(
    user_data_dir: Path, extension_id: str
) -> list[dict]:
    """只读扫描浏览器 profile，返回该扩展在各 profile 的状态。

    每项：{"profile": 目录名, "installed": bool, "enabled": bool,
    "disable_reasons": [...], "uninstall_blocked": bool}。
    installed/enabled/disable_reasons 来自 Secure Preferences 的 extensions.settings；
    uninstall_blocked 来自 Preferences 的 extensions.external_uninstalls
    （Chromium：外部扩展曾被用户卸载后，external_registry_loader 会永久跳过重装，
    即使注册表条目仍在——见 docs/extension-install.md 排查）。
    文件缺失/不可解析的 profile 跳过（不抛错）。
    """
    states: list[dict] = []
    if not user_data_dir.is_dir():
        return states
    for child in sorted(user_data_dir.iterdir()):
        if not child.is_dir():
            continue
        secure_path = child / "Secure Preferences"
        if not secure_path.is_file():
            continue  # 非真实 profile（无 Secure Preferences 的目录不探测）
        secure = _profile_prefs_json(child, "Secure Preferences")
        settings = ((secure.get("extensions") or {}).get("settings") or {}).get(
            extension_id
        )
        regular = _profile_prefs_json(child, "Preferences")
        uninstalled = (regular.get("extensions") or {}).get(
            "external_uninstalls"
        ) or []
        blocked = extension_id in uninstalled
        if settings is None and not blocked:
            continue  # 该 profile 无此扩展记录，也不在卸载跳过名单——跳过
        disable = (settings or {}).get("disable_reasons") or []
        states.append(
            {
                "profile": child.name,
                "installed": settings is not None,
                "enabled": settings is not None and not disable and bool(
                    settings.get("ack_external") or settings.get("state") == 1
                ),
                "disable_reasons": disable,
                "uninstall_blocked": blocked,
            }
        )
    return states


def read_unpacked_extension_state(
    user_data_dir: Path, extension_dir: Path
) -> list[dict]:
    """检测「开发者模式 Load unpacked」加载状态：按扩展源码目录路径匹配。

    开发者模式加载的扩展 ID 由目录路径派生（非 pem 密钥），无法用 extension_id
    匹配；且 Chrome/Edge 在 Secure Preferences 中不保存 unpacked 扩展的 manifest
    （manifest 为 None），故改为匹配记录里的 ``path`` == 本扩展源码目录
    （location==4 unpacked）。返回每 profile：
    {"profile", "installed", "enabled", "location"}。
    """
    target = str(extension_dir.resolve())
    states: list[dict] = []
    if not extension_dir.is_dir() or not user_data_dir.is_dir():
        return states
    for child in sorted(user_data_dir.iterdir()):
        if not child.is_dir():
            continue
        if not (child / "Secure Preferences").is_file():
            continue
        secure = _profile_prefs_json(child, "Secure Preferences")
        settings = (secure.get("extensions") or {}).get("settings") or {}
        matched = [
            record for record in settings.values()
            if record.get("location") == 4
            and str(record.get("path") or "").lower() == target.lower()
        ]
        if not matched:
            continue
        record = matched[0]
        disable = record.get("disable_reasons") or []
        states.append({
            "profile": child.name,
            "installed": True,
            "enabled": not disable,
            "location": record.get("location"),
        })
    return states


def open_path_in_explorer(path: Path) -> bool:
    """在系统文件管理器中打开指定目录（定位/选中）。跨平台 best-effort。"""
    import webbrowser

    if sys.platform == "win32":
        # explorer /select 打开父目录并选中目标
        try:
            subprocess.Popen(
                ["explorer", "/select,", str(path)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return True
        except OSError:
            return False
    # macOS/Linux：open/xdg-open 直接打开目录（无选中能力）
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    try:
        subprocess.Popen(
            [opener, str(path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return True
    except OSError:
        try:
            webbrowser.open(path.as_uri())
            return True
        except Exception:
            return False


def open_browser(browser: str) -> bool:
    """启动浏览器（不带 URL，新窗口/既有实例聚焦）。"""
    for candidate in _browser_binary_candidates(browser):
        if candidate.is_file():
            try:
                subprocess.Popen(
                    [str(candidate)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                return True
            except OSError:
                return False
    return False


def extension_status(
    extension_id: str,
    build_dir: Path | None = None,
    extension_dir: Path | None = None,
) -> dict:
    """报告每浏览器安装状态：binary/registry 条目/profile 已装与已启用 +
    开发者模式(Load unpacked)加载状态。

    纯只读；build_dir/extension_dir 缺省时对应探测项为空（packed 为 None）。
    extension_dir 给定后按 manifest.name 匹配开发者模式加载（location 4）。
    """
    packed = load_packed_extension(build_dir) if build_dir is not None else None
    packed_info = None
    if packed is not None:
        packed_info = {
            "extensionId": packed.extension_id,
            "version": packed.version,
            "crxPath": str(packed.crx_path),
        }
    browsers: dict[str, dict] = {}
    for browser in ("chrome", "edge"):
        registry = _read_external_registry_entry_status(browser, extension_id)
        profiles: list[dict] = []
        for user_data in browser_user_data_dirs(browser):
            profiles.extend(read_profile_extension_state(user_data, extension_id))
        unpacked_profiles: list[dict] = []
        for user_data in browser_user_data_dirs(browser):
            if extension_dir is not None:
                unpacked_profiles.extend(
                    read_unpacked_extension_state(user_data, extension_dir)
                )
        browsers[browser] = {
            "binary": any(
                path.is_file() for path in _browser_binary_candidates(browser)
            ),
            "registryEntry": registry,
            "profiles": profiles,
            "unpackedProfiles": unpacked_profiles,
            "installed": any(item["installed"] for item in profiles)
                or any(item["installed"] for item in unpacked_profiles),
            "enabled": any(item["enabled"] for item in profiles)
                or any(item["enabled"] for item in unpacked_profiles),
            "uninstallBlocked": any(
                item.get("uninstall_blocked") for item in profiles
            ),
        }
    return {"packed": packed_info, "browsers": browsers}


def clear_uninstall_block(
    extension_id: str, browsers: tuple[str, ...] = ("chrome", "edge")
) -> dict[str, list[str]]:
    """从指定浏览器各 profile 的 Preferences.extensions.external_uninstalls 移除本扩展 ID。

    仅在浏览器**已关闭**时调用有效（浏览器运行中会覆写该文件）。返回
    {browser: [被清除的 profile 目录名]}。
    """
    cleared: dict[str, list[str]] = {"chrome": [], "edge": []}
    for browser, dirs in (("chrome", browser_user_data_dirs("chrome")),
                          ("edge", browser_user_data_dirs("edge"))):
        if browser not in browsers:
            continue
        for user_data in dirs:
            if not user_data.is_dir():
                continue
            for child in sorted(user_data.iterdir()):
                if not child.is_dir():
                    continue
                prefs_path = child / "Preferences"
                if not prefs_path.is_file():
                    continue
                prefs = _profile_prefs_json(child, "Preferences")
                ext_prefs = prefs.get("extensions")
                if not isinstance(ext_prefs, dict):
                    continue
                uninstalled = ext_prefs.get("external_uninstalls")
                if not isinstance(uninstalled, list) or extension_id not in uninstalled:
                    continue
                uninstalled = [item for item in uninstalled if item != extension_id]
                if uninstalled:
                    ext_prefs["external_uninstalls"] = uninstalled
                else:
                    ext_prefs.pop("external_uninstalls", None)
                prefs_path.write_text(
                    json.dumps(prefs, ensure_ascii=False), encoding="utf-8"
                )
                cleared[browser].append(child.name)
    return cleared


_BROWSER_PROCESSES = {"chrome": "chrome.exe", "edge": "msedge.exe"}


def browser_running(browser: str) -> bool:
    """目标浏览器当前是否有进程在运行（win32；其他平台一律 False）。

    仅用于判断"卸载屏蔽清除是否可靠/loader 是否要等重启"——运行中的浏览器
    会在退出时用内存副本覆写 Preferences，导致清除被冲掉。
    """
    if sys.platform != "win32":
        return False
    exe = _BROWSER_PROCESSES.get(browser)
    if exe is None:
        return False
    try:
        completed = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {exe}"],
            capture_output=True, text=True, timeout=10, encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return exe.lower() in completed.stdout.lower()


def install_external_guided(
    extension_id: str,
    crx_path: Path,
    version: str,
    browsers: tuple[str, ...] = ("chrome", "edge"),
) -> dict:
    """对外部注册表安装做引导：对**当前未运行**的浏览器先清卸载屏蔽再写注册表。

    运行中的浏览器无法可靠清除（退出会覆写），返回 runningBrowsers 提示需关闭重开。
    返回 {"installed", "unblocked", "runningBrowsers", "note"}。
    """
    unblockable = tuple(
        browser for browser in browsers if not browser_running(browser)
    )
    unblocked = clear_uninstall_block(extension_id, browsers=unblockable) \
        if unblockable else {"chrome": [], "edge": []}
    installed = install_external_registry_entry(
        extension_id, crx_path, version, browsers=browsers
    )
    running = [browser for browser in browsers if browser_running(browser)]
    note = "已写入外部扩展注册表。"
    if running:
        note += " 检测到浏览器正在运行：请先关闭，再重新打开扩展页并点一次启用。"
    return {
        "installed": installed,
        "unblocked": unblocked,
        "runningBrowsers": running,
        "note": note,
    }


# -- 提权降级（HKCU\Software\Policies 被加固的机器：单次 UAC，提权写 HKLM）------

_ELEVATION_TIMEOUT_SECONDS = 180.0
_SEE_MASK_NOCLOSEPROCESS = 0x00000040
_SEE_MASK_FLAG_NO_UI = 0x00000400


def install_elevated(extension_id: str, update_url: str) -> dict[str, str]:
    """弹一次 UAC，由提权子进程写 HKLM 策略，结果经临时文件回传。"""
    import ctypes
    import tempfile
    from ctypes import wintypes

    if sys.platform != "win32":
        raise ExtensionInstallError(
            "PLATFORM_UNSUPPORTED", "静默安装当前仅支持 Windows（macOS/Linux 需 root/MDM，暂缓）"
        )

    descriptor, result_file = tempfile.mkstemp(suffix=".json", prefix="rpa-elevated-")
    os.close(descriptor)
    os.unlink(result_file)
    parameters = (
        f"-m rpa_core.cli install-extension --elevated --extension-id {extension_id} "
        f'--update-url "{update_url}" --result-file "{result_file}"'
    )

    class _ShellExecuteInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", wintypes.ULONG),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIconOrMonitor", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    shell_execute = ctypes.windll.shell32.ShellExecuteExW
    info = _ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(_ShellExecuteInfo)
    info.fMask = _SEE_MASK_NOCLOSEPROCESS | _SEE_MASK_FLAG_NO_UI
    info.lpVerb = "runas"
    info.lpFile = sys.executable
    info.lpParameters = parameters
    info.nShow = 0  # SW_HIDE：隐藏子进程控制台窗口
    if not shell_execute(ctypes.byref(info)):
        raise ExtensionInstallError("UAC_DECLINED", "用户未批准提权（UAC 取消）")
    if info.hProcess:
        ctypes.windll.kernel32.WaitForSingleObject(
            wintypes.HANDLE(info.hProcess), int(_ELEVATION_TIMEOUT_SECONDS * 1000)
        )
        ctypes.windll.kernel32.CloseHandle(info.hProcess)
    if not os.path.exists(result_file):
        raise ExtensionInstallError(
            "ELEVATION_TIMEOUT", "提权子进程未回传结果（可能取消了 UAC 或超时）"
        )
    try:
        result = json.loads(Path(result_file).read_text(encoding="utf-8"))
    finally:
        os.unlink(result_file)
    if result.get("status") != "ok":
        raise ExtensionInstallError(
            str(result.get("code", "ELEVATION_FAILED")), str(result.get("message", "提权安装失败"))
        )
    return dict(result.get("browsers", {}))
