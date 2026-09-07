# 浏览器扩展静默安装方案（extension-installer）

> 状态：代码与合同测试完成（2026-09-06）；静默安装机制本机实测通过，产品主路径为商店上架；本机另有策略键清除工具（§6）。**Chrome 152 + Edge 152(.62) 双浏览器对照实测（2026-09-07，含影刀插件真机重装对比）见 §6.5；重大修正见 §6.5.1**——外部注册表来源指向商店的**未上架**本地 CRX 启用后约 30s 被异步商店校验判损坏（不可持久），**默认安装方式已改为开发者模式 Load unpacked 引导**（源码目录，持久可用）；`--registry` 保留仅商店上架后可靠。
> 调研底稿：`docs/extension-install-research.md`（本文是对它的落地修订，部分结论已被实测推翻）。

## 1. 方案总览

自研捕获扩展（`extension/`，MV3 Chromium-only）的安装，Windows 上通道与定位（2026-09-07 修订）：

### 1.0 默认（未上架期间）：开发者模式 Load unpacked 引导（手动加载源码目录）

```
1. 打开浏览器扩展页（chrome://extensions / edge://extensions，需手动输入地址）
2. 开启「开发者模式」
3. 点「加载已解压的扩展程序」，选 extension/ 源码目录
→ location=4 unpacked，持久可用，无需打包/上架/重启
```

- 影刀 Chrome 同款方案（§8）；`install-extension` 默认与编辑器「⇲ 插件」引导此流程。
- **chrome:// 与 edge:// 扩展页无法从外部命令可靠导航**（安全设计，冷启动/传 URL 均落新标签页）；
  编辑器引导自动复制扩展页地址并提示粘贴。

### 1.0a 备选（商店上架后）：HKCU 外部扩展注册表（external_registry_loader，免管理员）

```
extension/ 源码 → 本机 Chromium --pack-extension 打 CRX（私钥持久化，ID 稳定）
   → HKCU\Software\Google\Chrome\Extensions\<id>   { path=本地CRX; version=x }
     HKCU\Software\Microsoft\Edge\Extensions\<id>  { path=本地CRX; version=x }
   → 浏览器下次启动：external_registry_loader 扫描注册表 → 从本地 CRX 静默安装
```

- **免管理员**（HKCU）、**免商店上架**、**免 devserver 在线**（本地 CRX 路径直装）、
  **不受未加域 forcelist 门控影响**。影刀/K-RPA Lite 即此通道（§8，已逆向验证）。
- **仅商店上架后可靠**：本机实证指向商店 update_url 的未上架本地 CRX 启用后 ~30s
  被异步商店校验判损坏（§6.5.1）；上架后 ID/key 商店可验即持久。
- 用户可在浏览器里正常卸载（这是"普通扩展"而非"策略强制"）。

### 1.0b 备选通道：ExtensionInstallForcelist 策略（`--policy`，受管环境强制安装）

```
CRX → devserver 托管（官方策略文档允许 http scheme）
   ├─ GET /api/extension/update-manifest → gupdate XML（Content-Type: application/xml）
   └─ GET /api/extension/crx             → CRX 字节（Content-Type: application/x-chrome-extension，MS 实测必需）
   → HKCU ExtensionInstallForcelist（被加固时单次 UAC 提权写 HKLM 自动降级）
   → 浏览器重启后静默安装（用户不可卸载）
```

### 1.1 重大门控：未加域机器 forcelist 只允许商店扩展（官方行为，2026-07 官方文档）

> "For Windows instances not joined to a Microsoft Active Directory domain, forced installation is limited to
> apps and extensions listed in the Microsoft Edge Add-ons website." —— [ExtensionInstallForcelist 官方文档](https://learn.microsoft.com/en-us/deployedge/microsoft-edge-policies/extensioninstallforcelist)

本机实测印证：自托管 CRX 的 forcelist 条目在 `edge://policy` 显示 `[BLOCKED]`（状态"错误/警告"），更新服务零请求；
同一键写入 Edge 商店条目（uBlock Origin Lite）后 **90 秒内零交互静默安装成功**（连浏览器重启都不需要，动态策略刷新直接生效）。

**结论**：策略通道（`--policy`）只适合受管（加域/MDM）环境或商店上架后的部署；未加域机器的日常安装走默认的
外部扩展注册表通道（§1.0，不受此门控影响）。

### 1.2 平台范围

- Firefox/Safari 不支持：扩展是 MV3 Chromium（service_worker），Firefox 移植与 Safari/MDM 路线均超出本切片。
- macOS/Linux 需 root/MDM，暂缓。

## 2. 与调研报告（extension-install-research.md）的实测修订

| 调研报告结论 | 实测修订 |
|---|---|
| HKCU 无需管理员即可写浏览器策略 | 机制正确，但本机 `HKCU\Software\Policies` 已被加固（SYSTEM 属主、用户仅 ReadKey）→ 增加**单次 UAC 提权写 HKLM** 自动降级 |
| 需给 manifest.json 加固定 `key` 字段并据此算 ID | **不采用**：CRX 安装身份由签名公钥决定；公开 key 提交仓库而私钥无法提交（违反无密钥入库），ID 会对不上。改为从打包 pem 的 SPKI 推导 ID（SHA-256 前 32 hex → a-p），私钥存 build 目录不入库 |
| macOS External Extensions / Linux JSON | 不做：macOS 该机制 Chrome 33+ 仅限 Web Store，Linux 需 root；Windows 先行 |
| update_url 默认写 Chrome Web Store | 不成立：扩展未上架，Web Store URL 拉不到 CRX。改为 devserver 自托管（见 §1.0b） |
| 自托管 CRX 可在任意 Windows 机器策略静默安装 | **不成立（官方门控）**：未加域/未 MDM 的机器上 Edge 只允许商店扩展走 forcelist，自托管条目被 `[BLOCKED]`（见 §1.1）。Chrome 同理需域管理才接受非商店来源 |
| 未加域机器只能商店上架或引导安装 | **不成立（最大遗漏）**：HKCU 外部扩展注册表（path+version 指向本地 CRX）是第三条官方通道，免管理员/免商店/免网络，影刀/K-RPA 多年在用（§8）；本机实证 location=3 静默安装成功 |
| Chrome 消费版对外部注册表保留"添加扩展程序"确认流（待真机验证） | **成立（2026-09-07 真机坐实，Chrome 152.0.7977.76）**：前提是 manifest `update_url` 指向 CWS；此时外部注册表安装（location=3）后 Chrome 弹"启用还是移除"确认框，点启用即生效（Chrome 补写 `ack_external=true`）。指向 Edge 商店 URL 则被当"不知情添加"灰禁（disable `[256]`）。**Edge 152(.62) 同构**：指 CWS 同样可点启用；指自家 Edge 商店 URL（未上架）则启用后报"可能已损坏"（disable `[1024]`）。详见 §6.5 |

## 3. 使用

```bash
# 默认：外部扩展注册表路线（免管理员，重启浏览器后自动安装，可手动卸载）
#   Chrome/Edge 152 实测：安装后用户需在扩展页点一次"启用"（无法零点击）；零点击须商店上架 + forcelist
uv run python -m rpa_core.cli install-extension

# 备选：强制安装策略路线（受管环境/不可卸载；HKCU 被拒时弹一次 UAC 提权写 HKLM）
uv run python -m rpa_core.cli install-extension --policy

# 卸载（双路线双浏览器双 hive 全清）
uv run python -m rpa_core.cli install-extension --remove

# 可选覆盖（仅策略路线用到 update-url）
uv run python -m rpa_core.cli install-extension --policy --update-url http://127.0.0.1:9999/api/extension/update-manifest
uv run python -m rpa_core.cli install-extension --build-dir D:\somewhere
```

WorkBuddy 连接器：`workbuddy-connector/cli.json` 的 `init.win32` 追加 `&& rpa-core.exe install-extension`（macOS/Linux init 不加，见 §1 平台范围）。

devserver 相关端点：未打包时返回 `503 EXTENSION_NOT_PACKED`（提示先跑安装命令）。

## 4. 扩展 ID 推导（无第三方依赖）

pem（PKCS#1 或 PKCS#8，stdlib 纯手写 DER 解析）→ 重建 SubjectPublicKeyInfo DER →
`sha256(SPKI)[:32 hex]`，每个 hex 字符映射 `a-p`。
与 openssl（`openssl rsa -pubout -outform DER`）标准答案互验一致（合同测试 + 真机 cryptography 独立实现复核）。

## 5. 验证清单

- [x] 打包产出 CRX+pem（本机 Edge `--pack-extension` 实测，CRX 6727B）
- [x] 扩展 ID 计算：openssl 夹具（`mhiiampbndgpcpcdnilnkbnkafeofcem`）+ 真机 CRX（`jmeibmciancogmebghdcjihinpamiaji`）双实现一致
- [x] update manifest XML / CRX 端点（合同测试 + curl 实测 Content-Type）
- [x] HKCU→HKLM UAC 降级（本机实弹：HKCU 被加固 → UAC → HKLM 写入成功）
- [x] 策略注册表读写幂等/卸载（fake winreg 合同测试，不触碰真实注册表）
- [x] **默认路线（外部扩展注册表）真机 E2E**：CLI 零 UAC 写 HKCU 双浏览器 → 全新 profile Edge 启动 → 扩展静默出现（location=3 EXTERNAL_REGISTRY）
- [x] **策略→静默安装机制**（未加域机器双向实证，见 §6）：自托管 CRX 条目 `[BLOCKED]`（官方门控，符合预期）；商店条目（uBlock）90 秒内零交互静默安装成功，动态策略刷新生效
- [x] **Chrome + Edge 真机验证（全新 profile + 默认目录双实测，2026-09-07，Chrome 152.0.7977.76 / Edge 152.0.4191.62）**：外部注册表通道双浏览器安装成功（location=3）；manifest update_url 指向 CWS 时均可点启用（Chrome/Edge 补写 ack_external，无需上架）；Chrome 指 Edge 商店 URL 灰禁（disable [256]）、Edge 指自家商店 URL（未上架）报损坏（disable [1024]）；影刀 Chrome/Edge 插件真机对照同结论（详见 §6.5）
- [ ] WorkBuddy init 全链路：`install-extension` 已挂 init.win32；待扩展运行期配对（devserver WebSocket）联调

## 6. 本机实证与取证记录（防翻案）

### 6.0 影刀逆向 → external_registry_loader 通道（决定性发现）

- 影刀 6.2.23 安装 Edge 插件前后全量 diff（注册表/文件/Secure Preferences/进程命令行）：
  - CRX 不复制：`C:\Users\<user>\AppData\Local\ShadowBot\support_X64\Chrome\ShadowBot-V3.crx` 原地引用。
  - `HKCU\Software\Microsoft\Edge\Extensions\hofgfmmdolnmimplihglefekekfcfijf` 写
    `path=<影刀目录>\ShadowBot-V3.crx; version=3.1.0.0`（Edge 商店同 ID 扩展）；
    `HKCU\Software\Google\Chrome\Extensions\nhkjnlcggomjhckdeamipedlomphkepc` 写 V2 CRX。
  - 通信层：`shadowbot.chrome.bridge(_v2)` native messaging host 注册（Edge+Chrome 各一）。
  - 副作用：`extensions.ui.developer_mode` 被写成 true（其兜底路线用）。
  - Edge 的 Extensions 商店目录无新条目；扩展未即时出现（需重启浏览器让 loader 扫描）——
    与影刀提示"去 Edge 中启用插件"吻合。
- 我们复刻：HKCU 注册我们的 CRX path+version → 全新 profile Edge → `location=3` 静默安装成功。
- 该通道解释了影刀在"未加域 + 不翻墙 + 无商店上架依赖"下双浏览器可用的全部现象。

### 6.1 官方门控（已破案，非环境故障）

- 现象：策略键存在、Edge 进程运行，但更新服务对 devserver **零请求**；`edge://policy` 显示 `ExtensionInstallForcelist`（来源=设备、等级=强制）但值为 `["[BLOCKED]jmeibmc...;http://..."]`、状态"错误/警告"。
- 根因：官方文档 §1.1 门控——**未加域/未 MDM 机器，forcelist 只接受 Edge Add-ons 商店扩展**；自托管 CRX 被浏览器拒收。
- 反向验证：同键写商店条目 `odfafepnkmbhccpbejgmiehpchacaeak;https://edge.microsoft.com/extensionwebstorebase/v1/crx`（uBlock Origin Lite）→ 运行中的 Edge **动态策略刷新，90 秒内静默安装成功**，无需重启。机制链路（策略读取→更新请求→静默安装→不可卸载）全部正常。

### 6.2 本机策略键清除工具（未定位到进程）

- `HKLM\...\Microsoft\Edge\ExtensionInstallForcelist` 的值多次在写入后数分钟内被清空（键/值均消失；同批写入的 Chrome 键因 Chrome 未安装而始终存活；本日多次复现）。
- 排除：非域、非 MDM、无本地 Registry.pol、无第三方杀软进程（仅 Defender）、OEM ControlCenter 为硬件面板；清键工具疑似隐藏式管控/防劫持 agent（同机器 `HKCU\Software\Policies` 亦早已被加固为 SYSTEM 属主、用户仅 ReadKey）。
- 影响：本机做策略实验需在写入后数分钟窗口内完成观察；长期策略路线在本机不可持续（已装扩展是否被连带卸载未测）。
- `HKCU\Software\Policies`：SYSTEM 属主、本用户仅 ReadKey（非 Windows 默认；在我们介入前已如此）→ HKCU 路线本机不可写，必须走 UAC 提权 HKLM。

### 6.3 对"机制是否有效"的结论

机制代码按 Chromium 官方源码（`external_policy_loader.cc`：URL 仅校验 GURL 合法性）与官方策略文档实现；商店来源实机静默安装成功证明链路本身无缺陷。自托管路线的成败取决于机器是否受管（加域/MDM），非代码问题。

### 6.4 外部注册表路线在 Edge 152（消费版）被硬封锁（实验矩阵，2026-09-06）

> **⚠ 2026-09-07 修订注记**：本节的"CWS 依旧禁用（8192）"结论在 Edge 152.0.4191.62 上**未被复现**——
> 09-07 全新 profile 干净安装实测：manifest `update_url` 指向 CWS 的外部注册表扩展**可点启用成功**
> （`ack_external=true`、disable_reasons 清空），详见 §6.5。疑似差异原因：本节豁免实验是在**同一已禁用
> 实例上叠加改 update_url 后重打包**（CRX version 未变，Edge 不重新判定），而 §6.5 是全新安装。两处均保留，
> 供后续在统一条件下复核。**"未知来源硬禁"现象本身真实存在**（指向 Edge 商店 URL 的版本启用后报"可能已损坏"，
> disable `[1024]`，§6.5 双复测一致），本节结论的整体方向未变——消费版 Edge 对外部注册表本地 CRX 不信任，
> 只是信任判据更接近"update_url 是否指向本浏览器认的商店源"，与 Chrome 同构。

外部注册表安装（location=3）会带上 `disable_reasons` 被禁用，启用开关**灰色不可点**，
提示"此扩展不是来自任何已知来源，可能是在你不知情的情况下添加的"。逐项豁免实验全部失败：

| 实验 | 结果 |
|---|---|
| manifest `update_url` = CWS（Google URL） | 依旧禁用（8192） |
| manifest `update_url` = Edge 商店 URL | 依旧禁用（8192） |
| 开发人员模式开启（用户 UI 确认） | 依旧灰色 |
| `ExtensionInstallAllowlist` 策略白名单（HKLM） | 依旧禁用（8192） |
| 注册表 `path` 指向解包目录（不打包 CRX） | 根本不安装（loader 仅接受 .crx） |
| 对照：**商店扩展 + forcelist**（uBlock） | 静默安装且**直接启用**（零确认零点击） |

影刀的 Edge 扩展（同为 location=3）能直接启用，其条目带 `ack_external=True`、`allowlist=1`、
`developer_mode=true`（HMAC 签名保护字段）——正常安装流程写不出这些，只有伪造
Secure Preferences 完整性签名（Chromium pref HMAC）才能预置。判定影刀用了灰手法（或
依赖"ID 已是知名商店条目"的服务器侧校验，未证）。**我们不复刻该手法**（脆弱、易被杀软
标记、违背浏览器安全确认语义）。

> 09-07 修订：影刀"预写 ack_external"的手法在 Chrome/Edge .62 上均**非必要**（见 §6.5 结论 3）——
> 未预写任何 Secure Preferences 的 CWS update_url CRX，双浏览器均点一次即可启用。该手法是否在
> .53 时代确有必要（本机 09-06 实测）留待复核，代码侧维持不复刻。

**产品定论**（09-07 修订，综合 §6.5）：
- **零点击启用只有一条干净路：扩展上架商店 + forcelist 指向商店 update URL**（uBlock 对照实验证明）。
- **未上架期间，外部注册表本地 CRX 通道在双浏览器都可用，代价是用户点一次"启用"确认**：
  Chrome 152 与 Edge 152(.62) 均要求 manifest `update_url` 指向**本浏览器认的商店源**
  （Chrome→CWS、Edge→Edge Add-ons？——注意 Edge 指向自家 Edge 商店 URL 的未上架 CRX 实测报"损坏"
  [1024]，指向 CWS 反而成功，判据细节见 §6.5 结论 2）。无法零点击。
- 上架后本地 CRX 路线是否被豁免（"ID 已知"假说）待验证；受管（加域/MDM）环境走 forcelist 不受此限。

### 6.5 Chrome/Edge 152 对照实测：update_url 决定外部注册表来源是否放行（2026-09-07）

用影刀 Chrome/Edge 插件真机重装做对照，逐项验证双浏览器外部注册表通道行为。
Chrome 152.0.7977.76、Edge 152.0.4191.62。全部用全新临时 profile 干净安装（避免已禁用实例缓存干扰 §6.4）。

**对照矩阵（同一窗口目检，关键行双复测）**：

| 浏览器 | CRX manifest update_url | location | UI 初态 | 点启用后 |
|---|---|---|---|---|
| Chrome | Edge 商店 URL | 3 | **灰禁**，红字"未列在 Chrome 应用商店，可能是在您不知情的情况下添加的"（disable [256]） | 不可点 |
| Chrome | `clients2.google.com`（CWS） | 3 | 可点启用，无红字（disable [8192] 待启用） | **成功**：ack_external=true、disable_reasons 清空 |
| Chrome | 影刀 ShadowBot-V3（CWS，预写 INTERNAL/HMAC） | 3 | 需点一次启用 | 成功（同上，用户手动） |
| **Edge** | **CWS**（rpa_core 两次复测） | 3 | 可点启用（disable [8192] 待启用） | **成功**：ack_external=true、disable_reasons 清空，无错误 |
| **Edge** | **Edge 商店 URL（未上架）**（es2 两次复测） | 3 | 可点启用（disable [8192]） | **报"此扩展可能已损坏"**：disable [1024]、ack_external=true |

**关键结论**：

1. **判据是 update_url 归属，且因浏览器而异**：
   - Chrome：指向 **CWS** → 放行手动启用（即便 ID 未上架）；指向 Edge 商店 URL → 判"不知情添加"灰禁 [256]。
   - Edge：指向 **CWS** → 放行手动启用（与 §6.4 于 .53 的"CWS 依旧禁用"相悖，见 §6.4 修订注记）；
     指向 **自家 Edge 商店 URL 但扩展未上架** → 启用后报"可能已损坏" [1024]（Edge 尝试按商店签名/来源校验本地自签名 CRX 失败）。
2. **disable_reasons 码位语义跨浏览器/跨版本不可套用**：Chrome 灰禁 [256]、"待启用" [8192]；Edge"待启用" [8192]、损坏 [1024]。Edge .62 的 [8192] 是**可恢复的待启用**而非 §6.4 在 .53 观察到的硬禁（版本或安装方式差异，待统一复核）。
3. **影刀预写 `location=1(INTERNAL)+allowlist=1+developer_mode=true+Secure Preferences HMAC` 在双浏览器均非必要**：实测影刀插件最终也是 location=3、由浏览器弹确认、用户点一次启用，与未预写任何东西的 CWS CRX 行为一致。预写记录被启动时无视并按外部注册表重装。
4. 安装过程双浏览器一致：HKCU 注册表写 path+version → 浏览器启动 external_registry_loader 扫描 → location=3 静默安装、CRX 解包落盘（免管理员、免商店、免 devserver）。

**产品含义**：

- **manifest `update_url` 已改为 CWS（2026-09-07）后，外部注册表通道在 Chrome 与 Edge 152(.62) 均可用**，
  `install-extension` 变成"一次手动点击启用"。代价：无法零点击，且每次改 manifest/重装需用户再确认一次。
- **Edge 指向自家商店 URL 的未上架 CRX 会报"损坏"**——切勿为走 Edge 商店源而把 update_url 指回 Edge
  Add-ons（未上架前）。上架后 update_url 指自家商店源才是正解（商店提供真实签名 CRX）。
- 零点击仍须上架 + forcelist；受管环境不受影响。
- 遗留待复核：§6.4 的 .53 硬禁是否版本差异；Edge 上架后本地 CRX 路线是否豁免（"ID 已知"假说）。

### 6.5.1 重大修正：CWS 未上架本地 CRX 启用后 ~30s 被商店校验判损坏（2026-09-07）

§6.5 只验证到"点启用瞬间 ack_external=true"即下"指 CWS 即可用、无需上架"的结论——
**该结论被真机推翻**。启用后约 30s，Chrome/Edge 对 update_url 指向商店的外部注册表扩展做
**异步商店校验**：比对 manifest `key` 与商店中该 ID 的已发布扩展，未上架（自签名、ID 不在商店）
→ 判损坏 `disable [1024]`（`corrupted_disable_count` +1），并提示"此扩展程序可能已损坏"。
全新 profile 双浏览器均稳定复现。影刀持久不损坏的唯一原因：其 ID 真在 CWS + Edge Add-ons 上架
（docs §8"影刀 CWS 上架版 100 万用户 + Edge 商店同 ID"），key 可被商店校验。

| 变体 | Chrome/Edge 结果 |
|---|---|
| 无 update_url | [256] 灰禁不可点 |
| update_url 指 devserver http | [256] 灰禁不可点（非商店源不认） |
| update_url 指 CWS/商店（ID 未上架） | 可启用，但 ~30s 后商店校验判损坏 [1024] |
| **开发者模式 Load unpacked（源码目录）** | **持久可用，无商店校验**（影刀 Chrome 实际方案） |
| ID 真上架 + 外部注册表 | 持久可用（影刀 Edge/Chrome） |

**产品定论（2026-09-07 修订）**：未上架期间，外部注册表本地 CRX 路线在消费版双浏览器
**不可持久**（30s 即损坏）；**默认安装方式改为「开发者模式 Load unpacked」引导**——加载本地
`extension/` 源码目录，无需打包/上架/重启，持久可用（影刀 Chrome 同款）。`install-extension`
默认输出引导步骤 + 自动打开源码目录/复制路径/尝试打开扩展页；注册表路线保留为 `--registry`
（仅商店上架后可靠），`--unblock`/`external_uninstalls` 排查仍保留。**chrome:// 与 edge://
扩展页无法从外部命令可靠导航**（安全设计，冷启动/传 URL 均落新标签页），编辑器引导自动复制
扩展页地址并提示粘贴。



## 7. 已知限制与运维注意

- **零点击启用 = 商店上架 + forcelist**（§6.4/§6.5 定论）：Edge Add-ons 大陆可直连，未加域机器可静默安装且直接启用；Chrome 需 CWS（国内被墙，仅覆盖翻墙/海外用户）。
- **本地 CRX 静默路线（外部注册表）——09-07 二次修订**：Chrome 152 与 Edge 152(.62) 上指向商店 update_url 的**未上架**本地 CRX，启用后约 30s 会被异步商店校验判损坏（disable [1024]），**不可持久**（详见 §6.5.1）。**默认安装方式已改为开发者模式 Load unpacked 引导**（源码目录，持久可用）；外部注册表路线保留为 `--registry`，仅商店上架后可靠。§6.4 记录的 .53 消费版"未知来源灰禁"行为未在 .62 复现（差异待统一条件复核）。代码保留（合同测试覆盖），不预置 HMAC 确认记录（影刀式灰手法，不复刻）。
- **未上架期间的默认 UX（09-07 起）**：开发者模式 Load unpacked 引导（`install-extension` 默认 / 编辑器「⇲ 插件」），本地源码目录加载、持久可用；chrome:///edge:// 扩展页需手动输入（外部无法导航，编辑器自动复制地址）。
- **策略路线（`--policy`）**：受管环境强制安装、用户不可卸载；未加域机器仅商店来源有效（§1.1）。
- 从 forcelist 移除条目（或整个策略消失）会让浏览器**自动卸载**该扩展（官方行为）；所以管控工具清键不仅阻断安装，还可能连带卸载已装扩展。
- devserver 不在默认端口运行时，策略路线的扩展拉取会失败（重试无害）；`--update-url` 覆盖。外部注册表路线不依赖 devserver。
- 版本更新：改 `extension/manifest.json` version → 重跑安装命令 → 注册表 version 变化触发浏览器重装（注册表路线）或 devserver/商店提供新 CRX（策略/商店路线）。
- **装不上先查 `external_uninstalls`（2026-09-07 真机排查根因，安装已自动处理）**：若浏览器曾记录该扩展被"用户卸载"，`<User Data>\<profile>\Preferences` 的 `extensions.external_uninstalls` 会含其 ID，此后 **external_registry_loader 永久跳过重装**——注册表条目/CRX/端点全对也装不上。修复：关闭浏览器 → 从该 `Preferences` 的 `external_uninstalls` 移除本扩展 ID → 冷启动浏览器（loader 延迟约 40-60s 才写入 profile）。**`install-extension`/编辑器「安装」现已对未运行的浏览器自动清除该记录**（`browser_running` 检测；运行中无法可靠清除，会提示关闭后重开）。手动补救仍可用 `--unblock` / 编辑器「解除屏蔽」按钮。
- **Chrome 与 Edge 的注册表项相互独立**（`Software\Google\Chrome\Extensions\<id>` vs `Software\Microsoft\Edge\Extensions\<id>`），各 loader 只读自己的；共用的只是 CRX 文件路径与扩展 ID（同 pem 推导，有意为之）。`external_uninstalls` 亦随各浏览器 User Data 独立。风险仅在双商店上架需各自 key/update_url（同 ID/CRX 不可两全，§6.5）。
- 防劫持/管控类软件（清浏览器策略键）环境下策略路线不可用，属环境限制而非缺陷；外部注册表路线写的是 `Software\<vendor>\Extensions`（非 Policies 树），通常不在其清除范围。

## 8. 竞品安装方式拆解（影刀 / K-RPA Lite / 阿里云 RPA，2026-09 调证）

"影刀、K-RPA 在 Edge/Chrome 都能静默安装"的表象下，实为**客户端引导式安装**，浏览器层面并未绕过 §1.1 门控：

- 影刀 Chrome 官方文档要求安装后"确保【开发者模式】、【影刀插件】都处于打开的状态"（"开启开发者模式后可能需要重启浏览器才生效"）——即其插件是**开发者模式解包加载**，非商店/策略安装。
- CRX 从**自有阿里云 OSS CDN** 分发（`winrobot-pub-a.oss-cn-hangzhou.aliyuncs.com`），因为国内不可达 CWS；其 CWS 上架版（100 万用户）服务翻墙用户。
- 失败兜底 = 手动拖 CRX；另有"两个注册表"（native messaging host 注册，HKLM/HKCU），企业无权限时需 IT 协助——注册表只负责通信，不负责安装。
- K-RPA Lite、阿里云 RPA、八爪鱼 RPA 文档同构：全部要求开发者模式 + 加载本地扩展目录。

**可借鉴**：国内 Chrome（不翻墙）场景的业界可行路线 = 自有 CDN CRX + 客户端驱动的"开发者模式 + 加载解压目录"引导安装（可做成半自动兜底，优于纯手动）。注意 Chrome M137+ 品牌 stable 已禁用 `--load-extension` 启动参数，首次加载需自动化 UI 流程或用户点击。**WorkBuddy 扩展走 devserver WebSocket 通信，无 native messaging，无需额外注册表**。
