# M12 编辑器美化与中文化

状态：`done`

## 目标

按 `docs/editor-design.md` 落地编辑器视觉升级与中文化：修复命令面板滚动条问题、全中文展示层（命令显示名 / 属性字段 / 条件操作符 / 术语表）、卡片化节点设计。不动命令契约（ADR 0002），中文层为编辑器本地映射，防漂移靠合同测试门禁。

## 前置

- [x] 设计方案：`docs/editor-design.md`（含契约同步保障 §4.3）
- 依赖：M11 树形画布已完成、dev server 静态 allowlist 机制（ADR 0008）已就位

## 任务（按切片）

- [x] 切片 0：`static/i18n.js`（命令/kind/effect/ops/flow/术语表映射）+ `static/icons.js`（内联 SVG）+ server allowlist 扩展 + `tests/contract/test_editor_i18n.py` 防漂移门禁（catalog 全量覆盖 + kind/effect/op 键对齐 + required 字段中文标签）
- [x] 切片 1：命令面板两行卡片（中文名主标题 + 英文 id 副标 + 分类图标）+ 横向滚动条根除（`min-width:0` + ellipsis + `overflow-x: hidden`）
- [x] 切片 2：画布节点卡片重构（类型图标块 + 中文标题 + 英文副标）+ 分支标签中文化（满足时/否则/出错时/尝试执行）+ if 摘要操作符中文化
- [x] 切片 3：属性面板字段中文化（公共键映射 + 英文键降为小字副标）+ if 操作符中文下拉 + 页脚术语表折叠区
- [x] 切片 4：多选悬浮操作条（画布底部居中，选中即出现；上移/下移/复制/删除；设计文档原案 ≥2，实现为 ≥1 以覆盖单节点快捷操作）
- [x] E2E 全面更新 + 完整门禁

## 验收标准

- [x] 命令面板无横向滚动条（E2E 断言 `scrollWidth - clientWidth <= 1`）
- [x] 画布与属性面板中文优先、英文 id 降级副标；26 条命令中文名全覆盖（门禁锁定）
- [x] i18n 映射与 catalog 漂移 = full gate 红（合同测试 `test_editor_i18n.py`）
- [x] 既有 E2E 向后兼容（打开/保存/拖拽/复制/撤销回归全绿）
- [x] 零构建边界不破（无 npm/框架/CDN），架构检查与 full gate 通过

## 范围外

- 深色主题（token 已预留）
- 面板宽度拖拽调节
- 命令详细使用文档面板
- manifest/工作流文件的任何字段变更

## 待定问题

- 无

## 完成证据

- `static/i18n.js` + `static/icons.js` + `server.py` allowlist 扩展
- `tests/contract/test_editor_i18n.py`：4 项防漂移断言（catalog 全量覆盖、kind/effect/op 枚举对齐、required 字段中文标签、术语表完整性）
- `tests/e2e/test_editor.py`：新增 2 个测试（中文展示 + 无横向滚动 + 术语表；悬浮操作条复制链路）
- 修复 `server.py` 413 排空 body 的真实健壮性缺口（Windows 下客户端写一半收 RST）
- 截图人工验收：中文命令名/分支标签/字段标签/悬浮条全部生效
- full gate 通过：99 tests + ruff + architecture + task check
