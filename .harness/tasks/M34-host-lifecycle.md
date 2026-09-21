# M34 扩展宿主生命周期：空闲自杀与残留垫片

状态：`done`

关联：M20（Native Messaging 桥 / ADR 0015）、`extension_installer.native_host_executable`、
`workers/ext_bridge.py`、`docs/extension-channel-baseline.md`

## 0. 触发（真实现场）

维护者在仓库跑 `uv run rpa-core gui` 被拒：

```
error: failed to remove file `...\.venv\Lib\site-packages\../../Scripts/rpa-core-ext-host.exe`:
拒绝访问。 (os error 5)
```

`uv run` 会重装本项目，需要覆写两个 console script。而**只要浏览器还连着扩展，
宿主进程就锁着这两个 exe**——这不是异常，是正常态。

## 1. 根因（已核实）

### 1.1 Windows 上「一个宿主 = 两个进程」

console script 在 Windows 下是**垫片（shim）**：`rpa-core-ext-host.exe` 先起来，
再由它把真正跑 `ext_bridge:main` 的 `python.exe` 拉成子进程。

实测进程树（15:30 现场）：

```
pid=3876  rpa-core-ext-host.exe  <- ppid=23680 cmd.exe        （垫片）
pid=17808 python.exe             <- ppid=3876 rpa-core-ext-host.exe （真宿主）
```

**两者都持有 exe 文件的句柄**——所以 `taskkill` 只杀垫片不够，杀垫片若带 `/T`
还会级联杀掉真宿主（笔者已踩，见 §4）。

### 1.2 真宿主退出，垫片不跟着走 → 残留

`_extension_loop`（`ext_bridge.py:140`）的退出条件是 stdin 读到 EOF
（`message is None`）或传输错误。扩展断开后 python.exe 退出，但**垫片没回收**。

实测：08:34 与 15:26 各起过一个浏览器宿主，攒下 `17536`（垫片，无日志）与
`708`（垫片，无日志）两个孤儿；同期真宿主 `17036`/`21436` 正常服务。

日志侧证据：孤儿 PID 在整个 `ext-host.log` 里**零条目**（因为它们不写日志，只有子进程写）。

### 1.3 影响

每起一次浏览器多一对残留。攒到 `uv run` 触发重装时必被锁，用户看到的是
一句没头没脑的 `os error 5`，完全不知道要去关浏览器。

## 2. 决定

- **D1｜不靠手工杀进程**：`taskkill` 是下策；用户侧不该知道「垫片」的存在。
- **D2｜宿主空闲自杀**：既无客户端连接、又长时间无扩展消息 → 主动退出。
  垫片子进程退出后自然回收，链路整体收敛。
- **D3｜阈值取保守值**：默认 30 分钟无活动才自杀。宿主是「浏览器活着就在」的角色，
  杀早会让正在等用户操作的流程掉线（扩展会重连，但会话/端点要重建）。
  宁可多等，不可误杀。
- **D4｜可配置、可关闭**：环境变量 `RPA_CORE_HOST_IDLE_EXIT_SECONDS`；
  设 `0` 表示关闭自杀（调试宿主时用）。
- **D5｜自杀必须留日志**：宿主由浏览器拉起，stderr 无人接收——不留痕等于没有现场
  （沿用 `ext_bridge.py` 既有约定）。

## 3. 交付

- [ ] `ext_bridge.py`：新增空闲监视线程 + `RPA_CORE_HOST_IDLE_EXIT_SECONDS` 解析
- [ ] 单测：空闲判定、阈值解析（含 `0`=关闭、非法值回退默认）
- [ ] 文档：`docs/extension-channel-baseline.md` 补「宿主生命周期」一节
- [ ] 任务单/记忆：把 `os error 5` 的处置写清楚（先关浏览器，再 `uv run`）

## 4. 反面教材（本次踩坑，写下来避免重犯）

我为了清残留，执行了 `taskkill /PID <垫片> /T /F`。**`/T` 会杀整棵子树**，
而垫片的子树里就是正在服务的真宿主：

```
成功: 已终止 PID 17036 (属于 PID 11468 子进程)的进程。
成功: 已终止 PID 11468 (属于 PID 17536 子进程)的进程。
成功: 已终止 PID 17536 (属于 PID 16500 子进程)的进程。
```

后果：一个在服务的 Edge 宿主被误杀（扩展 5 秒后自动重连恢复）。

**教训**：事前已查过父子关系、看到过那条链，却没有推演 `/T` 的级联后果。
**用 `/T` 之前必须先把整棵树画出来**，并在命令里显式确认要杀的每一个 PID。

## 5. 未覆盖 / 已知缺口

- 空闲自杀的**真机验证**：需要浏览器开着、宿主静置过阈值，确认自动退出且不留残留。
  本次只做逻辑单测，不拿单测结论冒充真机结论。
- **macOS/Linux 无垫片问题**（console script 是直接的解释器脚本，POSIX 无 `.exe`），
  但空闲自杀逻辑跨平台生效，需在 mac 上确认不会误退。

## 6. 实现中踩的两个坑（写下来避免重犯）

### 6.1 假绿灯：合成脚本证明「关 fd 能唤醒阻塞读」

我先用一个**单线程**合成脚本（`os.close(0)` 后 `buf.read` 返回 EOF）证明「关 stdin fd
能唤醒阻塞中的 ``read_message``」，于是写了 `_wake_extension_loop`（`shutdown()` 里
`os.close(self._stdin.fileno())`）。**实测在真宿主（多线程、stdin 是管道读端）下完全失效**：
关掉的是**宿主自己的读端**，写端由浏览器/父进程握着，关读端不会让在读上阻塞的线程收到
EOF。宿主卡死、自杀等于没做。

教训（与 M30 S4 / M32 同根）：**负向验证的「通过」必须来自真场景**。单线程玩具脚本
和「真宿主多线程 + 管道」是两回事，前者过了不代表后者过了。正确做法是真 spawn 子进程、
走完整 `hello` 握手、看进程到底退没退。

### 6.2 死锁：`shutdown()` 从监视线程调会卡在 `server.close()`

第一版 `_idle_monitor_loop` 先调 `shutdown()` 再优雅退出。实测 `shutdown()` 在
`self._server.close()` 的 `with self._lock`（`_PipeServer._lock`）处**死锁**——后台
`accept_loop` 线程攥着这把锁、阻塞在 `WaitForSingleObject(overlapped.hEvent, 500ms)`，
监视线程拿不到锁就一直等，主线程又卡在 stdin 读，进程永远不退出。

逐行加 `IDLE-DBG` 日志定位到「`closing server...` 有、`server closed` 没有」即卡在
`close()` 取锁。最终改法：监视线程**不调 `shutdown()`**，只 `self._closed.set()`（信号
`accept_loop` 下一轮自行退出）+ `os._exit(0)` 强杀；管道句柄由 OS 回收。正常路径
（浏览器关闭 stdin → 主线程 `read_message` 收 EOF → `shutdown()`）不受影响，那条路
`accept_loop` 会在 ~500ms 内放锁，`close()` 正常走完。
