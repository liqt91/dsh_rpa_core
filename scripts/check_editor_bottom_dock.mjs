// 编辑器底栏（元素资产 / 全局参数 / 运行日志）的 UI 契约判据（进默认门禁）。
//
// 分两层，缺一层都会漏：
//   1. 结构层 —— index.html 里三个模块各就各位、id 齐全、版式在 style.css 里，
//      且 HTML 的 tab/panel key 与 main.js 的 DOCK_TABS 不漂移；
//   2. 行为层 —— 用带父子关系的 DOM shim **真跑 main.js 的绑定**，再驱动 tab、
//      折叠、全局参数增删改与保存 RPC。只扫源码证明不了「结构在但点不着」。
//
// ADR 0022-R3 之后原型目录是三分支共享，DOM/样式不再从 main.js 运行时注入。
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
    this.attributes = {}
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
      contains(c) {
        return self.className.split(/\s+/).includes(c)
      },
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
  setAttribute(key, value) { this.attributes[key] = value }
  addEventListener(type, fn) { (this.handlers[type] ||= []).push(fn) }
  click() { for (const fn of this.handlers.click || []) fn({ type: 'click' }) }
  contains(node) {
    return node === this || (this.children || []).some((child) => child.contains?.(node))
  }
  querySelector() { return null }
  querySelectorAll() { return [] }
  focus() {}
  scrollIntoView() {}
}
function walk(node, out = []) {
  out.push(node)
  for (const child of node.children || []) walk(child, out)
  return out
}
const hasClass = (node, name) => String(node.className).split(/\s+/).includes(name)

// ---- 按 index.html 的形状预置 DOM（真实浏览器里由 HTML 解析器生成） ----
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

const head = new El('head')
const mainHost = new El('main')
const dock = make('section', 'bottomDock', 'bottomDock')
const tabBar = make('div', '', 'dockTabs')
const panelsWrap = make('div', '', 'dockPanels')
const labels = { elements: '元素资产', globals: '全局参数', log: '运行日志' }
// shim 的 key **从 index.html 解析**，不手写一份：否则 HTML 改了 key 时动态判据
// 还在跑旧的替身结构，静态层会红而行为层假装没事（判据覆盖不到用户走的那条路）。
const htmlTabKeys = [...html.matchAll(/data-tab="([a-z]+)"/g)].map((m) => m[1])
const htmlPanelKeys = [...html.matchAll(/data-panel="([a-z]+)"/g)].map((m) => m[1])
for (const key of htmlTabKeys) {
  const button = make('button', '', `dockTab${key === 'globals' ? ' active' : ''}`, { tab: key })
  button.append(make('span', '', ''))
  button.children[0].textContent = labels[key] || key
  tabBar.append(button)
}
for (const key of htmlPanelKeys) {
  const panel = make('div', '', `dockPanel${key === 'globals' ? ' active' : ''}`, { panel: key })
  panelsWrap.append(panel)
}
make('span', 'elemCount', 'count').parent = tabBar.children[0]
tabBar.children[0].children.push(byId.get('elemCount'))
make('span', 'globalsCount', 'count').parent = tabBar.children[1]
tabBar.children[1].children.push(byId.get('globalsCount'))
make('span', 'runTitle', '').parent = tabBar.children[2]
tabBar.children[2].children.push(byId.get('runTitle'))
const fold = make('button', '', 'dockFold')
fold.textContent = '收起'
tabBar.append(fold)
dock.append(tabBar, panelsWrap)
mainHost.append(dock)

const panelByKey = (key) => walk(panelsWrap).find((n) => hasClass(n, 'dockPanel') && n.dataset.panel === key)
const tabByKey = (key) => walk(tabBar).find((n) => hasClass(n, 'dockTab') && n.dataset.tab === key)

const elemBox = make('details', 'elemBox', 'elemBox')
elemBox.append(make('div', 'elemBody', 'elemBody'))
panelByKey('elements').append(elemBox)
const runBox = make('details', 'runBox', 'runBox')
runBox.append(make('div', 'runLog', 'runLog'))
panelByKey('log').append(runBox)
const globalsPanel = make('div', '', 'globalsPanel')
globalsPanel.append(
  make('div', '', 'globalsHint'),
  make('div', 'globalsRows', 'globalsRows'),
  make('div', '', 'globalsBar'),
)
panelByKey('globals').append(globalsPanel)
for (const id of ['globalsAdd', 'globalsSave', 'globalsRevert', 'globalsNote']) {
  const node = make('button', id, '')
  node.parent = globalsPanel
  globalsPanel.children.push(node)
}
// globalsRows 已注册成 div，renderGlobals 会往它里面写行

const calls = []
let saveInputsResponse = null
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
      if (name === 'rpa.openFlow') {
        return {
          name: '夹具流程', path: args.path, sha: 'abc', cards: [],
          inputs: { keyword: 'AI效率工具', limit: 20, enabled: true },
        }
      }
      if (name === 'rpa.saveFlowInputs') {
        if (saveInputsResponse && saveInputsResponse.fail) throw saveInputsResponse.fail
        return saveInputsResponse || {
          ok: true,
          inputs: { keyword: 'AI效率工具', limit: 20, enabled: true },
          compile: { ok: true },
        }
      }
      return { ok: true, missing: [], resolved: [], elements: [] }
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

// ---------- 1. 结构层 ----------
check('index.html 里三个模块各就各位，折叠面板都搬进底栏', () => {
  assert(html.includes('id="bottomDock"'), 'index.html 没有底栏')
  // 期望集是设计规格，不能从 HTML 自己解析出来的 key 反查 HTML（那样恒绿）。
  for (const key of ['elements', 'globals', 'log']) {
    assert(html.includes(`data-tab="${key}"`), `缺 ${key} tab`)
    assert(html.includes(`data-panel="${key}"`), `缺 ${key} panel`)
  }
  for (const id of [
    'elemBox', 'elemBody', 'elemCount', 'runBox', 'runLog', 'runTitle',
    'globalsRows', 'globalsAdd', 'globalsSave', 'globalsRevert', 'globalsNote', 'globalsCount',
  ]) {
    assert(html.includes(`id="${id}"`), `index.html 缺 #${id}`)
  }
  const dockPart = html.slice(html.indexOf('id="bottomDock"'))
  for (const id of ['elemBox', 'runBox']) {
    assert(dockPart.includes(`id="${id}"`), `${id} 没搬进底栏`)
  }
  assert(html.includes('${inputs.名称}'), '全局参数面板没写引用提示')
})

check('HTML 的 tab/panel key 与 main.js 的 DOCK_TABS 不漂移', () => {
  const htmlTabs = [...html.matchAll(/data-tab="([a-z]+)"/g)].map((m) => m[1])
  const htmlPanels = [...html.matchAll(/data-panel="([a-z]+)"/g)].map((m) => m[1])
  const jsKeys = [...mainSrc.matchAll(/key: '([a-z]+)'/g)].map((m) => m[1])
  assert(jsKeys.length === 3, `DOCK_TABS 有 ${jsKeys.length} 项，应为 3`)
  assert(JSON.stringify(htmlTabs) === JSON.stringify(jsKeys), 'tab key 与 DOCK_TABS 不一致')
  assert(JSON.stringify(htmlPanels) === JSON.stringify(jsKeys), 'panel key 与 DOCK_TABS 不一致')
})

check('版式在 style.css，main.js 不再运行时注入样式或 DOM', () => {
  for (const needle of [
    'grid-template-rows: minmax(0, 1fr) auto',
    'grid-column: 1 / -1',
    '.dockTab.active',
    '#bottomDock .elemBox > summary',
    '.globalRow',
    '.card.drop-before',
  ]) {
    assert(css.includes(needle), `style.css 缺 ${needle}`)
  }
  for (const banned of ['DND_CSS', 'DOCK_CSS', 'injectDndStyles', 'injectDockStyles', 'mountBottomDock']) {
    assert(!mainSrc.includes(banned), `main.js 里还有运行时注入残留：${banned}`)
  }
  assert(mainSrc.includes('bindBottomDock()'), 'main.js 没在顶层绑定底栏')
})

// ---------- 2. 行为层 ----------
check('真跑绑定：三个 tab / panel 都接上，默认展示全局参数', () => {
  assert(run('Object.keys(dockTabs).length') === 3, 'dockTabs 没绑满')
  assert(run('Object.keys(dockPanels).length') === 3, 'dockPanels 没绑满')
  assert(run('dockActive') === 'globals', '默认 tab 不是全局参数')
  assert(tabByKey('globals').classList.contains('active'), 'globals tab 没激活')
  assert(panelByKey('globals').classList.contains('active'), 'globals panel 没激活')
  assert(runBox.open === true && runBox.hidden === false, 'runBox 没被强制展开')
  assert(elemBox.open === true, 'elemBox 没被强制展开')
  assert(byId.get('elemCount').parent === tabByKey('elements'), '元素计数不在 elements tab')
  assert(byId.get('runTitle').parent === tabByKey('log'), '运行徽标不在 log tab')
})

check('tab 切换与折叠按钮真的改变状态', () => {
  tabByKey('log').click()
  assert(run('dockActive') === 'log', '点日志 tab 没切过去')
  assert(panelByKey('log').classList.contains('active'), '日志 panel 没显示')
  assert(!panelByKey('globals').classList.contains('active'), '全局参数 panel 没隐藏')
  tabByKey('globals').click()
  assert(run('dockActive') === 'globals', '点回全局参数 tab 没切过去')
  fold.click()
  assert(dock.classList.contains('collapsed'), '折叠没生效')
  fold.click()
  assert(!dock.classList.contains('collapsed'), '展开没生效')
})

check('inputs → 编辑行：类型推断与 JSON 编码和后端同口径', () => {
  const rows = run('globalsFromInputs({ s: "中文", n: 3, b: false, o: { a: 1 }, z: null })')
  assert(rows.map((r) => r.kind).join(',') === 'string,number,boolean,json,json', '类型推断错')
  assert(rows[0].text === '中文', '字符串不该带引号展示')
  assert(run('encodeGlobalRow({ kind: "string", text: "AI效率工具" })') === '"AI效率工具"', '字符串没编码成 JSON 文本')
  assert(run('encodeGlobalRow({ kind: "number", text: "3" })') === '3', '数字不该加引号')
  assert(run('encodeGlobalRow({ kind: "json", text: "  " })') === 'null', '空 JSON 没落成 null')
})

check('渲染出行、增删行、非法值拦在保存前', () => {
  run('globalsRows = globalsFromInputs({ keyword: "AI效率工具", limit: 20 }); globalsDirty = false; globalsError = ""; renderGlobals()')
  const rowsNode = run('el.globalsRows')
  assert(rowsNode.children.length === 2, `渲染行数 ${rowsNode.children.length} != 2`)
  for (const row of rowsNode.children) {
    assert(row.children.length === 4, '一行不是「名称/类型/值/删除」四列')
    assert(row.children[1].children.length === 4, '类型下拉没有四种类型')
  }
  byId.get('globalsAdd').click()
  assert(rowsNode.children.length === 3, '添加参数没加行')
  assert(run('globalsDirty') === true, '添加后没标脏')
  assert(run('globalsProblems().length') === 1, '空名不该通过预检')
  run('globalsRows = [{ name: "bad", kind: "number", text: "abc" }]; globalsDirty = true; globalsError = ""')
  assert(run('globalsProblems()[0]').includes('JSON'), '非法数字没被拦')
  run('globalsRows = [{ name: "a", kind: "string", text: "x" }, { name: "a", kind: "string", text: "y" }]')
  assert(run('globalsProblems()[0]').includes('重复'), '重名没被拦')
})

await checkAsync('全局参数预检失败时不发 RPC', async () => {
  const before = calls.length
  run('globalsRows = [{ name: "", kind: "string", text: "" }]; globalsDirty = true; globalsError = ""; currentPath = "f.json"')
  await run('saveGlobals()')
  assert(calls.length === before, '预检失败却发了保存 RPC')
  assert(String(run('globalsError')).includes('参数名'), '没把预检原因显示出来')
})

await checkAsync('保存走 rpa.saveFlowInputs，values 是整份声明且按类型编码', async () => {
  run('globalsRows = globalsFromInputs({ keyword: "AI效率工具", limit: 20, enabled: true }); globalsDirty = true; globalsError = ""; currentPath = "f.json"; renderGlobals()')
  await run('saveGlobals()')
  const call = calls.filter((c) => c.name === 'rpa.saveFlowInputs').at(-1)
  assert(call, '没调用 rpa.saveFlowInputs')
  assert(call.args.path === 'f.json', 'path 不对')
  assert(Object.keys(call.args.values).join(',') === 'keyword,limit,enabled', '不是整份声明')
  assert(call.args.values.keyword === '"AI效率工具"', '文本没编码成 JSON 字符串')
  assert(call.args.values.limit === '20' && call.args.values.enabled === 'true', '数字/布尔被加引号了')
  assert(run('globalsDirty') === false, '保存后还是脏的')
  assert(run('globalsRows.length') === 3, '保存后没按服务端返回重建行')
})

await checkAsync('保存失败时保留编辑态与错误文案', async () => {
  saveInputsResponse = { fail: { code: 'INVALID_DECLARATION', message: '输入名不合法' } }
  run('globalsRows = [{ name: "a.b", kind: "string", text: "x" }]; globalsDirty = true; globalsError = ""')
  await run('saveGlobals()')
  assert(String(run('globalsError')).includes('INVALID_DECLARATION'), '没显示服务端错误码')
  assert(run('globalsDirty') === true, '失败后不该清脏标记')
  assert(run('globalsRows.length') === 1, '失败后把用户编辑丢了')
  saveInputsResponse = null
})

await checkAsync('openFlow 真的把 inputs 刷进编辑行，runFlow 会切到日志 tab', async () => {
  run('refreshElements = async () => {}')
  await run('openFlow("f.json")')
  assert(run('globalsRows.length') === 3, 'openFlow 没刷新全局参数')
  assert(run('el.globalsRows').children.length === 3, 'openFlow 后表格没重渲染')
  assert(run('globalsDirty') === false, 'openFlow 后不该是脏的')
  const flowSrc = mainSrc.slice(mainSrc.indexOf('async function runFlow()'), mainSrc.indexOf('async function runFlow()') + 900)
  assert(/^\s*activateDockTab\('log'\)/m.test(flowSrc), '运行时没自动切到日志 tab')
})

for (const result of results) {
  console.log(`${result.ok ? 'PASS' : 'FAIL'} ${result.name}${result.ok ? '' : ` — ${result.detail}`}`)
}
const failed = results.filter((r) => !r.ok).length
console.log(`EDITOR BOTTOM DOCK CHECK ${failed ? 'FAILED' : 'PASSED'} (${results.length - failed}/${results.length})`)
if (failed) process.exitCode = 1
