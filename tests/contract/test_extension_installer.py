import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from rpa_core import extension_installer as ext
from rpa_core.cli import main as cli_main
from rpa_core.devserver import DevServer
from rpa_core.extension_installer import (
    BROWSER_EXTERNAL_ROOTS,
    BROWSER_POLICY_ROOTS,
    FORCELIST_KEY,
    ExtensionInstallError,
    extension_id_from_pem,
    install_external_registry_entry,
    remove_external_registry_entry,
    remove_forcelist_entries,
    update_manifest_xml,
    write_external_registry_entry,
    write_forcelist_entry,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures"
# 夹具由 Git openssl（独立实现）生成：openssl rsa -pubout -outform DER 后 SHA-256 映射
EXPECTED_ID = "mhiiampbndgpcpcdnilnkbnkafeofcem"
_PKCS1 = (FIXTURES / "extension-test-key-pkcs1.pem").read_text(encoding="utf-8")
_PKCS8 = (FIXTURES / "extension-test-key.pem").read_text(encoding="utf-8")


class FakeWinreg:
    HKEY_CURRENT_USER = 0x80000001
    HKEY_LOCAL_MACHINE = 0x80000002
    KEY_READ = 0x20019
    KEY_WRITE = 0x20006
    REG_SZ = 1

    def __init__(self):
        self.stores: dict[str, dict[str, tuple[str, int]]] = {}
        self.created: list[tuple[int, str]] = []

    class _Key:
        def __init__(self, store, path=""):
            self._store = store
            self.path = path

        def Close(self):
            pass

    def EnumValue(self, key, index):
        names = list(key._store)
        if index >= len(names):
            raise OSError("no more items")
        name = names[index]
        data, data_type = key._store[name]
        return name, data, data_type

    def SetValueEx(self, key, name, reserved, data_type, data):
        key._store[name] = (data, data_type)

    def DeleteValue(self, key, name):
        del key._store[name]

    def CreateKeyEx(self, hive, path, reserved, access):
        self.created.append((hive, path))
        return FakeWinreg._Key(self.stores.setdefault(path, {}), path)

    def OpenKey(self, hive, path, reserved, access):
        if path not in self.stores:
            raise FileNotFoundError(path)
        return FakeWinreg._Key(self.stores[path], path)

    def DeleteKey(self, hive, path):
        if path not in self.stores:
            raise OSError(f"missing key: {path}")
        del self.stores[path]


# -- 扩展 ID（纯数学，与 openssl 标准答案互验）--------------------------------


def test_extension_id_matches_openssl_ground_truth():
    assert extension_id_from_pem(_PKCS1) == EXPECTED_ID
    assert extension_id_from_pem(_PKCS8) == EXPECTED_ID
    assert len(EXPECTED_ID) == 32
    assert all("a" <= char <= "p" for char in EXPECTED_ID)


def test_update_manifest_xml_fields():
    class _Packed:
        extension_id = EXPECTED_ID
        version = "1.2.3"

    xml = update_manifest_xml(_Packed(), "http://127.0.0.1:8765/api/extension/crx")
    assert xml.startswith("<?xml")
    assert 'xmlns="http://www.google.com/update2/response"' in xml
    assert 'protocol="2.0"' in xml
    assert f'appid="{EXPECTED_ID}"' in xml
    assert 'codebase="http://127.0.0.1:8765/api/extension/crx"' in xml
    assert 'version="1.2.3"' in xml


def test_extension_manifest_declares_webstore_update_url():
    # manifest.update_url 必须指向已知商店（上架前置）。注意：本地打包 CRX 的
    # 外部注册表安装不会因此豁免"未知来源"禁用（实验矩阵 docs §6.4）。
    manifest = json.loads(
        (ext.extension_root() / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["update_url"] in (
        "https://clients2.google.com/service/update2/crx",
        "https://edge.microsoft.com/extensionwebstorebase/v1/crx",
    )


# -- HKCU 策略读写（fake winreg，不触碰真实注册表）-----------------------------


def test_write_forcelist_entry_appends_and_is_idempotent():
    fake = FakeWinreg()
    root = BROWSER_POLICY_ROOTS["chrome"]
    assert write_forcelist_entry(fake, root, "id1;http://u") == "added"
    assert write_forcelist_entry(fake, root, "id1;http://u") == "already"
    assert write_forcelist_entry(fake, root, "id2;http://u") == "added"
    store = fake.stores[f"{root}\\{FORCELIST_KEY}"]
    assert store == {"1": ("id1;http://u", 1), "2": ("id2;http://u", 1)}


def test_remove_forcelist_entries_by_extension_id():
    fake = FakeWinreg()
    root = BROWSER_POLICY_ROOTS["edge"]
    write_forcelist_entry(fake, root, "id1;http://u")
    write_forcelist_entry(fake, root, "other;http://u")
    assert remove_forcelist_entries(fake, root, extension_id="id1") == 1
    store = fake.stores[f"{root}\\{FORCELIST_KEY}"]
    assert list(store.values()) == [("other;http://u", 1)]


def test_remove_forcelist_entries_by_update_url_when_id_unknown():
    fake = FakeWinreg()
    root = BROWSER_POLICY_ROOTS["chrome"]
    write_forcelist_entry(fake, root, "id1;http://mine/update-manifest")
    write_forcelist_entry(fake, root, "id2;http://other/update-manifest")
    removed = remove_forcelist_entries(
        fake, root, extension_id=None, update_url="http://mine/update-manifest"
    )
    assert removed == 1
    store = fake.stores[f"{root}\\{FORCELIST_KEY}"]
    assert list(store.values()) == [("id2;http://other/update-manifest", 1)]


def test_remove_missing_key_is_noop():
    assert remove_forcelist_entries(FakeWinreg(), BROWSER_POLICY_ROOTS["chrome"], "x") == 0


def test_install_policy_entry_writes_both_browsers(monkeypatch):
    fake = FakeWinreg()
    monkeypatch.setattr(ext, "_winreg_module", lambda: fake)
    monkeypatch.setattr(sys, "platform", "win32")
    results = ext.install_policy_entry("abc", "http://u/update-manifest")
    assert results == {"chrome": "added", "edge": "added"}
    for _browser, root in BROWSER_POLICY_ROOTS.items():
        assert fake.stores[f"{root}\\{FORCELIST_KEY}"] == {
            "1": ("abc;http://u/update-manifest", 1)
        }
        assert (FakeWinreg.HKEY_CURRENT_USER, f"{root}\\{FORCELIST_KEY}") in fake.created


def test_install_policy_entry_elevated_targets_hklm(monkeypatch):
    fake = FakeWinreg()
    monkeypatch.setattr(ext, "_winreg_module", lambda: fake)
    monkeypatch.setattr(sys, "platform", "win32")
    results = ext.install_policy_entry("abc", "http://u/update-manifest", elevated=True)
    assert results == {"chrome": "added", "edge": "added"}
    for root in BROWSER_POLICY_ROOTS.values():
        assert (FakeWinreg.HKEY_LOCAL_MACHINE, f"{root}\\{FORCELIST_KEY}") in fake.created
        assert (FakeWinreg.HKEY_CURRENT_USER, f"{root}\\{FORCELIST_KEY}") not in fake.created


def test_install_policy_entry_rejects_non_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(ExtensionInstallError) as excinfo:
        ext.install_policy_entry("abc", "http://u")
    assert excinfo.value.code == "PLATFORM_UNSUPPORTED"


# -- HKCU 外部扩展注册表（external_registry_loader 通道，默认路线）--------------


def test_write_external_registry_entry_adds_updates_and_is_idempotent():
    fake = FakeWinreg()
    root = BROWSER_EXTERNAL_ROOTS["edge"]
    crx = Path("C:/x/a.crx")
    assert write_external_registry_entry(fake, root, "abc", crx, "1.0") == "added"
    assert write_external_registry_entry(fake, root, "abc", crx, "1.0") == "already"
    assert write_external_registry_entry(fake, root, "abc", crx, "1.1") == "updated"
    assert fake.stores[f"{root}\\abc"] == {
        "path": (str(crx), 1),
        "version": ("1.1", 1),
    }


def test_remove_external_registry_entry_is_noop_when_missing():
    fake = FakeWinreg()
    root = BROWSER_EXTERNAL_ROOTS["chrome"]
    write_external_registry_entry(fake, root, "abc", Path("C:/x/a.crx"), "1.0")
    assert remove_external_registry_entry(fake, root, "abc") == 1
    assert remove_external_registry_entry(fake, root, "abc") == 0


def test_install_external_registry_entry_writes_both_browsers(monkeypatch):
    fake = FakeWinreg()
    monkeypatch.setattr(ext, "_winreg_module", lambda: fake)
    monkeypatch.setattr(sys, "platform", "win32")
    results = install_external_registry_entry("abc", Path("C:/x/a.crx"), "1.0")
    assert results == {"chrome": "added", "edge": "added"}
    for _browser, root in BROWSER_EXTERNAL_ROOTS.items():
        entry = fake.stores[f"{root}\\abc"]
        assert entry == {"path": (str(Path("C:/x/a.crx")), 1), "version": ("1.0", 1)}
        assert (FakeWinreg.HKEY_CURRENT_USER, f"{root}\\abc") in fake.created


def test_install_external_registry_entry_rejects_non_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(ExtensionInstallError) as excinfo:
        install_external_registry_entry("abc", Path("C:/x/a.crx"), "1.0")
    assert excinfo.value.code == "PLATFORM_UNSUPPORTED"


# -- CLI 接线（fake winreg + 预置打包产物，不启动浏览器）-----------------------


@pytest.fixture()
def _fake_registry(monkeypatch):
    fake = FakeWinreg()
    monkeypatch.setattr(ext, "_winreg_module", lambda: fake)
    monkeypatch.setattr(sys, "platform", "win32")
    return fake


def _cli(*argv: str, monkeypatch) -> tuple[int, str]:
    import contextlib
    import io

    monkeypatch.setattr(sys, "argv", ["rpa-core", *argv])
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = cli_main()
    return code, buffer.getvalue()


def test_cli_install_extension_defaults_to_external_registry(_fake_registry, monkeypatch, tmp_path):
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    (build_dir / "extension.pem").write_text(_PKCS8, encoding="utf-8")
    (build_dir / "extension.crx").write_bytes(b"Cr24-fake")
    monkeypatch.setattr(ext, "find_browser_binary", lambda: None)
    code, out = _cli("install-extension", "--build-dir", str(build_dir), monkeypatch=monkeypatch)
    assert code == 0
    payload = json.loads(out)
    assert payload["extensionId"] == EXPECTED_ID
    assert payload["mechanism"] == "external-registry"
    assert payload["browsers"] == {"chrome": "added", "edge": "added"}
    assert "policyHive" not in payload
    for root in BROWSER_EXTERNAL_ROOTS.values():
        entry = _fake_registry.stores[f"{root}\\{EXPECTED_ID}"]
        assert entry["path"][0].endswith("extension.crx")
        assert entry["version"] == ("0.1.2", 1)
    assert all(FORCELIST_KEY not in path for path in _fake_registry.stores)


def test_cli_policy_flag_uses_forcelist_route(_fake_registry, monkeypatch, tmp_path):
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    (build_dir / "extension.pem").write_text(_PKCS8, encoding="utf-8")
    (build_dir / "extension.crx").write_bytes(b"Cr24-fake")
    monkeypatch.setattr(ext, "find_browser_binary", lambda: None)
    code, out = _cli(
        "install-extension", "--policy", "--build-dir", str(build_dir), monkeypatch=monkeypatch
    )
    assert code == 0
    payload = json.loads(out)
    assert payload["mechanism"] == "policy"
    assert payload["policyHive"] == "HKCU"
    for root in BROWSER_POLICY_ROOTS.values():
        assert _fake_registry.stores[f"{root}\\{FORCELIST_KEY}"]["1"][0] == (
            f"{EXPECTED_ID};http://127.0.0.1:8765/api/extension/update-manifest"
        )


def test_cli_remove_extension_clears_both_mechanisms(_fake_registry, monkeypatch, tmp_path):
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    (build_dir / "extension.pem").write_text(_PKCS8, encoding="utf-8")
    (build_dir / "extension.crx").write_bytes(b"Cr24-fake")
    monkeypatch.setattr(ext, "find_browser_binary", lambda: None)
    code, _ = _cli("install-extension", "--build-dir", str(build_dir), monkeypatch=monkeypatch)
    assert code == 0
    code, _ = _cli(
        "install-extension", "--policy", "--build-dir", str(build_dir), monkeypatch=monkeypatch
    )
    assert code == 0
    code, out = _cli("install-extension", "--remove", "--build-dir", str(build_dir),
                     monkeypatch=monkeypatch)
    assert code == 0
    assert json.loads(out) == {"removed": {"chrome": 2, "edge": 2}}
    assert all(not store for store in _fake_registry.stores.values())


def test_cli_install_error_output(_fake_registry, monkeypatch, tmp_path):
    monkeypatch.setattr(ext, "find_browser_binary", lambda: None)
    code, out = _cli("install-extension", "--build-dir", str(tmp_path / "empty"),
                     monkeypatch=monkeypatch)
    assert code == 1
    assert json.loads(out) == {
        "error": "NO_BROWSER",
        "message": "未找到 Chrome/Edge，无法打包扩展",
    }


def test_cli_falls_back_to_elevated_on_registry_denied(_fake_registry, monkeypatch, tmp_path):
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    (build_dir / "extension.pem").write_text(_PKCS8, encoding="utf-8")
    (build_dir / "extension.crx").write_bytes(b"Cr24-fake")
    monkeypatch.setattr(ext, "find_browser_binary", lambda: None)

    def deny(extension_id, update_url):
        raise ExtensionInstallError("REGISTRY_DENIED", "denied")

    monkeypatch.setattr(ext, "install_policy_entry", deny)
    monkeypatch.setattr(
        ext, "install_elevated",
        lambda extension_id, update_url: {"chrome": "added", "edge": "added"},
    )
    code, out = _cli(
        "install-extension", "--policy", "--build-dir", str(build_dir), monkeypatch=monkeypatch
    )
    assert code == 0
    payload = json.loads(out)
    assert payload["policyHive"] == "HKLM"
    assert payload["browsers"] == {"chrome": "added", "edge": "added"}


def test_cli_elevated_child_writes_result_file(_fake_registry, monkeypatch, tmp_path):
    result_file = tmp_path / "result.json"
    code, _ = _cli(
        "install-extension", "--elevated",
        "--extension-id", EXPECTED_ID,
        "--update-url", "http://u/update-manifest",
        "--result-file", str(result_file),
        monkeypatch=monkeypatch,
    )
    assert code == 0
    payload = json.loads(result_file.read_text(encoding="utf-8"))
    assert payload == {
        "status": "ok",
        "browsers": {"chrome": "added", "edge": "added"},
    }
    for root in BROWSER_POLICY_ROOTS.values():
        assert _fake_registry.stores[f"{root}\\{FORCELIST_KEY}"] == {
            "1": (f"{EXPECTED_ID};http://u/update-manifest", 1)
        }


# -- devserver 托管端点 --------------------------------------------------------


def _raw(base: str, path: str, method: str = "GET"):
    request = urllib.request.Request(f"{base}{path}", method=method)
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, response.read(), response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers.get("Content-Type", "")


@pytest.fixture()
def packed_server(tmp_path):
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    (build_dir / "extension.pem").write_text(_PKCS8, encoding="utf-8")
    (build_dir / "extension.crx").write_bytes(b"Cr24-fake-bytes")
    server = DevServer(
        commands_root=ROOT / "commands",
        workflows_root=tmp_path / "workflows",
        port=0,
        extension_build_dir=build_dir,
    )
    server.start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture()
def unpacked_server(tmp_path):
    server = DevServer(
        commands_root=ROOT / "commands",
        workflows_root=tmp_path / "workflows",
        port=0,
        extension_build_dir=tmp_path / "empty",
    )
    server.start()
    try:
        yield server
    finally:
        server.stop()


def test_update_manifest_served_with_crx_codebase(packed_server):
    base = f"http://127.0.0.1:{packed_server.port}"
    status, body, content_type = _raw(base, "/api/extension/update-manifest")
    assert status == 200
    assert content_type == "application/xml"
    text = body.decode("utf-8")
    assert f'appid="{EXPECTED_ID}"' in text
    assert f'codebase="{base}/api/extension/crx"' in text
    assert 'version="0.1.2"' in text


def test_crx_served_with_chrome_extension_content_type(packed_server):
    base = f"http://127.0.0.1:{packed_server.port}"
    status, body, content_type = _raw(base, "/api/extension/crx")
    assert status == 200
    assert content_type == "application/x-chrome-extension"
    assert body == b"Cr24-fake-bytes"


def test_endpoints_reject_non_get(packed_server):
    base = f"http://127.0.0.1:{packed_server.port}"
    assert _raw(base, "/api/extension/crx", method="POST")[0] == 405
    assert _raw(base, "/api/extension/update-manifest", method="POST")[0] == 405


def test_endpoints_503_when_not_packed(unpacked_server):
    base = f"http://127.0.0.1:{unpacked_server.port}"
    status, body, _ = _raw(base, "/api/extension/crx")
    assert status == 503
    assert json.loads(body.decode("utf-8"))["error"] == "EXTENSION_NOT_PACKED"
    status, body, _ = _raw(base, "/api/extension/update-manifest")
    assert status == 503
    assert json.loads(body.decode("utf-8"))["error"] == "EXTENSION_NOT_PACKED"
