"""Native Messaging host 注册单测（M20 / ADR 0015 S3）。

覆盖：路径→ID 推导（含 Windows 真机锚点）、manifest+注册表写入、幂等自愈、
注销、状态只读、ID 解析优先级（发现 > 推导）、host 入口缺失报错。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path, PureWindowsPath

import pytest

from rpa_core import extension_installer as ext


class FakeWinreg:
    HKEY_CURRENT_USER = 0x80000001
    KEY_READ = 0x20019
    KEY_WRITE = 0x20006
    REG_SZ = 1

    def __init__(self):
        self.stores: dict[str, dict[str, tuple[str, int]]] = {}

    class _Key:
        def __init__(self, store, path=""):
            self._store = store
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def Close(self):
            pass

    def CreateKeyEx(self, hive, path, reserved, access):
        return FakeWinreg._Key(self.stores.setdefault(path, {}), path)

    def OpenKey(self, hive, path, reserved=0, access=0):
        if path not in self.stores:
            raise FileNotFoundError(path)
        return FakeWinreg._Key(self.stores[path], path)

    def SetValueEx(self, key, name, reserved, data_type, data):
        key._store[name] = (data, data_type)

    def QueryValueEx(self, key, name):
        if name not in key._store:
            raise FileNotFoundError(name)
        return key._store[name]

    def DeleteKey(self, hive, path):
        if path not in self.stores:
            raise FileNotFoundError(path)
        del self.stores[path]


@pytest.fixture(autouse=True)
def _isolate_profile(monkeypatch):
    """默认不触碰真实浏览器 profile（发现路径显式关闭）。"""
    monkeypatch.setattr(ext, "browser_user_data_dirs", lambda browser: [])


@pytest.fixture()
def fake_registry(monkeypatch):
    fake = FakeWinreg()
    monkeypatch.setattr(ext, "_winreg_module", lambda: fake)
    return fake


@pytest.fixture()
def fake_paths(tmp_path, monkeypatch):
    """host 入口与 manifest 落到临时目录。"""
    name = "rpa-core-ext-host.exe" if sys.platform == "win32" else "rpa-core-ext-host"
    executable = tmp_path / name
    executable.write_text("", encoding="utf-8")
    monkeypatch.setattr(ext, "native_host_executable", lambda: executable)
    monkeypatch.setattr(
        ext, "native_host_manifest_path", lambda browser: tmp_path / f"{browser}.json"
    )
    return {"tmp": tmp_path, "executable": executable}


def _manifest(browser: str, tmp: Path) -> dict:
    return json.loads((tmp / f"{browser}.json").read_text(encoding="utf-8"))


# -- 路径 → ID -----------------------------------------------------------------


def test_extension_id_from_path_shape(tmp_path):
    result = ext.extension_id_from_path(tmp_path)
    assert len(result) == 32
    assert all("a" <= char <= "p" for char in result)


@pytest.mark.skipif(sys.platform != "win32", reason="UTF-16LE 路径编码仅 Windows")
def test_extension_id_from_path_matches_real_edge_value():
    """S0 真机锚点：该字面路径在 Edge 的 profile 里就是此 ID（防算法漂移）。

    锚点用字面量路径而非本机 resolve 结果——spike 扩展当初在旧开发机
    ``D:\\...\\代码\\rpa_core`` 下被 Edge 加载，锚定本机位置会在仓库迁移后误报。
    """
    spike = PureWindowsPath(
        r"D:\Users\Administrator\Documents\代码\rpa_core"
        r"\.harness\spike\native_messaging\extension"
    )
    assert ext.extension_id_from_path(spike) == "dfbjkpbeppapijmjcpppconbchmeinek"


# -- 注册 / 注销 ----------------------------------------------------------------


def test_register_native_host_writes_manifest(fake_paths, fake_registry, tmp_path):
    source = tmp_path / "extension"
    source.mkdir()
    result = ext.register_native_host(
        "edge", source, extension_id="abcdefghijklmnopabcdefghijklmnop"
    )
    manifest = _manifest("edge", fake_paths["tmp"])
    assert manifest["name"] == ext.NATIVE_HOST_NAME
    assert manifest["type"] == "stdio"
    assert manifest["path"] == str(fake_paths["executable"])
    assert manifest["allowed_origins"] == [
        "chrome-extension://abcdefghijklmnopabcdefghijklmnop/"
    ]
    assert result["extensionId"] == "abcdefghijklmnopabcdefghijklmnop"
    assert result["hostExecutable"] == str(fake_paths["executable"])


def test_register_writes_hkcu_key_on_windows(fake_paths, fake_registry, tmp_path):
    source = tmp_path / "extension"
    source.mkdir()
    result = ext.register_native_host("edge", source, extension_id="a" * 32)
    if sys.platform != "win32":
        assert result["registry"] is None
        return
    key = f"{ext.WINDOWS_NATIVE_HOST_ROOTS['edge']}\\{ext.NATIVE_HOST_NAME}"
    assert key in fake_registry.stores
    assert fake_registry.stores[key][""][0] == str(fake_paths["tmp"] / "edge.json")


def test_register_is_idempotent(fake_paths, fake_registry, tmp_path):
    source = tmp_path / "extension"
    source.mkdir()
    first = ext.register_native_host("edge", source, extension_id="b" * 32)
    second = ext.register_native_host("edge", source, extension_id="b" * 32)
    assert first == second


def test_ensure_native_host_self_heals_after_source_dir_change(
    fake_paths, fake_registry, tmp_path
):
    """源码目录变动 → 派生 ID 变化 → manifest 被重写（幂等自愈）。"""
    first_dir = tmp_path / "ext-a"
    second_dir = tmp_path / "ext-b"
    first_dir.mkdir()
    second_dir.mkdir()
    ext.ensure_native_host("edge", first_dir)
    id_a = _manifest("edge", fake_paths["tmp"])["allowed_origins"][0]
    ext.ensure_native_host("edge", second_dir)
    id_b = _manifest("edge", fake_paths["tmp"])["allowed_origins"][0]
    assert id_a != id_b
    assert id_b == f"chrome-extension://{ext.extension_id_from_path(second_dir)}/"


def test_unregister_native_host_removes_manifest_and_key(
    fake_paths, fake_registry, tmp_path
):
    source = tmp_path / "extension"
    source.mkdir()
    ext.register_native_host("edge", source, extension_id="c" * 32)
    result = ext.unregister_native_host("edge")
    assert result["manifestRemoved"] is True
    assert not (fake_paths["tmp"] / "edge.json").exists()
    if sys.platform == "win32":
        assert result["registryRemoved"] is True
    # 幂等：再次注销不报错
    again = ext.unregister_native_host("edge")
    assert again["manifestRemoved"] is False


def test_register_raises_without_host_executable(monkeypatch, tmp_path):
    monkeypatch.setattr(ext, "native_host_executable", lambda: None)
    with pytest.raises(ext.ExtensionInstallError) as exc:
        ext.register_native_host("edge", tmp_path)
    assert exc.value.code == "NATIVE_HOST_MISSING"


# -- 状态 ----------------------------------------------------------------------


def test_native_host_status_reports_registration(fake_paths, fake_registry, tmp_path):
    source = tmp_path / "extension"
    source.mkdir()
    ext.register_native_host("chrome", source, extension_id="d" * 32)
    status = ext.native_host_status("chrome")
    assert status["registered"] is True
    assert status["hostExecutableExists"] is True
    assert status["extensionId"] == "d" * 32


def test_native_host_status_unregistered(fake_paths, fake_registry):
    status = ext.native_host_status("edge")
    assert status["registered"] is False
    assert status["extensionId"] is None


# -- ID 解析优先级 --------------------------------------------------------------


def test_resolve_prefers_discovered_over_derived(monkeypatch, tmp_path):
    monkeypatch.setattr(
        ext, "discover_unpacked_extension_id", lambda browser, directory: "e" * 32
    )
    assert ext.resolve_native_host_extension_id("edge", tmp_path) == "e" * 32


def test_resolve_falls_back_to_path_derivation(monkeypatch, tmp_path):
    monkeypatch.setattr(
        ext, "discover_unpacked_extension_id", lambda browser, directory: None
    )
    assert ext.resolve_native_host_extension_id("edge", tmp_path) == (
        ext.extension_id_from_path(tmp_path)
    )


def test_discover_unpacked_extension_id_from_profile(monkeypatch, tmp_path):
    """profile 的 Secure Preferences 里 location==4 且 path 匹配 → 取其 key 为 ID。"""
    source = tmp_path / "extension"
    source.mkdir()
    profile = tmp_path / "User Data" / "Default"
    profile.mkdir(parents=True)
    (profile / "Secure Preferences").write_text(
        json.dumps(
            {
                "extensions": {
                    "settings": {
                        "f" * 32: {
                            "location": 4,
                            "path": str(source.resolve()),
                        },
                        "g" * 32: {"location": 5, "path": str(source.resolve())},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        ext, "browser_user_data_dirs", lambda browser: [tmp_path / "User Data"]
    )
    assert ext.discover_unpacked_extension_id("edge", source) == "f" * 32
