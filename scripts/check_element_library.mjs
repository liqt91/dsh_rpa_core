// 元素资产清单的 UI 契约判据（进默认门禁）。
//
// 与 check_element_pick_ui.mjs 分工：那边管「捕获 → 确认入库」这条链；这边管
// 「清单本身」——结构（工具区在 elemBody 之外、只有一个按钮）、版式（行高/字号/
// 危险动作色）、以及「详情 / 删除」两个动作真接线、删除失败不把行抹掉。
//
// 只读源码证明不了「有按钮但点不着」「删失败却从界面消失」，所以行为层用最小
// DOM shim 真跑 main.js 的绑定与渲染。
import fs from 'node:fs'
import vm from 'node:vm'
import path from 'node:path'

const ROOT = path.resolve(import.meta.dirname, '..')
const GUI = path.join(ROOT, '.harness/demo/ppx_probe/app/gui')
const MAIN = path.join(GUI, 'src/main.js')
const HTML = path.join(GUI, 'index.html')
const CSS = path.join(GUI, 'src/style.css')

const results = []
function check(name, fn) {
  try {
    fn()
    results.push({ name, ok: true })
  } catch (error) {
    results.push({ name, ok: false, detail: String(error.message || error) })
  }
}
async function checkAsync(name, fn) {
  try {
    await fn()
    results.push({ name, ok: true })
  } catch (error) {
    results.push({ name, ok: false, detail: String(error.message || error) })
  }
}
function assert(cond, msg) {
  if (!cond) throw new Error(msg)
}

const mainSrc = fs.readFileSync(MAIN, 'utf8')
const html = fs.readFileSync(HTML, 'utf8')
const css = fs.readFileSync(CSS, 'utf8')

class El {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase()
    this.children = []
    this.id = ''
    this.className = ''
    this.attributes = {}
    this.handlers = {}
    this.dataset = {}
    this.textContent = ''
    this.value = ''
    this.placeholder = ''
    this.title = ''
    this.hidden = false
    this.open = false
    this.disabled = false
    this.readOnly = false
    this.style = { setProperty() {} }
    const self = this
    this.classList = {
      add(c) {
        const set = new Set(self.className.split(/\s+/).filter(Boolean))
        set.add(c)
        self.className = [...set].join(' ')
      },
      remove(c) {
        self.className = self.className.split(/\s+/).filter((x) => x && x !== c).join(' ')
      },
      contains(c) { return self.className.split(/\s+/).includes(c) },
    }
  }
  append(...nodes) { for (const n of nodes) this.children.push(n) }
  replaceChildren(...nodes) { this.children = [...nodes] }
  setAttribute(k, v) { this.attributes[k] = v }
  addEventListener(type, fn) { (this.handlers[type] ||= []).push(fn) }
  click() {
    return Promise.all((this.handlers.click || []).map((fn) => fn({ type: 'click' })))
  }
  querySelector() { return null }
  querySelectorAll() { return [] }
  focus() {}
  select() {}
  scrollIntoView() {}
}
const walk = (node, out = []) => {
  out.push(node)
  for (const child of node.children || []) walk(child, out)
  return out
}
const hasClass = (node, name) => String(node.className).split(/\s+/).includes(name)

const registry = new Map()
function nodeById(id) {
  if (!registry.has(id)) {
    const node = new El('div')
    node.id = id
    registry.set(id, node)
  }
  return registry.get(id)
}

// ---- 按 index.html 的形状预置 dock；key 从 HTML 解析，手写一份会恒绿 ----
const TAB_KEYS = [...html.matchAll(/data-tab="([a-z]+)"/g)].map((m) => m[1])
const PANEL_KEYS = [...html.matchAll(/data-panel="([a-z]+)"/g)].map((m) => m[1])

const state = {
  missing: [],
  elements: [{ name: 'searchBox', kind: 'browser', selector: '#q' }],
  detailResponse: {
    ok: true, name: 'searchBox', selectorText: '#q', referencedBy: ['openHome'],
    document: { kind: 'browser', selector: { css: '#q' }, verifyCount: 2, capturedAt: '2026-11-12' },
  },
  deleteResponse: { ok: true, deleted: true, remaining: [] },
}

function makeContext() {
  const calls = []
  const docHandlers = {}
  const dockTabs = TAB_KEYS.map((key) => {
    const node = new El('button')
    node.dataset.tab = key
    return node
  })
  const dockPanels = PANEL_KEYS.map((key) => {
    const node = new El('div')
    node.dataset.panel = key
    return node
  })
  const doc = {
    head: new El('head'),
    createElement: (tag) => new El(tag),
    querySelector: (sel) => (sel.startsWith('#') ? nodeById(sel.slice(1)) : null),
    querySelectorAll: (sel) => {
      if (sel === '.dockTab') return dockTabs
      if (sel === '.dockPanel') return dockPanels
      return []
    },
    addEventListener(type, fn) { (docHandlers[type] ||= []).push(fn) },
    activeElement: null,
  }
  const defaults = {
    'rpa.bootstrap': { commands: [], flows: [], catalogDigest: '', python: '', catalogMs: 0 },
    'rpa.missingElements': () => ({ ok: true, missing: state.missing, resolved: [], capabilities: {} }),
    'rpa.listElements': () => ({ ok: true, elements: state.elements }),
    'rpa.elementDetail': () => state.detailResponse,
    'rpa.deleteElement': () => state.deleteResponse,
    'rpa.startCapture': { ok: true, state: 'waiting', captureId: 'c1', name: null, suggestedName: '元素1' },
    'rpa.capturePoll': { ok: true, state: 'pending' },
    'rpa.commitElement': { ok: true, name: 'searchBox' },
    'rpa.discardCapture': { ok: true, discarded: true },
    'rpa.saveElement': { ok: true, name: 'searchBox' },
    // 默认按「桌面编辑框不可用」处理：详情走页面兜底（非 Windows 路径）
    'rpa.editElement': { ok: false, code: 'EDIT_DIALOG_UNAVAILABLE' },
  }
  const sandbox = {
    document: doc,
    ppx: {
      call: async (name, args) => {
        calls.push({ name, args })
        const value = defaults[name]
        return typeof value === 'function' ? value() : { ...(value || { ok: true }) }
      },
    },
    console,
    performance: { now: () => 0 },
    setInterval: () => 0,
    clearInterval: () => {},
    setTimeout: () => 0,
    clearTimeout: () => {},
    Date, Math, JSON, String, Number, Array, Object, Set, Map, Promise, RegExp, Error,
  }
  const ctx = vm.createContext(sandbox)
  vm.runInContext(mainSrc.replace(/^import .*$/gm, ''), ctx, { filename: 'main.js' })
  vm.runInContext(
    `globalThis.__api = { refreshElements, renderElementsPanel, openAssetDetail, deleteAsset,
       setState: (missing, elements) => { pickMissing = missing; pickElements = elements; renderElementsPanel() },
       setCapture: (id, phase) => { captureSession = id ? { id } : null; capturePhase = phase },
       getCapture: () => ({ session: captureSession, phase: capturePhase }) }
     currentPath = 'flows/probe.json'`,
    ctx,
  )
  return {
    ctx, calls, api: vm.runInContext('globalThis.__api', ctx),
    keydown: docHandlers.keydown || [],
  }
}

const flush = () => new Promise((resolve) => setImmediate(resolve))

// ---------- 1. 结构层 ----------
check('工具区在 elemBody 之外、只有一个按钮', () => {
  const panelStart = html.indexOf('data-panel="elements"')
  const toolsAt = html.indexOf('class="elemTools"', panelStart)
  const bodyAt = html.indexOf('id="elemBody"', panelStart)
  assert(toolsAt > panelStart && toolsAt < bodyAt, '工具区不在元素面板里 / 落在 elemBody 之后')
  const tools = html.slice(toolsAt, bodyAt)
  const buttons = [...tools.matchAll(/<button[^>]*id="([^"]+)"/g)].map((m) => m[1])
  assert(buttons.length === 1 && buttons[0] === 'elemCapture', `工具区按钮：${buttons}`)
})

check('版式在 style.css，main.js 没有运行时注入样式', () => {
  // 带花括号的等值形状：只写 '.elemTools' 会被 .elemToolsRow 之类子串糊弄成恒绿。
  for (const needle of ['.elemTools {', '.elemRow {', '.elemMeta {', '.elemAct.danger {']) {
    assert(css.includes(needle), `style.css 缺 ${needle}`)
  }
  for (const banned of ['DND_CSS', 'DOCK_CSS', 'injectDndStyles', 'injectDockStyles', 'mountBottomDock']) {
    assert(!mainSrc.includes(banned), `main.js 里还有运行时注入残留：${banned}`)
  }
})

check('main.js 真接了清单与四个资产 RPC', () => {
  for (const needle of [
    "ppx.call('rpa.missingElements'",
    "ppx.call('rpa.listElements'",
    "ppx.call('rpa.elementDetail'",
    "ppx.call('rpa.deleteElement'",
    "ppx.call('rpa.commitElement'",
  ]) {
    assert(mainSrc.includes(needle), `main.js 缺 ${needle}`)
  }
})

// ---------- 2. 行为层 ----------
await checkAsync('刷新真跑：待补段与资产段同时渲染', async () => {
  state.missing = [{ name: 'userField', nodeId: 'n1', command: 'browser.input' }]
  state.elements = [{ name: 'searchBox', kind: 'browser', selector: '#q' }]
  const { api, calls } = makeContext()
  await api.refreshElements()
  const body = nodeById('elemBody')
  const rows = walk(body).filter((n) => hasClass(n, 'elemRow'))
  assert(rows.length === 2, `行数=${rows.length}`)
  assert(rows.some((n) => hasClass(n, 'missing')), '没有待补行')
  assert(rows.some((n) => hasClass(n, 'asset')), '没有资产行')
  assert(calls.filter((c) => c.name === 'rpa.listElements').length === 1, '资产没刷新')
})

check('资产行有 详情 / 删除 两个动作', () => {
  const { api } = makeContext()
  api.setState([], [{ name: 'searchBox', kind: 'browser', selector: '#q' }])
  const row = walk(nodeById('elemBody')).find((n) => hasClass(n, 'asset'))
  assert(row, '没有资产行')
  const labels = walk(row).filter((n) => hasClass(n, 'elemAct')).map((n) => n.textContent)
  assert(labels.length === 2, `动作数 ${labels.length}：${labels}`)
  assert(labels.includes('详情') && labels.includes('删除'), labels.join('/'))
})

await checkAsync('点详情取完整描述符并弹编辑框（不是行内展开）', async () => {
  const { api, calls } = makeContext()
  api.setState([], [{ name: 'searchBox', kind: 'browser', selector: '#q' }])
  const row = walk(nodeById('elemBody')).find((n) => hasClass(n, 'asset'))
  const detail = walk(row).find((n) => hasClass(n, 'elemAct') && n.textContent === '详情')
  await detail.click()
  await flush()
  assert(calls.some((c) => c.name === 'rpa.elementDetail'), '没取完整描述符')
  assert(nodeById('elemDialog').hidden === false, '详情没弹编辑框')
  assert(!walk(nodeById('elemBody')).some((n) => hasClass(n, 'elemDetail')), '还在行内展开详情')
})

await checkAsync('删除成功才刷新，失败保留行', async () => {
  state.deleteResponse = { ok: false, code: 'ELEMENT_IN_USE', error: '还被引用着', referencedBy: ['openHome'] }
  // 删前检查放行（无引用），这段测的是「RPC 也挡下时不能把行抹掉」的兜底。
  state.detailResponse = { ok: true, referencedBy: [] }
  const run = makeContext()
  run.api.setState([], [{ name: 'searchBox', kind: 'browser', selector: '#q' }])
  const row = walk(nodeById('elemBody')).find((n) => hasClass(n, 'asset'))
  const del = walk(row).find((n) => hasClass(n, 'elemAct') && n.textContent === '删除')
  const before = run.calls.length
  await del.click()
  await flush()
  const after = run.calls.slice(before).map((c) => c.name)
  assert(after.includes('rpa.deleteElement'), '没发删除')
  assert(!after.includes('rpa.listElements'), '删除失败还刷新了列表')
  assert(walk(nodeById('elemBody')).some((n) => hasClass(n, 'asset')), '删除失败却把行抹了')

  state.deleteResponse = { ok: true, deleted: true, remaining: [] }
  state.elements = []
  await del.click()
  await flush()
  assert(run.calls.filter((c) => c.name === 'rpa.listElements').length === 1, '删除成功没刷新列表')
})

await checkAsync('删前检查引用：查出引用就不发删除，说明框能退出', async () => {
  state.detailResponse = { ok: true, referencedBy: ['openHome', 'clickFilter'] }
  const run = makeContext()
  const dialog = nodeById('elemDialog')
  dialog.hidden = true // registry 跨用例共享，先对齐真实 DOM 的初始态
  run.api.setState([], [{ name: 'searchBox', kind: 'browser', selector: '#q' }])
  const row = walk(nodeById('elemBody')).find((n) => hasClass(n, 'asset'))
  const del = walk(row).find((n) => hasClass(n, 'elemAct') && n.textContent === '删除')
  const before = run.calls.length
  await del.click()
  await flush()
  const after = run.calls.slice(before).map((c) => c.name)
  assert(after.includes('rpa.elementDetail'), '删前没查引用')
  assert(!after.includes('rpa.deleteElement'), '查到引用还发了删除 RPC')
  assert(dialog.hidden === false, '网页端没给说明')
  const lines = walk(nodeById('elemDialogMeta')).map((n) => n.textContent).join('|')
  assert(lines.includes('2 个节点'), `没写清几处引用：${lines}`)
  // 用户抱怨的「提示框无法退出」：Esc 必须能收掉，且不误伤清单
  for (const fn of run.keydown) fn({ key: 'Escape', preventDefault() {} })
  await flush()
  assert(dialog.hidden === true, '说明框按 Esc 关不掉')
  assert(walk(nodeById('elemBody')).some((n) => hasClass(n, 'asset')), '失败却把行抹了')
  state.detailResponse = { ok: true, referencedBy: [] }
})

await checkAsync('捕获中按 Esc 取消会话，桌面编辑态不抢', async () => {
  const run = makeContext()
  const before = run.calls.length
  run.api.setCapture('c9', 'pending')
  for (const fn of run.keydown) fn({ key: 'Escape', preventDefault() {} })
  await flush()
  assert(
    run.calls.slice(before).some((c) => c.name === 'rpa.cancelCapture'),
    '捕获等待期按 Esc 没取消',
  )
  assert(run.api.getCapture().session === null, 'Esc 之后会话还在')

  const before2 = run.calls.length
  run.api.setCapture('c9', 'editing')
  for (const fn of run.keydown) fn({ key: 'Escape', preventDefault() {} })
  await flush()
  assert(
    !run.calls.slice(before2).some((c) => c.name === 'rpa.cancelCapture'),
    '桌面编辑框正在用时 Esc 抢了整次会话',
  )
})

check('清单里不出现提示节点（说明走可关闭的网页弹框）', () => {
  const { api } = makeContext()
  api.setState([{ name: 'userField', nodeId: 'n1', command: 'browser.input' }], [])
  const nodes = walk(nodeById('elemBody'))
  for (const banned of ['pickBar', 'elemNewNote', 'hint']) {
    assert(!nodes.some((n) => hasClass(n, banned)), `清单里还有提示节点：${banned}`)
  }
})

for (const result of results) {
  console.log(`${result.ok ? 'PASS' : 'FAIL'} ${result.name}${result.ok ? '' : ` — ${result.detail}`}`)
}
const failed = results.filter((r) => !r.ok).length
console.log(`ELEMENT LIBRARY CHECK ${failed ? 'FAILED' : 'PASSED'} (${results.length - failed}/${results.length})`)
if (failed) process.exitCode = 1
