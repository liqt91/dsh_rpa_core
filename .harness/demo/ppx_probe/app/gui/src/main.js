import { ppx } from 'ppx-js'
import './style.css'

const DEPTH = ['#0969da', '#1a7f37', '#9a6700', '#8250df', '#bc4c00', '#0598bc']
// 默认打开试验台副本，不碰 examples/ 里的既有流程。
// 选 struct.json 而不是 baidu 的副本：它同时含 action / if / forEach / try / return，
// 四种节点形态都能在界面上点到（`struct_probe.py` 每次运行都会重建这份夹具）。
const DEMO_FLOW = '.harness/demo/ppx_probe/_sandbox/struct.json'

/* 控制流是 workflow AST 节点，不是 catalog 里的 manifest 命令（AGENTS.md 规则 1）。
   所以它们不进 89 条指令的分组，单独一组，点击走 rpa.insertControlNode。
   id 必须与后端 _CONTROL_TEMPLATES 的键一致。 */
const CONTROL_NODES = [
  { id: 'if', label: '如果 / 否则', control: true },
  { id: 'forEach', label: '循环每一项', control: true },
  { id: 'try', label: '尝试 / 捕获', control: true },
  { id: 'return', label: '返回', control: true },
]

// 同源 iframe 里 HTML5 DnD 可用；自定义 MIME 避免把 JSON 当普通文本拖进输入框。
const DND_MIME = 'application/x-ppx-node'

/* 版式（拖拽落点、底部 dock）都在 style.css 里，DOM 骨架在 index.html 里。
   ADR 0022-R3 把原型目录改为三分支共享后，不再需要往 main.js 里塞运行时样式。 */

/* 底部 dock：元素资产 / 全局参数 / 运行日志，结构见 index.html。 */

const el = {
  status: document.querySelector('#status'),
  picker: document.querySelector('#flowPicker'),
  compile: document.querySelector('#compileBtn'),
  run: document.querySelector('#runBtn'),
  stop: document.querySelector('#stopBtn'),
  runBox: document.querySelector('#runBox'),
  runTitle: document.querySelector('#runTitle'),
  runLog: document.querySelector('#runLog'),
  search: document.querySelector('#search'),
  cmds: document.querySelector('#cmds'),
  cmdCount: document.querySelector('#cmdCount'),
  title: document.querySelector('#flowTitle'),
  cards: document.querySelector('#cards'),
  detail: document.querySelector('#detail'),
  rpcMs: document.querySelector('#rpcMs'),
  compileOut: document.querySelector('#compileOut'),
  form: document.querySelector('#paramForm'),
  save: document.querySelector('#saveBtn'),
  revert: document.querySelector('#revertBtn'),
  dirty: document.querySelector('#dirty'),
  unset: document.querySelector('#unset'),
  elemBody: document.querySelector('#elemBody'),
  elemDetail: document.querySelector('#elemDetail'),
  elemCount: document.querySelector('#elemCount'),
  elemCapture: document.querySelector('#elemCapture'),
  elemCaptureImage: document.querySelector('#elemCaptureImage'),
  elemSearch: document.querySelector('#elemSearch'),
  elemVerifyAll: document.querySelector('#elemVerifyAll'),
  elemLibLayer: document.querySelector('#elemLibLayer'),
  dialog: document.querySelector('#elemDialog'),
  dialogTitle: document.querySelector('#elemDialogTitle'),
  dialogName: document.querySelector('#elemDialogName'),
  dialogMeta: document.querySelector('#elemDialogMeta'),
  dialogNote: document.querySelector('#elemDialogNote'),
  dialogSave: document.querySelector('#elemDialogSave'),
  dialogCancel: document.querySelector('#elemDialogCancel'),
  dialogClose: document.querySelector('#elemDialogClose'),
  dialogEditor: document.querySelector('#elemDialogEditor'),
  dialogTreePane: document.querySelector('#elemTreePane'),
  dialogAttrPane: document.querySelector('#elemAttrPane'),
  dialogSimplePane: document.querySelector('#elemSimplePane'),
  dialogLevels: document.querySelector('#elemDialogLevels'),
  dialogAttrs: document.querySelector('#elemDialogAttrs'),
  dialogSelector: document.querySelector('#elemDialogSelector'),
  dialogPreviewField: document.querySelector('#elemDialogPreviewField'),
  dialogPreview: document.querySelector('#elemDialogPreview'),
  dialogRecapture: document.querySelector('#elemDialogRecapture'),
  dialogValidate: document.querySelector('#elemDialogValidate'),
  dialogSaveContinue: document.querySelector('#elemDialogSaveContinue'),
  dock: document.querySelector('#bottomDock'),
  dockFold: document.querySelector('.dockFold'),
  dockResize: document.querySelector('#dockResize'),
  globalsRows: document.querySelector('#globalsRows'),
  globalsCount: document.querySelector('#globalsCount'),
  globalsAdd: document.querySelector('#globalsAdd'),
  globalsSave: document.querySelector('#globalsSave'),
  globalsRevert: document.querySelector('#globalsRevert'),
  globalsNote: document.querySelector('#globalsNote'),
}

/* ---- 底部 dock：元素资产 / 全局参数 / 运行日志 ----------------------------
 * 三块原本分散且都是折叠的：元素资产缩在右栏、运行日志缩在中栏、全局参数没有界面。
 * 现在整块搬进横跨三列的底栏，节点 id 一个不改，所以 renderElementsPanel /
 * setRunTitle / runLog 的既有写入路径原样有效（只是父节点换了）。
 */
const DOCK_TABS = [
  { key: 'elements', label: '元素资产' },
  { key: 'globals', label: '全局参数' },
  { key: 'log', label: '运行日志' },
]
const GLOBAL_KINDS = [
  { id: 'string', label: '文本' },
  { id: 'number', label: '数字' },
  { id: 'boolean', label: '布尔' },
  { id: 'json', label: 'JSON' },
]

const dockTabs = {}
const dockPanels = {}
let dockActive = 'globals'
let globalsSaved = {}
let globalsRows = []
let globalsDirty = false
let globalsError = ''
let globalsBusy = false

function activateDockTab(key) {
  if (!DOCK_TABS.some((tab) => tab.key === key)) return
  dockActive = key
  for (const tab of DOCK_TABS) {
    const action = tab.key === key ? 'add' : 'remove'
    dockTabs[tab.key]?.classList[action]('active')
    dockPanels[tab.key]?.classList[action]('active')
  }
}

/* ---- 底栏高度：顶边拖拽 + 记住高度（M94）---------------------------------
 * CSS 的 clamp 只解决「默认够高」；元素一多还是得按屏幕自己拉。这里把影刀式
 * 可拖拽底栏接上：上拖加高、下拖压低，范围夹在 140px ~ 视口减 160px（给顶栏和
 * 画布留地方），拉完写 localStorage，刷新后保持。折叠态由 CSS 隐藏手柄。
 */
const DOCK_H_MIN = 140
const DOCK_H_RESERVE = 160
const DOCK_H_KEY = 'rpa.dockHeight'
function dockHeightBounds() {
  const vh = typeof window !== 'undefined' && Number.isFinite(window.innerHeight)
    ? window.innerHeight : 900
  return { min: DOCK_H_MIN, max: Math.max(DOCK_H_MIN, vh - DOCK_H_RESERVE) }
}
function savedDockHeight() {
  if (typeof localStorage === 'undefined') return 0
  try { return Number(localStorage.getItem(DOCK_H_KEY)) || 0 } catch { return 0 }
}
function saveDockHeight(px) {
  if (typeof localStorage === 'undefined') return
  try { localStorage.setItem(DOCK_H_KEY, String(px)) } catch { /* 隐私模式不阻止拖拽 */ }
}
function setDockHeight(px) {
  const { min, max } = dockHeightBounds()
  const height = Math.round(Math.min(max, Math.max(min, px)))
  el.dock.style.height = `${height}px`
  return height
}
function bindDockResize() {
  const grip = el.dockResize
  if (!grip || !el.dock) return
  const saved = savedDockHeight()
  const bounds = dockHeightBounds()
  if (saved >= bounds.min && saved <= bounds.max) setDockHeight(saved)

  let startY = 0
  let startH = 0
  let onMove = null
  let onUp = null
  grip.addEventListener('mousedown', (event) => {
    if (el.dock.classList.contains('collapsed')) return
    const box = el.dock.getBoundingClientRect?.()
    const measured = box && Number.isFinite(box.height) ? box.height : 0
    startH = measured || (saved > 0 ? saved : 320)
    startY = Number.isFinite(event.clientY) ? event.clientY : 0
    grip.classList.add('dragging')
    onMove = (move) => {
      if (!Number.isFinite(move.clientY)) return
      setDockHeight(startH + (startY - move.clientY))
    }
    onUp = () => {
      grip.classList.remove('dragging')
      document.removeEventListener?.('mousemove', onMove)
      document.removeEventListener?.('mouseup', onUp)
      onMove = onUp = null
      const px = parseFloat(el.dock.style.height)
      if (Number.isFinite(px)) saveDockHeight(px)
    }
    document.addEventListener?.('mousemove', onMove)
    document.addEventListener?.('mouseup', onUp)
    event.preventDefault?.()
  })
}

function bindBottomDock() {
  const tabNodes = Array.from(document.querySelectorAll('.dockTab') || [])
  const panelNodes = Array.from(document.querySelectorAll('.dockPanel') || [])
  // 渲染层判据的最小 DOM shim 没有完整选择器；真实页面里两列各 3 个。
  if (tabNodes.length !== DOCK_TABS.length || panelNodes.length !== DOCK_TABS.length) return
  for (const tab of DOCK_TABS) {
    const button = tabNodes.find((n) => n.dataset.tab === tab.key)
    const panel = panelNodes.find((n) => n.dataset.panel === tab.key)
    if (!button || !panel) return
    dockTabs[tab.key] = button
    dockPanels[tab.key] = panel
    button.addEventListener('click', () => activateDockTab(tab.key))
  }
  if (el.dockFold) {
    let folded = false
    el.dockFold.addEventListener('click', () => {
      folded = !folded
      el.dock.classList[folded ? 'add' : 'remove']('collapsed')
      el.dockFold.textContent = folded ? '展开' : '收起'
    })
  }
  // 两个 details 在 HTML 里已 open，这里再钉一次：runFlow 也会重设，别让旧态复活。
  bindDockResize()
  if (el.runBox) {
    el.runBox.hidden = false
    el.runBox.open = true
  }
  const elemBox = document.querySelector('#elemBox')
  if (elemBox) elemBox.open = true
  bindGlobalsPanel()
  bindElementTools()
  activateDockTab(dockActive)
  renderGlobals()
}

/* ---- 全局参数：编辑的是 workflow.inputs 声明 ------------------------------
 * 权威规则在 rpa_core.model.inputs（名称格式 / 保留名 / 默认值 JSON），前端只做
 * 「非空、不重名、JSON 能解析、类型对得上」这层语法预检，不复制一套标识符规则。
 * 行内文本按类型编码成 JSON 文本再交给 rpa.saveFlowInputs，与 Qt RunParamsDialog 同口径。
 */
function globalsFromInputs(inputs) {
  return Object.entries(inputs || {}).map(([name, value]) => {
    const kind =
      typeof value === 'string'
        ? 'string'
        : typeof value === 'number'
          ? 'number'
          : typeof value === 'boolean'
            ? 'boolean'
            : 'json'
    return { name, kind, text: kind === 'string' ? value : JSON.stringify(value) }
  })
}

function encodeGlobalRow(row) {
  if (row.kind === 'string') return JSON.stringify(row.text)
  const text = String(row.text).trim()
  return text === '' ? 'null' : text
}

function globalsProblems() {
  const seen = new Set()
  const problems = []
  for (const row of globalsRows) {
    const name = String(row.name).trim()
    if (!name) {
      problems.push('参数名不能为空')
      continue
    }
    if (seen.has(name)) {
      problems.push(`参数名重复：${name}`)
      continue
    }
    seen.add(name)
    if (row.kind === 'string') continue
    let parsed
    try {
      parsed = JSON.parse(encodeGlobalRow(row))
    } catch {
      problems.push(`${name} 的默认值不是合法 JSON`)
      continue
    }
    if (row.kind === 'number' && typeof parsed !== 'number') problems.push(`${name} 应为数字`)
    if (row.kind === 'boolean' && typeof parsed !== 'boolean') problems.push(`${name} 应为 true / false`)
  }
  return problems
}

function bindGlobalsPanel() {
  if (el.globalsAdd) {
    el.globalsAdd.addEventListener('click', () => {
      globalsRows.push({ name: '', kind: 'string', text: '' })
      markGlobalsDirty()
      renderGlobals()
    })
  }
  if (el.globalsSave) el.globalsSave.addEventListener('click', () => saveGlobals())
  if (el.globalsRevert) {
    el.globalsRevert.addEventListener('click', () => {
      globalsRows = globalsFromInputs(globalsSaved)
      globalsDirty = false
      globalsError = ''
      renderGlobals()
    })
  }
}

function renderGlobalsNote() {
  if (!el.globalsNote) return
  el.globalsNote.className = globalsError ? 'globalsNote error' : 'globalsNote'
  el.globalsNote.textContent = globalsError || (globalsDirty ? '未保存' : `${globalsRows.length} 项`)
  if (el.globalsSave) el.globalsSave.disabled = globalsBusy || (!globalsDirty && !globalsError)
  if (el.globalsRevert) el.globalsRevert.disabled = globalsBusy || !globalsDirty
  if (el.globalsAdd) el.globalsAdd.disabled = globalsBusy
  if (el.globalsCount) el.globalsCount.textContent = `${globalsRows.length} 项`
}

function markGlobalsDirty() {
  globalsDirty = true
  globalsError = ''
  renderGlobalsNote()
}

function renderGlobals() {
  if (!el.globalsRows) return
  el.globalsRows.replaceChildren()
  if (!globalsRows.length) {
    el.globalsRows.append(
      mk('div', 'globalsEmpty', '还没有全局参数。点「+ 添加参数」，之后在节点参数里用 ${inputs.名称} 引用。'),
    )
  }
  for (const [index, row] of globalsRows.entries()) {
    const line = mk('div', 'globalRow')
    const name = document.createElement('input')
    name.value = row.name
    name.placeholder = '如 keyword'
    name.setAttribute('aria-label', '参数名')
    name.addEventListener('input', () => {
      row.name = name.value
      markGlobalsDirty()
    })
    const kind = document.createElement('select')
    kind.setAttribute('aria-label', '类型')
    for (const option of GLOBAL_KINDS) {
      const item = document.createElement('option')
      item.value = option.id
      item.textContent = option.label
      kind.append(item)
    }
    kind.value = row.kind
    kind.addEventListener('change', () => {
      row.kind = kind.value
      markGlobalsDirty()
      renderGlobals()
    })
    const value = document.createElement('input')
    value.value = row.text
    value.placeholder = row.kind === 'string' ? '文本' : 'JSON'
    value.setAttribute('aria-label', '默认值')
    value.addEventListener('input', () => {
      row.text = value.value
      markGlobalsDirty()
    })
    const del = mk('button', 'globalDel', '×')
    del.type = 'button'
    del.title = '删除该参数'
    del.addEventListener('click', () => {
      globalsRows.splice(index, 1)
      markGlobalsDirty()
      renderGlobals()
    })
    line.append(name, kind, value, del)
    el.globalsRows.append(line)
  }
  renderGlobalsNote()
}

async function saveGlobals() {
  if (!currentPath || globalsBusy) return
  const problems = globalsProblems()
  if (problems.length) {
    globalsError = problems[0]
    renderGlobalsNote()
    return
  }
  const values = {}
  for (const row of globalsRows) values[String(row.name).trim()] = encodeGlobalRow(row)
  globalsBusy = true
  renderGlobalsNote()
  try {
    const res = await ppx.call('rpa.saveFlowInputs', { path: currentPath, values })
    globalsSaved = res.inputs || {}
    globalsRows = globalsFromInputs(globalsSaved)
    globalsDirty = false
    const compile = res.compile
    globalsError = compile && compile.ok === false ? `已保存，但编译不通过：${compile.error}` : ''
    setStatus(
      `全局参数已保存 · ${globalsRows.length} 项`,
      compile && compile.ok === false ? compile.error : '',
    )
    renderGlobals()
  } catch (error) {
    globalsError = `${error?.code || '保存失败'}：${String(error?.message || error)}`
  } finally {
    globalsBusy = false
    renderGlobalsNote()
  }
}

bindBottomDock()

let commands = []
let flows = []
let currentPath = null
let cards = []
let selected = null // 当前选中的卡片
let fields = [] // 该节点的可编辑字段（服务端算的，含未设置的声明字段）
let groups = [] // 分组定义（顺序 + 折叠），服务端给
let panelMode = 'param' // param | struct —— 决定保存走哪个 RPC
let formError = null
// ★ ref-only：被引用参数故意不进 with，参数表里就是「未设置」。真值只在
// node.elementRefs 里，不渲染出来用户就会以为删除提示是假的、也找不到地方改。
let nodeRefs = {} // 当前节点的 elementRefs 全量快照（param → 资产名）
let elemCapableKeys = null // null=还没问过后端；不猜参数键，事实源在内核 element_refs
let elemCapableLoading = false
let pendingUnbinds = new Set() // 待随「保存参数」一起提交的解除项
// 待补元素所在的节点 id 集合：AI 生成的工作流把交互元素写成 elementRefs 占位，
// 这些节点在卡片上标「⚠ 待补」，并在元素面板里给出捕获入口。
let missingNodeIds = new Set()
// 运行态：id/seen 是轮询游标（只追加），activeNode 用来在卡片上标「正在跑」
const RUN = { id: null, seen: 0, timer: null, running: false, activeNode: null }

function mk(tag, cls, text) {
  const n = document.createElement(tag)
  if (cls) n.className = cls
  if (text != null) n.textContent = text
  return n
}

/* 顶栏状态是单行省略的。长句会被夹断，所以「显示一行短的 + 全文进 tooltip」必须
   一起写：只 set textContent 会留下上一次的 tooltip，悬停时报出的是旧消息。 */
function setStatus(text, detail = '') {
  el.status.textContent = text
  el.status.title = detail || text
}

function renderCommands(list) {
  el.cmds.replaceChildren()
  const groups = new Map()
  for (const c of list) {
    if (!groups.has(c.ns)) groups.set(c.ns, [])
    groups.get(c.ns).push(c)
  }
  for (const [ns, items] of groups) {
    el.cmds.append(mk('div', 'nsGroup', `${ns} · ${items.length}`))
    for (const c of items) {
      const row = mk('div', 'cmd')
      row.append(mk('span', null, c.label))
      row.title = c.id
      row.draggable = true
      row.addEventListener('dragstart', (event) =>
        setDragPayload(event, { mode: 'insert', command: c }, 'copy'),
      )
      row.addEventListener('click', () => insertCommand(c))
      el.cmds.append(row)
    }
  }
  // 控制流与搜索框共用一份过滤：搜「如果」也要能找到它。cmdCount 仍只数 catalog 指令。
  const q = el.search.value.trim().toLowerCase()
  const controls = q
    ? CONTROL_NODES.filter((c) => c.id.toLowerCase().includes(q) || c.label.includes(q))
    : CONTROL_NODES
  if (controls.length) {
    el.cmds.append(mk('div', 'nsGroup', `流程控制 · ${controls.length}`))
    for (const c of controls) {
      const row = mk('div', 'cmd')
      row.append(mk('span', null, c.label))
      row.title = `插入结构节点：${c.id}`
      row.draggable = true
      row.addEventListener('dragstart', (event) =>
        setDragPayload(event, { mode: 'insert', command: c }, 'copy'),
      )
      row.addEventListener('click', () => insertCommand(c))
      el.cmds.append(row)
    }
  }
  el.cmdCount.textContent = `${list.length} 条`
}

/** 插入一个真节点。点击走 anchor 末尾语义；拖入传 dropTarget 走显式落点。 */
async function insertCommand(command, dropTarget = null) {
  if (!currentPath) return
  // 选中「否则 / 异常」这类虚拟组行时，锚点是它的父容器 + 分支键；
  // 选中普通卡片时沿用原语义（容器进默认分支、叶子进父容器的当前分支）。
  const isGroup = selected?.kind === 'group'
  const anchor = isGroup ? selected.parentId : (selected?.id || '')
  const branch = isGroup ? selected.branch : ''
  const t0 = performance.now()
  try {
    const rpc = command.control ? 'rpa.insertControlNode' : 'rpa.insertNode'
    const args = { path: currentPath, anchor_id: anchor, branch }
    if (command.control) args.kind = command.id
    else args.command = command.id
    // 拖拽落点：服务端按 (parent_id, branch, index) 精确插入，不再追加到分支末尾。
    if (dropTarget) {
      args.anchor_id = ''
      args.branch = dropTarget.branch
      args.explicit = true
      args.parent_id = dropTarget.parentId
      args.index = dropTarget.index
    }
    const res = await ppx.call(rpc, args)
    if (!res.ok) {
      setStatus(`插入失败 ${res.code}：${res.error}`)
      return
    }
    cards = res.flow.cards
    const placed = res.placed
    renderCards()
    renderCompile(res.compile)
    const from = dropTarget ? '拖放落点' : `anchor ${placed.anchor || 'root'}`
    setStatus(`插入 ${command.label} → ${placed.parent}/${placed.branch}[${placed.index}] · ${res.bytes[0]}→${res.bytes[1]} B · ${res.ms} ms（${from}）`)
    // 新节点 with 是空的（与 Qt 一致），参数全未设置 ⇒ 直接跳到它，省一次点击
    const fresh = cards.find((c) => c.id === res.newNodeId)
    if (fresh) {
      await selectCard(fresh)
      // 落点是「anchor 所在分支的末尾」，常常不在当前视野里。不滚过去 + 闪一下，
      // 用户会觉得「点了没反应」，然后再点一次——生成两个重复节点。
      const row = el.cards.querySelector(`[data-node-id="${CSS.escape(fresh.id)}"]`)
      if (row) {
        row.scrollIntoView({ block: 'nearest' })
        row.classList.add('flash')
        row.addEventListener('animationend', () => row.classList.remove('flash'), { once: true })
      }
    }
  } catch (error) {
    setStatus(`${error.code}: ${error.message}`)
  } finally {
    el.rpcMs.textContent = `${(performance.now() - t0).toFixed(0)} ms`
  }
}

/* ---- 拖拽：左栏拖入 + 画布 grip 排序 -------------------------------------
 * 两条通道共用一套落点。卡片的 parentId / branch / index 由 openFlow 下发，
 * 前端只算「落在哪一行的上半区还是下半区」，AST 怎么改全部交给后端。
 */
function setDragPayload(event, payload, effect) {
  event.dataTransfer.setData(DND_MIME, JSON.stringify(payload))
  event.dataTransfer.effectAllowed = effect
}

function readDragPayload(event) {
  const raw = event.dataTransfer.getData(DND_MIME)
  if (!raw) return null
  try {
    return JSON.parse(raw)
  } catch {
    return null
  }
}

// dragover 阶段读不到 data（浏览器只在 drop 时放开），但 types 一直可读。
function hasDragPayload(event) {
  return Array.from(event.dataTransfer.types || []).includes(DND_MIME)
}

function clearDropHint() {
  for (const row of el.cards.querySelectorAll('.drop-before, .drop-after, .drop-into')) {
    row.classList.remove('drop-before', 'drop-after', 'drop-into')
  }
}

function dragTarget(event) {
  const row = event.target instanceof Element ? event.target.closest('.card') : null
  const empty = { parentId: '', branch: '', index: -1, row: null, mode: '' }
  if (!row) return empty // 画布空白处 = root 末尾
  const card = cards.find((c) => (c.id || '') === row.dataset.cardId)
  if (!card) return empty
  if (card.kind === 'group') {
    // 虚拟组行代表整条分支，拖到它上面一律插这条分支的末尾
    return {
      parentId: card.parentId || '',
      branch: card.branch || '',
      index: card.index ?? 0,
      row,
      mode: 'into',
    }
  }
  const box = row.getBoundingClientRect()
  const before = event.clientY < box.top + box.height / 2
  return {
    parentId: card.parentId || '',
    branch: card.branch || '',
    index: (card.index ?? 0) + (before ? 0 : 1),
    row,
    mode: before ? 'before' : 'after',
  }
}

function showDropHint(target) {
  clearDropHint()
  if (target.row) target.row.classList.add(`drop-${target.mode}`)
}

async function moveCard(nodeId, target) {
  if (!currentPath) return
  const t0 = performance.now()
  try {
    const res = await ppx.call('rpa.moveNode', {
      path: currentPath,
      node_id: nodeId,
      parent_id: target.parentId,
      branch: target.branch,
      index: target.index,
    })
    if (!res.ok) {
      setStatus(`移动失败 ${res.code}：${res.error}`)
      return
    }
    cards = res.flow.cards
    renderCards()
    renderCompile(res.compile)
    const moved = res.moved
    setStatus(`移动 ${moved.nodeId} → ${moved.parent}/${moved.branch}[${moved.index}] · ${res.ms} ms`)
  } catch (error) {
    setStatus(`${error.code}: ${error.message}`)
  } finally {
    el.rpcMs.textContent = `${(performance.now() - t0).toFixed(0)} ms`
  }
}

async function removeCard(card) {
  if (!currentPath) return
  const doomed = cards.find((c) => c.id === card.id)
  if (!doomed) return
  if (!window.confirm(`删除「${doomed.label}」？容器节点会连同里面的节点一起删。`)) return
  try {
    const res = await ppx.call('rpa.deleteNode', { path: currentPath, node_id: card.id })
    if (!res.ok) {
      setStatus(`删除失败 ${res.code}：${res.error}`)
      return
    }
    cards = res.flow.cards
    if (selected && res.removed.ids.includes(selected.id)) {
      selected = null
      fields = []
      groups = []
      renderForm()
    }
    renderCards()
    renderCompile(res.compile)
    const extra = res.removed.ids.length > 1 ? `（连带 ${res.removed.ids.length - 1} 个子节点）` : ''
    setStatus(`删除 ${res.removed.id}${extra} · ${res.bytes[0]}→${res.bytes[1]} B · ${res.ms} ms`)
  } catch (error) {
    setStatus(`${error.code}: ${error.message}`)
  }
}

function renderCards() {
  // replaceChildren 会先把内容清空，滚动容器的 scrollTop 随之被夹到 0；
  // 于是「滚到第 20 行点它」会在重绘后跳回顶部，选中的卡片跑出视野。
  const keepTop = el.cards.scrollTop
  el.cards.replaceChildren()
  for (const card of cards) {
    const depth = card.depth || 0
    if (card.kind === 'group') {
      const group = mk(
        'div',
        'card group' + (selected && card.id && card.id === selected.id ? ' selected' : ''),
      )
      // 组行也要参与缩进与竖线，否则「则执行 / 否则 / 异常」会漂在父节点左边的同一列
      group.dataset.depth = String(depth)
      group.dataset.cardId = card.id || ''
      group.style.setProperty('--depth-n', String(depth))
      group.append(mk('span', 'gtag', '↳'), mk('span', 'gname', card.label))
      // 组行不是 AST 节点，但它是「往这条分支插节点」的唯一可见落点：可点、可键盘选中。
      if (card.branch) {
        group.title = `选中后点左侧指令，新节点会插进「${card.label}」分支`
        group.tabIndex = 0
        group.setAttribute('role', 'button')
        group.addEventListener('click', () => selectCard(card))
        group.addEventListener('keydown', (event) => {
          if (event.key !== 'Enter' && event.key !== ' ') return
          event.preventDefault()
          selectCard(card)
        })
      }
      el.cards.append(group)
      continue
    }
    const row = mk(
      'div',
      'card' +
        (card.draft ? ' draft' : '') +
        (selected && card.id === selected.id ? ' selected' : '') +
        (RUN.activeNode && card.id === RUN.activeNode ? ' running' : '') +
        (missingNodeIds.has(card.id) ? ' missing' : ''),
    )
    row.dataset.nodeId = card.id || ''
    row.dataset.cardId = card.id || ''
    row.dataset.depth = String(depth)
    row.style.setProperty('--depth-n', String(depth))
    row.style.setProperty('--accent', DEPTH[depth % DEPTH.length])
    row.append(mk('span', 'no', String(card.no)), mk('span', 'name', card.label))
    if (card.summary) row.append(mk('span', 'sum', card.summary))
    if (card.command) row.append(mk('span', 'badge', card.command.split('.')[0]))
    if (missingNodeIds.has(card.id)) row.append(mk('span', 'cap', '⚠ 待补'))
    if (!card.draft) {
      // 只有 grip 可拖：整行 draggable 会跟「点选 / 删除 / 键盘空格」抢事件。
      const grip = mk('span', 'grip', '⠿')
      grip.title = '拖动排序：拖到目标行的上半区插在前面，下半区插在后面'
      grip.draggable = true
      grip.addEventListener('dragstart', (event) => {
        event.stopPropagation()
        setDragPayload(event, { mode: 'move', nodeId: card.id }, 'move')
      })
      row.append(grip)
      const del = mk('button', 'del', '×')
      del.title = '删除这个节点（容器会连同里面的一起删）'
      del.addEventListener('click', (event) => {
        event.stopPropagation()
        removeCard(card)
      })
      row.append(del)
      row.addEventListener('click', () => selectCard(card))
      // 卡片是 div：不补 tabindex 的话，整个画布对键盘用户是死的（一个节点都选不中）
      row.tabIndex = 0
      row.setAttribute('role', 'button')
      row.setAttribute('aria-current', selected && card.id === selected.id ? 'true' : 'false')
      row.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter' && event.key !== ' ') return
        if (event.target !== row) return // 空格落在删除按钮上时别抢
        event.preventDefault()
        selectCard(card)
      })
    }
    el.cards.append(row)
  }
  el.cards.scrollTop = keepTop
}

/* ---- 参数表单 ----------------------------------------------------------
 * 语义与 Qt 的「应用参数」一致：表单呈现的是**命令声明的全部字段**，
 * 空 = 未设置 = 不写进节点的 with。所以每个控件都必须能表达「未设置」这个第三态，
 * 否则「打开面板 → 保存」就会把没填过的参数写成显式空值。
 */

function currentValues() {
  const values = {}
  for (const field of fields) {
    const node = el.form.querySelector(`[data-key="${CSS.escape(field.key)}"]`)
    // 控件缺失时回落到原文而不是跳过：漏传一个键会被服务端判 INCOMPLETE_SUBMIT，
    // 与其让保存失败，不如原样提交（用户没改过它）。
    values[field.key] = node ? node.value : field.valueText
  }
  return values
}

function isDirty() {
  // 「解除引用」不落在任何控件里，得单独算脏，否则保存按钮是灰的、解除白点了。
  if (pendingUnbinds.size) return true
  if (!fields.length) return false
  const values = currentValues()
  return fields.some((field) => (values[field.key] ?? '') !== field.valueText)
}

function unsetCount() {
  const values = currentValues()
  return fields.filter((field) => (values[field.key] ?? '') === '').length
}

function refreshDirty() {
  const dirty = isDirty()
  el.dirty.textContent = dirty ? '● 未保存' : ''
  el.save.disabled = !dirty
  el.revert.disabled = !dirty
  if (!fields.length) {
    el.unset.textContent = ''
    return
  }
  const blank = unsetCount()
  el.unset.textContent = blank ? `${fields.length - blank}/${fields.length} 已设置` : `${fields.length} 项全设置`
  el.unset.className = 'unsetCount' + (blank ? ' partial' : '')
}

function addOption(control, value, label) {
  const option = document.createElement('option')
  option.value = value
  option.textContent = label
  control.append(option)
  return option
}

/* ---- 参数面板的「从元素库选」 -------------------------------------------
 * 哪些参数能引用元素由内核 element_refs 决定（与运行期解析同一份集合），
 * 前端不硬编码 selector/locator。选中只写 nodeRefs，随「保存参数」全量提交。
 */

async function fillElemPick(select, key) {
  try {
    const res = await ppx.call('rpa.elementCandidates', { path: currentPath, param_key: key })
    for (const e of res?.elements || []) {
      addOption(select, e.name, `${e.name}（${kindLabel(e.kind)}）`)
    }
  } catch {
    // 候选拿不到就留空选项：不猜名字，更不把手填的值当引用写进去。
  }
}

function buildElemPick(field, refName) {
  if (!elemCapableKeys || !elemCapableKeys.has(field.key)) return null
  const select = mk('select', 'elemPick')
  addOption(select, '', refName ? '换一个元素…' : '从元素库选…')
  select.dataset.key = field.key
  select.title = '选中后只写元素引用，运行期从资产库取最新定位'
  fillElemPick(select, field.key)
  select.addEventListener('change', () => {
    const name = select.value
    if (!name) return
    nodeRefs[field.key] = name
    pendingUnbinds.delete(field.key)
    refreshDirty()
    renderForm()
  })
  return select
}

function defaultHint(field) {
  if (!field.hasDefault) return ''
  const text = typeof field.default === 'string' ? field.default : JSON.stringify(field.default)
  return `默认 ${text}`
}

function buildControl(field) {
  const wrap = mk('div', 'field' + (field.set ? '' : ' unset'))
  const row = mk('div', 'labelRow')
  // 结构字段（if/forEach/return）有中文标签，指令参数的标签就是参数名——统一走 label，
  // 参数名降级进 tooltip，这样两种节点在同一个侧栏里排版一致。
  const keyLabel = mk('span', 'key', field.label || field.key)
  if (field.label && field.label !== field.key) keyLabel.title = field.key
  row.append(keyLabel)
  row.append(mk('span', 'type', field.type))
  if (!field.declared) {
    const warn = mk('span', 'type warn', 'manifest 未声明')
    warn.title = '该参数不在命令声明的 input_schema 里：类型按文件里现有值推断'
    row.append(warn)
  }
  if (field.required) row.append(mk('span', 'req', '必填'))
  wrap.append(row)

  // ★ 被引用参数本来就不在 with 里：给一个空输入框 +「必填但当前未设置」是假报警。
  // 换成只读的引用说明，保留隐藏 input 让 currentValues() 仍能全量提交（漏键会被
  // 服务端判 INCOMPLETE_SUBMIT）。解除待生效时按普通字段渲染，用户好补值。
  const refName = pendingUnbinds.has(field.key) ? null : nodeRefs[field.key]
  let control
  if (refName) {
    control = document.createElement('input')
    control.type = 'hidden'
    control.value = field.valueText
    wrap.classList.add('refBound')
    wrap.append(mk('div', 'refBoundValue', `已引用元素「${refName}」`))
    const replace = buildElemPick(field, refName)
    if (replace) wrap.append(replace)
  } else if (field.enum.length) {
    control = document.createElement('select')
    // 结构字段的 enum（条件操作符）必有值，不给「未设置」——空操作符没有语义
    if (field.semantic !== 'enum') {
      addOption(control, '', field.hasDefault ? `（未设置 · ${defaultHint(field)}）` : '（未设置）')
    }
    const known = new Set(field.enum.map((o) => String(o.value)))
    if (field.set && !known.has(String(field.value))) {
      addOption(control, String(field.value), `${field.value}（当前值，不在枚举内）`)
    }
    for (const option of field.enum) addOption(control, String(option.value), option.label)
    control.value = field.set || field.semantic === 'enum' ? String(field.value ?? '') : ''
  } else if (field.type === 'boolean') {
    control = document.createElement('select')
    addOption(control, '', field.hasDefault ? `（未设置 · ${defaultHint(field)}）` : '（未设置）')
    addOption(control, 'true', '是（true）')
    addOption(control, 'false', '否（false）')
    control.value = field.set ? String(field.value) : ''
  } else if (
    field.type === 'array' ||
    field.type === 'object' ||
    field.type === 'json' ||
    field.multiline
  ) {
    control = document.createElement('textarea')
    control.spellcheck = false
    control.value = field.valueText
    if (field.type === 'array' || field.type === 'object') {
      control.placeholder = 'JSON，如 ["a", "b"]'
    } else if (field.type === 'json') {
      control.placeholder = 'JSON 值；留空表示缺省'
    }
  } else {
    control = document.createElement('input')
    control.type = 'text'
    control.spellcheck = false
    control.value = field.valueText
    control.placeholder = field.hasDefault ? `未设置 · ${defaultHint(field)}` : '未设置'
  }
  control.dataset.key = field.key
  control.addEventListener('input', refreshDirty)
  control.addEventListener('change', refreshDirty)
  wrap.append(control)
  const note = refName
    ? `已由元素引用「${refName}」提供，运行期从资产库解析；这里不用填`
    : field.description || (!field.set && field.required ? '必填但当前未设置' : '')
  if (note) {
    const desc = mk('div', 'desc', note)
    // manifest 的 description 动辄四五句（browserType 那段 90 字），全铺开会把 340px 的
    // 侧栏撑成一大片留白。收成两行，全文进 tooltip——想读的人有地方读。
    desc.title = note
    wrap.append(desc)
  }
  if (!refName) {
    const pick = buildElemPick(field, refName)
    if (pick) wrap.append(pick)
  }
  return wrap
}

/** 按服务端给的 groups 顺序切桶；没分组的字段直接铺开。 */
function groupFields() {
  const buckets = new Map()
  for (const field of fields) {
    const key = field.group ?? null
    if (!buckets.has(key)) buckets.set(key, [])
    buckets.get(key).push(field)
  }
  const ordered = []
  for (const group of groups) {
    const items = buckets.get(group.label)
    if (items && items.length) {
      ordered.push({ label: group.label, collapsed: group.collapsed, fields: items })
      buckets.delete(group.label)
    }
  }
  for (const [label, items] of buckets) ordered.push({ label, collapsed: false, fields: items })
  return ordered
}

/** 引用面板：ref-only 的真值入口。列出 param → 资产名，并给出可撤销的「解除引用」。 */
function buildRefPanel() {
  const box = mk('div', 'refPanel')
  box.append(mk('div', 'refPanelTitle', '元素引用（运行期从资产库解析）'))
  for (const [param, name] of Object.entries(nodeRefs)) {
    const row = mk('div', 'refRow')
    row.append(mk('span', 'refParam', param), mk('span', 'refArrow', '→'), mk('span', 'refName', name))
    const btn = mk('button', 'refUnbind', pendingUnbinds.has(param) ? '撤销解除' : '解除引用')
    btn.addEventListener('click', () => {
      if (pendingUnbinds.has(param)) pendingUnbinds.delete(param)
      else pendingUnbinds.add(param)
      renderForm()
    })
    row.append(btn)
    box.append(row)
  }
  if (pendingUnbinds.size) {
    box.append(mk('div', 'refPanelHint', '点「保存参数」生效；解除后该参数要自己填值，否则流程缺定位。'))
  }
  return box
}

function renderForm() {
  // 能引用元素的参数集合只在内核定义；异步取一次，回来再重画一次表单。
  // 成功/失败都赋值，避免 RPC 一直失败时每次 renderForm 都发起新请求。
  if (!elemCapableKeys && !elemCapableLoading && currentPath) {
    elemCapableLoading = true
    ppx.call('rpa.elementCandidates', { path: currentPath })
      .then((res) => {
        elemCapableKeys = new Set(
          res && res.ok && Array.isArray(res.capableKeys) ? res.capableKeys : [],
        )
      })
      .catch(() => { elemCapableKeys = new Set() })
      .finally(() => {
        elemCapableLoading = false
        renderForm()
      })
  }
  el.form.replaceChildren()
  if (formError) el.form.append(mk('div', 'formError', formError))
  if (Object.keys(nodeRefs).length) el.form.append(buildRefPanel())
  if (!selected) {
    el.form.append(mk('div', 'hint', '点击中间卡片查看参数'))
  } else if (!fields.length) {
    el.form.append(
      mk(
        'div',
        'hint',
        selected.kind === 'action'
          ? `${selected.label} 没有可改的参数（命令未声明任何字段）`
          : `${selected.label} 没有可编辑的结构字段（顺序容器只是壳）`,
      ),
    )
  } else {
    for (const group of groupFields()) {
      if (!group.label) {
        for (const field of group.fields) el.form.append(buildControl(field))
        continue
      }
      const details = document.createElement('details')
      details.className = 'paramGroup'
      details.open = !group.collapsed
      const summary = document.createElement('summary')
      const filled = group.fields.filter((f) => f.set).length
      summary.textContent = `${group.label} · ${filled}/${group.fields.length}`
      details.append(summary)
      for (const field of group.fields) details.append(buildControl(field))
      el.form.append(details)
    }
  }
  refreshDirty()
}

async function selectCard(card) {
  if (isDirty() && !window.confirm('当前改动还没保存，切换会丢弃。继续？')) return
  if (card.kind === 'group') {
    // 虚拟组行没有节点详情可查，它的作用只是给下一次插入指定 branch。
    selected = card
    fields = []
    groups = []
    nodeRefs = {}
    pendingUnbinds.clear()
    renderCards()
    renderForm()
    el.detail.textContent =
      `分支锚点：${card.label}（${card.branch}）\n父节点：${card.parentId}\n\n` +
      '点左侧任意指令或控制流节点，会插进这条分支。'
    setStatus(`已选中「${card.label}」分支 · 点左侧指令插入`)
    return
  }
  selected = card
  formError = null
  const hadFocus = el.cards.contains(document.activeElement)
  renderCards()
  if (hadFocus) {
    const row = el.cards.querySelector(`[data-node-id="${CSS.escape(card.id)}"]`)
    if (row) row.focus({ preventScroll: true })
  }
  const rpc = card.kind === 'action' ? 'rpa.nodeDetail' : 'rpa.structuralDetail'
  panelMode = card.kind === 'action' ? 'param' : 'struct'
  const t0 = performance.now()
  try {
    const res = await ppx.call(rpc, { path: currentPath, node_id: card.id })
    el.rpcMs.textContent = `${(performance.now() - t0).toFixed(1)} ms`
    fields = res.fields || []
    groups = res.groups || []
    nodeRefs = { ...((res.node && res.node.elementRefs) || {}) }
    pendingUnbinds.clear()
    renderForm()
    el.detail.textContent = JSON.stringify(res.node ?? res, null, 2)
  } catch (error) {
    fields = []
    groups = []
    nodeRefs = {}
    pendingUnbinds.clear()
    renderForm()
    el.detail.textContent = `${error.code}: ${error.message}`
  }
}

async function saveParams() {
  if (!selected || !fields.length) return
  const params = currentValues()
  el.save.disabled = true
  el.revert.disabled = true
  const rpc = panelMode === 'struct' ? 'rpa.saveStructuredParams' : 'rpa.saveNodeParams'
  const t0 = performance.now()
  try {
    // 引用也是全量提交：解除标记在这里落地，服务端按整份 elementRefs 覆盖。
    // 结构表单没有 elementRefs 这条通道，别给它塞未知参数。
    const refsPayload =
      panelMode === 'struct'
        ? {}
        : {
          element_refs: Object.fromEntries(
            Object.entries(nodeRefs).filter(([key]) => !pendingUnbinds.has(key)),
          ),
        }
    const res = await ppx.call(rpc, {
      path: currentPath,
      node_id: selected.id,
      params,
      ...refsPayload,
    })
    if (!res.ok) {
      formError = `${res.code}：${res.error}`
      renderForm()
      return
    }
    formError = null
    cards = res.flow.cards
    const keepId = selected.id
    selected = cards.find((c) => c.id === keepId) || null
    const detailRpc = panelMode === 'struct' ? 'rpa.structuralDetail' : 'rpa.nodeDetail'
    const detail = await ppx.call(detailRpc, { path: currentPath, node_id: keepId })
    fields = detail.fields || []
    groups = detail.groups || []
    nodeRefs = { ...((detail.node && detail.node.elementRefs) || {}) }
    pendingUnbinds.clear()
    renderCards()
    renderForm()
    renderCompile(res.compile)
    const bytes = res.changed ? `${res.bytes[0]} → ${res.bytes[1]} B` : '内容未变，未写盘'
    const touched = []
    if (res.added?.length) touched.push(`+${res.added.join(',')}`)
    if (res.removed?.length) touched.push(`−${res.removed.join(',')}`)
    const delta = touched.length ? ` · ${touched.join(' ')}` : ''
    // 结构节点的软警告（能存但可能跑不起来，如 items 不是数组）必须说出来，
    // 否则就是「保存成功 + 编译通过」而运行期才 INVALID_INPUT。
    const warned = (res.warnings || []).length
      ? ` · ⚠ ${res.warnings.map((w) => `${w.key}：${w.reason}`).join('；')}`
      : ''
    setStatus(`保存 ${selected ? selected.label : keepId} · ${bytes}${delta}${warned} · sha ${res.sha} · ${(performance.now() - t0).toFixed(0)} ms`)
  } catch (error) {
    formError = `${error.code}：${error.message}`
    renderForm()
  } finally {
    refreshDirty()
  }
}

function revertParams() {
  // 解除标记也算一次编辑，撤销要把它一起撤掉——重建表单用 field.valueText 回填。
  pendingUnbinds.clear()
  renderForm()
}

/* ---- 流程与编译 -------------------------------------------------------- */

async function openFlow(path) {
  const t0 = performance.now()
  try {
    const flow = await ppx.call('rpa.openFlow', { path })
    currentPath = path
    globalsSaved = flow.inputs || {}
    globalsRows = globalsFromInputs(globalsSaved)
    globalsDirty = false
    globalsError = ''
    renderGlobals()
    cards = flow.cards
    selected = null
    fields = []
    groups = []
    formError = null
    renderForm()
    el.detail.textContent = ''
    el.rpcMs.textContent = ''
    el.title.replaceChildren(
      mk('span', null, flow.name),
      // 可见只留「路径 · 行数」；sha 和耗时是排查用的，放 tooltip，不占标题的横向预算
      mk('small', null, `${path} · ${flow.cards.length} 行`),
    )
    el.title.title =
      `${path} · ${flow.cards.length} 行 · sha ${flow.sha} · ${(performance.now() - t0).toFixed(1)} ms`
    renderCards()
    el.run.disabled = RUN.running || !currentPath
    // 打开即刷新元素资产：摊开「AI 生成的占位元素」并给出捕获入口
    await refreshElements()
    // 打开流程后直接落到第一张可编辑卡片：右栏空着会让用户以为「这东西没参数」
    const firstEditable = cards.find((card) => card.kind !== 'group' && card.id)
    if (firstEditable) await selectCard(firstEditable)
    return flow
  } catch (error) {
    const failure = {
      code: error?.code || 'INTERNAL_ERROR',
      message: String(error?.message || error),
    }
    el.title.replaceChildren(mk('span', null, `打开失败：${path}`))
    const reason = mk('div', 'card group', `${failure.code}: ${failure.message}`)
    reason.style.whiteSpace = 'pre-wrap'
    el.cards.replaceChildren(reason)
    // 不能只把原因写在界面上：会话侧（Agent）在等 open-failed 事件。
    // 吞掉异常它只能拿到一句「窗口内打开失败」，拿不到 BAD_PATH 和修法。
    return { ok: false, error: failure }
  }
}

function renderCompile(result) {
  el.compileOut.replaceChildren()
  if (!result) return
  if (result.ok) {
    el.compileOut.append(mk('div', 'ok', `编译通过 · ${result.ms ?? ''} ${result.ms != null ? 'ms' : ''}`.trim()))
    const caps = (result.capabilities || []).join(', ') || '（无）'
    el.compileOut.append(
      mk('pre', null, `workflow  ${result.workflowId}\ndigest    ${result.digest}\ncaps      ${caps}`),
    )
  } else {
    el.compileOut.append(mk('div', 'bad', `编译失败 · ${result.ms ?? ''} ${result.ms != null ? 'ms' : ''}`.trim()))
    el.compileOut.append(mk('pre', null, result.error))
  }
}

/* ---- 元素资产（捕获入库 + 待补元素） --------------------------------------
 * 交互口径（2026-11-12 改版，替换 M66 的六态状态条）：
 * 1. 面板里只有**一个按钮**「捕获元素」：点一下就抓，不预填名字、不选网页/桌面，
 *    也不提供手写选择器入库——那三件事要么系统能推断，要么本来就该抓。
 * 2. 面板里**不显示任何提示**：检环境 / 没连上 / 这次没成 / 删不掉，全部落在桌面
 *    浮窗里（后端 startCapture 先亮浮窗再查环境，见 api.py 的 _show_blocked / _notify）。
 * 3. 抓完**不直接入库**：弹编辑框确认名字（允许中文），点保存才写 elements/<name>.json。
 * 4. 资产行的「详情」打开**同一个**编辑框，对已保存元素改名。
 */

// 用户可见文案的唯一来源。静态判据扫这张表的值：出现实现名词即判红。
// 动态内容一律用 {name} / {step} / {reason} 占位，避免把拼接藏进源码绕过判据。
const ELEM_TEXT = {
  capture: '捕获元素',
  missingHead: '待补元素',
  doneHead: '已有资产',
  fill: '补一下',
  none: '这个流程还没有元素',
  detail: '详情',
  verify: '校验',
  verifyRunning: '校验中…',
  verifyHit: '校验命中 {count} 个',
  verifyNone: '这次没命中',
  verifyNoTree: '没命中：该窗口没有无障碍树，退出应用重开后再试',
  verifyError: '没校验成：{reason}',
  remove: '删除',
  confirmTitle: '确认这个元素',
  detailTitle: '元素属性',
  nameLabel: '资产名称',
  namePlaceholder: '起个名字，可以用中文',
  save: '保存',
  cancel: '取消',
  close: '关闭',
  nameEmpty: '名字不能为空',
  saveFailed: '没保存上：{reason}',
  detailFailed: '打不开这个元素：{reason}',
  missingMeta: '第 {step} 步 · {where}',
  kindBrowser: '网页',
  kindDesktop: '桌面',
  wherePage: '网页',
  whereApp: '桌面软件',
  matches: '捕获时命中 {count} 个',
  refStep: '第 {step} 步正在用它',
  refDetail: '第 {step} 步 · {param} → {name}',
  refNone: '还没有流程引用它，保存后只是一条库存',
  capturedAt: '捕获时间 {time}',
  stepFallback: '这一步',
  loadFailed: '元素没加载出来：{reason}',
  deleteBlockedTitle: '这个元素删不掉',
  deleteBlockedHint: '流程里还有 {count} 处引用：',
  deleteBlockedRef: '第 {step} 步 · {param} → {name}（点这里去处理）',
  deleteBlockedNote: '点引用跳到节点，解除引用并保存后再删',
  deleteCheckFailed: '没查到引用情况：{reason}',
  deleteFailed: '没删掉：{reason}',
  deleteFailedHint: '删除没成功，元素还在清单里',
  // M93：影刀式分组树 / 右键管理
  ungrouped: '未分组',
  groupNew: '新建分组',
  groupRename: '重命名分组',
  groupDelete: '删除分组',
  groupNameEmpty: '给分组起个名字',
  groupNameTaken: '已经有同名分组了',
  moveTo: '移动到分组',
  copy: '复制一份',
  findRefs: '查找引用',
  selCount: '已选 {count} 项',
  batchMove: '批量移动',
  batchVerify: '批量校验',
  batchDelete: '批量删除',
  clearSel: '取消选择',
  onlyUnused: '只看未使用',
  unusedNone: '没有未使用的元素',
  menuNewHere: '在这里新建元素',
  menuFold: '折叠 / 展开',
  refsTitle: '引用了「{name}」的位置',
  refsZero: '没有流程引用它',
  copiedAs: '已复制为「{name}」',
  // M102 S2：右栏详情
  detailPick: '点左边一行，这里显示它的截图和完整定位',
  shotNone: '这个元素还没有截图',
  detailKind: '类型',
  detailWhere: '所属',
  detailGroup: '分组',
  detailSelector: '定位',
  detailRefs: '引用',
  detailCaptured: '捕获时间',
  detailNoRefs: '还没被流程引用',
}

function elemText(key, vars = {}) {
  return (ELEM_TEXT[key] || key).replace(/\{(\w+)\}/g, (_, k) => String(vars[k] ?? ''))
}

let pickMissing = []
let pickElements = []
let pickGroups = [] // M93：element_groups.json 的分组树（含 elementCount/collapsed）
let pickUnused = [] // M93：引用计数为 0 的资产名
let elemFilterText = '' // 只影响显示，不动资产与引用
let elemOnlyUnused = false // M93：「只看未使用」筛选开关
let elemSelected = new Set() // M93：多选资产名（批量操作）
// M102 S2：右栏正在看的资产名 + 缩略图缓存（按「名字|screenshot 路径」做键，
// 重捕或改名后自动失效，不用手动清）。图按需拉，不进 listElements 的批量响应。
let elemActive = null
const elemShotCache = new Map()
const elemThumbNodes = new Map()
let libMenu = null // M93：当前右键菜单节点（点别处收起）
let captureSession = null // { id, name, nodeId } —— 捕获期唯一的状态
let captureTimer = null
// 'pending' | 'editing'：桌面编辑框开着时 Esc 归它自己（IDCANCEL → discarded），
// 网页端不能顺手把整次会话取消掉。
let capturePhase = null
let dialogMode = null // 'capture' | 'detail' | 'blocked'
let dialogSave = null
let dialogDiscard = null
let dialogSaveContinue = null // 保存后不关框，直接起下一次捕获
let dialogRecapture = null
let dialogLevels = null // 网页层级编辑的工作副本（勾选/属性改动只动它）
let dialogNodes = null // 桌面层级/属性编辑的工作副本
let dialogEditorKind = null // 'web' | 'desktop' | null
// 捕获结束时 captureSession 已被 endCapture() 清掉，编辑框要自己留住 id，
// 否则重组/校验都拿不到会话（桌面会丢 candidates、覆盖同名会误报重名）。
let dialogCaptureId = null
let dialogSelectedLevel = -1

function stepOf(nodeId) {
  const card = cards.find((c) => c.id === nodeId)
  return card && card.no != null ? String(card.no) : ELEM_TEXT.stepFallback
}

function whereOf(m) {
  const cmd = String(m.command || '')
  if (cmd.startsWith('browser.')) return elemText('wherePage')
  if (cmd.startsWith('desktop.')) return elemText('whereApp')
  return m.decision && m.decision.target === 'browser' ? elemText('wherePage') : elemText('whereApp')
}

function kindLabel(kind) {
  if (kind === 'browser') return elemText('kindBrowser')
  if (kind === 'desktop') return elemText('kindDesktop')
  return ''
}

async function refreshElements() {
  if (!currentPath) {
    el.elemBody.replaceChildren(mk('div', 'hint', elemText('none')))
    el.elemCount.textContent = ''
    return
  }
  try {
    const [miss, list, groups, unused] = await Promise.all([
      ppx.call('rpa.missingElements', { path: currentPath }),
      ppx.call('rpa.listElements', { path: currentPath }),
      ppx.call('rpa.listElementGroups', { path: currentPath }),
      ppx.call('rpa.findUnusedElements', { path: currentPath }),
    ])
    pickMissing = miss.missing || []
    pickElements = list.elements || []
    // M93：分组/未使用是锦上添花，RPC 缺席（旧宿主）时按空处理，别拖垮清单。
    pickGroups = groups?.groups || []
    pickUnused = unused?.elements || []
    missingNodeIds = new Set(pickMissing.map((m) => m.nodeId).filter(Boolean))
    renderCards()
    renderElementsPanel()
  } catch (error) {
    el.elemBody.replaceChildren(
      mk('div', 'hint', elemText('loadFailed', { reason: `${error.code}: ${error.message}` })),
    )
  }
}

function bindElementTools() {
  el.elemCapture?.addEventListener('click', () => startCapture(null))
  el.elemCaptureImage?.addEventListener('click', () => startCapture(null, true))
  el.elemSearch?.addEventListener('input', () => {
    elemFilterText = String(el.elemSearch.value || '')
    renderElementsPanel()
  })
  el.elemVerifyAll?.addEventListener('click', () => verifyAllAssets())
  el.dialogSave?.addEventListener('click', () => confirmElemDialog())
  el.dialogSaveContinue?.addEventListener('click', () => confirmElemDialog(true))
  el.dialogValidate?.addEventListener('click', () => validateElemDialog())
  el.dialogRecapture?.addEventListener('click', async () => {
    const run = dialogRecapture
    closeElemDialog()
    if (run) await run()
  })
  el.dialogCancel?.addEventListener('click', () => cancelElemDialog())
  el.dialogClose?.addEventListener('click', () => cancelElemDialog())
  el.dialogName?.addEventListener('keydown', (event) => {
    // 中文输入法里回车是「上屏」，不是「保存」：isComposing 为真时绝不抢。
    if (event.key === 'Enter' && !event.isComposing) {
      event.preventDefault()
      confirmElemDialog()
    }
  })
}

/* ---- 捕获：一个按钮 → 浮窗负责全部提示 → 抓完弹编辑框 -------------------- */

async function startCapture(name, image = false) {
  if (!currentPath) return
  let res
  try {
    res = await ppx.call('rpa.startCapture', {
      path: currentPath,
      name: name || null,
      image,
    })
  } catch (error) {
    // 起捕获失败的原因已经写在桌面浮窗里（后端 _show_blocked），这里不再显示提示。
    return
  }
  if (!res.ok) return // 被挡住 / 起不来：同样只在浮窗里说
  captureSession = { id: res.captureId, name: res.name || null, nodeId: res.nodeId || null }
  capturePhase = 'pending'
  pollCapture()
}

// 主窗的最小化 / 还原由后端同进程的 pywebview 窗口做（最小化后 JS 定时器可能被节流，
// 不能把「还原」押在前端）。这里的 restore 只是兜底：后端异常没还原时也能拉回来。
async function restoreApp() {
  try { await ppx.call('window.restore') } catch { /* 环境不支持就保持原样 */ }
}

function endCapture() {
  captureSession = null
  capturePhase = null
  if (captureTimer) clearTimeout(captureTimer)
  captureTimer = null
}

// 捕获期 Esc 的网页端一路（另一路是桌面浮窗的全局键状态轮询，两条都幂等）。
// 只在 pending 发：editing 时桌面编辑框自己有 Esc（取消这次入库），别抢。
async function cancelCaptureByEsc() {
  const session = captureSession
  if (!session) return
  endCapture()
  try {
    await ppx.call('rpa.cancelCapture', { path: currentPath, capture_id: session.id })
  } catch { /* 后端已经收场也一样 */ }
  await restoreApp()
}

async function pollCapture() {
  if (captureTimer) clearTimeout(captureTimer)
  const tick = async () => {
    const session = captureSession
    if (!session) return
    let res
    try {
      res = await ppx.call('rpa.capturePoll', { path: currentPath, capture_id: session.id })
    } catch (error) {
      await restoreApp()
      endCapture()
      return
    }
    if (!res.ok) {
      await restoreApp()
      endCapture()
      return
    }
    capturePhase = res.state === 'editing' ? 'editing' : 'pending'
    if (res.state === 'ready') {
      // 非 Windows 兜底：确认框开在页面里（win32 上确认框由 capture_kit 开在桌面，
      // 状态机会走 editing → committed/discarded，这条路不会到）。
      await restoreApp()
      endCapture()
      openCaptureDialog(session.id, res.preview || {})
      return
    }
    if (res.state === 'committed') {
      // 桌面的原生编辑框已完成入库（或详情改名）：刷新资产列表即可。
      await restoreApp()
      endCapture()
      await refreshElements()
      return
    }
    // 终态：取消 / 放弃 / 离线 / 失败 / 被挡下。原因都不在面板里说，浮窗已经说过。
    if (['cancelled', 'discarded', 'offline', 'failed', 'blocked'].includes(res.state)) {
      await restoreApp()
      endCapture()
      return
    }
    // 'pending' / 'editing'（桌面编辑框开着）都继续等。
    captureTimer = setTimeout(tick, 1000)
  }
  captureTimer = setTimeout(tick, 800)
}

/* ---- 编辑框（抓完确认 + 详情改名共用同一个） ------------------------------ */

function setDialogNote(text, isError = false) {
  if (!el.dialogNote) return
  el.dialogNote.textContent = text || ''
  el.dialogNote.classList[isError && text ? 'add' : 'remove']('error')
}

/* ---- 影刀式编辑区 -------------------------------------------------------
 * 层级/属性/定位三者共用后端给的一份模型；前端只改工作副本。网页勾层级让后端
 * rpa.elementCompose(levels) 重组 css；桌面勾层级/属性让它 compose(nodes) 重组
 * selector 字典（含命中候选）。重组侧唯一权威在内核，前端不拼字符串。
 * 没有层级模型时退回单一定位文本框——旧资产走这条。
 */

function elemLevelLabel(level) {
  const bits = [String(level.tag || '*')]
  if (level.id && level.id.value) bits.push(`id="${level.id.value}"`)
  const classes = (level.classes || []).map((c) => c.value).filter(Boolean).join(' ')
  if (classes) bits.push(`class="${classes}"`)
  if (level.nth && level.nth.value) bits.push(`index="${level.nth.value}"`)
  return `<${bits.join(' ')}>`
}

function renderElemEditor(model) {
  dialogLevels = null
  dialogNodes = null
  dialogEditorKind = null
  dialogSelectedLevel = -1
  if (!el.dialogEditor) return
  const levels = model && Array.isArray(model.levels) && model.levels.length ? model.levels : null
  const nodes = model && Array.isArray(model.nodes) && model.nodes.length ? model.nodes : null
  if (!model || (!levels && !nodes && model.selector == null)) {
    el.dialogEditor.hidden = true
    if (el.dialogPreviewField) el.dialogPreviewField.hidden = true
    return
  }
  el.dialogEditor.hidden = false
  if (levels || nodes) {
    if (levels) {
      dialogEditorKind = 'web'
      dialogLevels = levels.map((lv) => JSON.parse(JSON.stringify(lv)))
      dialogSelectedLevel = levels.length - 1
    } else {
      dialogEditorKind = 'desktop'
      dialogNodes = nodes.map((n) => JSON.parse(JSON.stringify(n)))
      dialogSelectedLevel = nodes.length - 1
    }
    if (el.dialogTreePane) el.dialogTreePane.hidden = false
    if (el.dialogAttrPane) el.dialogAttrPane.hidden = false
    if (el.dialogSimplePane) el.dialogSimplePane.hidden = true
    if (el.dialogPreviewField) el.dialogPreviewField.hidden = false
    if (el.dialogPreview) {
      el.dialogPreview.value = String(model.selector || '')
      el.dialogPreview.title = String(model.selector || '')
    }
    if (dialogEditorKind === 'web') {
      renderElemLevels()
      renderElemAttrs()
    } else {
      renderDesktopLevels()
      renderDesktopAttrs()
    }
  } else {
    if (el.dialogTreePane) el.dialogTreePane.hidden = true
    if (el.dialogAttrPane) el.dialogAttrPane.hidden = true
    if (el.dialogSimplePane) el.dialogSimplePane.hidden = false
    if (el.dialogPreviewField) el.dialogPreviewField.hidden = true
    if (el.dialogSelector) el.dialogSelector.value = String(model.selector || '')
  }
}

// 层级勾选口径：目标级（链尾）永远参与；与内核 selector_tree 的同名纯函数一致。
function levelIncludedAfterToggle(count, index, wanted) {
  return count > 0 && index === count - 1 ? true : Boolean(wanted)
}

// 勾上本层任一属性 ⇒ 该层自动参与定位；取消属性不反取消层。
// 与内核 selector_tree.included_after_attr_check 同口径，桌面节点共用。
function includedAfterAttrCheck(included, attrOn) {
  return Boolean(included) || Boolean(attrOn)
}

function renderElemLevels() {
  if (!el.dialogLevels || !dialogLevels) return
  el.dialogLevels.replaceChildren(...dialogLevels.map((level, index) => {
    const row = mk('div', `elemLevel${index === dialogSelectedLevel ? ' selected' : ''}`)
    const box = mk('input')
    box.type = 'checkbox'
    box.checked = Boolean(level.included)
    box.addEventListener('change', () => {
      // 目标级必须参与定位：点掉弹回，但方框保持可点（点行文字只选中看属性）
      level.included = levelIncludedAfterToggle(dialogLevels.length, index, box.checked)
      box.checked = level.included
      refreshElemCompose()
    })
    row.append(box, mk('span', '', elemLevelLabel(level)))
    row.addEventListener('click', (event) => {
      if (event && event.target === box) return
      dialogSelectedLevel = index
      renderElemLevels()
      renderElemAttrs()
    })
    return row
  }))
}

function renderElemAttrs() {
  if (!el.dialogAttrs || !dialogLevels) return
  const level = dialogLevels[dialogSelectedLevel]
  if (!level) {
    el.dialogAttrs.replaceChildren()
    return
  }
  const rows = []
  const addRow = (label, obj, withMode) => {
    const row = mk('div', 'elemAttr')
    const on = mk('input')
    on.type = 'checkbox'
    on.checked = Boolean(obj.on)
    on.addEventListener('change', () => {
      obj.on = on.checked
      // 勾上属性 ⇒ 所在层自动参与定位（否则选择器不变，等于点了没反应）。
      // 取消属性不反取消层；层级框的显示要跟着刷新。
      const wasIncluded = Boolean(level.included)
      level.included = includedAfterAttrCheck(wasIncluded, obj.on)
      if (level.included !== wasIncluded) renderElemLevels()
      refreshElemCompose()
    })
    const value = mk('input')
    value.value = String(obj.value || '')
    value.addEventListener('input', () => {
      obj.value = value.value
      refreshElemCompose()
    })
    row.append(on, value)
    if (withMode) {
      const mode = mk('select')
      for (const option of ['精确', '包含']) {
        const opt = mk('option', '', option)
        opt.value = option
        mode.append(opt)
      }
      mode.value = obj.mode || '精确'
      mode.addEventListener('change', () => {
        obj.mode = mode.value
        refreshElemCompose()
      })
      row.append(mode)
    } else {
      row.append(mk('span', 'modalLabel', label))
    }
    rows.push(row)
  }
  addRow('id', level.id, false)
  for (const cls of level.classes || []) addRow('class', cls, true)
  if (level.nth) addRow('序号', level.nth, false)
  el.dialogAttrs.replaceChildren(...rows)
}

/** 桌面节点标签：<控件类型 "名称">，与影刀层级树同形。 */
function desktopNodeLabel(node) {
  const bits = [String(node.type || '?')]
  if (node.name) bits.push(`"${node.name}"`)
  return `<${bits.join(' ')}>`
}

function renderDesktopLevels() {
  if (!el.dialogLevels || !dialogNodes) return
  el.dialogLevels.replaceChildren(...dialogNodes.map((node, index) => {
    const row = mk('div', `elemLevel${index === dialogSelectedLevel ? ' selected' : ''}`)
    const box = mk('input')
    box.type = 'checkbox'
    box.checked = Boolean(node.selected)
    // 窗口信息行只读；目标级恒参与（勾掉会让 locator 没有身份字段）。
    box.disabled = Boolean(node.readonly) || index === dialogNodes.length - 1
    box.addEventListener('change', () => {
      node.selected = box.checked
      refreshElemCompose()
    })
    row.append(box, mk('span', '', desktopNodeLabel(node)))
    row.addEventListener('click', (event) => {
      if (event && event.target === box) return
      dialogSelectedLevel = index
      renderDesktopLevels()
      renderDesktopAttrs()
    })
    return row
  }))
}

function renderDesktopAttrs() {
  if (!el.dialogAttrs || !dialogNodes) return
  const node = dialogNodes[dialogSelectedLevel]
  if (!node) {
    el.dialogAttrs.replaceChildren()
    return
  }
  const isTarget = dialogSelectedLevel === dialogNodes.length - 1
  const rows = []
  for (const attr of node.attributes || []) {
    const row = mk('div', 'elemAttr desk')
    const on = mk('input')
    on.type = 'checkbox'
    on.checked = Boolean(attr.required)
    on.disabled = Boolean(attr.readonly)
    on.addEventListener('change', () => {
      attr.required = on.checked
      // 窗口信息行 readonly（属性框已禁用）；目标级恒参与。
      // 其余节点：勾上属性 ⇒ 该节点自动参与定位，并刷新层级框显示。
      const wasSelected = Boolean(node.selected)
      node.selected = includedAfterAttrCheck(wasSelected, attr.required)
      if (node.selected !== wasSelected) renderDesktopLevels()
      refreshElemCompose()
    })
    const label = mk('span', 'modalLabel', String(attr.label || attr.field || ''))
    const value = mk('input')
    value.value = String(attr.value || '')
    value.readOnly = Boolean(attr.readonly)
    value.addEventListener('input', () => {
      attr.value = value.value
      refreshElemCompose()
    })
    const mode = mk('select')
    // 运行期 matchMode 是 locator 的单个字段，只作用于 name / automationId
    // （controlType 恒等值）⇒ 其它行给只读「精确」，不给点了不生效的假下拉。
    const canFuzzy = !attr.readonly && isTarget
      && (attr.field === 'name' || attr.field === 'automationId')
    for (const option of ['精确', '包含', '正则']) {
      const opt = mk('option', '', option)
      opt.value = option
      mode.append(opt)
    }
    mode.value = attr.operator || '精确'
    mode.disabled = !canFuzzy
    if (canFuzzy) {
      mode.addEventListener('change', () => {
        attr.operator = mode.value
        refreshElemCompose()
      })
    }
    row.append(on, label, mode, value)
    rows.push(row)
  }
  el.dialogAttrs.replaceChildren(...rows)
}

async function refreshElemCompose() {
  if (dialogEditorKind === 'desktop') {
    if (!dialogNodes || !el.dialogPreview) return
    try {
      const res = await ppx.call('rpa.elementCompose', {
        nodes: dialogNodes,
        path: currentPath,
        capture_id: dialogCaptureId,
      })
      if (res && res.ok && typeof res.selector === 'string') {
        el.dialogPreview.value = res.selector
        el.dialogPreview.title = res.selector
      } else if (res && res.error) setDialogNote(res.error, true)
    } catch {
      // 桥异常时保留上一次预览，不把用户已勾的层级丢掉。
    }
    return
  }
  if (dialogEditorKind !== 'web' || !dialogLevels || !el.dialogPreview) return
  try {
    const res = await ppx.call('rpa.elementCompose', { levels: dialogLevels })
    if (res && res.ok && typeof res.selector === 'string') el.dialogPreview.value = res.selector
    else if (res && res.error) setDialogNote(res.error, true)
  } catch {
    // 桥异常时保留上一次预览，不把用户已勾的层级丢掉。
  }
}

function elemEditorSelector() {
  if (dialogLevels && el.dialogPreview) return el.dialogPreview.value
  return el.dialogSelector ? el.dialogSelector.value : ''
}

function elemEditorState() {
  if (dialogEditorKind === 'web' && dialogLevels) return { levels: dialogLevels }
  if (dialogEditorKind === 'desktop' && dialogNodes) return { nodes: dialogNodes }
  return null
}

async function validateElemDialog() {
  if (!dialogMode) return
  const name = String(el.dialogName?.value || '').trim()
  setDialogNote('')
  const args = { path: currentPath, name, selector: elemEditorSelector() }
  if (dialogCaptureId) args.capture_id = dialogCaptureId
  try {
    const res = await ppx.call('rpa.elementValidate', args)
    if (res && res.ok) setDialogNote('校验通过：名字和定位都没有问题')
    else setDialogNote((res && res.error) || '校验没过', true)
  } catch (error) {
    setDialogNote(`校验没过：${error?.message || error}`, true)
  }
}

async function recaptureFromDialog() {
  const old = captureSession?.id
  if (!old) return
  try {
    const res = await ppx.call('rpa.recaptureElement', { path: currentPath, capture_id: old })
    if (!res || !res.ok) return
    captureSession = { id: res.captureId, name: res.name || null, nodeId: res.nodeId || null }
    capturePhase = 'pending'
    pollCapture()
  } catch {
    // 起不来时后端浮窗已经说了原因。
  }
}

function showElemDialog(options) {
  dialogMode = options.mode
  dialogSave = options.save || null
  dialogDiscard = options.discard || null
  dialogSaveContinue = options.saveContinue || null
  dialogRecapture = options.recapture || null
  dialogCaptureId = options.captureId || null
  if (!el.dialog) return
  el.dialog.hidden = false
  el.dialog.classList.add('open')
  if (el.dialogTitle) el.dialogTitle.textContent = options.title || ''
  if (el.dialogName) {
    el.dialogName.value = options.name || ''
    el.dialogName.placeholder = elemText('namePlaceholder')
    el.dialogName.readOnly = Boolean(options.readOnly)
    el.dialogName.focus?.()
    el.dialogName.select?.()
  }
  if (el.dialogMeta) {
    // 引用行是可点的按钮（跳到节点去解除），其余仍是纯文本行。
    el.dialogMeta.replaceChildren(
      ...(options.meta || []).map((line) =>
        typeof line === 'string' || typeof line === 'number'
          ? mk('div', 'modalLine', String(line))
          : line,
      ),
    )
  }
  if (el.dialogSave) el.dialogSave.disabled = !dialogSave
  if (el.dialogValidate) el.dialogValidate.disabled = options.mode === 'detail'
  if (el.dialogRecapture) el.dialogRecapture.disabled = !dialogRecapture
  if (el.dialogSaveContinue) el.dialogSaveContinue.disabled = !dialogSaveContinue
  renderElemEditor(options.model)
  setDialogNote(options.note || '', Boolean(options.error))
}

function closeElemDialog() {
  dialogMode = null
  dialogSave = null
  dialogDiscard = null
  dialogSaveContinue = null
  dialogRecapture = null
  dialogLevels = null
  dialogNodes = null
  dialogEditorKind = null
  dialogCaptureId = null
  dialogSelectedLevel = -1
  if (!el.dialog) return
  el.dialog.hidden = true
  el.dialog.classList.remove('open')
  setDialogNote('')
}

async function confirmElemDialog(keepOpen = false) {
  if (!dialogSave) return
  const name = String(el.dialogName?.value || '').trim()
  if (!name) {
    setDialogNote(elemText('nameEmpty'), true)
    return
  }
  const selector = elemEditorSelector()
  const editState = elemEditorState()
  let res
  try {
    res = await dialogSave(name, selector, editState)
  } catch (error) {
    setDialogNote(elemText('saveFailed', { reason: error?.message || error }), true)
    return
  }
  if (!res || !res.ok) {
    setDialogNote(elemText('saveFailed', { reason: (res && res.error) || '' }), true)
    return
  }
  // 「保存并继续」要先记住回调：closeElemDialog 会把面板状态清空。
  const next = keepOpen ? dialogSaveContinue : null
  closeElemDialog()
  await refreshElements()
  if (next) await next()
}

async function cancelElemDialog() {
  const discard = dialogDiscard
  closeElemDialog()
  if (!discard) return
  try {
    await discard()
  } catch {
    // 丢掉暂存失败也不该再弹一句：用户已经按了取消。
  }
}

async function openCaptureDialog(captureId, preview) {
  const meta = []
  meta.push(`${kindLabel(preview.kind)} · ${preview.selector || ''}`.trim())
  if (preview.matches) meta.push(elemText('matches', { count: preview.matches }))
  meta.push(
    preview.nodeId
      ? elemText('refStep', { step: stepOf(preview.nodeId) })
      : elemText('refNone'),
  )
  // preview 只带定位字符串；层级树要单独取完整描述符（拿不到就退回简式文本框）。
  let model = { kind: preview.kind, selector: preview.selector || '' }
  try {
    const res = await ppx.call('rpa.elementEditModel', { path: currentPath, capture_id: captureId })
    if (res && res.ok) model = { ...res, selector: res.selector ?? preview.selector ?? '' }
  } catch {
    // 模型 RPC 不可用时保留 preview 的简式形态，捕获确认不能被它卡住。
  }
  showElemDialog({
    mode: 'capture',
    title: elemText('confirmTitle'),
    name: preview.name || '',
    meta,
    model,
    captureId,
    save: (name, selector, editState) => ppx.call('rpa.commitElement', {
      path: currentPath, capture_id: captureId, name, selector, edit_state: editState,
    }),
    discard: () => ppx.call('rpa.discardCapture', { path: currentPath, capture_id: captureId }),
    recapture: () => recaptureFromDialog(),
    saveContinue: () => startCapture(null),
  })
}

async function openAssetDetail(name) {
  // win32：详情编辑在桌面原生框里做（与捕获确认同一个 ElementEditDialog），
  // 前端只轮询终态；非 Windows / 框不可用才回退下面的页面弹窗。
  try {
    const edit = await ppx.call('rpa.editElement', { path: currentPath, name })
    if (edit && edit.ok && edit.state === 'editing') {
      captureSession = { id: edit.editId, name: null, nodeId: null }
      capturePhase = 'editing' // 桌面详情框的 Esc 归它自己，别在这儿取消会话
      pollCapture()
      return
    }
  } catch (error) {
    // 桥/服务异常也回退页面形态：详情不能打不开
  }
  let res
  try {
    res = await ppx.call('rpa.elementDetail', { path: currentPath, name })
  } catch (error) {
    showBrokenDetail(name, error?.message || error)
    return
  }
  if (!res.ok) {
    showBrokenDetail(name, res.error || res.code || '')
    return
  }
  const doc = res.document || {}
  const meta = []
  meta.push(`${kindLabel(doc.kind)} · ${res.selectorText || ''}`.trim())
  if (doc.verifyCount) meta.push(elemText('matches', { count: doc.verifyCount }))
  if (doc.capturedAt) meta.push(elemText('capturedAt', { time: doc.capturedAt }))
  const refs = res.referencedBy || []
  if (!refs.length) meta.push(elemText('refNone'))
  else {
    // 只说「第 N 步」不够：被引用参数不在 with 里，用户翻参数表找不到。
    // 把「哪一步的哪个字段指向哪个资产」逐条摊开。
    for (const ref of refs) {
      meta.push(
        elemText('refDetail', { step: stepOf(ref.nodeId), param: ref.param, name: ref.name }),
      )
    }
  }
  showElemDialog({
    mode: 'detail',
    title: elemText('detailTitle'),
    name,
    meta,
    save: (newName, _selector, editState) => ppx.call('rpa.saveElement', {
      path: currentPath, name, new_name: newName, edit_state: editState,
    }),
  })
}

function showBrokenDetail(name, reason) {
  showElemDialog({
    mode: 'detail',
    title: elemText('detailTitle'),
    name,
    meta: [],
    readOnly: true,
    error: true,
    note: elemText('detailFailed', { reason }),
  })
}

/* ---- 列表 ---------------------------------------------------------------- */

// 删不掉的说明框：复用页面编辑框的只读形态（有「取消」「关闭」，Esc 也能退）。
// ★ kit 只管捕获和编辑，删除的业务判断与反馈全部留在网页端，不再弹桌面浮窗。
function refLink(ref) {
  const line = mk(
    'button',
    'modalLine refLink',
    elemText('deleteBlockedRef', {
      step: stepOf(ref.nodeId),
      param: ref.param,
      name: ref.name,
    }),
  )
  line.type = 'button'
  line.title = `跳到节点 ${ref.nodeId}`
  line.addEventListener('click', () => {
    closeElemDialog()
    const card = cards.find((c) => c.id === ref.nodeId)
    if (card) selectCard(card)
  })
  return line
}

function showDeleteProblem(name, refs, reason = '') {
  const meta = refs.length
    ? [elemText('deleteBlockedHint', { count: refs.length }), ...refs.map(refLink)]
    : [elemText('deleteFailedHint')]
  showElemDialog({
    mode: 'blocked',
    title: elemText('deleteBlockedTitle'),
    name,
    readOnly: true,
    meta,
    error: true,
    note: reason || (refs.length ? elemText('deleteBlockedNote') : ''),
  })
}

async function deleteAsset(name) {
  // 删之前先查「哪几个节点引用了它」。查到就不发删除，直接在网页端列出节点。
  let detail
  try {
    detail = await ppx.call('rpa.elementDetail', { path: currentPath, name })
  } catch (error) {
    showDeleteProblem(name, [], elemText('deleteCheckFailed', { reason: error?.message || error }))
    return
  }
  const refs = (detail && detail.referencedBy) || []
  if (refs.length) {
    showDeleteProblem(name, refs)
    return
  }
  let res
  try {
    res = await ppx.call('rpa.deleteElement', { path: currentPath, name })
  } catch (error) {
    showDeleteProblem(name, [], elemText('deleteFailed', { reason: error?.message || error }))
    return
  }
  if (!res || !res.ok) {
    // 服务端护栏还在（防绕过界面直调 RPC / 并发改动）：同样只在网页端说。
    showDeleteProblem(name, (res && res.referencedBy) || [], (res && res.error) || '')
    return
  }
  await refreshElements()
}

function missingRow(m) {
  const row = mk('div', 'elemRow missing')
  row.dataset.pickName = m.name
  row.append(mk('span', 'elemName', m.name))
  row.append(
    mk('span', 'elemMeta', elemText('missingMeta', { step: stepOf(m.nodeId), where: whereOf(m) })),
  )
  // 唯一主操作：怎么抓由后端决定，界面不提供技术选择题。
  const fill = mk('button', 'elemFill', elemText('fill'))
  fill.addEventListener('click', () => startCapture(m.name))
  row.append(fill)
  return row
}

/* M102 S2：一个资产行 = 缩略图 + 名字 + 一行元信息 + 悬停动作。
 *
 * 为什么把选择器从「独占一行」收回成单行截断：M93 那版为了看得全让 meta 折行，
 * 行高被撑到 60px 上下，一屏看不到几项。现在完整定位在右栏常驻显示，行里只需要
 * 「能不能认出是哪个」。反过来说，这也是**故意**改回单行——不是没注意到旧反馈。
 *
 * 三个动作仍留在行里（详情/校验/删除），只是默认透明、hover 或聚焦时浮现：
 * 删除依然是最后一个，且要 hover 才现形，避免手滑点掉。
 */
function assetRow(e) {
  const row = mk('div', 'elemRow asset')
  row.dataset.elemName = e.name
  if (e.name === elemActive) row.classList.add('active')
  const pick = mk('input', 'elemPick')
  pick.type = 'checkbox'
  pick.checked = elemSelected.has(e.name)
  pick.addEventListener('change', () => {
    if (pick.checked) elemSelected.add(e.name)
    else elemSelected.delete(e.name)
    renderBatchBar()
  })
  row.append(pick)
  row.append(assetThumb(e))
  row.append(mk('span', 'elemName', e.name))
  const bits = []
  if (kindLabel(e.kind)) bits.push(kindLabel(e.kind))
  if (e.processName) bits.push(e.processName)
  if (e.winTitle) bits.push(e.winTitle)
  if (e.selector) bits.push(selectorLine(e.selector))
  const meta = mk('span', 'elemMeta', bits.filter(Boolean).join(' · '))
  meta.title = selectorText(e.selector)
  row.append(meta)
  if (e.refCount) row.append(mk('span', 'elemRefs', `${e.refCount} 处引用`))
  const actions = mk('span', 'elemRowAct')
  const detail = mk('button', 'elemAct', elemText('detail'))
  detail.addEventListener('click', () => openAssetDetail(e.name))
  const verify = mk('button', 'elemAct', elemText('verify'))
  verify.addEventListener('click', () => verifyAsset(e.name, meta, verify))
  const remove = mk('button', 'elemAct danger', elemText('remove'))
  remove.addEventListener('click', () => deleteAsset(e.name))
  actions.append(detail, verify, remove)
  row.append(actions)
  // 点行本身 = 选中（右栏看截图与完整定位）；行内按钮自己 stopPropagation 不了，
  // 但 selector 只影响右栏，误触也无副作用。
  row.addEventListener('click', () => selectAssetRow(e.name))
  row.addEventListener('contextmenu', (event) => {
    event.preventDefault?.()
    openLibMenu(event, 'asset', e)
  })
  return row
}

function selectAssetRow(name) {
  elemActive = name
  for (const node of Array.from(el.elemBody.children || [])) {
    if (!node.classList?.contains?.('elemRow')) continue
    if (node.dataset?.elemName === name) node.classList.add('active')
    else if (node.dataset?.elemName) node.classList.remove('active')
  }
  renderElementDetail()
}

// 元信息里那一小段定位：单行截断用，桌面 locator 也压成一句。
function selectorLine(value) {
  if (typeof value === 'string') return value.replace(/\s+/g, ' ').trim()
  const locator = value && typeof value === 'object' ? value : null
  if (!locator) return ''
  const path = Array.isArray(locator.path) ? locator.path : []
  const last = path[path.length - 1]
  if (last && typeof last === 'object') {
    return String(last.name || last.automationId || last.controlType || '')
  }
  if (locator.driver === 'image') return '图像'
  return String(locator.controlType || locator.control_type || '')
}

// 右栏/详情里要看得全：对象形状直接摊成 JSON，别显示成 [object Object]。
function selectorText(value) {
  if (value == null || value === '') return '—'
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function shotKey(e) {
  return `${e.name}|${e.screenshot || ''}`
}

function assetThumb(e) {
  const box = mk('span', 'elemThumb')
  const key = shotKey(e)
  const url = elemShotCache.get(key)
  if (url) {
    box.classList.add('has')
    const img = mk('img', 'elemThumbImg')
    img.src = url
    img.alt = ''
    box.append(img)
  } else {
    box.append(mk('span', 'elemThumbFallback', String((e.name || '?').slice(0, 1))))
  }
  const nodes = elemThumbNodes.get(key) || new Set()
  nodes.add(box)
  elemThumbNodes.set(key, nodes)
  if (e.screenshot && !elemShotCache.has(key)) loadElementShot(e, key)
  return box
}

// 缩略图按需拉：一次几个 KB，但二十个元素一起塞进 listElements 就是几百 KB。
async function loadElementShot(e, key) {
  elemShotCache.set(key, null) // 先钉住，避免同一次渲染重复发请求
  try {
    const res = await ppx.call('rpa.elementShot', { path: currentPath, name: e.name })
    const url = res && res.shot ? String(res.shot) : ''
    if (!url) return
    elemShotCache.set(key, url)
    for (const node of elemThumbNodes.get(key) || []) {
      // 用 `=== false` 而不是 `!node.isConnected`：真 DOM 里它恒为布尔值，
      // 而最小 DOM shim 里根本没有这个属性——写成取反会让缩略图永远不出现。
      if (node.isConnected === false) continue
      node.classList.add('has')
      const img = document.createElement('img')
      img.className = 'elemThumbImg'
      img.src = url
      img.alt = ''
      node.replaceChildren(img)
    }
    if (elemActive === e.name) renderElementDetail()
  } catch {
    // 没图就是没图：界面显示首字母占位，不弹错。
  }
}

/* 右栏详情：截图 + 完整定位 + 引用/来源。**只读展示**——三个动作仍在行上，
 * 这里不复制第二套按钮（两处按钮必然漂移成两种行为）。 */
function renderElementDetail() {
  const pane = el.elemDetail
  if (!pane) return
  const asset = pickElements.find((e) => e.name === elemActive) || null
  pane.replaceChildren()
  if (pane.hidden !== !asset) pane.hidden = !asset
  if (!asset) {
    pane.append(mk('div', 'elemDetailPick', elemText('detailPick')))
    return
  }
  const head = mk('div', 'elemDetailHead')
  head.append(mk('div', 'elemDetailName', asset.name))
  const sub = []
  if (kindLabel(asset.kind)) sub.push(kindLabel(asset.kind))
  if (asset.groupName) sub.push(asset.groupName)
  head.append(mk('div', 'elemDetailSub', sub.join(' · ')))
  pane.append(head)

  const shotBox = mk('div', 'elemDetailShot')
  const url = elemShotCache.get(shotKey(asset))
  if (url) {
    const img = mk('img', 'elemDetailImg')
    img.src = url
    img.alt = ''
    shotBox.append(img)
  } else {
    shotBox.append(mk('div', 'elemDetailShotNone', elemText('shotNone')))
  }
  pane.append(shotBox)

  const rows = []
  if (asset.winTitle) rows.push([elemText('detailWhere'), asset.winTitle])
  if (asset.refCount) rows.push([elemText('detailRefs'), `${asset.refCount} 处`])
  else rows.push([elemText('detailRefs'), elemText('detailNoRefs')])
  if (asset.capturedAt) rows.push([elemText('detailCaptured'), String(asset.capturedAt)])
  if (rows.length) {
    const info = mk('div', 'elemDetailInfo')
    for (const [label, value] of rows) {
      const line = mk('div', 'elemDetailLine')
      line.append(mk('span', 'elemDetailLabel', label))
      line.append(mk('span', 'elemDetailValue', String(value)))
      info.append(line)
    }
    pane.append(info)
  }

  const block = mk('div', 'elemDetailBlock')
  block.append(mk('div', 'elemDetailLabel', elemText('detailSelector')))
  block.append(mk('pre', 'elemDetailCode', selectorText(asset.selector)))
  pane.append(block)
}

async function verifyAsset(name, meta, button) {
  const original = meta.textContent
  button.disabled = true
  meta.textContent = elemText('verifyRunning')
  try {
    const res = await ppx.call('rpa.verifyElement', { path: currentPath, name })
    if (!res || res.ok === false) {
      meta.textContent = elemText('verifyError', { reason: (res && res.message) || '' })
    } else if (res.error) {
      meta.textContent = elemText('verifyError', { reason: res.error })
    } else if (res.count === 0) {
      // 命中 0 有两种含义，不能都糊成「这次没命中」：目标窗口根本没有无障碍树时，
      // 改选择器怎么改都没用，得让应用重新走一次「首次 UIA 查询」（M98/M100）。
      meta.textContent = res.hint === 'tree-missing'
        ? elemText('verifyNoTree')
        : elemText('verifyNone')
    } else {
      meta.textContent = elemText('verifyHit', { count: res.count })
    }
  } catch (error) {
    meta.textContent = elemText('verifyError', { reason: error?.message || error })
  } finally {
    button.disabled = false
    setTimeout(() => {
      if (meta.isConnected) meta.textContent = original
    }, 4000)
  }
}

/* M93：影刀式「分组 + 元素同树」。分组来自 element_groups.json，元素归属用
   顶层 group（组 id）。前端只按 group 分桶，不在前端算引用数/校验状态——那些
   都由 listElements RPC 给。行仍是 elemBody 的**直接子节点**（verifyAllAssets
   按 children 找行），所以树用「分组头行 + 缩进资产行」的扁平形态表达。 */
function visibleAssets() {
  const q = elemFilterText.trim().toLowerCase()
  let assets = q
    ? pickElements.filter((e) =>
        `${e.name || ''} ${e.selector || ''} ${e.groupName || ''} ${e.processName || ''}`
          .toLowerCase().includes(q))
    : pickElements.slice()
  if (elemOnlyUnused) assets = assets.filter((e) => pickUnused.includes(e.name))
  return assets
}

// 纯函数：分组桶顺序 = 服务端分组顺序 + 末尾未分组桶。测试可直接断言，
// 不依赖 DOM。空分组也保留（影刀允许先建组再拖元素进来）。
function elementTreeBuckets(groups, elements) {
  const ids = new Set(groups.map((g) => g.id))
  const buckets = groups.map((g) => ({
    id: g.id, name: g.name, collapsed: !!g.collapsed,
    elements: elements.filter((e) => e.group === g.id),
  }))
  const ungrouped = elements.filter((e) => !e.group || !ids.has(e.group))
  buckets.push({
    id: null, name: elemText('ungrouped'), collapsed: false, elements: ungrouped,
  })
  return buckets
}

function groupHeaderRow(bucket) {
  const row = mk('div', 'elemGroup elemGroupRow')
  row.dataset.groupId = bucket.id == null ? '' : bucket.id
  const caret = mk('span', 'elemGroupCaret', bucket.collapsed ? '▸' : '▾')
  const name = mk('span', 'elemGroupName', bucket.name)
  const count = mk('span', 'elemGroupCount', String(bucket.elements.length))
  const more = mk('button', 'elemGroupMore', '⋯')
  more.type = 'button'
  more.setAttribute('aria-label', elemText('groupRename'))
  row.append(caret, name, count, more)
  row.addEventListener('click', () => toggleGroup(bucket))
  more.addEventListener('click', (event) => {
    event.stopPropagation?.()
    openLibMenu(event, 'group', bucket)
  })
  row.addEventListener('contextmenu', (event) => {
    event.preventDefault?.()
    openLibMenu(event, 'group', bucket)
  })
  return row
}

function renderElementsPanel() {
  el.elemBody.replaceChildren()
  el.elemCount.textContent = pickMissing.length ? String(pickMissing.length) : ''
  const assets = visibleAssets()
  if (!pickMissing.length && !assets.length) {
    el.elemBody.append(mk('div', 'hint',
      elemOnlyUnused ? elemText('unusedNone') : (elemFilterText.trim() ? '没有匹配的元素' : elemText('none'))))
    renderElementDetail()
    return
  }
  if (pickMissing.length) {
    el.elemBody.append(mk('div', 'elemHead', elemText('missingHead')))
    for (const m of pickMissing) el.elemBody.append(missingRow(m))
  }
  if (assets.length || pickGroups.length) {
    el.elemBody.append(mk('div', 'elemHead', elemText('doneHead')))
    for (const bucket of elementTreeBuckets(pickGroups, assets)) {
      el.elemBody.append(groupHeaderRow(bucket))
      if (bucket.collapsed) continue
      for (const e of bucket.elements) el.elemBody.append(assetRow(e))
    }
  }
  // 选中集可能因刷新而失效（资产被删），同步收敛后再决定是否显示批量条。
  const names = new Set(pickElements.map((e) => e.name))
  for (const name of Array.from(elemSelected)) if (!names.has(name)) elemSelected.delete(name)
  // 右栏跟着收敛：元素被删/改名后不能还挂着上一份详情。
  if (elemActive && !names.has(elemActive)) elemActive = null
  renderElementDetail()
  renderBatchBar()
}

/* ---- M93：影刀式分组树管理（右键菜单 / 多选批量条） -------------------------
 * 菜单与批量条都是动态 DOM（建在 elemBody 之外的 elemLibLayer），不碰
 * index.html 静态按钮白名单，也不动资产行「详情/校验/删除」三按钮。
 * 所有写操作走 P1 RPC；前端只负责收集参数与刷新，不自己改盘、不算引用。 */

function closeLibMenu() {
  libMenu = null
  if (el.elemLibLayer) el.elemLibLayer.replaceChildren()
  // 菜单收起后若有选中项，批量条重新浮出（两者共用 layer）。
  if (elemSelected.size) renderBatchBar()
}

function libMenuItem(label, action, danger) {
  const b = mk('button', `libMenuItem${danger ? ' danger' : ''}`, label)
  b.type = 'button'
  b.addEventListener('click', async () => {
    closeLibMenu()
    await action()
  })
  return b
}

// 相对视口的右键定位（shim 里 event 没有坐标就落到 0，不影响断言）。
function openLibMenu(event, kind, target) {
  closeLibMenu()
  if (!el.elemLibLayer) return
  const menu = mk('div', 'libMenu')
  const x = Number(event && event.clientX) || 0
  const y = Number(event && event.clientY) || 0
  menu.style.left = `${x}px`
  menu.style.top = `${y}px`
  if (kind === 'group') {
    menu.append(libMenuItem(elemText('menuNewHere'), () => startCapture(null)))
    menu.append(libMenuItem(elemText('groupNew'), () => createGroupFlow()))
    menu.append(libMenuItem(elemText('onlyUnused'), () => {
      elemOnlyUnused = !elemOnlyUnused
      renderElementsPanel()
    }))
    if (target.id != null) {
      menu.append(libMenuItem(elemText('groupRename'), () => renameGroupFlow(target)))
      menu.append(libMenuItem(elemText('menuFold'), () => toggleGroup(target)))
      menu.append(libMenuItem(elemText('groupDelete'), () => deleteGroupFlow(target), true))
    }
  } else {
    const name = target.name
    menu.append(libMenuItem(elemText('detail'), () => openAssetDetail(name)))
    menu.append(libMenuItem(elemText('copy'), () => copyAssetFlow(name)))
    menu.append(libMenuItem(elemText('moveTo'), () => moveFlow([name])))
    menu.append(libMenuItem(elemText('findRefs'), () => showRefsFlow(name)))
    menu.append(libMenuItem(elemText('remove'), () => deleteAsset(name), true))
  }
  el.elemLibLayer.append(menu)
  libMenu = menu
}

// 文本输入浮层：分组命名、复制命名、查引用结果都在这里（不引入 window.prompt）。
function libPrompt(title, initial, onOk) {
  closeLibMenu()
  if (!el.elemLibLayer) return
  const box = mk('div', 'libPrompt')
  box.append(mk('div', 'libPromptTitle', title))
  const input = mk('input', 'libPromptInput')
  input.type = 'text'
  input.value = initial || ''
  box.append(input)
  const confirm = libMenuItem(elemText('save'), async () => {
    const value = String(input.value || '').trim()
    if (!value) return
    await onOk(value)
  })
  const cancel = libMenuItem(elemText('cancel'), () => {})
  box.append(confirm, cancel)
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.isComposing) confirm.click()
  })
  el.elemLibLayer.append(box)
  input.focus?.()
  input.select?.()
}

function notifyLib(res, okText) {
  if (!res || !res.ok) setStatus((res && res.error) || elemText('saveFailed', { reason: '' }))
  else if (okText) setStatus(okText)
}

async function createGroupFlow() {
  libPrompt(elemText('groupNew'), '', async (name) => {
    const res = await ppx.call('rpa.createElementGroup', { path: currentPath, name })
    if (res && res.ok) await refreshElements()
    notifyLib(res)
  })
}

async function renameGroupFlow(group) {
  libPrompt(elemText('groupRename'), group.name, async (name) => {
    const res = await ppx.call('rpa.renameElementGroup', {
      path: currentPath, group_id: group.id, name,
    })
    if (res && res.ok) await refreshElements()
    notifyLib(res)
  })
}

async function deleteGroupFlow(group) {
  // 删分组不删元素：组内元素移到未分组（后端 deleteElementGroup 已保证）。
  const res = await ppx.call('rpa.deleteElementGroup', { path: currentPath, group_id: group.id })
  if (res && res.ok) await refreshElements()
  notifyLib(res)
}

async function copyAssetFlow(name) {
  libPrompt(elemText('copy'), `${name} 副本`, async (newName) => {
    const res = await ppx.call('rpa.copyElement', { path: currentPath, name, new_name: newName })
    if (res && res.ok) await refreshElements()
    notifyLib(res, elemText('copiedAs', { name: newName }))
  })
}

async function moveFlow(names) {
  if (!pickGroups.length) {
    setStatus(elemText('groupNameEmpty'))
    return
  }
  libPrompt(elemText('moveTo'), '', async (groupName) => {
    const group = pickGroups.find((g) => g.name === groupName)
    if (!group) return
    const res = await ppx.call('rpa.moveElementsToGroup', {
      path: currentPath, names, group_id: group.id,
    })
    if (res && res.ok) {
      elemSelected.clear()
      await refreshElements()
    }
    notifyLib(res)
  })
}

async function showRefsFlow(name) {
  const res = await ppx.call('rpa.elementReferences', { path: currentPath, name })
  const refs = (res && res.references) || []
  const lines = refs.length
    ? refs.map((r) => mk('div', 'modalLine',
        elemText('refDetail', { step: stepOf(r.nodeId), param: r.param, name: r.name })))
    : [elemText('refsZero')]
  showElemDialog({
    mode: 'detail', title: elemText('refsTitle', { name }), name, meta: lines,
    note: '', readOnly: true,
  })
}

async function toggleGroup(bucket) {
  if (bucket.id == null) return // 未分组桶不落盘
  const group = pickGroups.find((g) => g.id === bucket.id)
  if (!group) return
  const collapsed = !group.collapsed
  group.collapsed = collapsed // 先本地翻，刷新后与落盘一致
  renderElementsPanel()
  const res = await ppx.call('rpa.setElementGroupCollapsed', {
    path: currentPath, group_id: group.id, collapsed,
  })
  if (!res || !res.ok) {
    group.collapsed = !collapsed
    renderElementsPanel()
    notifyLib(res)
  }
}

function renderBatchBar() {
  if (!el.elemLibLayer) return
  // 批量条是**单例**：每次重绘先撤掉上一条，再按当前选中集决定要不要浮出。
  // 早先只 append 不清理 ⇒ 勾 N 项叠 N 条；取消最后一项时旧的「已选 N 项」
  // 还会赖着不走。layer 同时承载右键菜单/提示框，只能挑掉批量条，不能整层清空。
  const kept = Array.from(el.elemLibLayer.children).filter(
    (node) => !node.classList?.contains('libBatchBar'))
  el.elemLibLayer.replaceChildren(...kept)
  // 右键菜单开着时批量条让位；两者共用 layer，避免互相覆盖。
  if (libMenu || !elemSelected.size) return
  const bar = mk('div', 'libBatchBar')
  bar.append(mk('span', 'libBatchCount', elemText('selCount', { count: elemSelected.size })))
  const names = Array.from(elemSelected)
  bar.append(libMenuItem(elemText('batchMove'), () => moveFlow(names)))
  bar.append(libMenuItem(elemText('batchVerify'), () => batchVerifyFlow(names)))
  bar.append(libMenuItem(elemText('batchDelete'), () => batchDeleteFlow(names), true))
  bar.append(libMenuItem(elemText('clearSel'), () => {
    elemSelected.clear()
    renderElementsPanel()
  }))
  el.elemLibLayer.append(bar)
}

async function batchVerifyFlow(names) {
  for (const name of names) {
    try {
      await ppx.call('rpa.verifyElement', { path: currentPath, name })
    } catch {
      // 单个失败不挡后面；行内校验会给出细节。
    }
  }
}

async function batchDeleteFlow(names) {
  for (const name of names) await deleteAsset(name)
}

async function verifyAllAssets() {
  if (!currentPath || !pickElements.length) return
  const button = el.elemVerifyAll
  if (button) button.disabled = true
  try {
    const res = await ppx.call('rpa.verifyAllElements', { path: currentPath })
    const byName = new Map((res?.results || []).map((r) => [r.name, r]))
    for (const e of pickElements) {
      const r = byName.get(e.name)
      if (!r) continue
      const meta = Array.from(el.elemBody.children || [])
        .filter((n) => n.classList?.contains('elemRow') && n.classList.contains('asset'))
        .find((n) => n.children?.[0]?.textContent === e.name)
        ?.children?.[1]
      if (!meta) continue
      const original = meta.textContent
      meta.textContent = r.error
        ? `校验失败：${r.error}`
        : r.count
          ? `命中 ${r.count} 个`
          : '没有命中'
      setTimeout(() => { if (meta.isConnected) meta.textContent = original }, 4000)
    }
  } catch {
    // 单个失败已经写在行上；这里是整条桥断了，用户可重试。
  } finally {
    if (button) button.disabled = false
  }
}

document.addEventListener('click', (event) => {
  // 点菜单/浮层之外收起；动态菜单里的按钮自己会先 closeLibMenu 再动作。
  if (!libMenu || !el.elemLibLayer) return
  if (typeof event.target?.closest === 'function' && event.target.closest('.libMenu, .libPrompt, .libBatchBar')) return
  closeLibMenu()
})

document.addEventListener('keydown', (event) => {
  if (event.key !== 'Escape') return
  // 右键菜单先于其它层收起，避免一次 Esc 同时关菜单和整次捕获。
  if (libMenu) {
    closeLibMenu()
    return
  }
  // 捕获等待期：Esc 取消整次捕获（桌面浮窗也在全局轮询，两条路都幂等）。
  // editing 时桌面编辑框自己接 Esc，这里不能抢；页面弹框打开时 Esc 先关它。
  if (captureSession && capturePhase === 'pending' && !dialogMode) {
    event.preventDefault?.()
    cancelCaptureByEsc()
    return
  }
  if (!dialogMode) return
  cancelElemDialog()
})

el.compile.addEventListener('click', async () => {
  if (!currentPath) return
  el.compile.disabled = true
  try {
    renderCompile(await ppx.call('rpa.compileCheck', { path: currentPath }))
  } catch (error) {
    el.compileOut.replaceChildren(mk('div', 'bad', `${error.code}: ${error.message}`))
  } finally {
    el.compile.disabled = false
  }
})

/* ---- 运行 ---------------------------------------------------------------
 * 执行不在页面线程里：后端 spawn `rpa-core run` 子进程，这里只轮询事件流。
 * 三种「结束」必须分开说，混成一个成功/失败会把原因丢掉：
 *   result      —— 有结论（succeeded / failed / paused / indeterminate）
 *   cancelled   —— 我们点的停止，所以没有 result，这不是故障
 *   startupError—— 没有结论就非零退出（编译/加载失败），原因只在 stderr
 */

const EVENT_KIND = {
  runStarted: '运行开始',
  runFinished: '运行结束',
  runPaused: '已暂停',
  runResumed: '已继续',
  stepStarted: '节点开始',
  stepAttemptStarted: '尝试开始',
  stepCompleted: '节点完成',
  stepFailed: '节点失败',
  stepRetried: '准备重试',
}

function fmtClock(iso) {
  const at = new Date(iso)
  return Number.isNaN(at.getTime()) ? '' : at.toTimeString().slice(0, 8)
}

function eventBody(event) {
  const p = event.payload || {}
  switch (event.type) {
    case 'runStarted':
      return p.workflowId || ''
    case 'stepStarted':
      return p.command || ''
    case 'stepAttemptStarted':
      return `${p.command || ''}${p.attempt ? ` · 第 ${p.attempt} 次` : ''}`
    case 'stepRetried':
      return `第 ${p.attempt} 次失败 → 第 ${p.nextAttempt} 次 · 等 ${p.backoffSeconds}s`
    case 'stepCompleted': {
      const outputs = Object.keys(p.outputs || {}).length
      const effects = (p.effects || []).length
      return [outputs ? `${outputs} 个产出` : '', effects ? `${effects} 个副作用` : ''].join(' · ') || '（无产出）'
    }
    case 'stepFailed':
      return [p.error?.code, p.error?.message].filter(Boolean).join('：')
    case 'runPaused':
      return `${p.reason || ''}${p.nodeId ? ` @ ${p.nodeId}` : ''}`
    case 'runFinished':
      return p.status === 'succeeded' ? 'succeeded' : `${p.status} · ${p.error?.code || ''} ${p.error?.message || ''}`.trim()
    default:
      return ''
  }
}

function eventClass(event) {
  if (event.type === 'stepFailed') return ' failed'
  if (event.type === 'stepCompleted') return ' done'
  if (event.type === 'stepStarted' || event.type === 'stepAttemptStarted') return ' started'
  if (event.type === 'runFinished') {
    return ` final${event.payload?.status === 'succeeded' ? ' done' : ' failed'}`
  }
  if (event.type === 'runPaused') return ' final'
  return ''
}

function nodeLabelOf(nodeId) {
  if (!nodeId) return ''
  const card = cards.find((c) => c.id === nodeId)
  return card ? `${card.no} ${card.label}` : nodeId
}

function eventRow(event) {
  const row = mk('div', 'ev' + eventClass(event))
  row.append(mk('span', 't', fmtClock(event.timestamp)))
  row.append(mk('span', 'seq', String(event.seq ?? '')))
  row.append(mk('span', 'kind', EVENT_KIND[event.type] || event.type))
  // 节点列只在事件真的挂在节点上时出现：runStarted/runFinished 没有 node_id，
  // 留一个空列会让整串日志看起来没对齐
  if (event.node_id) row.append(mk('span', 'node', nodeLabelOf(event.node_id)))
  const body = eventBody(event)
  if (body) {
    const text = mk('span', 'body', body)
    text.title = body
    row.append(text)
  }
  row.title = `${event.type}${event.node_id ? ` · ${event.node_id}` : ''}`
  const target = event.node_id && cards.find((c) => c.id === event.node_id)
  if (target) {
    row.classList.add('clickable')
    row.addEventListener('click', () => selectCard(target))
  }
  return row
}

function appendEvents(events) {
  if (!events || !events.length) return
  // 只在用户本来就贴着底部时才自动滚：否则等于抢走他正在翻的历史
  const atBottom = el.runLog.scrollTop + el.runLog.clientHeight >= el.runLog.scrollHeight - 24
  for (const event of events) el.runLog.append(eventRow(event))
  while (el.runLog.children.length > 400) el.runLog.firstElementChild.remove()
  if (atBottom) el.runLog.scrollTop = el.runLog.scrollHeight
}

/** 把「正在执行哪个节点」算出来：stepStarted 点亮，同节点的完成/失败熄灭。 */
function syncActiveNode(events) {
  let changed = false
  for (const event of events) {
    if (event.type === 'stepStarted' && event.node_id && RUN.activeNode !== event.node_id) {
      RUN.activeNode = event.node_id
      changed = true
    } else if (
      (event.type === 'stepCompleted' || event.type === 'stepFailed') &&
      event.node_id === RUN.activeNode
    ) {
      RUN.activeNode = null
      changed = true
    }
  }
  if (changed) renderCards()
}

function setRunTitle(text, badge, badgeClass) {
  el.runTitle.replaceChildren(mk('span', null, text))
  if (badge) el.runTitle.append(mk('span', `badge ${badgeClass}`.trim(), badge))
}

function setRunRunning(on) {
  RUN.running = on
  el.run.disabled = on || !currentPath
  el.stop.disabled = !on
}

function renderRunSummary(kind, title, lines) {
  const block = mk('div', 'runSummary')
  block.append(mk('div', kind, title))
  if (lines.length) block.append(mk('pre', null, lines.join('\n')))
  el.runLog.append(block)
  el.runLog.scrollTop = el.runLog.scrollHeight
}

function finishRun(res) {
  setRunRunning(false)
  RUN.activeNode = null
  renderCards()
  const total = `${res.total} 条事件`
  if (res.result) {
    const result = res.result
    const ok = result.status === 'succeeded'
    // 不加徽章：标题里已经有 status，再加一个同字徽章等于同一句话说两遍
    setRunTitle(`${res.runId} ${result.status}`)
    const lines = [
      `status   ${result.status}`,
      `workflow ${result.workflowId}`,
      `return   ${result.returnValue ?? '（无）'}`,
    ]
    if (result.error) {
      lines.push(`error    ${result.error.code}：${result.error.message}`)
    }
    const steps = Object.entries(result.outputs || {})
    if (steps.length) {
      lines.push(
        'outputs  ' +
          steps.map(([id, s]) => `${id}(${s.keys.join(',') || '无键'}${s.effects ? ` · ${s.effects} 副作用` : ''})`).join(' '),
      )
    }
    renderRunSummary(ok ? 'ok' : 'bad', `${ok ? '运行成功' : `运行结束：${result.status}`} · ${total}`, lines)
    setStatus(`${res.runId} ${result.status} · ${total} · 返回 ${result.returnValue ?? '（无）'}`)
    return
  }
  if (res.cancelled) {
    setRunTitle(`${res.runId} 已停止`)
    renderRunSummary('bad', `已停止 · 退出码 ${res.exitCode} · ${total}`, [])
    setStatus(`${res.runId} 已停止（退出码 ${res.exitCode}）· ${total}`)
    return
  }
  if (res.startupError) {
    const error = res.startupError
    setRunTitle(`${res.runId} 启动失败`, error.code || '失败', 'dead')
    const lines = error.message ? [error.message] : []
    if (error.tail?.length) lines.push('', ...error.tail)
    renderRunSummary(
      'bad',
      `${error.code || '失败'} · 运行没起来（退出码 ${res.exitCode}）· ${total}`,
      lines,
    )
    setStatus(`${res.runId} 启动失败 ${error.code || ''}：${error.message || '原因见日志'}`)
    return
  }
  setRunTitle(`${res.runId} 结束`, `退出码 ${res.exitCode}`, 'dead')
  renderRunSummary('bad', `进程退出（退出码 ${res.exitCode}）但没有 result.json · ${total}`, res.stderrTail || [])
  setStatus(`${res.runId} 退出码 ${res.exitCode}，没有 result.json`)
}

async function pollRun() {
  if (!RUN.id) return
  let res
  try {
    res = await ppx.call('rpa.runPoll', { run_id: RUN.id, since: RUN.seen })
  } catch (error) {
    setStatus(`轮询失败 ${error.code}：${error.message}`)
    setRunRunning(false)
    return
  }
  if (!res.ok) {
    setStatus(`轮询失败 ${res.code}：${res.error}`)
    setRunRunning(false)
    return
  }
  appendEvents(res.events)
  RUN.seen = res.total
  syncActiveNode(res.events)
  if (res.running) {
    setRunTitle(
      `${res.runId} 运行中`,
      `${res.total} 条 · ${(res.elapsedMs / 1000).toFixed(1)}s`,
      'live',
    )
    RUN.timer = setTimeout(pollRun, 250)
    return
  }
  finishRun(res)
}

async function runFlow() {
  if (!currentPath || RUN.running) return
  if (isDirty() && !window.confirm('参数改动还没保存，运行的是**磁盘上**的版本。继续？')) return
  el.run.disabled = true
  RUN.id = null
  RUN.seen = 0
  RUN.activeNode = null
  el.runLog.replaceChildren()
  el.runBox.hidden = false
  el.runBox.open = true
  activateDockTab('log') // 一跑就把底栏切到日志，省得用户手动找
  const t0 = performance.now()
  try {
    const res = await ppx.call('rpa.startRun', { path: currentPath })
    if (!res.ok) {
      setRunTitle('没起来', res.code, 'dead')
      renderRunSummary('bad', `${res.code}：${res.error}`, [])
      setStatus(`运行失败 ${res.code}：${res.error}`)
      return
    }
    RUN.id = res.runId
    setRunRunning(true)
    setRunTitle(`${res.runId} 启动中`, 'live', 'live')
    // 解释器与 cwd 是「这个运行到底由谁跑、会往哪写」的证据，但塞进状态栏会把按钮挤坏；
    // 放进面板的 tooltip，想看的人鼠标一停就有。
    el.runBox.title = `解释器：${res.python}\ncwd：${res.cwd}\nartifacts：${res.artifacts}`
    setStatus(`${res.runId} 已启动 · pid ${res.pid}`)
    RUN.timer = setTimeout(pollRun, 80)
  } catch (error) {
    setRunTitle('没起来', error.code, 'dead')
    renderRunSummary('bad', `${error.code}：${error.message}`, [])
    setStatus(`${error.code}：${error.message}`)
  } finally {
    el.rpcMs.textContent = `${(performance.now() - t0).toFixed(0)} ms`
    if (!RUN.running) el.run.disabled = !currentPath
  }
}

async function stopRun() {
  if (!RUN.id || !RUN.running) return
  el.stop.disabled = true
  clearTimeout(RUN.timer)
  try {
    const res = await ppx.call('rpa.cancelRun', { run_id: RUN.id })
    if (!res.ok) {
      setStatus(`停止失败 ${res.code}：${res.error}`)
      return
    }
    setStatus(`${res.runId} 已请求停止 · 退出码 ${res.exitCode}`)
    await pollRun() // 立刻收口一次，不必等下一个 tick
  } catch (error) {
    setStatus(`${error.code}：${error.message}`)
  }
}

el.run.addEventListener('click', runFlow)
el.stop.addEventListener('click', stopRun)
// 状态栏被省略号截断时，全文得有个地方看：鼠标一停就把它放进 tooltip。
// 用事件而不是在每个写入点同步，是因为 el.status.textContent 的写入点有 20 多处。
el.status.addEventListener('mouseenter', () => {
  el.status.title = el.status.textContent
})

/* 画布是同源 iframe 里的容器：dragover 不 preventDefault 就不会有 drop。 */
el.cards.addEventListener('dragover', (event) => {
  if (!hasDragPayload(event)) return
  event.preventDefault()
  event.dataTransfer.dropEffect = event.dataTransfer.effectAllowed === 'move' ? 'move' : 'copy'
  showDropHint(dragTarget(event))
})
el.cards.addEventListener('drop', async (event) => {
  if (!hasDragPayload(event)) return
  event.preventDefault()
  const payload = readDragPayload(event)
  const target = dragTarget(event)
  clearDropHint()
  if (!payload || !currentPath) return
  if (payload.mode === 'insert') await insertCommand(payload.command, target)
  else if (payload.mode === 'move') await moveCard(payload.nodeId, target)
})
el.cards.addEventListener('dragleave', (event) => {
  if (!el.cards.contains(event.relatedTarget)) clearDropHint()
})
document.addEventListener('dragend', clearDropHint)

el.save.addEventListener('click', saveParams)
el.revert.addEventListener('click', revertParams)

el.search.addEventListener('input', () => {
  const q = el.search.value.trim().toLowerCase()
  const list = q ? commands.filter((c) => c.id.toLowerCase().includes(q) || c.label.includes(q)) : commands
  renderCommands(list)
})

/* ---- 会话通道（WorkBuddy Agent 把文件推进来）----------------------------
   可选依赖：Agent 侧服务不在就静默离线，不影响本地任何功能。
   协议：workbuddy-suite/skills/workflow-studio/references/protocol.md
   - 轮询 /inbox（经 Python 侧 studio.pollSession），kind=open 就 openFlow
   - 回 opened（带 sha）+ 心跳，让 Agent 知道窗口在线、在编辑哪个文件 */

const SESSION = { since: 0, online: false, handled: new Set() }

async function pollSession() {
  try {
    const res = await ppx.call('studio.pollSession', { since: SESSION.since })
    if (!res?.ok) return
    SESSION.online = Boolean(res.online)
    SESSION.since = res.cursor ?? SESSION.since
    for (const request of res.requests || []) {
      if (request.kind !== 'open' || !request.path || SESSION.handled.has(request.id)) continue
      // 先占位再打开：openFlow 失败时返回 {ok:false, error:{code,message}}。
      // 必须把这份结构化原因回进 open-failed，否则 Agent 侧只能等到超时，原因全丢在窗口里。
      SESSION.handled.add(request.id)
      try {
        const flow = await openFlow(request.path)
        if (!flow || flow.ok === false) {
          const failure = flow?.error
          const reason = new Error(failure?.message || '窗口内打开失败')
          reason.code = failure?.code || 'INTERNAL_ERROR'
          throw reason
        }
        await ppx.call('studio.notify', {
          kind: 'opened',
          payload: { path: request.path, sha: flow.sha },
        })
      } catch (error) {
        SESSION.handled.delete(request.id)
        await ppx.call('studio.notify', {
          kind: 'open-failed',
          payload: {
            path: request.path,
            code: error?.code || null,
            message: String(error?.message || error),
          },
        })
      }
    }
  } catch {
    SESSION.online = false
  }
}

async function heartbeat() {
  if (!SESSION.online || !currentPath) return
  try {
    await ppx.call('studio.notify', { kind: 'heartbeat', payload: { path: currentPath, dirty: isDirty() } })
  } catch {
    /* 心跳失败不影响任何本地功能 */
  }
}

async function boot() {
  const t0 = performance.now()
  try {
    const info = await ppx.call('rpa.bootstrap')
    commands = info.commands
    flows = info.flows
    // 顶栏只放「看一眼就够」的短句：整条诊断串（digest/首帧/耗时）太长，
    // 在 1280 宽下会把流程下拉和按钮往右挤，全文进 tooltip。
    const brief = `catalog ${info.commands.length} · Python ${info.python}`
    setStatus(
      brief,
      `catalog ${info.commands.length} 条 · digest ${info.catalogDigest} · Python ${info.python}` +
        ` · 首帧→数据 ${(performance.now() - t0).toFixed(0)} ms（catalog ${info.catalogMs} ms）`,
    )
    renderCommands(commands)
    el.picker.replaceChildren(
      ...flows.map((f) => {
        const option = document.createElement('option')
        option.value = f.path
        option.textContent = f.label
        return option
      }),
    )
    el.picker.addEventListener('change', () => openFlow(el.picker.value))
    const demo = flows.find((f) => f.path === DEMO_FLOW)
    if (demo) {
      el.picker.value = demo.path
      await openFlow(demo.path)
    } else if (flows.length) {
      await openFlow(flows[0].path)
    }
  } catch (error) {
    setStatus(`${error.code}: ${error.message}`)
  }
}

boot()
// 会话轮询先于首帧数据也无妨：open 请求会等 boot 完成后由 handled/异常路径兜住，
// 更重要的是 Agent 排队的请求最迟 1.4s 后一定会被看到。
setInterval(pollSession, 1400)
setInterval(heartbeat, 5000)
