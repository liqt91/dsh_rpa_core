# S0 spike：Native Messaging 真机验证

M20 / ADR 0015 的**硬门槛**。本目录是独立脚手架（不接产品代码），用于在真实 Edge/Chrome
上验证四个决定方案可行性的问题。

**结论：2026-09-17 Edge 152 真机四项全过**（详见
`.harness/tasks/M20-native-messaging.md` 的「S0 完成证据」）。本目录保留为可复跑的实证工具。

| # | 问题 | 判定 |
|---|---|---|
| 1 | MV3 service worker 在长连 native port 下是否保活 | 观测器里 `PING` 间隔稳定 ≈15s（无空洞）= 保活 |
| 2 | 扩展 reload / 浏览器退出 → host 是否被回收 | `ENDPOINT ... closed-by-host` / `HOST-DOWN`，且无残留 `rpa-core-ext-host.exe` |
| 3 | 断线重连时延 | `HOST-DOWN` 到下一个 `HOST-UP` 的间隔 |
| 4 | Load unpacked 扩展 ID 从 Secure Preferences 可稳定读出 | `register_host.py` 打印的 `discovered=<id>` 与 `edge://extensions` 一致 |

## 关键实现约束（S0 实测得出）

- host manifest 的 `path` **必须是无参数可执行文件**：指向 venv 里的 console script
  `rpa-core-ext-host.exe`（由 `pyproject.toml` 的 `[project.scripts]` 生成）。
  **不要**写 `"pythonw.exe" "script.py"`——Chromium 不按命令行解析该字段，会把解释器
  无参拉起（退化成读 stdin 的 REPL），表现为「扩展连上但 host 无逻辑、无日志」。
- 扩展侧必须串行化 `connectNative`（连接中标志），否则并发调用会起多个 host 进程。

## 步骤

1. 确保已生成 host 入口：`uv sync --all-groups`（生成 `.venv/Scripts/rpa-core-ext-host.exe`）
2. **加载扩展**：`edge://extensions` → 开「开发人员模式」→「加载解压缩的扩展」→ 选 `extension/`
3. **注册 host**（HKCU，免管理员；自动发现扩展 ID）：
   ```powershell
   uv run python .harness/spike/native_messaging/register_host.py --browser edge
   ```
4. **起观测器**（错误上报 + 端点/心跳观测，写 `observe.log`）：
   ```powershell
   uv run python .harness/spike/native_messaging/observe.py --seconds 600
   ```
5. **触发连接**：扩展页点该扩展「重新加载」（或等 ≤30s 的 alarm 兜底）→ 徽标变绿 `on`
6. **观察** `observe.log`：
   - `HOST-UP rpa_core_ext_msedge_<uuid>` = host 被浏览器拉起、端点带浏览器+实例 id
   - `PING seq=…` 每 ≈15s 一条且无空洞 = SW 保活
   - `EXT-REPORT` 出现 `disconnect` 等 = 连接异常（含 `lastError` 原文）
7. **验回收**：扩展页再点「重新加载」→ 应见 `ENDPOINT closed-by-host` + 新 `HOST-UP`；
   完全退出浏览器 → 应见 `HOST-DOWN` 且无残留 `rpa-core-ext-host.exe`

## 清理

```powershell
uv run python .harness/spike/native_messaging/register_host.py --browser both --unregister
```
再到扩展页移除该扩展。`observe.log`、`host/generated/` 均为运行产物，可随时删除。
