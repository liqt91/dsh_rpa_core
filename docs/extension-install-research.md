# 浏览器扩展静默安装调研报告

> 调研时间：2026-09-06
> 目标：评估在WorkBuddy场景下，让用户用最简单的方式安装浏览器插件

---

## 一、需求背景

项目`rpa_core`自带浏览器扩展（`extension/`目录），用于元素捕获功能。需要评估：
1. 各浏览器扩展安装机制
2. Python脚本自动安装的可行性
3. 跨平台支持方案

---

## 二、各浏览器扩展安装机制对比

### 2.1 总览表

| 浏览器 | 注册表/配置路径 | 值格式 | 用户操作 | 权限要求 | 支持版本 |
|--------|----------------|--------|----------|----------|----------|
| **Chrome** | `HKLM\SOFTWARE\Policies\Google\Chrome\ExtensionInstallForcelist` | `ID; URL` | 无（静默） | HKCU无需管理员 | Windows 77+ |
| **Edge** | `HKLM\SOFTWARE\Policies\Microsoft\Edge\ExtensionInstallForcelist` | `ID; URL` | 无（静默） | HKCU无需管理员 | Windows 77+ |
| **Firefox** | `HKLM\SOFTWARE\Policies\Mozilla\Firefox\Extensions` | JSON对象 | 无（静默） | 需管理员 | Firefox 60+ |
| **Safari** | 需要MDM解决方案 | - | 需企业环境 | 需企业环境 | macOS 15+ |

### 2.2 Chrome（Windows）

**注册表路径：**
```
# 系统级（需管理员）
HKLM\SOFTWARE\Policies\Google\Chrome\ExtensionInstallForcelist

# 用户级（无需管理员）
HKCU\Software\Policies\Google\Chrome\ExtensionInstallForcelist
```

**值格式：**
```
值名称: "1" (递增序号)
值类型: REG_SZ
值数据: "<extension_id>; https://clients2.google.com/service/update2/crx"
```

**Python示例：**
```python
import winreg

def install_extension(extension_id):
    key = winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER,
        r"Software\Policies\Google\Chrome\ExtensionInstallForcelist",
        0, winreg.KEY_WRITE
    )
    winreg.SetValueEx(key, "1", 0, winreg.REG_SZ, 
                      f"{extension_id}; https://clients2.google.com/service/update2/crx")
    winreg.CloseKey(key)
```

**限制：**
- Chrome 44+禁止从本地CRX文件安装
- update_URL必须指向Chrome Web Store或HTTPS服务器
- 重启Chrome后生效

### 2.3 Edge（Windows）

**注册表路径：**
```
# 系统级（需管理员）
HKLM\SOFTWARE\Policies\Microsoft\Edge\ExtensionInstallForcelist

# 用户级（无需管理员）
HKCU\Software\Policies\Microsoft\Edge\ExtensionInstallForcelist
```

**值格式：**
```
值名称: "1" (递增序号)
值类型: REG_SZ
值数据: "<extension_id>; https://edge.microsoft.com/extensionwebstorebase/v1/crx"
```

**与Chrome对比：**
- 注册表路径不同（Microsoft vs Google）
- 更新URL不同
- 安装机制完全相同

### 2.4 Firefox（Windows）

**注册表路径：**
```
HKLM\SOFTWARE\Policies\Mozilla\Firefox\Extensions
```

**值格式（JSON）：**
```json
{
  "ExtensionSettings": {
    "<extension_id>": {
      "installation_mode": "force_installed",
      "install_url": "https://addons.mozilla.org/firefox/downloads/file.xpi"
    }
  }
}
```

**Python示例：**
```python
import winreg
import json

def install_firefox_extension(extension_id, xpi_url):
    key = winreg.CreateKeyEx(
        winreg.HKEY_LOCAL_MACHINE,
        r"SOFTWARE\Policies\Mozilla\Firefox\Extensions",
        0, winreg.KEY_WRITE
    )
    
    settings = {
        "ExtensionSettings": {
            extension_id: {
                "installation_mode": "force_installed",
                "install_url": xpi_url
            }
        }
    }
    
    winreg.SetValueEx(key, "ExtensionSettings", 0, winreg.REG_SZ, json.dumps(settings))
    winreg.CloseKey(key)
```

**限制：**
- 需要管理员权限（HKLM）
- JSON格式，比Chrome/Edge复杂
- Firefox 60+支持

### 2.5 Safari

**安装机制：**
- 需要MDM（移动设备管理）解决方案
- 需要macOS 15+ (Sequoia)
- 需要supervised设备
- 通过App Store分发

**限制：**
- 不适合普通用户
- 需要企业级环境
- 扩展格式不同（.appex vs .crx）

---

## 三、macOS平台对比

### 3.1 Chrome/Edge（macOS）

**配置方式：preferences文件**

```json
// 文件路径：~Library/Application Support/Google/Chrome/External Extensions/<extension_id>.json
{
  "external_update_url": "https://clients2.google.com/service/update2/crx"
}
```

**用户级路径：**
```
~USERNAME/Library/Application Support/Google/Chrome/External Extensions/
```

**所有用户路径：**
```
/Library/Application Support/Google/Chrome/External Extensions/
```

**限制：**
- Chrome 33+限制：update_URL必须指向Chrome Web Store
- 用户会看到确认对话框（无法完全静默）
- 所有用户级安装需要root权限

### 3.2 Firefox（macOS）

**配置方式：**
```
/Library/Application Support/Mozilla/Firefox/EnterprisePolicies/policies.json
```

**格式：**
```json
{
  "policies": {
    "Extensions": {
      "ForceInstalled": {
        "<extension_id>": "<xpi_url>"
      }
    }
  }
}
```

### 3.3 Safari（macOS）

**安装方式：**
- App Store分发
- 需要Xcode项目打包
- 需要Apple Developer Program（$99/年）

---

## 四、跨平台安装脚本设计

### 4.1 架构图

```
┌─────────────────────────────────────────────────────────────┐
│                    ExtensionInstaller                        │
├─────────────────────────────────────────────────────────────┤
│  detect_browser() → 检测Chrome/Edge/Firefox安装路径          │
│  calculate_extension_id() → 从key字段计算extension_id        │
│  pack_extension() → 打包.crx/.xpi文件                       │
│  install() → 根据平台和浏览器选择安装方式                     │
└─────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
┌───────────────┐    ┌───────────────┐    ┌───────────────┐
│ Windows       │    │ macOS         │    │ Linux         │
├───────────────┤    ├───────────────┤    ├───────────────┤
│ Chrome: HKCU  │    │ Chrome: plist │    │ Chrome: plist │
│ Edge: HKCU    │    │ Edge: plist   │    │ Edge: plist   │
│ Firefox: HKLM │    │ Firefox: json │    │ Firefox: json │
└───────────────┘    └───────────────┘    └───────────────┘
```

### 4.2 Python实现框架

```python
"""
跨平台浏览器扩展安装器
支持Chrome、Edge、Firefox
"""
import platform
import subprocess
import json
from pathlib import Path
from typing import Optional


class BrowserExtensionInstaller:
    """浏览器扩展安装器"""
    
    # 浏览器配置
    BROWSERS = {
        "chrome": {
            "win32": {
                "policy_path": r"Software\Policies\Google\Chrome\ExtensionInstallForcelist",
                "update_url": "https://clients2.google.com/service/update2/crx"
            },
            "darwin": {
                "external_dir": "~/Library/Application Support/Google/Chrome/External Extensions",
                "update_url": "https://clients2.google.com/service/update2/crx"
            },
            "linux": {
                "external_dir": "~/.config/google-chrome/External Extensions",
                "update_url": "https://clients2.google.com/service/update2/crx"
            }
        },
        "edge": {
            "win32": {
                "policy_path": r"Software\Policies\Microsoft\Edge\ExtensionInstallForcelist",
                "update_url": "https://edge.microsoft.com/extensionwebstorebase/v1/crx"
            },
            "darwin": {
                "external_dir": "~/Library/Application Support/Microsoft Edge/External Extensions",
                "update_url": "https://edge.microsoft.com/extensionwebstorebase/v1/crx"
            },
            "linux": {
                "external_dir": "~/.config/microsoft-edge/External Extensions",
                "update_url": "https://edge.microsoft.com/extensionwebstorebase/v1/crx"
            }
        },
        "firefox": {
            "win32": {
                "policy_path": r"SOFTWARE\Policies\Mozilla\Firefox\Extensions",
                "install_url": "https://addons.mozilla.org/firefox/downloads/file.xpi"
            },
            "darwin": {
                "policy_path": "/Library/Application Support/Mozilla/Firefox/EnterprisePolicies",
                "install_url": "https://addons.mozilla.org/firefox/downloads/file.xpi"
            },
            "linux": {
                "policy_path": "/etc/firefox/policies",
                "install_url": "https://addons.mozilla.org/firefox/downloads/file.xpi"
            }
        }
    }
    
    def __init__(self):
        self.system = platform.system().lower()
        if self.system == "darwin":
            self.system = "darwin"
        elif self.system == "windows":
            self.system = "win32"
        else:
            self.system = "linux"
    
    def detect_browsers(self) -> list[str]:
        """检测已安装的浏览器"""
        installed = []
        
        if self.system == "win32":
            # Windows：检查注册表或程序目录
            chrome_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
            edge_path = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
            firefox_path = r"C:\Program Files\Mozilla Firefox\firefox.exe"
            
            if Path(chrome_path).exists():
                installed.append("chrome")
            if Path(edge_path).exists():
                installed.append("edge")
            if Path(firefox_path).exists():
                installed.append("firefox")
                
        elif self.system == "darwin":
            # macOS：检查Applications目录
            chrome_path = "/Applications/Google Chrome.app"
            edge_path = "/Applications/Microsoft Edge.app"
            firefox_path = "/Applications/Firefox.app"
            
            if Path(chrome_path).exists():
                installed.append("chrome")
            if Path(edge_path).exists():
                installed.append("edge")
            if Path(firefox_path).exists():
                installed.append("firefox")
                
        else:
            # Linux：检查/usr/bin
            try:
                subprocess.run(["which", "google-chrome"], capture_output=True, check=True)
                installed.append("chrome")
            except (subprocess.CalledProcessError, FileNotFoundError):
                pass
            
            try:
                subprocess.run(["which", "microsoft-edge"], capture_output=True, check=True)
                installed.append("edge")
            except (subprocess.CalledProcessError, FileNotFoundError):
                pass
            
            try:
                subprocess.run(["which", "firefox"], capture_output=True, check=True)
                installed.append("firefox")
            except (subprocess.CalledProcessError, FileNotFoundError):
                pass
        
        return installed
    
    def calculate_extension_id(self, key_pem: str) -> str:
        """从PEM格式公钥计算Chrome扩展ID"""
        import hashlib
        import base64
        
        # 移除PEM头尾
        if key_pem.startswith("-----"):
            key_pem = key_pem.split("-----")[2]
        key_pem = key_pem.replace("\n", "").replace("\r", "").strip()
        
        # Base64解码得到DER格式公钥
        pub_key_der = base64.b64decode(key_pem)
        
        # SHA-256哈希
        sha = hashlib.sha256(pub_key_der).hexdigest()
        
        # 取前32个十六进制字符，转换为a-p
        extension_id = ""
        for char in sha[:32]:
            code = int(char, 16)
            extension_id += chr(ord("a") + code)
        
        return extension_id
    
    def pack_extension(self, extension_dir: str, browser: str = "chrome") -> Optional[str]:
        """打包扩展为.crx或.xpi文件"""
        if browser in ("chrome", "edge"):
            return self._pack_crx(extension_dir)
        elif browser == "firefox":
            return self._pack_xpi(extension_dir)
        return None
    
    def _pack_crx(self, extension_dir: str) -> Optional[str]:
        """打包为.crx文件"""
        import platform as plat
        
        # 检测Chrome路径
        if plat.system() == "Darwin":
            chrome_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        elif plat.system() == "Windows":
            chrome_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        else:
            chrome_path = "google-chrome"
        
        # 构造打包命令
        cmd = [chrome_path, f"--pack-extension={extension_dir}"]
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode == 0:
                # 确定输出文件路径
                ext_name = Path(extension_dir).name
                crx_path = Path(extension_dir).parent / f"{ext_name}.crx"
                if crx_path.exists():
                    return str(crx_path)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass
        
        return None
    
    def _pack_xpi(self, extension_dir: str) -> Optional[str]:
        """打包为.xpi文件（实际上是zip重命名）"""
        import zipfile
        
        ext_name = Path(extension_dir).name
        xpi_path = Path(extension_dir).parent / f"{ext_name}.xpi"
        
        try:
            with zipfile.ZipFile(xpi_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for file in Path(extension_dir).rglob("*"):
                    if file.is_file():
                        arcname = file.relative_to(extension_dir)
                        zf.write(file, arcname)
            return str(xpi_path)
        except Exception:
            return None
    
    def install(
        self,
        extension_id: str,
        browser: str,
        update_url: Optional[str] = None,
        xpi_url: Optional[str] = None
    ) -> bool:
        """安装扩展到指定浏览器"""
        if browser not in self.BROWSERS:
            print(f"不支持的浏览器: {browser}")
            return False
        
        config = self.BROWSERS[browser].get(self.system)
        if not config:
            print(f"不支持的平台: {self.system} for {browser}")
            return False
        
        if self.system == "win32":
            return self._install_windows(extension_id, browser, config, update_url, xpi_url)
        elif self.system == "darwin":
            return self._install_macos(extension_id, browser, config, update_url, xpi_url)
        else:
            return self._install_linux(extension_id, browser, config, update_url, xpi_url)
    
    def _install_windows(
        self,
        extension_id: str,
        browser: str,
        config: dict,
        update_url: Optional[str],
        xpi_url: Optional[str]
    ) -> bool:
        """Windows安装"""
        import winreg
        
        try:
            if browser in ("chrome", "edge"):
                # Chrome/Edge：写入ExtensionInstallForcelist
                key = winreg.CreateKeyEx(
                    winreg.HKEY_CURRENT_USER,
                    config["policy_path"],
                    0, winreg.KEY_WRITE | winreg.KEY_READ
                )
                
                # 查找下一个可用序号
                index = 0
                try:
                    while True:
                        index += 1
                        winreg.EnumValue(key, index - 1)
                except OSError:
                    pass
                
                url = update_url or config["update_url"]
                value = f"{extension_id}; {url}"
                winreg.SetValueEx(key, str(index), 0, winreg.REG_SZ, value)
                winreg.CloseKey(key)
                
                print(f"已添加到 {browser} 强制安装列表: {extension_id}")
                return True
                
            elif browser == "firefox":
                # Firefox：写入ExtensionSettings JSON
                key = winreg.CreateKeyEx(
                    winreg.HKEY_LOCAL_MACHINE,
                    config["policy_path"],
                    0, winreg.KEY_WRITE
                )
                
                settings = {
                    "ExtensionSettings": {
                        extension_id: {
                            "installation_mode": "force_installed",
                            "install_url": xpi_url or config["install_url"]
                        }
                    }
                }
                
                winreg.SetValueEx(key, "ExtensionSettings", 0, winreg.REG_SZ, json.dumps(settings))
                winreg.CloseKey(key)
                
                print(f"已添加到 Firefox 强制安装列表: {extension_id}")
                return True
                
        except PermissionError:
            print("权限不足，需要管理员权限")
            return False
        except Exception as e:
            print(f"安装失败: {e}")
            return False
    
    def _install_macos(
        self,
        extension_id: str,
        browser: str,
        config: dict,
        update_url: Optional[str],
        xpi_url: Optional[str]
    ) -> bool:
        """macOS安装"""
        import os
        
        try:
            if browser in ("chrome", "edge"):
                # Chrome/Edge：创建preferences文件
                external_dir = Path(config["external_dir"]).expanduser()
                external_dir.mkdir(parents=True, exist_ok=True)
                
                prefs_file = external_dir / f"{extension_id}.json"
                prefs_data = {
                    "external_update_url": update_url or config["update_url"]
                }
                
                prefs_file.write_text(json.dumps(prefs_data))
                print(f"已创建preferences文件: {prefs_file}")
                print("注意：Chrome会弹出确认对话框，需要用户点击确认")
                return True
                
            elif browser == "firefox":
                # Firefox：创建policies.json
                policy_dir = Path(config["policy_path"])
                policy_dir.mkdir(parents=True, exist_ok=True)
                
                policies_file = policy_dir / "policies.json"
                if policies_file.exists():
                    policies = json.loads(policies_file.read_text())
                else:
                    policies = {"policies": {}}
                
                if "Extensions" not in policies["policies"]:
                    policies["policies"]["Extensions"] = {}
                if "ForceInstalled" not in policies["policies"]["Extensions"]:
                    policies["policies"]["Extensions"]["ForceInstalled"] = {}
                
                policies["policies"]["Extensions"]["ForceInstalled"][extension_id] = \
                    xpi_url or config["install_url"]
                
                policies_file.write_text(json.dumps(policies, indent=2))
                print(f"已更新policies.json: {policies_file}")
                return True
                
        except PermissionError:
            print("权限不足，需要管理员权限")
            return False
        except Exception as e:
            print(f"安装失败: {e}")
            return False
    
    def _install_linux(
        self,
        extension_id: str,
        browser: str,
        config: dict,
        update_url: Optional[str],
        xpi_url: Optional[str]
    ) -> bool:
        """Linux安装"""
        import os
        
        try:
            if browser in ("chrome", "edge"):
                # Chrome/Edge：创建preferences文件
                external_dir = Path(config["external_dir"]).expanduser()
                external_dir.mkdir(parents=True, exist_ok=True)
                
                prefs_file = external_dir / f"{extension_id}.json"
                prefs_data = {
                    "external_update_url": update_url or config["update_url"]
                }
                
                prefs_file.write_text(json.dumps(prefs_data))
                print(f"已创建preferences文件: {prefs_file}")
                return True
                
            elif browser == "firefox":
                # Firefox：创建policies.json
                policy_dir = Path(config["policy_path"])
                policy_dir.mkdir(parents=True, exist_ok=True)
                
                policies_file = policy_dir / "policies.json"
                if policies_file.exists():
                    policies = json.loads(policies_file.read_text())
                else:
                    policies = {"policies": {}}
                
                if "Extensions" not in policies["policies"]:
                    policies["policies"]["Extensions"] = {}
                if "ForceInstalled" not in policies["policies"]["Extensions"]:
                    policies["policies"]["Extensions"]["ForceInstalled"] = {}
                
                policies["policies"]["Extensions"]["ForceInstalled"][extension_id] = \
                    xpi_url or config["install_url"]
                
                policies_file.write_text(json.dumps(policies, indent=2))
                print(f"已更新policies.json: {policies_file}")
                return True
                
        except PermissionError:
            print("权限不足，需要管理员权限")
            return False
        except Exception as e:
            print(f"安装失败: {e}")
            return False
    
    def check_installed(self, extension_id: str, browser: str) -> bool:
        """检查扩展是否已安装"""
        # 简化实现，实际需要检查注册表或配置文件
        return False


# 使用示例
if __name__ == "__main__":
    installer = BrowserExtensionInstaller()
    
    # 检测已安装的浏览器
    browsers = installer.detect_browsers()
    print(f"检测到的浏览器: {browsers}")
    
    # 计算扩展ID（需要manifest.json中的key字段）
    # ext_id = installer.calculate_extension_id("your_key_pem_here")
    
    # 安装扩展
    # installer.install(extension_id, "chrome")
```

---

## 五、WorkBuddy集成方案

### 5.1 cli.json修改

```json
{
  "runtime": {
    "type": "python",
    "version": "3.12"
  },
  "init": {
    "darwin": "python -m pip install rpa-core-runtime && python -m rpa_core.cli install-extension",
    "linux": "python -m pip install rpa-core-runtime && python -m rpa_core.cli install-extension",
    "win32": "python -m pip install rpa-core-runtime && python -m rpa_core.cli install-extension"
  },
  "auth": {
    "darwin": "rpa-core auth",
    "linux": "rpa-core auth",
    "win32": "rpa-core.cmd auth"
  },
  "unAuth": {
    "darwin": "rpa-core unauth",
    "linux": "rpa-core unauth",
    "win32": "rpa-core.cmd unauth"
  },
  "status": {
    "darwin": "rpa-core status",
    "linux": "rpa-core status",
    "win32": "rpa-core status"
  },
  "statusMatch": "\"status\": \"ready\""
}
```

### 5.2 CLI命令扩展

```python
# cli.py添加命令
def _cmd_install_extension(args) -> int:
    """安装浏览器扩展"""
    from rpa_core.extension_installer import BrowserExtensionInstaller
    
    installer = BrowserExtensionInstaller()
    
    # 检测浏览器
    browsers = installer.detect_browsers()
    if not browsers:
        print(json.dumps({"error": "NO_BROWSER", "message": "未检测到支持的浏览器"}))
        return 1
    
    # 读取manifest.json获取key
    manifest_path = Path(__file__).parent.parent / "extension" / "manifest.json"
    if not manifest_path.exists():
        print(json.dumps({"error": "NO_MANIFEST", "message": "未找到manifest.json"}))
        return 1
    
    manifest = json.loads(manifest_path.read_text())
    if "key" not in manifest:
        print(json.dumps({"error": "NO_KEY", "message": "manifest.json中缺少key字段"}))
        return 1
    
    # 计算扩展ID
    ext_id = installer.calculate_extension_id(manifest["key"])
    
    # 安装到所有检测到的浏览器
    results = {}
    for browser in browsers:
        success = installer.install(ext_id, browser)
        results[browser] = success
    
    print(json.dumps({
        "extension_id": ext_id,
        "installed": results
    }, indent=2))
    return 0 if any(results.values()) else 1
```

---

## 六、限制与注意事项

### 6.1 平台限制

| 平台 | 限制 | 解决方案 |
|------|------|----------|
| **Windows** | Chrome 44+禁止本地CRX | 使用HTTPS服务器托管 |
| **macOS** | 用户会看到确认对话框 | 提供安装指南 |
| **Linux** | 无限制 | 自动安装 |

### 6.2 浏览器限制

| 浏览器 | 限制 | 解决方案 |
|--------|------|----------|
| **Chrome** | update_URL必须指向Chrome Web Store | 搭建HTTPS服务器 |
| **Edge** | 同Chrome | 同Chrome |
| **Firefox** | 需管理员权限 | 提示用户 |
| **Safari** | 需要MDM环境 | 暂不支持 |

### 6.3 更新机制

- 扩展更新需要重新打包.crx/.xpi
- 需要HTTP服务器托管新版本
- 浏览器会自动检查更新

---

## 七、结论与建议

### 7.1 推荐方案

1. **优先支持Chrome/Edge**（Windows用户最多）
2. **使用HKCU注册表**（无需管理员权限）
3. **Firefox作为可选**（需管理员权限）
4. **Safari暂不支持**（需企业环境）

### 7.2 实施步骤

1. 修改`manifest.json`，添加key字段
2. 创建`extension_installer.py`模块
3. 在CLI中添加`install-extension`命令
4. 更新`cli.json`，集成自动安装
5. 提供安装指南文档

### 7.3 验证清单

- [ ] Chrome/Edge扩展ID计算正确
- [ ] Windows注册表写入成功
- [ ] macOS preferences文件创建成功
- [ ] Linux配置文件创建成功
- [ ] Firefox JSON配置正确
- [ ] 重启浏览器后扩展自动安装
- [ ] 扩展功能正常工作
