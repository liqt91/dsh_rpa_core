# M94 — 底栏默认高度与顶边拖拽

Status: `done`

## 背景

P2 把元素库变成分组树后，资产行多出来源副行、引用徽标、分组头、批量条，
原底栏固定 `height: 228px` 一屏只放得下三四行，用户反馈「元素资产 tab 高度太低，
看不全信息」。影刀的底部面板本来就是可折叠、可调高度的。

## 改动

- `style.css`：`#bottomDock` 从固定 228px 改为 `clamp(300px, 34vh, 440px)`，
  加 `position: relative` 作拖拽条包含块；新增 `.dockResize` 顶边 6px 手柄，
  折叠态隐藏。默认高覆盖三个 tab（元素库/全局参数/日志），不另开分支。
- `index.html`：底栏首位加 `#dockResize`（div，不是按钮，不进静态按钮白名单）。
- `main.js`：新增 `bindDockResize()`，上拖加高、下拖压低，范围 `140px ~ 视口-160px`；
  拉完写 `localStorage['rpa.dockHeight']`，下次打开恢复；折叠时不响应拖拽。
  沙箱守卫：`window`/`localStorage` 用 `typeof` 检查，兼容三个 DOM shim。
- 拖拽条压在 tab 顶端 6px，不占 flex 高度，`.dockTab` 的点击与 key 不变。

## 判据与验证

- `check_editor_bottom_dock.mjs`：DOM shim 预置 `#dockResize` 并让 `document` 收集
  `mousemove/mouseup`；新增 M94 用例真发 mousedown/move/up 断言 300→380→280、
  解绑、折叠态不拖，静态层同时钉住 CSS clamp / HTML 手柄 / JS 绑定。
- `probe_m94_negative.py`：5 个注入（CSS 改回 228、删手柄、不接绑定、拖拽不改高、
  折叠不挡拖）失败集逐项相等、md5 逐字节还原、还原后复跑绿。
- dist 用 `npx vite build --emptyOutDir false` 重建（沙箱删除守卫会卡清空目录）。
- 全量 `check_all.py`：`FULL GATE PASSED` + `rc=0`。
