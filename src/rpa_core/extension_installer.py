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
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
import time
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
    """浏览器可执行文件候选——复用 extension_launch 的单一路径表。

    收敛：原本本模块自带一份候选路径，与自启模块的查找表重复、易漂移；
    现在统一从这里取，保证「检测安装」与「自启定位」看到同一份 Windows 路径。
    """
    from rpa_core.extension_launch import browser_binary_candidates as _candidates

    return _candidates(browser)


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
    """返回某浏览器 User Data 目录候选（本地用户目录；不存在的目录剔除）。

    三平台各持一张相对路径表：
    - win32：``%LOCALAPPDATA%`` 下 ``Google/Chrome/User Data``、``Microsoft/Edge/User Data``
    - darwin：``~/Library/Application Support/Google/Chrome``、``…/Microsoft Edge``
      （macOS 上 Chrome/Edge **不**用 ``~/.config``，Edge 也不在 ``Microsoft/Edge`` 下；
      漏了这张表会让 profile 探测恒空，进而把已装/已启用的插件误报为未安装）
    - 其它（Linux）：``~/.config/google-chrome``、``~/.config/microsoft-edge``
    """
    home = Path.home()
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
    if sys.platform == "darwin":
        candidates = {
            "chrome": (
                Path("Library") / "Application Support" / "Google" / "Chrome",
                Path("Library") / "Application Support" / "Chromium",
            ),
            "edge": (Path("Library") / "Application Support" / "Microsoft Edge",),
        }.get(browser)
        return [home / item for item in candidates] if candidates else []
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


# -- Native Messaging host 注册（ADR 0015 / M20 S3） --------------------------
#
# 扩展经 chrome.runtime.connectNative 连本机 bridge host；host 由浏览器按需拉起
# （无 8765 常驻 hub）。注册 = 写 host manifest（allowed_origins 白名单扩展 ID）+
# Windows HKCU 键 / macOS/Linux NativeMessagingHosts 目录。
#
# 两条 ID 来源（S0 真机验证）：
# - **路径推导**：unpacked 扩展 ID 由源码目录绝对路径派生，与浏览器无关，可在扩展
#   加载前预注册；
# - **Secure Preferences 发现**：浏览器落盘后的权威值（二者实测逐字一致）。

NATIVE_HOST_NAME = "com.rpa_core.ext_bridge"
NATIVE_HOST_ENTRY = "rpa-core-ext-host"

WINDOWS_NATIVE_HOST_ROOTS = {
    "chrome": r"Software\Google\Chrome\NativeMessagingHosts",
    "edge": r"Software\Microsoft\Edge\NativeMessagingHosts",
}


def native_host_store_dir() -> Path:
    """host manifest 的存放目录（Windows 由注册表指向此处）。"""
    return Path.home() / ".rpa-core" / "native-host"


def _posix_native_host_dirs(browser: str) -> list[Path]:
    """macOS/Linux 的 NativeMessagingHosts 目录（manifest 直接放这里）。"""
    home = Path.home()
    if sys.platform == "darwin":
        base = {
            "chrome": Path("Library") / "Application Support" / "Google" / "Chrome",
            "edge": Path("Library") / "Application Support" / "Microsoft Edge",
        }.get(browser)
    else:
        base = {
            "chrome": Path(".config/google-chrome"),
            "edge": Path(".config/microsoft-edge"),
        }.get(browser)
    return [home / base / "NativeMessagingHosts"] if base else []


def native_host_manifest_path(browser: str) -> Path:
    """host manifest 的规范路径（Windows 用户目录 / POSIX 浏览器目录）。"""
    if sys.platform == "win32":
        return native_host_store_dir() / f"{NATIVE_HOST_NAME}.{browser}.json"
    dirs = _posix_native_host_dirs(browser)
    if not dirs:
        return native_host_store_dir() / f"{NATIVE_HOST_NAME}.{browser}.json"
    return dirs[0] / f"{NATIVE_HOST_NAME}.json"


def native_host_executable() -> Path | None:
    """host 可执行入口（console script ``rpa-core-ext-host``），不存在返回 None。

    manifest 的 ``path`` 必须是**无参数可执行文件**——S0 实测：写成
    ``"pythonw.exe" "script.py"`` 这类命令行时 Chromium 不解析参数，会把解释器
    无参拉起（退化成读 stdin 的 REPL），表现为「扩展连上却无 host 逻辑、无日志」。
    """
    suffix = ".exe" if os.name == "nt" else ""
    sibling = Path(sys.executable).parent / f"{NATIVE_HOST_ENTRY}{suffix}"
    if sibling.is_file():
        return sibling
    found = shutil.which(NATIVE_HOST_ENTRY)
    return Path(found) if found else None


def extension_id_from_path(extension_dir: Path) -> str:
    """unpacked 扩展 ID：源码目录绝对路径的 SHA-256 前 128 位映射到 a-p。

    Chromium 对 ``FilePath::value()`` 取字节：Windows 为 UTF-16LE、POSIX 为 UTF-8。
    S0 实测（Windows）与 Edge profile 发现值逐字一致，Chrome 亦接受。
    """
    resolved = str(Path(extension_dir).resolve())
    raw = resolved.encode("utf-16-le" if sys.platform == "win32" else "utf-8")
    digest = hashlib.sha256(raw).hexdigest()[:32]
    alphabet: list[str] = []
    for char in digest:
        if char.isdigit():
            alphabet.append(chr(ord("a") + int(char)))
        else:
            alphabet.append(chr(ord("a") + 10 + (ord(char) - ord("a"))))
    return "".join(alphabet)


def discover_unpacked_extension_id(
    browser: str, extension_dir: Path
) -> str | None:
    """从浏览器 profile 发现 Load unpacked 扩展 ID（location==4 且 path 匹配）。"""
    target = str(Path(extension_dir).resolve()).lower()
    for user_data in browser_user_data_dirs(browser):
        if not user_data.is_dir():
            continue
        for child in sorted(user_data.iterdir()):
            if not child.is_dir() or not (child / "Secure Preferences").is_file():
                continue
            secure = _profile_prefs_json(child, "Secure Preferences")
            settings = (secure.get("extensions") or {}).get("settings") or {}
            for extension_id, record in settings.items():
                if not isinstance(record, dict) or record.get("location") != 4:
                    continue
                if str(record.get("path") or "").lower() == target:
                    return extension_id
    return None


def resolve_native_host_extension_id(
    browser: str, extension_dir: Path, extension_id: str | None = None
) -> str:
    """ID 解析：显式参数 > profile 发现（权威）> 路径推导（可预注册）。"""
    if extension_id:
        return extension_id
    found = discover_unpacked_extension_id(browser, extension_dir)
    if found:
        return found
    return extension_id_from_path(extension_dir)


def packed_extension_id(build_dir: Path | None = None) -> str | None:
    """打包产物的扩展 ID（pem 派生，CRX/商店安装用的那个）；无产物返回 None。"""
    packed = load_packed_extension(build_dir if build_dir is not None else default_build_dir())
    return packed.extension_id if packed is not None else None


def native_host_origins(
    browser: str,
    extension_dir: Path,
    extension_id: str | None = None,
    build_dir: Path | None = None,
    include_packed: bool = True,
) -> list[str]:
    """host manifest 的 ``allowed_origins``：覆盖扩展可能出现的**多个 ID**。

    同一个扩展在不同安装方式下 ID 不同，host manifest 必须同时放行，否则换一种
    安装方式就 ``connectNative`` forbidden：

    - **unpacked（Load unpacked）**：ID 由源码目录绝对路径派生（随路径/机器变化）；
    - **CRX / 商店**：ID 由签名私钥（pem）派生，更新版本与上架都不变。

    主 ID = 显式 > Secure Preferences 发现 > 路径推导；再追加打包/商店 ID（有产物才加）。
    """
    ids: list[str] = []
    primary = resolve_native_host_extension_id(browser, extension_dir, extension_id)
    if primary:
        ids.append(primary)
    if include_packed:
        packed = packed_extension_id(build_dir)
        if packed and packed not in ids:
            ids.append(packed)
    return [f"chrome-extension://{item}/" for item in ids]


def register_native_host(
    browser: str,
    extension_dir: Path | None = None,
    extension_id: str | None = None,
    build_dir: Path | None = None,
    include_packed: bool = True,
) -> dict:
    """写 host manifest + 注册（Windows HKCU / POSIX 目录）；幂等，可反复调用。

    ``allowed_origins`` 为多 ID 合并（见 ``native_host_origins``）：Load unpacked 的
    路径派生 ID 与打包/商店的 pem 派生 ID 同时放行。
    """
    source = Path(extension_dir) if extension_dir is not None else extension_root()
    origins = native_host_origins(
        browser, source, extension_id, build_dir, include_packed
    )
    resolved_id = origins[0].removeprefix("chrome-extension://").rstrip("/")
    executable = native_host_executable()
    if executable is None:
        raise ExtensionInstallError(
            "NATIVE_HOST_MISSING",
            f"未找到 host 入口 {NATIVE_HOST_ENTRY}；先执行 uv sync（生成 console script）",
        )
    manifest = {
        "name": NATIVE_HOST_NAME,
        "description": "rpa_core extension bridge host",
        "path": str(executable),
        "type": "stdio",
        "allowed_origins": origins,
    }
    manifest_path = native_host_manifest_path(browser)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    registry: str | None = None
    if sys.platform == "win32":
        winreg = _winreg_module()
        key_path = f"{WINDOWS_NATIVE_HOST_ROOTS[browser]}\\{NATIVE_HOST_NAME}"
        key = winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_WRITE
        )
        try:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(manifest_path))
        finally:
            key.Close()
        registry = f"HKCU\\{key_path}"
    return {
        "browser": browser,
        "extensionId": resolved_id,
        "manifest": str(manifest_path),
        "hostExecutable": str(executable),
        "registry": registry,
    }


def unregister_native_host(browser: str) -> dict:
    """删除 host manifest 与注册键（幂等）。"""
    manifest_path = native_host_manifest_path(browser)
    manifest_removed = False
    if manifest_path.is_file():
        manifest_path.unlink()
        manifest_removed = True
    registry_removed = False
    if sys.platform == "win32":
        winreg = _winreg_module()
        key_path = f"{WINDOWS_NATIVE_HOST_ROOTS[browser]}\\{NATIVE_HOST_NAME}"
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
            registry_removed = True
        except OSError:  # FileNotFoundError（键不存在）等一律视为未删除
            registry_removed = False
    return {
        "browser": browser,
        "manifestRemoved": manifest_removed,
        "registryRemoved": registry_removed,
    }


def _native_host_registry_value(browser: str) -> str | None:
    if sys.platform != "win32":
        return None
    winreg = _winreg_module()
    key_path = f"{WINDOWS_NATIVE_HOST_ROOTS[browser]}\\{NATIVE_HOST_NAME}"
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ
        )
    except (FileNotFoundError, OSError):
        return None
    try:
        return str(winreg.QueryValueEx(key, "")[0])
    except OSError:
        return None
    finally:
        key.Close()


def native_host_status(browser: str) -> dict:
    """只读状态：是否已注册、manifest 内容、host 入口是否存在。"""
    registry_value = _native_host_registry_value(browser)
    manifest_path = native_host_manifest_path(browser)
    if sys.platform == "win32":
        registered = registry_value is not None
        readable = Path(registry_value) if registry_value else manifest_path
    else:
        registered = manifest_path.is_file()
        readable = manifest_path
    manifest: dict | None = None
    if registered:
        try:
            loaded = json.loads(readable.read_text(encoding="utf-8"))
            manifest = loaded if isinstance(loaded, dict) else None
        except (OSError, ValueError):
            manifest = None
    executable = native_host_executable()
    origins: list[str] = []
    if manifest:
        origins = [str(item) for item in (manifest.get("allowed_origins") or [])]
    origin = origins[0] if origins else ""
    extension_id = origin.removeprefix("chrome-extension://").rstrip("/") or None
    return {
        "browser": browser,
        "registered": registered,
        "registry": registry_value,
        "manifest": str(manifest_path),
        "hostExecutable": str(executable) if executable else None,
        "allowedOrigins": origins,
        "hostExecutableExists": executable is not None,
        "extensionId": extension_id,
    }


def ensure_native_host(
    browser: str,
    extension_dir: Path | None = None,
    extension_id: str | None = None,
    build_dir: Path | None = None,
    include_packed: bool = True,
) -> dict:
    """幂等自愈：重算 ID 与 host 路径并重写注册（venv/源码目录/pem 变动后修复）。

    调用点：`install-extension` 各安装路线、GUI 启动、插件对话框刷新——保证
    「扩展已加载但 host 注册漂移」能自愈。
    """
    return register_native_host(
        browser, extension_dir, extension_id, build_dir, include_packed
    )


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


def _derive_install_mode(status: dict) -> str:
    """从实测状态推导实际安装途径（不再写死 load-unpacked）。

    优先级：开发者模式(Load unpacked) > 外部注册表 CRX > 仅打包未装。
    """
    for browser in ("chrome", "edge"):
        info = status["browsers"][browser]
        if any(p["enabled"] for p in info.get("unpackedProfiles", [])):
            return "load-unpacked"
    for browser in ("chrome", "edge"):
        info = status["browsers"][browser]
        if any(p["enabled"] for p in info.get("profiles", [])):
            return "packed-external-registry"
    if status.get("packed"):
        return "packed-external-registry"
    return "not-installed"


def env_status_base(build_dir: Path | None = None) -> dict:
    """环境诊断的「纯静态」部分：浏览器安装×运行×插件安装×安装途径 × 引擎版本。

    `online`（在线心跳）属 devserver 进程内 hub 状态，不在本函数计算；由调用方
    （devserver `/api/env/status` 或 CLI）按情况并上实时在线名单，保证两端同源。
    """
    try:
        version = importlib.metadata.version("rpa-core-runtime")
    except importlib.metadata.PackageNotFoundError:
        from rpa_core import __version__ as version  # noqa: PLC0415

    status = extension_status("", build_dir, extension_dir=extension_root())
    browsers = {
        name: {
            "binary": bool(info["binary"]),
            "running": browser_running(name),
            "installed": bool(info["installed"]),
            "enabled": bool(info["enabled"]),
            "uninstallBlocked": bool(info["uninstallBlocked"]),
        }
        for name, info in status["browsers"].items()
    }
    return {
        "version": version,
        "installMode": _derive_install_mode(status),
        "browsers": browsers,
    }


# -- 通道诊断：把「离线」拆成可区分的原因（状态栏 / CLI / 插件对话框共用） -------
#
# 只报「离线」无法定位：可能是 bridge 没注册、插件没装、浏览器没开，也可能是
# **浏览器开着但扩展没被注入**（2026-09-20 真机踩到：浏览器单实例会吞掉后续命令行
# 的 --load-extension，注入型扩展因此永久离线，而探测只会说「离线」）。
# 本节全部**只读**、不启动浏览器，使用户在打开浏览器之前就能看清缺哪一环。

OFFLINE_BROWSER_NOT_INSTALLED = "browser-not-installed"
OFFLINE_BRIDGE_NOT_REGISTERED = "bridge-not-registered"
OFFLINE_EXTENSION_NOT_INSTALLED = "extension-not-installed"
OFFLINE_RUNNING_WITHOUT_EXTENSION = "browser-running-without-extension"
OFFLINE_HOST_NOT_REACHABLE = "host-not-reachable"
OFFLINE_BROWSER_NOT_RUNNING = "browser-not-running"
OFFLINE_UNKNOWN = "unknown"

_CHANNEL_STATIC_TTL_SECONDS = 30.0
_channel_static_cache: tuple[float, dict] | None = None


def _pgrep_literal_pattern(literal: str) -> str:
    """字面串 → pgrep -f 可用模式。

    只还原 ``re.escape`` 对 ``-`` 的转义：macOS 的 pgrep 走 BSD regcomp（默认 BRE），
    ``\\-`` 在那里行为未定义，会让本可命中的命令行查不到。
    """
    return re.escape(literal).replace(r"\-", "-")


def browser_extension_injected(browser: str, extension_dir: Path | str | None) -> bool | None:
    """当前是否有实例把本扩展以命令行 ``--load-extension`` 注入着。

    profile 里 ``location == 8``（LOCATION_COMMAND_LINE）只是**历史记录**：它能证明
    "曾经注入过"，不能证明当前实例加载了。唯一可靠的当前态判据是进程命令行。
    返回 ``None`` 表示该平台未实现该判定——调用方不要把 None 当成"没注入"。

    **只是"加载"的一条腿**：开发者模式（``location == 4``）加载的扩展不带任何命令行
    参数，只看这里会误报「浏览器在跑却没加载插件」。判定当前加载见
    ``_extension_loaded``。
    """
    if not extension_dir:
        return None
    if sys.platform == "win32":
        return None  # Windows 的命令行探测未实现（宁可不结论，也不误报）
    target = Path(extension_dir)
    try:
        resolved = target.resolve()
    except OSError:
        resolved = target
    pattern = _pgrep_literal_pattern(f"--load-extension={resolved}")
    return _posix_process_matches(pattern)


def _extension_loaded(info: dict, running: bool, injected: bool | None) -> bool | None:
    """尽力判定「当前这个浏览器实例是否加载了本扩展」（纯判定，不查进程）。

    两条腿（任一成立即算已加载）：
    - **开发者模式加载**（profile 有 location==4 且 path 指向本扩展目录）：浏览器会
      持久化该记录并在重启后继续加载，因此「有记录 + 实例在跑」即可认定；
    - **命令行注入**（进程 argv 带 ``--load-extension=<本扩展目录>``，由 ``injected`` 传入）。
    都没命中且命令行判定可用时为 False（确定没加载）；命令行判定不可用（Windows）
    时 ``injected is None`` → 返回 None，调用方据此说「未知」而不是「没加载」。
    """
    if not running:
        return False  # 没有实例在跑 = 确定没加载
    if info.get("extensionUnpacked"):
        return True
    return injected


def _channel_static_probe(build_dir: Path | None, extension_dir: Path | None) -> dict:
    """静态体检的实算部分：逐浏览器 bridge 注册 + 插件安装/启用（读 profile）。"""
    ext_dir = extension_root() if extension_dir is None else Path(extension_dir)
    status = extension_status("", build_dir, extension_dir=ext_dir)
    browsers: dict[str, dict] = {}
    for name, info in status["browsers"].items():
        host = native_host_status(name)
        browsers[name] = {
            "binary": bool(info["binary"]),
            "bridgeRegistered": bool(host["registered"]),
            "bridgeHostExecutableExists": bool(host["hostExecutableExists"]),
            "bridgeExtensionId": host["extensionId"],
            "extensionInstalled": bool(info["installed"]),
            "extensionEnabled": bool(info["enabled"]),
            # 开发者模式（location==4）加载记录：浏览器会持久化它，是「实例在跑即已加载」
            # 的依据（这类加载**不带**任何命令行参数，只看 pgrep 会误报"没加载"）。
            "extensionUnpacked": any(
                item["installed"] for item in info.get("unpackedProfiles", [])
            ),
            "uninstallBlocked": bool(info["uninstallBlocked"]),
        }
    return {"browsers": browsers, "extensionDir": str(ext_dir)}


def channel_static_status(
    build_dir: Path | None = None,
    extension_dir: Path | None = None,
    *,
    ttl: float | None = None,
) -> dict:
    """静态体检（bridge 注册 × 插件安装）：读 profile 较重，故带 TTL 缓存。

    状态栏每 5s 轮询一次，实算却要读多份 Secure Preferences——缓存让重活每 TTL
    只做一次。传 ``ttl=0`` 强制重算（测试/手动刷新用）。
    """
    global _channel_static_cache
    window = _CHANNEL_STATIC_TTL_SECONDS if ttl is None else ttl
    now = time.monotonic()
    if window > 0 and _channel_static_cache is not None:
        cached_at, payload = _channel_static_cache
        if now - cached_at < window:
            return payload
    payload = _channel_static_probe(build_dir, extension_dir)
    _channel_static_cache = (now, payload)
    return payload


def _primary_browser(browsers: dict) -> dict | None:
    """挑一个「最该被用户关注」的浏览器：在运行的 > 已装插件的 > 有二进制的。"""
    for predicate in (
        lambda i: i["running"],
        lambda i: i["extensionInstalled"] or i["extensionEnabled"],
        lambda i: i["binary"],
    ):
        for info in browsers.values():
            if predicate(info):
                return info
    return None


def classify_offline_reason(browsers: dict) -> str:
    """离线时给出最可能的一环（按用户可操作性排序，不做推测性归因）。"""
    usable = [info for info in browsers.values() if info["binary"]]
    if not usable:
        return OFFLINE_BROWSER_NOT_INSTALLED
    if any(i["running"] and i["extensionLoaded"] for i in usable):
        # 扩展加载着却没端点：问题在 host 侧（注册/host 入口/端点路径），不在插件
        return OFFLINE_HOST_NOT_REACHABLE
    if not any(i["bridgeRegistered"] for i in usable):
        return OFFLINE_BRIDGE_NOT_REGISTERED
    if not any(i["extensionInstalled"] for i in usable):
        return OFFLINE_EXTENSION_NOT_INSTALLED
    if any(i["running"] and i["extensionLoaded"] is False for i in usable):
        return OFFLINE_RUNNING_WITHOUT_EXTENSION
    if any(i["running"] for i in usable):
        return OFFLINE_UNKNOWN
    return OFFLINE_BROWSER_NOT_RUNNING


_OFFLINE_HINTS = {
    OFFLINE_BROWSER_NOT_INSTALLED: "未检测到支持的浏览器",
    OFFLINE_BRIDGE_NOT_REGISTERED: "先在「插件」里注册 bridge",
    OFFLINE_EXTENSION_NOT_INSTALLED: "先在「插件」里加载插件",
    OFFLINE_RUNNING_WITHOUT_EXTENSION: (
        "浏览器已在运行但未加载插件：完全退出浏览器后由本工具拉起"
        "（浏览器单实例会吞掉注入参数）"
    ),
    OFFLINE_HOST_NOT_REACHABLE: "插件已注入但 host 未上线：查 ~/.rpa-core/logs/ext-host.log",
    OFFLINE_BROWSER_NOT_RUNNING: "静态体检通过，打开浏览器即可",
    OFFLINE_UNKNOWN: "详见「插件」对话框",
}

# 状态栏一行的空间有限：徽标只放「三态 + 一句短标签」，处置建议走 offline_hint()
_OFFLINE_LABELS = {
    OFFLINE_BROWSER_NOT_INSTALLED: "无可用浏览器",
    OFFLINE_BRIDGE_NOT_REGISTERED: "bridge 未注册",
    OFFLINE_EXTENSION_NOT_INSTALLED: "插件未安装",
    OFFLINE_RUNNING_WITHOUT_EXTENSION: "浏览器未加载插件",
    OFFLINE_HOST_NOT_REACHABLE: "host 未上线",
    OFFLINE_BROWSER_NOT_RUNNING: "浏览器未运行",
    OFFLINE_UNKNOWN: "原因待查",
}


def offline_hint(reason: str) -> str:
    """该离线原因对应的处置建议（tooltip / 对话框用，不进状态栏徽标）。"""
    return _OFFLINE_HINTS.get(reason, "")


def describe_offline_reason(reason: str, browsers: dict) -> str:
    """一行短摘要：先答「bridge 注册? 插件安装? 浏览器运行?」，再补一句结论标签。"""
    info = _primary_browser(browsers)
    parts: list[str] = []
    if info is not None:
        parts.append("bridge 已注册" if info["bridgeRegistered"] else "bridge 未注册")
        if info["extensionInstalled"]:
            parts.append("插件已安装")
        elif info["extensionInjected"]:
            parts.append("插件已注入")
        else:
            parts.append("插件未安装")
        parts.append("浏览器在运行" if info["running"] else "浏览器未运行")
    label = _OFFLINE_LABELS.get(reason, "")
    if label and label not in parts:
        parts.append(label)
    return " · ".join(parts)


def browser_diagnostics_line(name: str, info: dict) -> str:
    """单浏览器明细行（tooltip / 对话框用）。"""
    bridge = "bridge 已注册" if info["bridgeRegistered"] else "bridge 未注册"
    if info["extensionInstalled"]:
        plugin = "插件已安装"
    elif info["extensionInjected"]:
        plugin = "插件已注入（未安装）"
    else:
        plugin = "插件未安装"
    if info["running"]:
        if info["extensionLoaded"]:
            runtime = "运行中·已加载插件"
        elif info["extensionLoaded"] is False:
            runtime = "运行中·未加载插件"
        else:
            runtime = "运行中·加载状态未知"
    else:
        runtime = "未运行"
    return f"{name}：{bridge} · {plugin} · {runtime}"


def channel_diagnostics(
    build_dir: Path | None = None,
    extension_dir: Path | None = None,
    *,
    static_ttl: float | None = None,
) -> dict:
    """通道离线的完整诊断：静态体检（缓存）+ 实时运行态，全部只读。

    返回 ``{"browsers": {name: {...}}, "reason": <稳定标识符>, "summary": <一行中文>}``；
    ``reason`` 取值见本模块 ``OFFLINE_*`` 常量。
    """
    static = channel_static_status(build_dir, extension_dir, ttl=static_ttl)
    ext_dir = static.get("extensionDir") or None
    browsers: dict[str, dict] = {}
    for name, info in static["browsers"].items():
        running = browser_running(name) if info["binary"] else False
        if not running:
            injected = False  # 没实例在跑 = 确定没注入，顺带省一次 pgrep
        elif info.get("extensionUnpacked"):
            injected = False  # 开发者模式加载不带命令行参数，无需查进程
        else:
            injected = browser_extension_injected(name, ext_dir)
        browsers[name] = {
            **info,
            "running": running,
            "extensionInjected": injected,
            "extensionLoaded": _extension_loaded(info, running, injected),
        }
    reason = classify_offline_reason(browsers)
    return {
        "browsers": browsers,
        "reason": reason,
        "summary": describe_offline_reason(reason, browsers),
    }


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

# POSIX（darwin/linux）按「整条命令行」匹配主进程：
# - macOS 主进程即 /Applications/<Name>.app/Contents/MacOS/<Name>（Helper 子进程路径含
#   "…Helper (Renderer)"，不会被下面这些子串命中）；
# - Linux 走发行版安装的二进制名。
_POSIX_BROWSER_PATTERNS = {
    "chrome": (
        r"Google Chrome\.app/Contents/MacOS/Google Chrome"
        r"|/google-chrome(-stable)?\b|/chromium(-browser)?\b"
    ),
    "edge": (
        r"Microsoft Edge\.app/Contents/MacOS/Microsoft Edge"
        r"|/microsoft-edge(-stable|-beta|-dev)?\b"
    ),
}


def _posix_process_matches(pattern: str) -> bool:
    """POSIX 下是否有进程命令行匹配 pattern：优先 pgrep -f，缺失时退 ps。"""
    try:
        completed = subprocess.run(
            ["pgrep", "-f", pattern],
            capture_output=True, text=True, timeout=10, encoding="utf-8", errors="replace",
        )
        return bool(completed.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        completed = subprocess.run(
            ["ps", "-A", "-o", "command"],
            capture_output=True, text=True, timeout=10, encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return re.search(pattern, completed.stdout) is not None


def browser_running(browser: str) -> bool:
    """目标浏览器当前是否有进程在运行（win32 用 tasklist，darwin/linux 匹配命令行）。

    仅用于判断"卸载屏蔽清除是否可靠/loader 是否要等重启"——运行中的浏览器
    会在退出时用内存副本覆写 Preferences，导致清除被冲掉。
    """
    if sys.platform == "win32":
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
    pattern = _POSIX_BROWSER_PATTERNS.get(browser)
    if pattern is None:
        return False
    return _posix_process_matches(pattern)


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
