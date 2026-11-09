# 命令参考（89 条，完整契约）

catalog digest `9f4f273419c78a2100ab157dacce33f0ef294b725474b5af7278b6ca0d86b22f`。本文件由 `tools/gen_capability_reference.py` 生成，
**不要手改**（改了会被 `--check` 与门禁抓住）。语义级 schema 以
`rpa-core catalog` 输出的 JSON Schema 为准；本表负责「有哪条、要传什么、会返回什么、会怎么失败」。


## 浏览器

### `browser.attach` · 获取已打开的网页

- 执行器 `browser.playwright` · lifecycle · 风险 `browser-control`
- 重放 `unsafe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 是 | — | 同浏览器下的任一已有会话，用于定位 context |
| `pattern`（匹配内容） | string | 是 | — | 匹配内容 |
| `matchBy`（匹配方式） | string enum | 否 | `url` | 按网址/按标题匹配 取值：`url`（按网址） / `title`（按标题） |
| `useRegex`（正则匹配） | boolean | 否 | false | pattern 是否为正则表达式 |

**输出**：`sessionId`、`url`、`resourceType`

**失败码**：`ELEMENT_NOT_FOUND`, `SESSION_NOT_FOUND`, `CANCELLED`, `EXECUTOR_FAILED`, `ELEMENT_COVERED`, `ELEMENT_DISABLED`, `ELEMENT_NOT_VISIBLE`

### `browser.check` · 设置复选框

- 执行器 `browser.playwright` · action · 风险 `browser-control`
- 重放 `unsafe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 是 | — | — |
| `selector`（操作目标） | string | 是 | — | — |
| `operation`（操作） | string enum | 否 | `check` | 勾选/取消勾选/反选 取值：`check`（勾选） / `uncheck`（取消勾选） / `toggle`（反选） |
| `timeoutMs`（超时（毫秒）） | integer | 否 | — | — |

**输出**：`checked`、`matchedCount`

**失败码**：`ELEMENT_NOT_FOUND`, `SESSION_NOT_FOUND`, `TIMEOUT`, `CANCELLED`, `EXECUTOR_FAILED`, `ELEMENT_COVERED`, `ELEMENT_DISABLED`, `ELEMENT_NOT_VISIBLE`

### `browser.click` · 点击元素

- 执行器 `browser.playwright` · action · 风险 `browser-control`
- 重放 `unsafe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |
| `selector`（操作目标） | string | 是 | — | — |
| `timeoutMs`（超时（毫秒）） | integer | 否 | — | — |
| `clickType`（点击方式） | string enum | 否 | `single` | 点击方式：single=单击，double=双击 取值：`single`（单击） / `double`（双击） |
| `button`（鼠标按钮） | string enum | 否 | `left` | 鼠标按钮 取值：`left`（左键） / `right`（右键） / `middle`（中键） |
| `modifiers`（辅助按键） | array<string> | 否 | `[]` | 辅助按键组合 |
| `simulateHuman`（模拟人工点击） | boolean | 否 | true | 模拟人工点击：true=派发完整鼠标按键事件链（mousedown→mouseup→click，兼容只监听 mousedown/mouseup 的页面）；false=最短路径直接调 el.click()（更快，但只有 click 事件）。两者都是合成事件（isTrusted=false），不能绕过风控；元素被遮挡时会直接报 ELEMENT_COVERED。false 仅对左键单击、无辅助按键生效——右键/中键/双击/带辅助键时仍走事件链 |
| `clickPosition`（点击位置） | string enum | 否 | `center` | 点击位置：center=元素中心（默认）；random=元素内随机位置（中心 ±35% 区间，风控/热力图类页面用）。坐标会写进事件的 clientX/clientY；元素部分在视口外时按「元素与视口的交集」取点，交集为空则报 ELEMENT_NOT_VISIBLE 取值：`center`（元素中心） / `random`（随机位置） |
| `postDelayMs`（执行后延迟（毫秒）） | integer | 否 | 0 | 执行后延迟（毫秒） |

**输出**：`matchedCount`、`sessionId`

**失败码**：`ELEMENT_NOT_FOUND`, `SESSION_NOT_FOUND`, `TIMEOUT`, `CANCELLED`, `EXECUTOR_FAILED`, `ELEMENT_COVERED`, `ELEMENT_DISABLED`, `ELEMENT_NOT_VISIBLE`

### `browser.close` · 关闭网页

- 执行器 `browser.playwright` · lifecycle · 风险 `browser-control`
- 重放 `unsafe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |

**失败码**：`SESSION_NOT_FOUND`, `CANCELLED`, `EXECUTOR_FAILED`

### `browser.closeBrowser` · 终止浏览器

- 执行器 `browser.playwright` · lifecycle · 风险 `browser-control`
- 重放 `unsafe` · 幂等 `none` · 超时 30s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `browserType`（浏览器类型） | string enum | 否 | `msedge` | 要终止的浏览器类型（按进程名匹配）。终止范围是该类型的全部进程——包含用户自己打开的窗口，执行本命令即视为显式确认 取值：`msedge`（Edge） / `chrome`（Chrome） |
| `force`（强制终止） | boolean | 否 | false | true=强制终止（不等待浏览器保存会话）；默认 false 会先请求正常退出 |
| `timeoutMs`（超时（毫秒）） | integer | 否 | — | 等待进程退出的超时；超时后不再补强杀（force 已决定终止方式） |

**输出**：`terminated`、`matchedCount`、`killedProcessIds`、`failedProcessIds`

**失败码**：`INVALID_INPUT`, `SESSION_NOT_FOUND`, `PLATFORM_UNSUPPORTED`, `TIMEOUT`, `CANCELLED`, `EXECUTOR_FAILED`

### `browser.closeTabs` · 关闭标签页

- 执行器 `browser.playwright` · lifecycle · 风险 `browser-control`
- 重放 `unsafe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |
| `tabIds`（标签页 ID 列表） | array<integer> | 否 | — | 要关闭的标签页 id 列表（来自 browser.listPages）；与 all 二选一 |
| `all`（关闭全部标签页） | boolean | 否 | — | 关闭当前所有标签页（含用户自己打开的）；与 tabIds 二选一，默认 false |
| `ignoreBeforeUnload`（忽略离开页面确认） | boolean | 否 | true | 关闭前向目标标签页注入脚本清除 beforeunload 拦截，避免「离开此页面？」确认框卡住程序化关闭。best-effort：addEventListener 形式注册的拦截无法枚举清除，仍可能弹窗（对应标签页会进 failedTabIds）；false 时保留页面拦截 |
| `timeoutMs`（超时（毫秒）） | integer | 否 | — | — |

**输出**：`closedCount`、`closedTabIds`、`failedTabIds`

**失败码**：`INVALID_INPUT`, `SESSION_NOT_FOUND`, `TIMEOUT`, `CANCELLED`, `EXECUTOR_FAILED`

### `browser.cookieGet` · 读取 Cookie

- 执行器 `browser.playwright` · query · 风险 `read`
- 重放 `safe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 是 | — | 浏览器实例会话；同时决定 cookie 的作用域 URL（该会话所绑标签页的当前地址） |
| `name`（Cookie 名称） | string | 是 | — | Cookie 名称 |

**输出**：`value`

**失败码**：`ELEMENT_NOT_FOUND`, `SESSION_NOT_FOUND`, `TIMEOUT`, `CANCELLED`, `EXECUTOR_FAILED`, `ELEMENT_COVERED`, `ELEMENT_DISABLED`, `ELEMENT_NOT_VISIBLE`

### `browser.cookieGetAll` · 获取 Cookie 列表

- 执行器 `browser.playwright` · query · 风险 `read`
- 重放 `safe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 是 | — | 浏览器实例会话；同时决定 cookie 的作用域 URL（该会话所绑标签页的当前地址） |
| `name`（Cookie 名称） | string | 否 | — | 按名称精确匹配（可选；Chrome cookie 过滤器语义，不是子串） |
| `domain`（域筛选） | string | 否 | — | 按域筛选（可选）：本域及其子域，如 example.com 也会匹配 a.example.com |
| `path`（路径筛选） | string | 否 | — | 按路径精确匹配（可选；Chrome cookie 过滤器语义，不是子串） |

**输出**：`cookies`、`count`

**失败码**：`SESSION_NOT_FOUND`, `TIMEOUT`, `CANCELLED`, `EXECUTOR_FAILED`

### `browser.cookieRemove` · 删除 Cookie

- 执行器 `browser.playwright` · action · 风险 `browser-control`
- 重放 `unsafe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 是 | — | 浏览器实例会话；同时决定 cookie 的作用域 URL（该会话所绑标签页的当前地址） |
| `name`（Cookie 名称） | string | 否 | — | 要删除的 Cookie 名称；缺省则清空全部 Cookie |

**输出**：`sessionId`

**失败码**：`SESSION_NOT_FOUND`, `TIMEOUT`, `CANCELLED`, `EXECUTOR_FAILED`

**调用前**：这条会改外部世界（Cookie 名称），先把目标与后果说清楚，拿到用户明确同意再执行。

### `browser.cookieSet` · 设置 Cookie

- 执行器 `browser.playwright` · action · 风险 `browser-control`
- 重放 `idempotent` · 幂等 `derived` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 是 | — | 浏览器实例会话；同时决定 cookie 的作用域 URL（该会话所绑标签页的当前地址） |
| `cookies`（Cookie 数据） | array<object> | 是 | — | Cookie 对象数组，每项至少含 name/value，且需提供 url 或 domain+path；可选 expires/httpOnly/secure/sameSite |

**输出**：`count`

**失败码**：`INVALID_INPUT`, `SESSION_NOT_FOUND`, `TIMEOUT`, `CANCELLED`, `EXECUTOR_FAILED`

### `browser.download` · 下载文件

- 执行器 `browser.playwright` · action · 风险 `browser-control`
- 重放 `unsafe` · 幂等 `none` · 超时 60s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |
| `saveDir`（保存目录） | string | 是 | — | 下载保存目录 |
| `timeoutMs`（超时（毫秒）） | integer | 否 | — | — |

**输出**：`filePath`

**失败码**：`SESSION_NOT_FOUND`, `TIMEOUT`, `CANCELLED`, `EXECUTOR_FAILED`

**调用前**：这条会改外部世界（保存目录），先把目标与后果说清楚，拿到用户明确同意再执行。

### `browser.drag` · 拖拽元素

- 执行器 `browser.playwright` · action · 风险 `browser-control`
- 重放 `unsafe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 是 | — | — |
| `selector`（操作目标） | string | 是 | — | 被拖拽元素 |
| `targetSelector`（目标选择器） | string | 是 | — | 拖拽目标位置元素 |
| `timeoutMs`（超时（毫秒）） | integer | 否 | — | — |

**输出**：`matchedCount`、`sessionId`

**失败码**：`ELEMENT_NOT_FOUND`, `SESSION_NOT_FOUND`, `TIMEOUT`, `CANCELLED`, `EXECUTOR_FAILED`, `ELEMENT_COVERED`, `ELEMENT_DISABLED`, `ELEMENT_NOT_VISIBLE`

### `browser.executeScript` · 执行JS脚本

- 执行器 `browser.playwright` · action · 风险 `browser-control`
- 重放 `unsafe` · 幂等 `none` · 超时 30s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |
| `script`（JS 脚本） | string | 是 | — | 要执行的 JavaScript 代码 |
| `args`（脚本参数） | array<string> | 否 | `[]` | 传递给脚本的参数数组 |
| `timeoutMs`（超时（毫秒）） | integer | 否 | — | — |

**输出**：`result`、`sessionId`

**失败码**：`SESSION_NOT_FOUND`, `TIMEOUT`, `CANCELLED`, `EXECUTOR_FAILED`

### `browser.getPosition` · 获取元素位置

- 执行器 `browser.playwright` · query · 风险 `read`
- 重放 `safe` · 幂等 `none` · 超时 15s · 可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |
| `selector`（操作目标） | string | 是 | — | — |
| `timeoutMs`（超时（毫秒）） | integer | 否 | — | — |

**输出**：`x`、`y`、`width`、`height`、`right`、`bottom`、`centerX`、`centerY`

**失败码**：`ELEMENT_NOT_FOUND`, `SESSION_NOT_FOUND`, `TIMEOUT`, `CANCELLED`, `EXECUTOR_FAILED`, `ELEMENT_COVERED`, `ELEMENT_DISABLED`, `ELEMENT_NOT_VISIBLE`

### `browser.getScrollPosition` · 获取滚动条位置

- 执行器 `browser.playwright` · query · 风险 `read`
- 重放 `safe` · 幂等 `none` · 超时 15s · 可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |
| `selector`（操作目标） | string | 否 | — | 可选：指定内部有独立滚动条的元素，缺省读取整个页面的滚动位置 |
| `direction`（滚动条方向） | string enum | 否 | `vertical` | 滚动条方向（对标影刀「获取滚动条位置」的纵向/横向）：vertical=纵向（默认）；horizontal=横向。只影响 scrollPosition 的取轴，scrollX/scrollY 恒按各自轴返回 取值：`vertical`（纵向） / `horizontal`（横向） |
| `position`（位置（当前位置/底部位置）） | string enum | 否 | `current` | 取哪个位置（对标影刀「获取滚动条位置」的当前位置/底部位置）：current=当前位置（默认，即 scrollX/scrollY）；bottom=底部位置（即滚动条长度＝可滚动内容总长，取 scrollHeight/scrollWidth）。只影响 scrollPosition 取值：`current`（当前位置） / `bottom`（底部位置） |
| `timeoutMs`（超时（毫秒）） | integer | 否 | — | — |

**输出**：`scrollX`、`scrollY`、`scrollPosition`

**失败码**：`ELEMENT_NOT_FOUND`, `SESSION_NOT_FOUND`, `TIMEOUT`, `CANCELLED`, `EXECUTOR_FAILED`, `ELEMENT_COVERED`, `ELEMENT_DISABLED`, `ELEMENT_NOT_VISIBLE`

### `browser.getSelectOptions` · 获取下拉框选项

- 执行器 `browser.playwright` · query · 风险 `read`
- 重放 `safe` · 幂等 `none` · 超时 15s · 可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |
| `selector`（操作目标） | string | 是 | — | — |
| `timeoutMs`（超时（毫秒）） | integer | 否 | — | — |

**输出**：`options`、`count`

**失败码**：`ELEMENT_NOT_FOUND`, `SESSION_NOT_FOUND`, `TIMEOUT`, `CANCELLED`, `EXECUTOR_FAILED`, `ELEMENT_COVERED`, `ELEMENT_DISABLED`, `ELEMENT_NOT_VISIBLE`

### `browser.getText` · 读取元素文本

- 执行器 `browser.playwright` · query · 风险 `read`
- 重放 `safe` · 幂等 `none` · 超时 15s · 可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |
| `selector`（操作目标） | string | 是 | — | — |
| `infoType`（信息类型） | string enum | 否 | `text` | 信息类型（对标影刀「获取元素信息」的操作项）：text=可见文本（默认）；html=元素内部 HTML；outerHTML=元素完整 HTML；value=表单值；href=链接地址；attribute=指定属性值（须配合 attributeName） 取值：`text`（可见文本） / `html`（内部 HTML） / `outerHTML`（完整 HTML） / `value`（表单值） / `href`（链接地址） / `attribute`（指定属性值） |
| `attributeName`（属性名） | string | 否 | — | 属性名（infoType=attribute 时生效，对标影刀「获取元素属性」）：如 href、src、class、data-*；留空或属性不存在时返回空串 |
| `completeUrlPrefix`（智能补全地址前缀） | boolean | 否 | false | 智能识别并补充地址前缀（仅 infoType=href 时生效，对标影刀同名选项）：取到的地址缺少协议头时按当前页面协议补 https:// 或 http://（以 // 开头则补「协议 + :」） |
| `timeoutMs`（超时（毫秒）） | integer | 否 | — | — |

**输出**：`value`

**失败码**：`ELEMENT_NOT_FOUND`, `SESSION_NOT_FOUND`, `TIMEOUT`, `CANCELLED`, `EXECUTOR_FAILED`, `ELEMENT_COVERED`, `ELEMENT_DISABLED`, `ELEMENT_NOT_VISIBLE`

### `browser.handleDialog` · 处理对话框

- 执行器 `browser.playwright` · action · 风险 `browser-control`
- 重放 `unsafe` · 幂等 `none` · 超时 10s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |
| `action`（操作） | string enum | 否 | `accept` | 对话框操作 取值：`accept`（接受） / `dismiss`（取消） |
| `promptText`（输入文本） | string | 否 | — | prompt 对话框的输入文本 |

**失败码**：`SESSION_NOT_FOUND`, `CANCELLED`, `EXECUTOR_FAILED`

### `browser.hover` · 鼠标悬停

- 执行器 `browser.playwright` · action · 风险 `browser-control`
- 重放 `unsafe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |
| `selector`（操作目标） | string | 是 | — | — |
| `timeoutMs`（超时（毫秒）） | integer | 否 | — | — |

**输出**：`matchedCount`、`sessionId`

**失败码**：`ELEMENT_NOT_FOUND`, `SESSION_NOT_FOUND`, `TIMEOUT`, `CANCELLED`, `EXECUTOR_FAILED`, `ELEMENT_COVERED`, `ELEMENT_DISABLED`, `ELEMENT_NOT_VISIBLE`

### `browser.input` · 输入文本

- 执行器 `browser.playwright` · action · 风险 `browser-control`
- 重放 `unsafe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |
| `selector`（操作目标） | string | 是 | — | — |
| `text`（文本内容） | string | 是 | — | — |
| `timeoutMs`（超时（毫秒）） | integer | 否 | — | — |
| `mode`（输入方式） | string enum | 否 | `fill` | 输入方式：fill=直接填写（快速稳定），type=逐字模拟人工输入，clipboard=剪贴板粘贴 取值：`fill`（直接填写） / `type`（逐字模拟输入） / `clipboard`（剪贴板粘贴） |
| `append`（追加输入） | boolean | 否 | false | 追加输入（不清空现有内容） |
| `pressEnter`（输入后按 Enter） | boolean | 否 | false | 输入后按 Enter 键 |
| `keyIntervalMs`（按键间隔（毫秒）） | integer | 否 | 50 | 逐字输入时的按键间隔（毫秒），仅 mode=type 时生效 |
| `clickBeforeInput`（输入前点击元素） | boolean | 否 | false | 输入前先点击元素获取焦点 |
| `postDelayMs`（执行后延迟（毫秒）） | integer | 否 | 0 | 执行后延迟（毫秒） |

**输出**：`matchedCount`、`sessionId`

**失败码**：`ELEMENT_NOT_FOUND`, `SESSION_NOT_FOUND`, `TIMEOUT`, `CANCELLED`, `EXECUTOR_FAILED`, `ELEMENT_COVERED`, `ELEMENT_DISABLED`, `ELEMENT_NOT_VISIBLE`

### `browser.listPages` · 获取网页列表

- 执行器 `browser.playwright` · query · 风险 `read`
- 重放 `safe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 是 | — | — |

**输出**：`pages`、`count`

**失败码**：`SESSION_NOT_FOUND`, `TIMEOUT`, `CANCELLED`, `EXECUTOR_FAILED`

### `browser.navigate` · 打开网页

- 执行器 `browser.playwright` · action · 风险 `browser-control`
- 重放 `unsafe` · 幂等 `none` · 超时 30s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `browserType`（浏览器类型） | string enum | 是 | `msedge` | 指定由哪个浏览器的自研插件打开网页（Edge/Chrome）。Chrome 与 Edge 可各自安装插件，命令会路由到所选浏览器的插件执行；未安装插件的浏览器在编辑器中置灰，运行时会快速失败 取值：`msedge`（Edge） / `chrome`（Chrome） |
| `action`（操作） | string enum | 否 | `goto` | 跳转方式（对标影刀「跳转至新网址」）：goto=打开新网页（默认，需 url，在用户真实浏览器里新建标签页）；back=后退；forward=前进；reload=重新加载（刷新），三者需提供已有 sessionId 取值：`goto`（打开新网址） / `back`（后退） / `forward`（前进） / `reload`（重新加载（刷新）） |
| `url`（网址） | string | 否 | — | 目标网址（action=goto 必填）。可省略 http(s):// 前导协议（如填 baidu.com 自动补全为 https://baidu.com） |
| `timeoutMs`（超时（毫秒）） | integer | 否 | — | — |
| `onTimeout`（加载超时后） | string enum | 否 | `error` | 加载超时后执行（对标影刀「加载超时后执行」）：页在 timeoutMs 内未加载完成时的处理——error=视为超时失败（走引擎错误处理，可配重试/忽略）；stop=停止网页加载并继续后续流程 取值：`error`（报错（走错误处理）） / `stop`（停止网页加载并继续） |
| `commandLineArgs`（命令行参数） | array<string> | 否 | — | 浏览器命令行参数（仅当目标浏览器离线自动拉起时生效，如 --user-data-dir=<独立配置目录> 以可靠注入插件、--incognito 等）；目标浏览器已在线时忽略 |

**输出**：`sessionId`、`url`、`resourceType`、`browserInstance`、`browserType`、`tabId`

**失败码**：`TIMEOUT`, `CANCELLED`, `EXECUTOR_FAILED`

### `browser.queryAll` · 抓取列表文本

- 执行器 `browser.playwright` · query · 风险 `read`
- 重放 `safe` · 幂等 `none` · 超时 15s · 可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |
| `selector`（操作目标） | string | 是 | — | — |

**输出**：`items`、`count`

**失败码**：`SESSION_NOT_FOUND`, `TIMEOUT`, `CANCELLED`, `EXECUTOR_FAILED`

### `browser.screenshot` · 网页截图

- 执行器 `browser.playwright` · action · 风险 `browser-control`
- 重放 `idempotent` · 幂等 `derived` · 超时 30s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |
| `savePath`（保存路径） | string | 是 | — | 截图保存路径 |

**输出**：`filePath`

**失败码**：`SESSION_NOT_FOUND`, `TIMEOUT`, `CANCELLED`, `EXECUTOR_FAILED`

### `browser.scroll` · 滚动网页

- 执行器 `browser.playwright` · action · 风险 `browser-control`
- 重放 `unsafe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 是 | — | — |
| `position`（滚动位置） | string enum | 是 | `bottom` | 滚动位置：top=顶部 bottom=底部 point=指定坐标(x,y) page=向下滚动一屏 取值：`top`（顶部） / `bottom`（底部） / `point`（指定坐标） / `page`（向下滚动一屏） |
| `selector`（操作目标） | string | 否 | — | 可选：在指定元素内部滚动（元素有独立滚动条时），缺省滚动整个页面 |
| `x`（横向位置） | number | 否 | — | 横向目标位置（position=point 时生效） |
| `y`（纵向位置） | number | 否 | — | 纵向目标位置（position=point 时生效） |
| `smooth`（平滑滚动） | boolean | 否 | false | 平滑滚动（区别于瞬间滚动） |
| `timeoutMs`（超时（毫秒）） | integer | 否 | — | — |

**输出**：`scrollY`

**失败码**：`ELEMENT_NOT_FOUND`, `SESSION_NOT_FOUND`, `TIMEOUT`, `CANCELLED`, `EXECUTOR_FAILED`, `ELEMENT_COVERED`, `ELEMENT_DISABLED`, `ELEMENT_NOT_VISIBLE`

### `browser.select` · 设置下拉框

- 执行器 `browser.playwright` · action · 风险 `browser-control`
- 重放 `unsafe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |
| `selector`（操作目标） | string | 是 | — | — |
| `value`（选择值） | string | 是 | — | 选择项的值/标签/索引 |
| `selectBy`（选择方式） | string enum | 否 | `value` | 选择方式 取值：`value`（按值） / `label`（按文本） / `index`（按序号） |
| `timeoutMs`（超时（毫秒）） | integer | 否 | — | — |

**输出**：`matchedCount`、`sessionId`

**失败码**：`ELEMENT_NOT_FOUND`, `SESSION_NOT_FOUND`, `TIMEOUT`, `CANCELLED`, `EXECUTOR_FAILED`, `ELEMENT_COVERED`, `ELEMENT_DISABLED`, `ELEMENT_NOT_VISIBLE`

### `browser.setAttribute` · 设置元素属性

- 执行器 `browser.playwright` · action · 风险 `browser-control`
- 重放 `unsafe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |
| `selector`（操作目标） | string | 是 | — | — |
| `name`（属性名） | string | 是 | — | 属性名（对标影刀「设置元素属性_web」） |
| `value`（属性值） | string | 是 | — | 属性值 |
| `timeoutMs`（超时（毫秒）） | integer | 否 | — | — |

**输出**：`matchedCount`

**失败码**：`ELEMENT_NOT_FOUND`, `SESSION_NOT_FOUND`, `TIMEOUT`, `CANCELLED`, `EXECUTOR_FAILED`, `ELEMENT_COVERED`, `ELEMENT_DISABLED`, `ELEMENT_NOT_VISIBLE`

### `browser.setValue` · 设置元素值

- 执行器 `browser.playwright` · action · 风险 `browser-control`
- 重放 `unsafe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |
| `selector`（操作目标） | string | 是 | — | — |
| `setWay`（设置方式） | string enum | 否 | `value` | 设置方式（本命令扩展，影刀「设置元素值_web」无此参数）：value=表单元素值（默认）；innerText=可见文本；innerHTML=元素内部 HTML 取值：`value`（表单元素值） / `innerText`（可见文本） / `innerHTML`（内部 HTML） |
| `value`（要设置的值） | string | 是 | — | 要设置的值 |
| `timeoutMs`（超时（毫秒）） | integer | 否 | — | — |

**输出**：`matchedCount`

**失败码**：`ELEMENT_NOT_FOUND`, `SESSION_NOT_FOUND`, `TIMEOUT`, `CANCELLED`, `EXECUTOR_FAILED`, `ELEMENT_COVERED`, `ELEMENT_DISABLED`, `ELEMENT_NOT_VISIBLE`

### `browser.stopLoading` · 停止网页加载

- 执行器 `browser.playwright` · action · 风险 `browser-control`
- 重放 `unsafe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |
| `timeoutMs`（超时（毫秒）） | integer | 否 | — | — |

**输出**：`url`

**失败码**：`SESSION_NOT_FOUND`, `TIMEOUT`, `CANCELLED`, `EXECUTOR_FAILED`

### `browser.upload` · 上传文件

- 执行器 `browser.playwright` · action · 风险 `browser-control`
- 重放 `unsafe` · 幂等 `none` · 超时 30s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |
| `selector`（操作目标） | string | 是 | — | — |
| `files`（文件列表） | array<string> | 是 | — | 要上传的文件路径列表 |
| `timeoutMs`（超时（毫秒）） | integer | 否 | — | — |

**输出**：`matchedCount`、`sessionId`

**失败码**：`ELEMENT_NOT_FOUND`, `SESSION_NOT_FOUND`, `TIMEOUT`, `CANCELLED`, `EXECUTOR_FAILED`, `ELEMENT_COVERED`, `ELEMENT_DISABLED`, `ELEMENT_NOT_VISIBLE`

**调用前**：这条会改外部世界（文件列表），先把目标与后果说清楚，拿到用户明确同意再执行。

### `browser.waitFor` · 等待元素出现

- 执行器 `browser.playwright` · query · 风险 `read`
- 重放 `safe` · 幂等 `none` · 超时 15s · 可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |
| `selector`（操作目标） | string | 是 | — | — |
| `state`（窗口状态） | string enum | 否 | `visible` | 等待条件（对标影刀「等待元素」）：visible=可见（默认）；hidden=不可见；detached=从 DOM 移除；attached=存在于 DOM 取值：`visible`（可见） / `hidden`（不可见） / `detached`（已从 DOM 移除） / `attached`（存在于 DOM） |
| `timeoutMs`（超时（毫秒）） | integer | 否 | — | — |

**输出**：`matchedCount`

**失败码**：`ELEMENT_NOT_FOUND`, `SESSION_NOT_FOUND`, `TIMEOUT`, `CANCELLED`, `EXECUTOR_FAILED`, `ELEMENT_COVERED`, `ELEMENT_DISABLED`, `ELEMENT_NOT_VISIBLE`

### `browser.waitLoad` · 等待页面加载

- 执行器 `browser.playwright` · query · 风险 `read`
- 重放 `safe` · 幂等 `none` · 超时 30s · 可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 是 | — | — |
| `timeoutMs`（超时（毫秒）） | integer | 否 | — | — |

**输出**：`url`

**失败码**：`SESSION_NOT_FOUND`, `TIMEOUT`, `CANCELLED`, `EXECUTOR_FAILED`


## 桌面（Win32）

### `desktop.win32.activateWindow` · 激活窗口（Win32）

- 执行器 `desktop.win32` · action · 风险 `desktop-control`
- 重放 `unsafe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |

**失败码**：`SESSION_NOT_FOUND`, `SESSION_LOST`, `EXECUTOR_FAILED`

### `desktop.win32.attachWindow` · 附着窗口（Win32）

- 执行器 `desktop.win32` · lifecycle · 风险 `desktop-control`
- 重放 `unsafe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `title`（标题） | string | 否 | — | — |
| `className`（类名） | string | 否 | — | 窗口类型名（可选）；两个后端都是等值比较（不受 matchMode 影响）；与 classNameRe 互斥 |
| `classNameRe`（类名（正则）） | string | 否 | — | 窗口类型名的正则（search），可选；用于跨机器匹配含动态哈希的类名；与 className 互斥 |
| `handle`（窗口句柄） | integer | 否 | — | 窗口句柄（可选，win32 独有） |
| `processId`（进程 ID） | integer | 否 | — | 进程 ID（可选） |
| `matchMode`（匹配方式） | string enum | 否 | `exact` | 标题匹配方式（只作用于 title，className 恒为等值） 取值：`exact`（精确匹配） / `contains`（包含匹配） / `regex`（正则匹配） |
| `timeoutMs`（超时（毫秒）） | integer | 否 | — | — |
| `operationTimeoutMs`（操作超时（毫秒）） | integer | 否 | — | 操作超时（毫秒），覆盖默认 15s；复杂桌面环境可增大 |

**输出**：`sessionId`、`resourceType`、`processId`、`workWindowId`

**失败码**：`INVALID_INPUT`, `ELEMENT_NOT_FOUND`, `ELEMENT_AMBIGUOUS`, `TIMEOUT`, `CANCELLED`, `PLATFORM_UNSUPPORTED`, `EXECUTOR_FAILED`

**调用前**：这条会改外部世界（操作超时（毫秒）），先把目标与后果说清楚，拿到用户明确同意再执行。

### `desktop.win32.click` · 点击控件（Win32）

- 执行器 `desktop.win32` · action · 风险 `desktop-control`
- 重放 `unsafe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |
| `elementId`（操作目标） | string | 是 | — | — |
| `timeoutMs`（超时（毫秒）） | integer | 否 | — | — |
| `clickType`（点击方式） | string enum | 否 | `single` | 点击方式：single=单击，double=双击 取值：`single`（单击） / `double`（双击） |
| `button`（鼠标按钮） | string enum | 否 | `left` | 鼠标按钮 取值：`left`（左键） / `right`（右键） |
| `modifiers`（辅助按键） | array<string> | 否 | `[]` | 辅助按键组合 |
| `simulateHuman`（模拟人工点击） | boolean | 否 | true | 默认 true=真实鼠标路径（会移动光标，支持双击/非左键/辅助键/随机落点）；false=优先 invoke() 直接调用（不移动鼠标），仅对普通左键单击生效——双击/非左键/辅助键会自动退回真实鼠标路径，退回原因写入执行证据。与 clickPosition=random 互斥。 |
| `clickPosition`（点击位置） | string enum | 否 | `center` | 点击落点：center=元素中心（默认）；random=元素矩形内偏中心带的随机点，需要真实鼠标路径，因此与 simulateHuman=false 互斥（同时使用报 INVALID_INPUT）；元素 click_input() 不接受 coords 时退回中心并把原因写入执行证据。 取值：`center`（元素中心） / `random`（随机位置） |
| `postDelayMs`（执行后延迟（毫秒）） | integer | 否 | 0 | 执行后延迟（毫秒） |

**失败码**：`INVALID_INPUT`, `ELEMENT_NOT_FOUND`, `ANCHOR_NOT_FOUND`, `SESSION_NOT_FOUND`, `SESSION_LOST`, `TIMEOUT`, `CANCELLED`, `EXECUTOR_FAILED`

### `desktop.win32.closeSession` · 关闭会话（Win32）

- 执行器 `desktop.win32` · lifecycle · 风险 `desktop-control`
- 重放 `unsafe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |
| `forceKill`（强制终止进程） | boolean | 否 | false | 强制终止进程而不仅是关闭窗口 |

**失败码**：`SESSION_NOT_FOUND`, `SESSION_LOST`, `TIMEOUT`, `CANCELLED`, `EXECUTOR_FAILED`

### `desktop.win32.drag` · 拖拽元素（Win32）

- 执行器 `desktop.win32` · action · 风险 `desktop-control`
- 重放 `unsafe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |
| `elementId`（操作目标） | string | 是 | — | — |
| `targetX`（目标 X 坐标） | integer | 是 | — | 目标 X 坐标 |
| `targetY`（目标 Y 坐标） | integer | 是 | — | 目标 Y 坐标 |

**失败码**：`ELEMENT_NOT_FOUND`, `SESSION_NOT_FOUND`, `SESSION_LOST`, `EXECUTOR_FAILED`

### `desktop.win32.findElement` · 查找控件（Win32）

- 执行器 `desktop.win32` · query · 风险 `read`
- 重放 `safe` · 幂等 `none` · 超时 15s · 可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |
| `locator`（操作目标） | object | 是 | — | — |
| `timeoutMs`（超时（毫秒）） | integer | 否 | — | — |

**输出**：`elementId`、`matchedCount`

**失败码**：`INVALID_INPUT`, `ELEMENT_NOT_FOUND`, `ELEMENT_AMBIGUOUS`, `ANCHOR_NOT_FOUND`, `SESSION_NOT_FOUND`, `SESSION_LOST`, `TIMEOUT`, `CANCELLED`, `EXECUTOR_FAILED`

### `desktop.win32.getSelectedText` · 获取选中文本（Win32）

- 执行器 `desktop.win32` · query · 风险 `read`
- 重放 `safe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |
| `elementId`（操作目标） | string | 是 | — | — |

**输出**：`text`

**失败码**：`ELEMENT_NOT_FOUND`, `SESSION_NOT_FOUND`, `SESSION_LOST`, `EXECUTOR_FAILED`

### `desktop.win32.getText` · 读取控件文本（Win32）

- 执行器 `desktop.win32` · query · 风险 `read`
- 重放 `safe` · 幂等 `none` · 超时 15s · 可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |
| `elementId`（操作目标） | string | 是 | — | — |
| `timeoutMs`（超时（毫秒）） | integer | 否 | — | — |

**输出**：`value`

**失败码**：`ELEMENT_NOT_FOUND`, `ANCHOR_NOT_FOUND`, `SESSION_NOT_FOUND`, `SESSION_LOST`, `TIMEOUT`, `CANCELLED`, `EXECUTOR_FAILED`

### `desktop.win32.getWindowList` · 获取窗口列表（Win32）

- 执行器 `desktop.win32` · query · 风险 `read`
- 重放 `safe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `titlePattern`（标题匹配模式） | string | 否 | — | 窗口标题匹配模式（可选） |
| `matchMode`（匹配方式） | string enum | 否 | `contains` | 匹配方式 取值：`exact`（精确匹配） / `contains`（包含匹配） / `regex`（正则匹配） |

**输出**：`windows`

**失败码**：`PLATFORM_UNSUPPORTED`, `EXECUTOR_FAILED`

### `desktop.win32.getWindowTitle` · 获取窗口信息（Win32）

- 执行器 `desktop.win32` · query · 风险 `read`
- 重放 `safe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |

**输出**：`title`、`className`、`processId`

**失败码**：`SESSION_NOT_FOUND`, `SESSION_LOST`, `EXECUTOR_FAILED`

### `desktop.win32.hotkey` · 组合按键（Win32）

- 执行器 `desktop.win32` · action · 风险 `desktop-control`
- 重放 `unsafe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |
| `keys`（按键） | string | 是 | — | — |

**失败码**：`SESSION_NOT_FOUND`, `TIMEOUT`, `CANCELLED`, `EXECUTOR_FAILED`

### `desktop.win32.input` · 控件输入（Win32）

- 执行器 `desktop.win32` · action · 风险 `desktop-control`
- 重放 `unsafe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |
| `elementId`（操作目标） | string | 是 | — | — |
| `text`（文本内容） | string | 是 | — | — |
| `timeoutMs`（超时（毫秒）） | integer | 否 | — | — |
| `mode`（输入方式） | string enum | 否 | `simulateHuman` | 输入方式 取值：`simulateHuman`（模拟人工） / `clipboard`（剪贴板粘贴） / `automation`（自动化接口） |
| `append`（追加输入） | boolean | 否 | false | 追加输入 |
| `keyIntervalMs`（按键间隔（毫秒）） | integer | 否 | 50 | 按键间隔（毫秒） |
| `clickBeforeInput`（输入前点击元素） | boolean | 否 | false | 输入前先点击元素获取焦点 |
| `postDelayMs`（执行后延迟（毫秒）） | integer | 否 | 0 | 执行后延迟（毫秒） |

**失败码**：`ELEMENT_NOT_FOUND`, `ANCHOR_NOT_FOUND`, `SESSION_NOT_FOUND`, `SESSION_LOST`, `TIMEOUT`, `CANCELLED`, `EXECUTOR_FAILED`

### `desktop.win32.menuSelect` · 菜单选择（Win32）

- 执行器 `desktop.win32` · action · 风险 `desktop-control`
- 重放 `unsafe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |
| `menuPath`（菜单路径） | array<string> | 是 | — | — |

**失败码**：`INVALID_INPUT`, `SESSION_NOT_FOUND`, `TIMEOUT`, `CANCELLED`, `EXECUTOR_FAILED`

### `desktop.win32.moveWindow` · 移动窗口（Win32）

- 执行器 `desktop.win32` · action · 风险 `desktop-control`
- 重放 `unsafe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |
| `x`（X 坐标） | integer | 是 | — | 目标 X 坐标 |
| `y`（Y 坐标） | integer | 是 | — | 目标 Y 坐标 |

**失败码**：`SESSION_NOT_FOUND`, `SESSION_LOST`, `EXECUTOR_FAILED`

### `desktop.win32.resizeWindow` · 调整窗口大小（Win32）

- 执行器 `desktop.win32` · action · 风险 `desktop-control`
- 重放 `unsafe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |
| `width`（宽度） | integer | 是 | — | 窗口宽度 |
| `height`（高度） | integer | 是 | — | 窗口高度 |

**失败码**：`SESSION_NOT_FOUND`, `SESSION_LOST`, `EXECUTOR_FAILED`

### `desktop.win32.screenshot` · 元素截图（Win32）

- 执行器 `desktop.win32` · action · 风险 `desktop-control`
- 重放 `idempotent` · 幂等 `derived` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |
| `elementId`（操作目标） | string | 是 | — | — |
| `savePath`（保存路径） | string | 是 | — | 截图保存路径 |

**输出**：`filePath`

**失败码**：`ELEMENT_NOT_FOUND`, `SESSION_NOT_FOUND`, `SESSION_LOST`, `EXECUTOR_FAILED`

### `desktop.win32.select` · 设置下拉框（Win32）

- 执行器 `desktop.win32` · action · 风险 `desktop-control`
- 重放 `unsafe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |
| `elementId`（操作目标） | string | 是 | — | — |
| `value`（选择值） | string | 是 | — | 选择项的值/标签/索引 |
| `selectBy`（选择方式） | string enum | 否 | `value` | 选择方式 取值：`value`（按值） / `label`（按文本） / `index`（按序号） |

**失败码**：`ELEMENT_NOT_FOUND`, `SESSION_NOT_FOUND`, `SESSION_LOST`, `EXECUTOR_FAILED`

### `desktop.win32.setWindowState` · 设置窗口状态（Win32）

- 执行器 `desktop.win32` · action · 风险 `desktop-control`
- 重放 `unsafe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |
| `state`（窗口状态） | string enum | 否 | `normal` | 窗口状态 取值：`maximized`（最大化） / `minimized`（最小化） / `normal`（常规） |

**失败码**：`SESSION_NOT_FOUND`, `SESSION_LOST`, `EXECUTOR_FAILED`

### `desktop.win32.setWindowVisible` · 设置窗口显隐（Win32）

- 执行器 `desktop.win32` · action · 风险 `desktop-control`
- 重放 `unsafe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |
| `visible`（是否显示） | boolean | 否 | true | 是否显示窗口 |

**失败码**：`SESSION_NOT_FOUND`, `SESSION_LOST`, `EXECUTOR_FAILED`


## 桌面（UIA）

### `desktop.activateWindow` · 激活窗口（UIA）

- 执行器 `desktop.uia` · action · 风险 `desktop-control`
- 重放 `unsafe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |

**失败码**：`SESSION_NOT_FOUND`, `SESSION_LOST`, `EXECUTOR_FAILED`

### `desktop.attachWindow` · 附着窗口（UIA）

- 执行器 `desktop.uia` · lifecycle · 风险 `desktop-control`
- 重放 `unsafe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `title`（标题） | string | 否 | — | — |
| `className`（类名） | string | 否 | — | 窗口类型名（可选）；两个后端都是等值比较（不受 matchMode 影响）；与 classNameRe 互斥 |
| `classNameRe`（类名（正则）） | string | 否 | — | 窗口类型名的正则（search），可选；用于跨机器匹配含动态哈希的类名；与 className 互斥 |
| `processId`（进程 ID） | integer | 否 | — | 进程 ID（可选） |
| `matchMode`（匹配方式） | string enum | 否 | `exact` | 标题匹配方式（只作用于 title，className 恒为等值） 取值：`exact`（精确匹配） / `contains`（包含匹配） / `regex`（正则匹配） |
| `timeoutMs`（超时（毫秒）） | integer | 否 | — | — |
| `operationTimeoutMs`（操作超时（毫秒）） | integer | 否 | — | 操作超时（毫秒），覆盖默认 15s；复杂桌面环境可增大 |

**输出**：`sessionId`、`resourceType`、`processId`、`workWindowId`

**失败码**：`INVALID_INPUT`, `ELEMENT_NOT_FOUND`, `ELEMENT_AMBIGUOUS`, `TIMEOUT`, `CANCELLED`, `PLATFORM_UNSUPPORTED`, `EXECUTOR_FAILED`

**调用前**：这条会改外部世界（操作超时（毫秒）），先把目标与后果说清楚，拿到用户明确同意再执行。

### `desktop.click` · 点击控件（UIA）

- 执行器 `desktop.uia` · action · 风险 `desktop-control`
- 重放 `unsafe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |
| `elementId`（操作目标） | string | 是 | — | — |
| `timeoutMs`（超时（毫秒）） | integer | 否 | — | — |
| `clickType`（点击方式） | string enum | 否 | `single` | 点击方式：single=单击，double=双击 取值：`single`（单击） / `double`（双击） |
| `button`（鼠标按钮） | string enum | 否 | `left` | 鼠标按钮 取值：`left`（左键） / `right`（右键） |
| `modifiers`（辅助按键） | array<string> | 否 | `[]` | 辅助按键组合 |
| `simulateHuman`（模拟人工点击） | boolean | 否 | true | 默认 true=真实鼠标路径（会移动光标，支持双击/非左键/辅助键/随机落点）；false=优先 invoke() 直接调用（不移动鼠标），仅对普通左键单击生效——双击/非左键/辅助键会自动退回真实鼠标路径，退回原因写入执行证据。与 clickPosition=random 互斥。 |
| `clickPosition`（点击位置） | string enum | 否 | `center` | 点击落点：center=元素中心（默认）；random=元素矩形内偏中心带的随机点，需要真实鼠标路径，因此与 simulateHuman=false 互斥（同时使用报 INVALID_INPUT）；元素 click_input() 不接受 coords 时退回中心并把原因写入执行证据。 取值：`center`（元素中心） / `random`（随机位置） |
| `postDelayMs`（执行后延迟（毫秒）） | integer | 否 | 0 | 执行后延迟（毫秒） |

**失败码**：`INVALID_INPUT`, `ELEMENT_NOT_FOUND`, `ANCHOR_NOT_FOUND`, `SESSION_NOT_FOUND`, `SESSION_LOST`, `TIMEOUT`, `CANCELLED`, `EXECUTOR_FAILED`

### `desktop.closeSession` · 关闭会话（UIA）

- 执行器 `desktop.uia` · lifecycle · 风险 `desktop-control`
- 重放 `unsafe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |
| `forceKill`（强制终止进程） | boolean | 否 | false | 强制终止进程而不仅是关闭窗口 |

**失败码**：`SESSION_NOT_FOUND`, `SESSION_LOST`, `TIMEOUT`, `CANCELLED`, `EXECUTOR_FAILED`

### `desktop.drag` · 拖拽元素（UIA）

- 执行器 `desktop.uia` · action · 风险 `desktop-control`
- 重放 `unsafe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |
| `elementId`（操作目标） | string | 是 | — | — |
| `targetX`（目标 X 坐标） | integer | 是 | — | 目标 X 坐标 |
| `targetY`（目标 Y 坐标） | integer | 是 | — | 目标 Y 坐标 |

**失败码**：`ELEMENT_NOT_FOUND`, `SESSION_NOT_FOUND`, `SESSION_LOST`, `EXECUTOR_FAILED`

### `desktop.findElement` · 查找控件（UIA）

- 执行器 `desktop.uia` · query · 风险 `read`
- 重放 `safe` · 幂等 `none` · 超时 15s · 可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |
| `locator`（操作目标） | object | 是 | — | — |
| `timeoutMs`（超时（毫秒）） | integer | 否 | — | — |

**输出**：`elementId`、`matchedCount`

**失败码**：`INVALID_INPUT`, `ELEMENT_NOT_FOUND`, `ELEMENT_AMBIGUOUS`, `ANCHOR_NOT_FOUND`, `SESSION_NOT_FOUND`, `SESSION_LOST`, `TIMEOUT`, `CANCELLED`, `EXECUTOR_FAILED`

### `desktop.getSelectedText` · 获取选中文本（UIA）

- 执行器 `desktop.uia` · query · 风险 `read`
- 重放 `safe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |
| `elementId`（操作目标） | string | 是 | — | — |

**输出**：`text`

**失败码**：`ELEMENT_NOT_FOUND`, `SESSION_NOT_FOUND`, `SESSION_LOST`, `EXECUTOR_FAILED`

### `desktop.getText` · 读取控件文本（UIA）

- 执行器 `desktop.uia` · query · 风险 `read`
- 重放 `safe` · 幂等 `none` · 超时 15s · 可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |
| `elementId`（操作目标） | string | 是 | — | — |
| `timeoutMs`（超时（毫秒）） | integer | 否 | — | — |

**输出**：`value`

**失败码**：`ELEMENT_NOT_FOUND`, `ANCHOR_NOT_FOUND`, `SESSION_NOT_FOUND`, `SESSION_LOST`, `TIMEOUT`, `CANCELLED`, `EXECUTOR_FAILED`

### `desktop.getWindowList` · 获取窗口列表（UIA）

- 执行器 `desktop.uia` · query · 风险 `read`
- 重放 `safe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `titlePattern`（标题匹配模式） | string | 否 | — | 窗口标题匹配模式（可选，为空返回所有可见窗口） |
| `matchMode`（匹配方式） | string enum | 否 | `contains` | 匹配方式 取值：`exact`（精确匹配） / `contains`（包含匹配） / `regex`（正则匹配） |

**输出**：`windows`

**失败码**：`PLATFORM_UNSUPPORTED`, `EXECUTOR_FAILED`

### `desktop.getWindowTitle` · 获取窗口信息（UIA）

- 执行器 `desktop.uia` · query · 风险 `read`
- 重放 `safe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |

**输出**：`title`、`className`、`processId`

**失败码**：`SESSION_NOT_FOUND`, `SESSION_LOST`, `EXECUTOR_FAILED`

### `desktop.input` · 控件输入（UIA）

- 执行器 `desktop.uia` · action · 风险 `desktop-control`
- 重放 `unsafe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |
| `elementId`（操作目标） | string | 是 | — | — |
| `text`（文本内容） | string | 是 | — | — |
| `timeoutMs`（超时（毫秒）） | integer | 否 | — | — |
| `mode`（输入方式） | string enum | 否 | `simulateHuman` | 输入方式：simulateHuman=模拟人工，clipboard=剪贴板粘贴，automation=自动化接口 取值：`simulateHuman`（模拟人工） / `clipboard`（剪贴板粘贴） / `automation`（自动化接口） |
| `append`（追加输入） | boolean | 否 | false | 追加输入 |
| `keyIntervalMs`（按键间隔（毫秒）） | integer | 否 | 50 | 按键间隔（毫秒） |
| `clickBeforeInput`（输入前点击元素） | boolean | 否 | false | 输入前先点击元素获取焦点 |
| `postDelayMs`（执行后延迟（毫秒）） | integer | 否 | 0 | 执行后延迟（毫秒） |

**失败码**：`ELEMENT_NOT_FOUND`, `ANCHOR_NOT_FOUND`, `SESSION_NOT_FOUND`, `SESSION_LOST`, `TIMEOUT`, `CANCELLED`, `EXECUTOR_FAILED`

### `desktop.moveWindow` · 移动窗口（UIA）

- 执行器 `desktop.uia` · action · 风险 `desktop-control`
- 重放 `unsafe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |
| `x`（X 坐标） | integer | 是 | — | 目标 X 坐标 |
| `y`（Y 坐标） | integer | 是 | — | 目标 Y 坐标 |

**失败码**：`SESSION_NOT_FOUND`, `SESSION_LOST`, `EXECUTOR_FAILED`

### `desktop.resizeWindow` · 调整窗口大小（UIA）

- 执行器 `desktop.uia` · action · 风险 `desktop-control`
- 重放 `unsafe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |
| `width`（宽度） | integer | 是 | — | 窗口宽度 |
| `height`（高度） | integer | 是 | — | 窗口高度 |

**失败码**：`SESSION_NOT_FOUND`, `SESSION_LOST`, `EXECUTOR_FAILED`

### `desktop.screenshot` · 元素截图（UIA）

- 执行器 `desktop.uia` · action · 风险 `desktop-control`
- 重放 `idempotent` · 幂等 `derived` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |
| `elementId`（操作目标） | string | 是 | — | — |
| `savePath`（保存路径） | string | 是 | — | 截图保存路径 |

**输出**：`filePath`

**失败码**：`ELEMENT_NOT_FOUND`, `SESSION_NOT_FOUND`, `SESSION_LOST`, `EXECUTOR_FAILED`

### `desktop.select` · 设置下拉框（UIA）

- 执行器 `desktop.uia` · action · 风险 `desktop-control`
- 重放 `unsafe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |
| `elementId`（操作目标） | string | 是 | — | — |
| `value`（选择值） | string | 是 | — | 选择项的值/标签/索引 |
| `selectBy`（选择方式） | string enum | 否 | `value` | 选择方式 取值：`value`（按值） / `label`（按文本） / `index`（按序号） |

**失败码**：`ELEMENT_NOT_FOUND`, `SESSION_NOT_FOUND`, `SESSION_LOST`, `EXECUTOR_FAILED`

### `desktop.setWindowState` · 设置窗口状态（UIA）

- 执行器 `desktop.uia` · action · 风险 `desktop-control`
- 重放 `unsafe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |
| `state`（窗口状态） | string enum | 否 | `normal` | 窗口状态 取值：`maximized`（最大化） / `minimized`（最小化） / `normal`（常规） |

**失败码**：`SESSION_NOT_FOUND`, `SESSION_LOST`, `EXECUTOR_FAILED`

### `desktop.setWindowVisible` · 设置窗口显隐（UIA）

- 执行器 `desktop.uia` · action · 风险 `desktop-control`
- 重放 `unsafe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sessionId`（会话 ID） | string | 否 | — | — |
| `visible`（是否显示） | boolean | 否 | true | 是否显示窗口 |

**失败码**：`SESSION_NOT_FOUND`, `SESSION_LOST`, `EXECUTOR_FAILED`


## 数据 / 文件

### `data.appendText` · 追加写入文本

- 执行器 `python.worker` · action · 风险 `workflow-edit`
- 重放 `unsafe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `workspace`（工作目录） | string | 是 | — | — |
| `path`（文件路径） | string | 是 | — | — |
| `text`（文本内容） | string | 否 | — | — |
| `lines`（按行内容） | array<string> | 否 | — | — |

**输出**：`path`

**失败码**：`CAPABILITY_DENIED`, `SCRIPT_FAILED`, `TIMEOUT`, `CANCELLED`

### `data.datetimeNow` · 获取当前时间

- 执行器 `python.worker` · query · 风险 `read`
- 重放 `safe` · 幂等 `none` · 超时 15s · 可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `format`（时间格式） | string | 否 | — | strftime 格式化模板；缺省输出 ISO 时间 |

**输出**：`value`、`iso`、`timestamp`

**失败码**：`INVALID_INPUT`, `SCRIPT_FAILED`, `TIMEOUT`, `CANCELLED`

### `data.deletePath` · 删除路径/文件

- 执行器 `python.worker` · action · 风险 `workflow-edit`
- 重放 `unsafe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `workspace`（工作目录） | string | 是 | — | — |
| `path`（文件路径） | string | 是 | — | — |
| `recursive`（是否递归删除） | boolean | 否 | false | — |

**输出**：`deleted`、`path`

**失败码**：`CAPABILITY_DENIED`, `INVALID_INPUT`, `SCRIPT_FAILED`, `TIMEOUT`, `CANCELLED`

### `data.fileExists` · 判断文件是否存在

- 执行器 `python.worker` · query · 风险 `read`
- 重放 `safe` · 幂等 `none` · 超时 15s · 可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `workspace`（工作目录） | string | 是 | — | — |
| `path`（文件路径） | string | 是 | — | — |

**输出**：`exists`、`path`

**失败码**：`CAPABILITY_DENIED`, `SCRIPT_FAILED`, `TIMEOUT`, `CANCELLED`

### `data.format` · 格式化文本

- 执行器 `python.worker` · transform · 风险 `read`
- 重放 `safe` · 幂等 `none` · 超时 15s · 可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `template`（模板） | string | 是 | — | — |
| `values`（替换值） | object | 是 | — | — |

**输出**：`text`

**失败码**：`INVALID_INPUT`, `TIMEOUT`, `CANCELLED`

### `data.limit` · 截取前 N 条

- 执行器 `python.worker` · transform · 风险 `read`
- 重放 `safe` · 幂等 `none` · 超时 15s · 可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `items`（数据列表） | array<any> | 是 | — | — |
| `count`（数量） | integer | 是 | — | — |

**输出**：`items`、`count`

**失败码**：`INVALID_INPUT`, `TIMEOUT`, `CANCELLED`

### `data.log` · 打印日志

- 执行器 `python.worker` · transform · 风险 `read`
- 重放 `safe` · 幂等 `none` · 超时 15s · 可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `message`（日志内容） | string | 是 | — | 要打印的内容。支持变量引用（如 抓到的标题是 ${title}）；fx 模式下可用 [变量名] 标签与文本混排 |
| `level`（日志级别） | string enum | 否 | `info` | 日志级别（仅作标记，便于在运行日志里区分） 取值：`info`（信息） / `warn`（警告） / `error`（错误） |

**输出**：`message`、`level`

**失败码**：`INVALID_INPUT`, `TIMEOUT`, `CANCELLED`

### `data.readText` · 读取文本文件

- 执行器 `python.worker` · query · 风险 `read`
- 重放 `safe` · 幂等 `none` · 超时 15s · 可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `workspace`（工作目录） | string | 是 | — | — |
| `path`（文件路径） | string | 是 | — | — |

**输出**：`text`、`path`

**失败码**：`CAPABILITY_DENIED`, `INVALID_INPUT`, `SCRIPT_FAILED`, `TIMEOUT`, `CANCELLED`

### `data.setVar` · 设置变量

- 执行器 `python.worker` · transform · 风险 `read`
- 重放 `safe` · 幂等 `none` · 超时 15s · 可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `varName`（变量名） | string | 是 | — | 目标变量名：可直接写新变量（定义），也可写已存在的变量名（重赋值，覆盖旧值） |
| `varType`（变量类型） | string enum | 否 | `string` | 变量类型：变量值会被格式化为对应类型并给默认值（对标影刀「设置变量」） 取值：`string`（字符串） / `number`（数字） / `boolean`（布尔） / `object`（对象） / `array`（数组） |
| `value`（变量值） | ['string', 'number', 'integer', 'boolean', 'object', 'array', 'null'] | 是 | — | 要赋给变量的值，可为任意类型；支持变量引用与 Python 表达式 |

**输出**：`varName`、`value`

**失败码**：`INVALID_INPUT`, `TIMEOUT`, `CANCELLED`

**调用前**：这条会改外部世界（变量名），先把目标与后果说清楚，拿到用户明确同意再执行。

### `data.table.appendRow` · 追加表格行

- 执行器 `python.worker` · action · 风险 `workflow-edit`
- 重放 `unsafe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `flowDir`（流程目录） | string | 否 | — | 流程目录（运行时自动注入）。 |
| `table`（数据表格名称） | string | 否 | `default` | 数据表格名称（当前固定为 default）。 |
| `row`（行号） | object | 是 | — | 要追加到表尾的行数据：{列名: 值}。多次运行会持续累积（影刀语义：不清空则累加）。 |

**输出**：`index`、`rowCount`

**失败码**：`INVALID_INPUT`, `TIMEOUT`, `CANCELLED`

### `data.table.clear` · 清空数据表格

- 执行器 `python.worker` · action · 风险 `workflow-edit`
- 重放 `idempotent` · 幂等 `derived` · 超时 15s · 可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `flowDir`（流程目录） | string | 否 | — | 流程目录（运行时自动注入）。 |
| `table`（数据表格名称） | string | 否 | `default` | 数据表格名称（当前固定为 default）。 |

**输出**：`rowCount`

**失败码**：`INVALID_INPUT`, `TIMEOUT`, `CANCELLED`

### `data.table.deleteRow` · 删除表格行

- 执行器 `python.worker` · action · 风险 `workflow-edit`
- 重放 `unsafe` · 幂等 `none` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `flowDir`（流程目录） | string | 否 | — | 流程目录（运行时自动注入）。 |
| `table`（数据表格名称） | string | 否 | `default` | 数据表格名称（当前固定为 default）。 |
| `row`（行号） | integer | 是 | — | 要删除的行号（从 1 开始），越界将报错。 |

**输出**：`rowCount`

**失败码**：`INVALID_INPUT`, `TIMEOUT`, `CANCELLED`

**调用前**：这条会改外部世界（行号），先把目标与后果说清楚，拿到用户明确同意再执行。

### `data.table.exportCsv` · 导出表格 CSV

- 执行器 `python.worker` · action · 风险 `workflow-edit`
- 重放 `idempotent` · 幂等 `derived` · 超时 15s · 可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `flowDir`（流程目录） | string | 否 | — | 流程目录（运行时自动注入）。 |
| `table`（数据表格名称） | string | 否 | `default` | 数据表格名称（当前固定为 default）。 |
| `path`（文件路径） | string | 否 | — | 导出文件路径（相对流程目录解析；缺省为 data/<table>.csv）。只允许导出到流程目录内。 |

**输出**：`path`

**失败码**：`CAPABILITY_DENIED`, `INVALID_INPUT`, `TIMEOUT`, `CANCELLED`

### `data.table.getCell` · 读取表格单元

- 执行器 `python.worker` · query · 风险 `read`
- 重放 `safe` · 幂等 `none` · 超时 15s · 可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `flowDir`（流程目录） | string | 否 | — | 流程目录（运行时自动注入）。 |
| `table`（数据表格名称） | string | 否 | `default` | 数据表格名称（当前固定为 default）。 |
| `row`（行号） | integer | 是 | — | 目标行号（从 1 开始）。 |
| `column`（列名） | string | 是 | — | 目标列名（表格列 label，如「姓名」）。 |
| `varName`（接收变量名） | string | 是 | — | 目标变量名，读取到的单元格值写入该变量。 |

**输出**：`varName`、`value`

**失败码**：`INVALID_INPUT`, `TIMEOUT`, `CANCELLED`

### `data.table.setCell` · 写入表格单元

- 执行器 `python.worker` · action · 风险 `workflow-edit`
- 重放 `idempotent` · 幂等 `derived` · 超时 15s · 可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `flowDir`（流程目录） | string | 否 | — | 流程目录（运行时自动注入）。 |
| `table`（数据表格名称） | string | 否 | `default` | 数据表格名称（当前固定为 default）。 |
| `row`（行号） | integer | 是 | — | 目标行号（从 1 开始）。 |
| `column`（列名） | string | 是 | — | 目标列名（表格列 label，如「姓名」）。 |
| `value`（选择值） | ['string', 'number', 'integer', 'boolean', 'object', 'array', 'null'] | 是 | — | 要写入单元格的值。缺失的行会先补齐。 |

**输出**：`cellCount`

**失败码**：`INVALID_INPUT`, `TIMEOUT`, `CANCELLED`

### `data.writeJson` · 写入 JSON 文件

- 执行器 `python.worker` · action · 风险 `workflow-edit`
- 重放 `idempotent` · 幂等 `derived` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `workspace`（工作目录） | string | 是 | — | — |
| `path`（文件路径） | string | 是 | — | — |
| `data`（数据） | any | 是 | — | — |

**输出**：`path`

**失败码**：`CAPABILITY_DENIED`, `SCRIPT_FAILED`, `TIMEOUT`, `CANCELLED`

### `data.writeText` · 写入文本文件

- 执行器 `python.worker` · action · 风险 `workflow-edit`
- 重放 `idempotent` · 幂等 `derived` · 超时 15s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `workspace`（工作目录） | string | 是 | — | — |
| `path`（文件路径） | string | 是 | — | — |
| `text`（文本内容） | string | 否 | — | — |
| `lines`（按行内容） | array<string> | 否 | — | — |

**输出**：`path`

**失败码**：`CAPABILITY_DENIED`, `SCRIPT_FAILED`, `TIMEOUT`, `CANCELLED`


## OCR / 验证码

### `ocr.detect` · 识别验证码目标位置

- 执行器 `ocr.ddddocr` · query · 风险 `read`
- 重放 `safe` · 幂等 `none` · 超时 60s · 可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `imagePath`（图片路径） | string | 否 | — | 图片文件路径。通常直接引用上一步「网页截图 / 桌面截图」输出的文件路径 |
| `imageBase64`（图片内容（Base64）） | string | 否 | — | 图片的 Base64 内容。与 imagePath 二选一（都填或都不填都会报 INVALID_INPUT） |

**输出**：`boxes`、`count`

**失败码**：`INVALID_INPUT`, `EXECUTOR_FAILED`, `TIMEOUT`, `CANCELLED`

### `ocr.recognize` · 识别验证码

- 执行器 `ocr.ddddocr` · query · 风险 `read`
- 重放 `safe` · 幂等 `none` · 超时 60s · 可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `imagePath`（图片路径） | string | 否 | — | 图片文件路径。通常直接引用上一步「网页截图 / 桌面截图」输出的文件路径 |
| `imageBase64`（图片内容（Base64）） | string | 否 | — | 图片的 Base64 内容。与 imagePath 二选一（都填或都不填都会报 INVALID_INPUT） |
| `withProbability`（返回置信度） | boolean | 否 | false | 是否同时返回识别置信度（0~1）。开启后速度略降 |
| `charsetRange`（允许的字符集） | string | 否 | — | 允许的字符集，例如 "0123456789"。留空用模型内置的全量字符集。注意：只有模型字符集里认得的字符才生效，其余字符会被静默忽略；若一个都不认得，识别结果会退化成空串（库的行为，如实透传、不做额外降级） |
| `modelPath`（自定义模型路径） | string | 否 | — | 自定义 onnx 识别模型路径。留空则使用内置模型 |

**输出**：`text`、`confidence`

**失败码**：`INVALID_INPUT`, `EXECUTOR_FAILED`, `TIMEOUT`, `CANCELLED`

### `ocr.slideMatch` · 识别滑块缺口位置

- 执行器 `ocr.ddddocr` · query · 风险 `read`
- 重放 `safe` · 幂等 `none` · 超时 60s · 可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `templateImagePath`（滑块块图路径） | string | 否 | — | 滑块块图（缺口那一块的图）的文件路径。与 templateImageBase64 二选一 |
| `templateImageBase64`（滑块块图（Base64）） | string | 否 | — | 滑块块图的 Base64 内容。与 templateImagePath 二选一 |
| `backgroundImagePath`（背景图路径） | string | 否 | — | 带缺口的背景图的文件路径。与 backgroundImageBase64 二选一 |
| `backgroundImageBase64`（背景图（Base64）） | string | 否 | — | 带缺口的背景图的 Base64 内容。与 backgroundImagePath 二选一 |
| `simpleTarget`（简单块图模式） | boolean | 否 | false | 块图是否为不带透明通道的简单块图。false 走边缘匹配、true 走原图模板匹配；不同站点的滑块风格命中率不同，可以两个都试 |
| `minConfidence`（最低匹配相关度） | number | 否 | 0 | 可选严格模式：匹配相关度低于此值就报 EXECUTOR_FAILED。默认 0 = 不判，原样透传坐标 |

**输出**：`x`、`y`、`confidence`

**失败码**：`INVALID_INPUT`, `EXECUTOR_FAILED`, `TIMEOUT`, `CANCELLED`


## 工作流控制

### `workflow.sleep` · 延时等待

- 执行器 `python.worker` · action · 风险 `read`
- 重放 `safe` · 幂等 `none` · 超时 60s · 不可重试


| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `seconds`（延时秒数） | number | 是 | — | 延时秒数，支持小数（如 0.5） |

**输出**：`sleptMs`

**失败码**：`INVALID_INPUT`, `SCRIPT_FAILED`, `TIMEOUT`, `CANCELLED`

