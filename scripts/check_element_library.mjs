// 元素资产清单的 UI 契约判据（进默认门禁）。
//
// 与 check_element_pick_ui.mjs 分工：那边管「捕获 → 确认入库」这条链；这边管
// 「清单本身」——结构（工具区在 elemBody 之外、只有两个按钮）、版式（行高/字号/
// 危险动作色）、以及「详情 / 校验 / 删除」三个动作真接线、删除失败不把行抹掉。
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
  contains() { return false }
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

// ★ 与服务端 _all_element_refs 同形状：nodeId 不够，param/name 才能让用户找到引用。
const ref = (nodeId, param = 'selector', name = 'searchBox') => ({
  nodeId, param, name, command: 'browser.click',
})

const state = {
  missing: [],
  elements: [{ name: 'searchBox', kind: 'browser', selector: '#q' }],
  // M93：分组树数据源（右键/折叠/批量操作的判据都从这里喂）
  groups: [],
  unused: [],
  detailResponse: {
    ok: true, name: 'searchBox', selectorText: '#q', referencedBy: [ref('openHome')],
    document: { kind: 'browser', selector: { css: '#q' }, verifyCount: 2, capturedAt: '2026-11-12' },
  },
  deleteResponse: { ok: true, deleted: true, remaining: [] },
  // 行内校验的 RPC 回传：默认命中 2 个；测「命中 0 的两种含义」时改它
  verifyResponse: { ok: true, count: 2 },
  // M102 S2：缩略图按需拉的 RPC 回传（假 data URL，只为断言「真塞进 <img>」）
  shotResponse: { ok: true, shot: 'data:image/png;base64,TUlOMTAy' },
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
    'rpa.listElementGroups': () => ({ ok: true, groups: state.groups, ungrouped: 0 }),
    'rpa.findUnusedElements': () => ({ ok: true, elements: state.unused }),
    'rpa.createElementGroup': { ok: true, group: { id: 'gNew', name: '新分组' } },
    'rpa.renameElementGroup': { ok: true },
    'rpa.deleteElementGroup': { ok: true, moved: [] },
    'rpa.setElementGroupCollapsed': { ok: true },
    'rpa.copyElement': { ok: true, name: '副本' },
    'rpa.moveElementsToGroup': { ok: true, moved: [] },
    'rpa.elementReferences': () => ({ ok: true, references: [ref('openHome')] }),
    'rpa.elementDetail': () => state.detailResponse,
    'rpa.elementShot': () => state.shotResponse,
    'rpa.verifyElement': () => state.verifyResponse,
    'rpa.deleteElement': () => state.deleteResponse,
    'rpa.startCapture': { ok: true, state: 'waiting', captureId: 'c1', name: null, suggestedName: '元素1' },
    'rpa.capturePoll': { ok: true, state: 'pending' },
    'rpa.commitElement': { ok: true, name: 'searchBox' },
    'rpa.discardCapture': { ok: true, discarded: true },
    'rpa.elementEditModel': () => ({
      ok: true, kind: 'browser', selector: 'div.a2 > span.b',
      levels: [
        { tag: 'div', included: true, id: { on: false, value: '' },
          classes: [{ on: false, value: 'a', mode: '精确' },
                    { on: true, value: 'a2', mode: '精确' }],
          nth: { on: false, value: '' } },
        { tag: 'span', included: true, id: { on: false, value: '' },
          classes: [{ on: true, value: 'b', mode: '精确' }],
          nth: { on: false, value: '' } },
      ],
    }),
    'rpa.saveElement': { ok: true, name: 'searchBox' },
    // 默认按「桌面编辑框不可用」处理：详情走页面兜底（非 Windows 路径）
    'rpa.editElement': { ok: false, code: 'EDIT_DIALOG_UNAVAILABLE' },
    // 第三步节点：selector 只在 elementRefs 里，with 里没有 —— 用户报的正是这条形态
    'rpa.nodeDetail': () => ({
      ok: true,
      node: {
        id: 'openHome', type: 'action', command: 'browser.click', with: {},
        elementRefs: { selector: 'searchBox' },
      },
      fields: [
        { key: 'selector', label: 'selector', type: 'string', required: true,
          set: false, valueText: '', enum: [], declared: true },
        { key: 'text', label: 'text', type: 'string', required: false,
          set: true, valueText: 'hi', enum: [], declared: true },
      ],
      groups: [],
    }),
    'rpa.saveNodeParams': () => ({
      ok: true, saved: true, changed: true, bytes: [10, 8], sha: 'abc123',
      added: [], removed: [], warnings: [],
      compile: { ok: true, digest: 'abc', capabilities: [], workflowId: 'probe' },
      flow: { cards: [] },
    }),
  }
  const sandbox = {
    document: doc,
    CSS: { escape: (s) => String(s) },
    window: { confirm: () => true },
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
       openCaptureDialog, confirmElemDialog,
       selectCard, saveParams, renderForm,
       elementTreeBuckets, openLibMenu, closeLibMenu, toggleGroup, renderBatchBar,
       getSelected: () => Array.from(elemSelected),
       libShim: (groups, unused, onlyUnused) => {
         pickGroups = groups; pickUnused = unused || []; elemOnlyUnused = !!onlyUnused
         renderElementsPanel()
       },
       setState: (missing, elements) => { pickMissing = missing; pickElements = elements; renderElementsPanel() },
       setCards: (list) => { cards = list },
       setCapture: (id, phase) => { captureSession = id ? { id } : null; capturePhase = phase },
       getCapture: () => ({ session: captureSession, phase: capturePhase }),
       getRefState: () => ({ refs: { ...nodeRefs }, pending: [...pendingUnbinds] }) }
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
check('工具区在 elemBody 之外、只有两个捕获按钮', () => {
  const panelStart = html.indexOf('data-panel="elements"')
  const toolsAt = html.indexOf('class="elemTools"', panelStart)
  const bodyAt = html.indexOf('id="elemBody"', panelStart)
  assert(toolsAt > panelStart && toolsAt < bodyAt, '工具区不在元素面板里 / 落在 elemBody 之后')
  const tools = html.slice(toolsAt, bodyAt)
  const buttons = [...tools.matchAll(/<button[^>]*id="([^"]+)"/g)].map((m) => m[1])
  assert(buttons.length === 2 && buttons[0] === 'elemCapture'
    && buttons[1] === 'elemCaptureImage', `工具区按钮：${buttons}`)
})

await checkAsync('按图捕获显式透传 image=true', async () => {
  const { calls } = makeContext()
  const button = nodeById('elemCaptureImage')
  await button.click()
  await flush()
  const hit = calls.find((c) => c.name === 'rpa.startCapture')
  assert(hit, '没调捕获入口')
  assert(hit.args.image === true, `image 没透传：${JSON.stringify(hit.args)}`)
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
    "ppx.call('rpa.verifyElement'",
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

check('资产行有 详情 / 校验 / 删除 三个动作', () => {
  const { api } = makeContext()
  api.setState([], [{ name: 'searchBox', kind: 'browser', selector: '#q' }])
  const row = walk(nodeById('elemBody')).find((n) => hasClass(n, 'asset'))
  assert(row, '没有资产行')
  const labels = walk(row).filter((n) => hasClass(n, 'elemAct')).map((n) => n.textContent)
  assert(labels.length === 3, `动作数 ${labels.length}：${labels}`)
  assert(labels.includes('详情') && labels.includes('校验') && labels.includes('删除'), labels.join('/'))
})

await checkAsync('点校验走统一入口并把命中数写回资产行', async () => {
  const { api, calls } = makeContext()
  api.setState([], [{ name: 'searchBox', kind: 'browser', selector: '#q' }])
  const row = walk(nodeById('elemBody')).find((n) => hasClass(n, 'asset'))
  const verify = walk(row).find((n) => hasClass(n, 'elemAct') && n.textContent === '校验')
  await verify.click()
  await flush()
  const hit = calls.find((c) => c.name === 'rpa.verifyElement')
  assert(hit, '没调活体校验入口')
  assert(hit.args.name === 'searchBox', `校验了别的元素：${hit.args.name}`)
  const meta = walk(row).find((n) => hasClass(n, 'elemMeta'))
  assert(meta.textContent.includes('校验命中 2 个'), `行内没显示结果：${meta.textContent}`)
})

await checkAsync('命中 0 且带 tree-missing：行内说清是对方没有无障碍树', async () => {
  // M100：count=0 有两种含义，糊成一句用户只会反复改选择器。
  state.verifyResponse = { ok: true, count: 0, hint: 'tree-missing' }
  try {
    const { api } = makeContext()
    api.setState([], [{ name: 'searchBox', kind: 'browser', selector: '#q' }])
    const row = walk(nodeById('elemBody')).find((n) => hasClass(n, 'asset'))
    const verify = walk(row).find((n) => hasClass(n, 'elemAct') && n.textContent === '校验')
    await verify.click()
    await flush()
    const meta = walk(row).find((n) => hasClass(n, 'elemMeta'))
    assert(meta.textContent.includes('无障碍树'), `没说明原因：${meta.textContent}`)
    assert(!meta.textContent.includes('这次没命中'), `还是笼统文案：${meta.textContent}`)
  } finally {
    state.verifyResponse = { ok: true, count: 2 }
  }
})

await checkAsync('命中 0 但树是活的：仍是「这次没命中」，不误导用户重开应用', async () => {
  state.verifyResponse = { ok: true, count: 0 }
  try {
    const { api } = makeContext()
    api.setState([], [{ name: 'searchBox', kind: 'browser', selector: '#q' }])
    const row = walk(nodeById('elemBody')).find((n) => hasClass(n, 'asset'))
    const verify = walk(row).find((n) => hasClass(n, 'elemAct') && n.textContent === '校验')
    await verify.click()
    await flush()
    const meta = walk(row).find((n) => hasClass(n, 'elemMeta'))
    assert(meta.textContent.includes('这次没命中'), `文案不对：${meta.textContent}`)
    assert(!meta.textContent.includes('无障碍树'), `把选择器问题说成环境问题：${meta.textContent}`)
  } finally {
    state.verifyResponse = { ok: true, count: 2 }
  }
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
  state.deleteResponse = { ok: false, code: 'ELEMENT_IN_USE', error: '还被引用着', referencedBy: [ref('openHome')] }
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
  state.detailResponse = {
    ok: true,
    referencedBy: [ref('openHome', 'selector'), ref('clickFilter', 'target')],
  }
  const run = makeContext()
  const dialog = nodeById('elemDialog')
  dialog.hidden = true // registry 跨用例共享，先对齐真实 DOM 的初始态
  run.api.setState([], [{ name: 'searchBox', kind: 'browser', selector: '#q' }])
  run.api.setCards([{ id: 'openHome', kind: 'action', label: 'browser.click', no: 3 }])
  const row = walk(nodeById('elemBody')).find((n) => hasClass(n, 'asset'))
  const del = walk(row).find((n) => hasClass(n, 'elemAct') && n.textContent === '删除')
  const before = run.calls.length
  await del.click()
  await flush()
  const after = run.calls.slice(before).map((c) => c.name)
  assert(after.includes('rpa.elementDetail'), '删前没查引用')
  assert(!after.includes('rpa.deleteElement'), '查到引用还发了删除 RPC')
  assert(dialog.hidden === false, '网页端没给说明')
  const meta = nodeById('elemDialogMeta')
  const lines = walk(meta).map((n) => n.textContent).join('|')
  assert(lines.includes('2 处引用'), `没写清几处引用：${lines}`)
  // ★ 用户这次卡住的根因：只说「第 N 步」不说是哪个参数，参数表里 selector
  // 又显示「未设置」，等于指向一个看不见的引用。字段名/资产名必须摊开。
  assert(lines.includes('selector') && lines.includes('target'), `没写清引用字段：${lines}`)
  assert(lines.includes('searchBox'), `没写清引用的资产：${lines}`)
  const links = walk(meta).filter((n) => hasClass(n, 'refLink'))
  assert(links.length === 2, `引用行不能点去处理：${links.length}`)
  // 用户抱怨的「提示框无法退出」：Esc 必须能收掉，且不误伤清单
  for (const fn of run.keydown) fn({ key: 'Escape', preventDefault() {} })
  await flush()
  assert(dialog.hidden === true, '说明框按 Esc 关不掉')
  assert(walk(nodeById('elemBody')).some((n) => hasClass(n, 'asset')), '失败却把行抹了')
  state.detailResponse = { ok: true, referencedBy: [] }
})

await checkAsync('说明框里的引用能点：关框并跳到被引用节点', async () => {
  state.detailResponse = { ok: true, referencedBy: [ref('openHome', 'selector')] }
  const run = makeContext()
  const dialog = nodeById('elemDialog')
  dialog.hidden = true
  run.api.setState([], [{ name: 'searchBox', kind: 'browser', selector: '#q' }])
  run.api.setCards([{ id: 'openHome', kind: 'action', label: 'browser.click', no: 3 }])
  const row = walk(nodeById('elemBody')).find((n) => hasClass(n, 'asset'))
  await walk(row).find((n) => hasClass(n, 'elemAct') && n.textContent === '删除').click()
  await flush()
  const link = walk(nodeById('elemDialogMeta')).find((n) => hasClass(n, 'refLink'))
  assert(link, '没有可点的引用行')
  await link.click()
  await flush()
  assert(dialog.hidden === true, '点引用没关掉说明框')
  assert(
    run.calls.some((c) => c.name === 'rpa.nodeDetail' && c.args.node_id === 'openHome'),
    '点引用没跳到对应节点',
  )
  state.detailResponse = { ok: true, referencedBy: [] }
})

await checkAsync('参数表摊开 elementRefs：不假报未设置，解除随保存全量提交', async () => {
  const run = makeContext()
  const card = { id: 'openHome', kind: 'action', label: 'browser.click', no: 3 }
  await run.api.selectCard(card)
  const form = nodeById('paramForm')
  const panel = walk(form).find((n) => hasClass(n, 'refPanel'))
  assert(panel, '参数表没有元素引用面板')
  const panelText = walk(panel).map((n) => n.textContent).join('|')
  assert(panelText.includes('selector') && panelText.includes('searchBox'), `引用没摊开：${panelText}`)
  const formText = walk(form).map((n) => n.textContent).join('|')
  assert(formText.includes('已引用元素'), '被引用字段没显示引用值')
  assert(!formText.includes('必填但当前未设置'), '被引用字段还在假报「必填但当前未设置」')
  assert(walk(form).some((n) => hasClass(n, 'refBound')), '引用字段没换成只读形态')
  const unbind = walk(panel).find((n) => hasClass(n, 'refUnbind'))
  await unbind.click()
  assert(run.api.getRefState().pending.includes('selector'), '点解除没进待提交集合')
  assert(!run.calls.some((c) => c.name === 'rpa.saveNodeParams'), '点解除就直接写盘了')
  await run.api.saveParams()
  const saved = run.calls.filter((c) => c.name === 'rpa.saveNodeParams').pop()
  assert(saved, '保存没发参数 RPC')
  assert(
    JSON.stringify(saved.args.element_refs) === '{}',
    `解除没进全量提交：${JSON.stringify(saved.args)}`,
  )
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

check('结构层：元素面板有搜索与全部校验，且不占捕获按钮名额', () => {
  assert(/<input[^>]*id="elemSearch"[^>]*type="search"/.test(html), '搜索框不是 search 输入')
  assert(html.includes('id="elemVerifyAll"'), '缺全部校验按钮')
  const panelAt = html.indexOf('data-panel="elements"')
  const searchAt = html.indexOf('id="elemSearch"')
  const toolsAt = html.indexOf('class="elemTools"')
  assert(panelAt < searchAt && searchAt < toolsAt, '搜索框不在捕获工具区之前')
})

check('契约层：搜索/分组/引用数/全部校验都真接线', () => {
  for (const needle of [
    "el.elemSearch?.addEventListener('input'",
    "el.elemVerifyAll?.addEventListener('click'",
    "ppx.call('rpa.verifyAllElements'",
    "elemFilterText = String(el.elemSearch.value || '')",
    'e.refCount',
    'elemGroup',
  ]) {
    assert(mainSrc.includes(needle), `main.js 缺 ${needle}`)
  }
  for (const needle of ['.elemFilter {', '.elemGroup {', '.elemRefs {']) {
    assert(css.includes(needle), `style.css 缺 ${needle}`)
  }
})

// ---------- M93：影刀式分组树 / 右键菜单 / 多选批量 ----------
check('契约层：P2 管理 RPC 与树渲染函数都真接线', () => {
  for (const needle of [
    'elementTreeBuckets', 'groupHeaderRow', 'openLibMenu', 'renderBatchBar',
    "ppx.call('rpa.listElementGroups'", "ppx.call('rpa.findUnusedElements'",
    "ppx.call('rpa.createElementGroup'", "ppx.call('rpa.renameElementGroup'",
    "ppx.call('rpa.deleteElementGroup'", "ppx.call('rpa.setElementGroupCollapsed'",
    "ppx.call('rpa.copyElement'", "ppx.call('rpa.moveElementsToGroup'",
    "ppx.call('rpa.elementReferences'", 'elemLibLayer',
  ]) {
    assert(mainSrc.includes(needle), `main.js 缺 ${needle}`)
  }
  assert(html.includes('id="elemLibLayer"'), 'index.html 缺动态菜单宿主')
  for (const needle of ['.elemGroupRow {', '.libMenu, .libPrompt {', '.libBatchBar {', '.elemPick {']) {
    assert(css.includes(needle), `style.css 缺 ${needle}`)
  }
})

check('分组桶：按服务端顺序 + 未分组兜底，空分组保留、折叠态带出', () => {
  const { api } = makeContext()
  const groups = [
    { id: 'g1', name: '登录相关', order: 0, collapsed: false },
    { id: 'g2', name: '空组', order: 1, collapsed: true },
  ]
  const elements = [
    { name: 'a', group: 'g1' }, { name: 'b', group: 'g2' },
    { name: 'c', group: null }, { name: 'd', group: '未知组' },
  ]
  const buckets = api.elementTreeBuckets(groups, elements)
  assert(buckets.length === 3 && buckets[0].id === 'g1' && buckets[1].id === 'g2'
    && buckets[2].id === null, `桶顺序 ${JSON.stringify(buckets.map((b) => b.id))}`)
  assert(buckets[0].elements.map((e) => e.name).join(',') === 'a', 'g1 分桶错')
  assert(buckets[2].elements.map((e) => e.name).join(',') === 'c,d', '未分组兜底错')
  assert(buckets[1].collapsed === true && buckets[1].elements.length === 1, '空组/折叠态丢了')
})

check('树渲染：分组头与资产行同树，折叠组不渲染其子行', () => {
  const { api } = makeContext()
  api.setState([], [
    { name: 'a', kind: 'browser', selector: '#a', group: 'g1' },
    { name: 'b', kind: 'desktop', selector: 'x', group: 'g2' },
    { name: 'c', kind: 'browser', selector: '#c', group: null },
  ])
  api.libShim([
    { id: 'g1', name: '登录相关', order: 0, collapsed: false },
    { id: 'g2', name: '空组', order: 1, collapsed: true },
  ], [])
  const body = nodeById('elemBody')
  const heads = walk(body).filter((n) => hasClass(n, 'elemGroupRow'))
  assert(heads.length === 3, `分组头数 ${heads.length}`)
  const rows = walk(body).filter((n) => hasClass(n, 'elemRow') && hasClass(n, 'asset'))
  assert(rows.length === 2, `资产行数 ${rows.length}（折叠组不该出子行）`)
  assert(rows.some((n) => n.dataset.elemName === 'a'), 'g1 的子行没渲染')
  assert(!rows.some((n) => n.dataset.elemName === 'b'), '折叠组 g2 的子行还在')
})

await checkAsync('点分组头折叠：先本地收起再落盘，失败回滚', async () => {
  const { api, calls } = makeContext()
  api.setState([], [
    { name: 'a', kind: 'browser', selector: '#a', group: 'g1' },
    { name: 'c', kind: 'browser', selector: '#c', group: null },
  ])
  const group = { id: 'g1', name: '登录相关', order: 0, collapsed: false }
  api.libShim([group], [])
  const head = walk(nodeById('elemBody')).find((n) => hasClass(n, 'elemGroupRow'))
  await head.handlers.click[0]({ type: 'click' })
  await flush()
  const hit = calls.find((c) => c.name === 'rpa.setElementGroupCollapsed')
  assert(hit, '折叠没落盘')
  assert(hit.args.group_id === 'g1' && hit.args.collapsed === true, `折叠参数 ${JSON.stringify(hit.args)}`)
  assert(group.collapsed === true, '本地折叠态没翻')
  const rows = walk(nodeById('elemBody')).filter((n) => hasClass(n, 'elemRow') && hasClass(n, 'asset'))
  assert(rows.length === 1 && rows[0].dataset.elemName === 'c', '收起后子行没隐藏')
})

check('资产右键菜单：详情/复制/移动/查引用/删除五项齐全，分组菜单有 CRUD', () => {
  const { api } = makeContext()
  api.setState([], [{ name: 'searchBox', kind: 'browser', selector: '#q' }])
  api.openLibMenu({ clientX: 12, clientY: 8 }, 'asset', { name: 'searchBox' })
  const labels = walk(nodeById('elemLibLayer')).filter((n) => hasClass(n, 'libMenuItem')).map((n) => n.textContent)
  assert(labels.length === 5, `资产菜单项 ${labels}`)
  assert(['详情', '复制一份', '移动到分组', '查找引用', '删除'].every((x) => labels.includes(x)), labels.join('/'))
  api.openLibMenu({ clientX: 0, clientY: 0 }, 'group', { id: 'g1', name: '登录相关' })
  const gl = walk(nodeById('elemLibLayer')).filter((n) => hasClass(n, 'libMenuItem')).map((n) => n.textContent)
  assert(['在这里新建元素', '新建分组', '重命名分组', '折叠 / 展开', '删除分组'].every((x) => gl.includes(x)), gl.join('/'))
})

check('资产行的右键事件真拉起菜单（不是只测 openLibMenu 本身）', () => {
  const { api } = makeContext()
  api.setState([], [{ name: 'searchBox', kind: 'browser', selector: '#q' }])
  const row = walk(nodeById('elemBody')).find((n) => hasClass(n, 'elemRow') && hasClass(n, 'asset'))
  const events = row?.handlers?.contextmenu || []
  assert(events.length === 1, '资产行没绑右键事件')
  events[0]({ preventDefault() {} })
  const labels = walk(nodeById('elemLibLayer')).filter((n) => hasClass(n, 'libMenuItem')).map((n) => n.textContent)
  assert(labels.length === 5, `右键事件没拉起资产菜单：${labels}`)
})

await checkAsync('右键删除资产走护栏：与行内删除同入口，不绕过引用检查', async () => {
  state.detailResponse = { ok: true, referencedBy: [ref('openHome', 'selector')] }
  const { api, calls } = makeContext()
  api.setState([], [{ name: 'searchBox', kind: 'browser', selector: '#q' }])
  api.openLibMenu({ clientX: 0, clientY: 0 }, 'asset', { name: 'searchBox' })
  const del = walk(nodeById('elemLibLayer')).find((n) => hasClass(n, 'libMenuItem') && n.textContent === '删除')
  const before = calls.length
  await del.click()
  await flush()
  const after = calls.slice(before).map((c) => c.name)
  assert(after.includes('rpa.elementDetail'), '菜单删除没走删前引用检查')
  assert(!after.includes('rpa.deleteElement'), '查到引用还直接删了')
  state.detailResponse = { ok: true, referencedBy: [] }
})

await checkAsync('多选批量：勾选进选中集、批量条浮出、取消选择清空', async () => {
  const { api } = makeContext()
  api.setState([], [
    { name: 'a', kind: 'browser', selector: '#a' },
    { name: 'b', kind: 'browser', selector: '#b' },
  ])
  const rows = walk(nodeById('elemBody')).filter((n) => hasClass(n, 'elemRow') && hasClass(n, 'asset'))
  const pickA = walk(rows[0]).find((n) => hasClass(n, 'elemPick'))
  const pickB = walk(rows[1]).find((n) => hasClass(n, 'elemPick'))
  assert(pickA && pickB, '资产行缺多选复选框')
  const bars = () => walk(nodeById('elemLibLayer')).filter((n) => hasClass(n, 'libBatchBar'))
  const countText = (bar) => (bar
    ? walk(bar).find((n) => hasClass(n, 'libBatchCount'))?.textContent
    : '<none>')
  pickA.checked = true
  await pickA.handlers.change[0]({ type: 'change' })
  // 单例判据：勾 N 项只能有 1 条，且计数跟着变。早先每次勾选都 append，
  // 勾 5 项就叠 5 条（现场截图），而用例只用 find 取一条 ⇒ 一直假绿。
  assert(bars().length === 1 && countText(bars()[0]) === '已选 1 项',
    `勾 1 项后批量条 ${bars().length} 条 / ${countText(bars()[0])}`)
  pickB.checked = true
  await pickB.handlers.change[0]({ type: 'change' })
  assert(api.getSelected().join(',') === 'a,b', `选中集 ${api.getSelected()}`)
  assert(bars().length === 1 && countText(bars()[0]) === '已选 2 项',
    `勾 2 项后批量条 ${bars().length} 条 / ${countText(bars()[0])}`)
  const bar = bars()[0]
  assert(bar, '批量条没浮出')
  const barLabels = walk(bar).filter((n) => hasClass(n, 'libMenuItem')).map((n) => n.textContent)
  assert(['批量移动', '批量校验', '批量删除', '取消选择'].every((x) => barLabels.includes(x)), barLabels.join('/'))
  const clear = walk(bar).find((n) => hasClass(n, 'libMenuItem') && n.textContent === '取消选择')
  await clear.click()
  await flush()
  assert(api.getSelected().length === 0, '取消选择没清空')
  assert(bars().length === 0, `取消选择后批量条还剩 ${bars().length} 条`)
})

await checkAsync('捕获确认保存把勾选快照随定位一起交给后端', async () => {
  const { api, calls } = makeContext()
  await api.openCaptureDialog('c7', {
    kind: 'browser', selector: 'div.a2 > span.b', name: '搜索框',
  })
  await flush()
  await nodeById('elemDialogSave').click()
  await flush()
  const saved = calls.filter((c) => c.name === 'rpa.commitElement').pop()
  assert(saved, '保存没发 commitElement')
  const levels = saved.args.edit_state?.levels
  assert(Array.isArray(levels) && levels.length === 2,
    `勾选快照没传：${JSON.stringify(saved.args)}`)
  assert(levels[0].classes[0].on === false && levels[0].classes[1].on === true,
    `取消勾选的 class 没保留：${JSON.stringify(levels)}`)
})

await checkAsync('新入库资产带来源/状态字段，但界面不伪造旧资产的值', async () => {
  const { api } = makeContext()
  api.setState([], [
    { name: 'rich', kind: 'desktop', selector: 'x', processName: '微信', winTitle: '聊天', refCount: 2 },
    { name: 'legacy', kind: 'browser', selector: '#q' },
  ])
  const rows = walk(nodeById('elemBody')).filter((n) => hasClass(n, 'elemRow') && hasClass(n, 'asset'))
  const rich = rows.find((n) => n.dataset.elemName === 'rich')
  const richText = walk(rich).map((n) => n.textContent).join('|')
  assert(richText.includes('微信') && richText.includes('聊天'), `来源列没显示：${richText}`)
  assert(walk(rich).some((n) => hasClass(n, 'elemRefs')), '引用徽标没显示')
})

check('只看未使用：开关真过滤，空结果给专门文案', () => {
  const { api } = makeContext()
  api.setState([], [
    { name: 'a', kind: 'browser', selector: '#a' },
    { name: 'b', kind: 'browser', selector: '#b' },
  ])
  api.libShim([], ['b'], true)
  const rows = walk(nodeById('elemBody')).filter((n) => hasClass(n, 'elemRow') && hasClass(n, 'asset'))
  assert(rows.length === 1 && rows[0].dataset.elemName === 'b', `未使用过滤 ${rows.map((r) => r.dataset.elemName)}`)
})

// ---------- M102 S2：左列表 + 右详情、缩略图按需拉 ----------
// 这一组判据都必须在「改回旧形状」时变红：详情栏挪进弹窗、缩略图改回批量塞进
// listElements、行内动作改回常显、行高改回折行——每一条对上一种退化。

check('结构层：详情栏在 elemSplit 里、排在列表之后，且不放静态按钮', () => {
  const panel = html.slice(html.indexOf('data-panel="elements"'), html.indexOf('data-panel="globals"'))
  const splitAt = panel.indexOf('id="elemSplit"')
  const bodyAt = panel.indexOf('id="elemBody"')
  const detailAt = panel.indexOf('id="elemDetail"')
  assert(splitAt > 0 && splitAt < bodyAt, '列表没被 elemSplit 包住，右栏没法并排')
  assert(detailAt > bodyAt, '详情栏排在列表之前（会被盖住）')
  const tail = panel.slice(detailAt, panel.indexOf('id="elemLibLayer"'))
  assert(!/<button/i.test(tail), '详情栏里放了静态按钮：会混进捕获按钮白名单')
})

check('样式层：窄窗口回落单列、行内动作默认隐身、行是单行截断', () => {
  assert(
    /@media\s*\(max-width:\s*1100px\)[\s\S]{0,160}\.elemDetail\s*\{\s*display:\s*none/.test(css),
    '没有「窄窗口隐藏右栏」的规则：窄屏会被挤成两条竖缝',
  )
  assert(/\.elemRowAct\s*\{[^}]*opacity:\s*0/.test(css), '行内动作不是默认隐藏，删除就没降权')
  assert(/\.elemRow\.asset:hover \.elemRowAct/.test(css), 'hover 不浮现动作，那就点不到了')
  const meta = css.match(/\.elemRow\.asset \.elemMeta\s*\{[^}]*\}/)
  assert(meta, 'style.css 缺 .elemRow.asset .elemMeta 规则')
  assert(/white-space:\s*nowrap/.test(meta[0]), '元信息还在折行——行高会被撑开，一屏看不到几项')
  assert(/text-overflow:\s*ellipsis/.test(meta[0]), '单行却没有省略号，长选择器会被硬切')
})

await checkAsync('行为层：点一行把名字与**完整**定位放进右栏（不截断）', async () => {
  const { api } = makeContext()
  const long = '#main > div.search-bar > input[type="search"][placeholder="搜索你感兴趣的内容"]'
  api.setState([], [{ name: 'searchBox', kind: 'browser', selector: long }])
  const row = walk(nodeById('elemBody')).find((n) => hasClass(n, 'asset'))
  await row.click()
  const pane = nodeById('elemDetail')
  assert(pane.hidden !== true, '选中了元素右栏还藏着')
  const texts = walk(pane).map((n) => n.textContent)
  assert(texts.includes('searchBox'), `右栏没显示名字：${texts.slice(0, 6)}`)
  const code = walk(pane).find((n) => hasClass(n, 'elemDetailCode'))
  assert(code && code.textContent === long, `右栏定位不是原文：${code && code.textContent}`)
})

await checkAsync('行为层：只有带截图的资产才去拉图，图回来真塞进 <img>', async () => {
  const { api, calls } = makeContext()
  api.setState([], [
    { name: 'withShot', kind: 'browser', selector: '#a', screenshot: 'shots/aa.png' },
    { name: 'noShot', kind: 'browser', selector: '#b' },
  ])
  await flush()
  const shots = calls.filter((c) => c.name === 'rpa.elementShot')
  assert(shots.length === 1, `按需拉的含义是「有图才拉」，实际拉了 ${shots.map((s) => s.args.name)}`)
  assert(shots[0].args.name === 'withShot', `拉错了元素：${shots[0].args.name}`)
  const row = walk(nodeById('elemBody'))
    .filter((n) => hasClass(n, 'asset'))
    .find((n) => n.dataset.elemName === 'withShot')
  const img = walk(row).find((n) => hasClass(n, 'elemThumbImg'))
  assert(img && String(img.src).startsWith('data:image/png'), `行内没出现缩略图：${img && img.src}`)
})

await checkAsync('行为层：资产没了右栏收敛，不挂着上一份详情', async () => {
  const { api } = makeContext()
  api.setState([], [{ name: 'searchBox', kind: 'browser', selector: '#q' }])
  const row = walk(nodeById('elemBody')).find((n) => hasClass(n, 'asset'))
  await row.click()
  assert(nodeById('elemDetail').hidden !== true, '选了却没显示')
  api.setState([], [])
  assert(nodeById('elemDetail').hidden === true, '元素已经没了，右栏还开着')
})

for (const result of results) {
  console.log(`${result.ok ? 'PASS' : 'FAIL'} ${result.name}${result.ok ? '' : ` — ${result.detail}`}`)
}
const failed = results.filter((r) => !r.ok).length
console.log(`ELEMENT LIBRARY CHECK ${failed ? 'FAILED' : 'PASSED'} (${results.length - failed}/${results.length})`)
if (failed) process.exitCode = 1
