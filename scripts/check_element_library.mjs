// 元素资产库 UI 契约判据（进默认门禁）。
//
// 「补一下」只服务 elementRefs 缺口；用户要的「捕获资产入库」是另一条路：
// 常驻工具条建预置资产 + 已就绪行的详情/重捕/删除。两层判据缺一不可：
//   1. 结构层 —— 工具条 DOM 在 index.html、版式在 style.css、逻辑在 main.js，
//      且工具条在 elemBody 之外（否则 renderElementsPanel 一刷就没了）；
//   2. 行为层 —— 最小 DOM shim 真跑 main.js 的绑定，点按钮、发 RPC、看落盘回显。
//      只扫源码证明不了「有按钮但点不着」「删了还显示在列表里」。
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
    this.parent = null
    this.className = ''
    this.dataset = {}
    this.handlers = {}
    this.textContent = ''
    this.value = ''
    this.hidden = false
    this.open = false
    this.disabled = false
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
  append(...nodes) {
    for (const node of nodes) {
      if (node.parent) node.parent.children = node.parent.children.filter((x) => x !== node)
      node.parent = this
      this.children.push(node)
    }
  }
  replaceChildren(...nodes) {
    for (const child of this.children) child.parent = null
    this.children = []
    this.append(...nodes)
  }
  setAttribute(key, value) { this.attributes = { ...(this.attributes || {}), [key]: value } }
  addEventListener(type, fn) { (this.handlers[type] ||= []).push(fn) }
  click() {
    return Promise.all((this.handlers.click || []).map((fn) => fn({ type: 'click' })))
  }
  querySelector() { return null }
  querySelectorAll() { return [] }
  focus() {}
  scrollIntoView() {}
}
const walk = (node, out = []) => {
  out.push(node)
  for (const child of node.children || []) walk(child, out)
  return out
}
const hasClass = (node, name) => String(node.className).split(/\s+/).includes(name)
const byId = new Map()
function make(tag, id, cls, dataset) {
  const node = new El(tag)
  if (id) { node.id = id; byId.set(id, node) }
  if (cls) node.className = cls
  if (dataset) Object.assign(node.dataset, dataset)
  return node
}
function nodeById(id) {
  if (!byId.has(id)) byId.set(id, make('div', id, ''))
  return byId.get(id)
}

// ---- 按 index.html 的形状预置 DOM；工具条 id 从 HTML 解析，手写一份会恒绿 ----
const TOOL_IDS = [...html.matchAll(/id="(elem(?:New|Tools)[A-Za-z]+)"/g)].map((m) => m[1])
const head = new El('head')
const mainHost = new El('main')
const dock = make('section', 'bottomDock', 'bottomDock')
const tabBar = make('div', '', 'dockTabs')
const panelsWrap = make('div', '', 'dockPanels')
const htmlTabKeys = [...html.matchAll(/data-tab="([a-z]+)"/g)].map((m) => m[1])
const htmlPanelKeys = [...html.matchAll(/data-panel="([a-z]+)"/g)].map((m) => m[1])
for (const key of htmlTabKeys) {
  tabBar.append(make('button', '', 'dockTab', { tab: key }))
}
for (const key of htmlPanelKeys) {
  panelsWrap.append(make('div', '', 'dockPanel', { panel: key }))
}
dock.append(tabBar, panelsWrap)
mainHost.append(dock)
const panelByKey = (key) => walk(panelsWrap).find((n) => hasClass(n, 'dockPanel') && n.dataset.panel === key)
const elemPanel = panelByKey('elements')
const elemTools = make('div', '', 'elemTools')
for (const id of TOOL_IDS) elemTools.append(nodeById(id))
elemPanel.append(elemTools)
const elemBox = make('details', 'elemBox', 'elemBox')
elemBox.append(nodeById('elemBody'))
elemPanel.append(elemBox)
panelByKey('log').append(make('details', 'runBox', 'runBox'))
for (const id of ['globalsRows', 'globalsAdd', 'globalsSave', 'globalsRevert', 'globalsNote']) {
  panelByKey('globals').append(nodeById(id))
}

const calls = []
let state = {
  missing: [],
  elements: [{ name: 'searchBox', kind: 'browser', selector: '#q' }],
  deleteResponse: { ok: true, name: 'searchBox', remaining: [] },
  writeResponse: { ok: true, name: 'newOne', boundNodeId: null },
  planResponse: { ok: true, blocked: false, capabilities: {}, decision: { target: 'browser' } },
}
const doc = {
  head,
  createElement: (tag) => new El(tag),
  querySelector: (sel) => {
    if (sel === 'main') return mainHost
    if (sel === 'head') return head
    if (sel.startsWith('#')) return nodeById(sel.slice(1))
    if (sel.startsWith('.')) return walk(mainHost).find((n) => hasClass(n, sel.slice(1))) || null
    return null
  },
  querySelectorAll: (sel) =>
    sel.startsWith('.') ? walk(mainHost).filter((n) => hasClass(n, sel.slice(1))) : [],
  addEventListener() {},
  activeElement: null,
}
const sandbox = {
  document: doc,
  ppx: {
    call: async (name, args) => {
      calls.push({ name, args })
      if (name === 'rpa.bootstrap') {
        return { commands: [], flows: [], catalogDigest: '', python: '', catalogMs: 0 }
      }
      if (name === 'rpa.missingElements') return { missing: state.missing, resolved: [], capabilities: { fix: '浏览器还没连上' } }
      if (name === 'rpa.listElements') return { elements: state.elements }
      if (name === 'rpa.elementDetail') {
        return {
          ok: true,
          name: args.name,
          file: `elements/${args.name}.json`,
          document: { kind: 'browser', selector: { css: '#q', candidates: [] }, capturedAt: '2026-11-12' },
          referencedBy: ['openHome'],
        }
      }
      if (name === 'rpa.deleteElement') return state.deleteResponse
      if (name === 'rpa.writeElement') return state.writeResponse
      if (name === 'rpa.pickPlan') return state.planResponse
      if (name === 'rpa.startCapture') return { ok: true, state: 'waiting', deadlineAt: 9999999999, instructions: '点一下' }
      if (name === 'rpa.capturePoll') return { ok: true, state: 'waiting' }
      return { ok: true }
    },
  },
  console,
  performance: { now: () => 0 },
  setInterval: () => 0,
  clearInterval: () => {},
  setTimeout: () => 0,
  clearTimeout: () => {},
  window: { confirm: () => true },
  CSS: { escape: (s) => s },
  URL,
  Date, Math, JSON, String, Number, Array, Object, Set, Map, Promise, RegExp, Error,
}
const src = mainSrc.replace(/^import .*$/gm, '')
const ctx = vm.createContext(sandbox)
vm.runInContext(src, ctx, { filename: 'main.js' })
const run = (expr) => vm.runInContext(expr, ctx)
const elemBody = nodeById('elemBody')
const note = nodeById('elemNewNote')
// 真实页面是 openFlow 设上 currentPath 并刷一次资产列表；夹具不做这一步，
// 工具条的 handler 会在第一行 `if (!currentPath) return` 静默退出（假绿）。
run('currentPath = "flows/probe.json"')
await run('refreshElements()')

// ---------- 1. 结构层 ----------
check('工具条 6 个控件在 index.html，且在 elemBody 之外', () => {
  const expected = [
    'elemNewName', 'elemNewWhere', 'elemNewCapture',
    'elemNewSelector', 'elemNewWrite', 'elemNewNote',
  ]
  for (const id of expected) assert(html.includes(`id="${id}"`), `index.html 缺 #${id}`)
  assert(expected.every((id) => TOOL_IDS.includes(id)), `HTML 解析没拿到全部工具条 id：${TOOL_IDS}`)
  const panelStart = html.indexOf('data-panel="elements"')
  const toolsAt = html.indexOf('class="elemTools"', panelStart)
  const bodyAt = html.indexOf('id="elemBody"', panelStart)
  assert(toolsAt > panelStart && toolsAt < bodyAt, '工具条不在元素资产面板里 / 落在 elemBody 之后')
  assert(html.indexOf('class="elemTools"', panelStart) > -1, '没有 elemTools 容器')
})

check('版式在 style.css，main.js 没有运行时注入 DOM 或样式', () => {
  // 带花括号的等值形状：只写 '.elemTools' 会被 .elemToolsRow 这个子串糊弄成恒绿。
  for (const needle of ['.elemTools {', '.elemDetail {', '.elemAct.danger', '.elemNewNote.error']) {
    assert(css.includes(needle), `style.css 缺 ${needle}`)
  }
  for (const banned of ['DND_CSS', 'DOCK_CSS', 'injectDndStyles', 'injectDockStyles', 'mountBottomDock']) {
    assert(!mainSrc.includes(banned), `main.js 里还有运行时注入残留：${banned}`)
  }
})

check('main.js 真接了工具条与三个资产 RPC', () => {
  for (const needle of [
    'bindElementTools()',
    "ppx.call('rpa.writeElement'",
    "ppx.call('rpa.elementDetail'",
    "ppx.call('rpa.deleteElement'",
  ]) {
    assert(mainSrc.includes(needle), `main.js 缺 ${needle}`)
  }
  assert(/if \(!pickMissing\.length \|\| !pickCapabilities/.test(mainSrc), '离线横幅没有按缺口收敛')
})

// ---------- 2. 行为层 ----------
check('顶层 bindBottomDock 真的把工具条按钮绑上', () => {
  assert((nodeById('elemNewCapture').handlers.click || []).length > 0, '捕获按钮没绑事件')
  assert((nodeById('elemNewWrite').handlers.click || []).length > 0, '写入库按钮没绑事件')
})

await checkAsync('手填写选择器真发 rpa.writeElement，成功后列表出现新资产', async () => {
  calls.length = 0
  nodeById('elemNewName').value = 'newOne'
  nodeById('elemNewSelector').value = '#fresh'
  state.elements = [...state.elements, { name: 'newOne', kind: 'browser', selector: '#fresh' }]
  await nodeById('elemNewWrite').click()
  const write = calls.find((c) => c.name === 'rpa.writeElement')
  assert(write, '没发 rpa.writeElement')
  assert(write.args.name === 'newOne' && write.args.selector === '#fresh', JSON.stringify(write.args))
  assert(calls.some((c) => c.name === 'rpa.listElements'), '写完没刷新资产列表')
  assert(walk(elemBody).some((n) => hasClass(n, 'elemName') && n.textContent === 'newOne'), '新资产没出现在界面')
  assert(note.textContent.includes('已入库'), `没给入库反馈：${note.textContent}`)
})

await checkAsync('非法资产名不发 RPC，工具条给出错误反馈', async () => {
  calls.length = 0
  nodeById('elemNewName').value = '../evil'
  await nodeById('elemNewWrite').click()
  assert(!calls.some((c) => c.name === 'rpa.writeElement'), '非法名竟然发了写入 RPC')
  assert(note.classList.contains('error') && note.textContent.length > 0, '没标红说明')
})

await checkAsync('＋ 捕获元素走 pickPlan，且带上 isNew 标记', async () => {
  calls.length = 0
  nodeById('elemNewName').value = 'preset_two'
  await nodeById('elemNewCapture').click()
  const plan = calls.find((c) => c.name === 'rpa.pickPlan')
  assert(plan && plan.args.name === 'preset_two', '没按新名字探测能力')
  assert(run('pickState.isNew') === true, '新建拾取没带 isNew')
  assert(run('pickState.state') === 'ready', `该进 ready，实际 ${run('pickState.state')}`)
})

check('已就绪行有 详情 / 重新捕获 / 删除 三个动作', () => {
  run('pickState = null; renderElementsPanel()')
  const row = walk(elemBody).find((n) => hasClass(n, 'elemRow') && hasClass(n, 'asset'))
  assert(row, '没有已就绪资产行')
  const labels = walk(row).filter((n) => hasClass(n, 'elemAct')).map((n) => n.textContent)
  assert(labels.length === 3, `动作数 ${labels.length}：${labels}`)
  assert(labels.includes('详情') && labels.includes('重新捕获') && labels.includes('删除'), labels.join('/'))
})

await checkAsync('点详情真取完整资产描述符并展开', async () => {
  calls.length = 0
  const row = walk(elemBody).find((n) => hasClass(n, 'elemRow') && hasClass(n, 'asset'))
  const detailBtn = walk(row).find((n) => hasClass(n, 'elemAct') && n.textContent === '详情')
  await detailBtn.click()
  assert(calls.some((c) => c.name === 'rpa.elementDetail'), '没发 rpa.elementDetail')
  const box = walk(elemBody).find((n) => hasClass(n, 'elemDetail'))
  assert(box, '详情没展开')
  assert(box.textContent.includes('#q') && box.textContent.includes('openHome'), `详情里缺选择器/引用：${box.textContent}`)
})

await checkAsync('删除被引用资产时把原因说清楚，不刷新成已删', async () => {
  state.deleteResponse = { ok: false, code: 'ELEMENT_IN_USE', error: '还被引用着', referencedBy: ['openHome'] }
  const row = walk(elemBody).find((n) => hasClass(n, 'elemRow') && hasClass(n, 'asset'))
  const del = walk(row).find((n) => hasClass(n, 'elemAct') && n.textContent === '删除')
  await del.click()
  assert(note.classList.contains('error'), '失败没标红')
  assert(note.textContent.includes('引用'), `没说清是被引用挡住：${note.textContent}`)
  assert(walk(elemBody).some((n) => hasClass(n, 'elemRow') && hasClass(n, 'asset')), '删除失败却把资产从界面抹了')
})

await checkAsync('重新捕获带资产类型作为 override，不假装绑了节点', async () => {
  calls.length = 0
  const row = walk(elemBody).find((n) => hasClass(n, 'elemRow') && hasClass(n, 'asset'))
  const again = walk(row).find((n) => hasClass(n, 'elemAct') && n.textContent === '重新捕获')
  await again.click()
  const plan = calls.find((c) => c.name === 'rpa.pickPlan')
  assert(plan, '没发 rpa.pickPlan')
  assert(plan.args.name === 'searchBox', `重捕名字错了：${plan.args.name}`)
  assert(plan.args.override === 'browser', `没按资产类型给 override：${plan.args.override}`)
  assert(run('pickState.nodeId') === null, '预置资产不该凭空绑节点')
})

check('没有待补元素时不播离线假警报，有缺口时才播', () => {
  run('pickState = null; pickMissing = []; pickCapabilities = { fix: "浏览器还没连上" }')
  run('renderElementsPanel()')
  assert(!walk(elemBody).some((n) => hasClass(n, 'pickBar')), '没有缺口却报离线')
  run('pickMissing = [{ name: "todo", nodeId: "n1", command: "browser.click" }]')
  run('renderElementsPanel()')
  const bar = walk(elemBody).find((n) => hasClass(n, 'pickBar'))
  assert(bar && bar.dataset.state === 'offline', '有缺口时不报离线了')
})

for (const result of results) {
  console.log(`${result.ok ? 'PASS' : 'FAIL'} ${result.name}${result.ok ? '' : ` — ${result.detail}`}`)
}
const failed = results.filter((r) => !r.ok).length
console.log(`ELEMENT LIBRARY CHECK ${failed ? 'FAILED' : 'PASSED'} (${results.length - failed}/${results.length})`)
if (failed) process.exitCode = 1
