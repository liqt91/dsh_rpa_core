import { ppx } from 'ppx-js'
import './style.css'

const DEPTH = ['#0969da', '#1a7f37', '#9a6700', '#8250df', '#bc4c00', '#0598bc']
// 默认打开试验台副本，不碰 examples/ 里的既有流程。
// 选 struct.json 而不是 baidu 的副本：它同时含 action / if / forEach / try / return，
// 四种节点形态都能在界面上点到（`struct_probe.py` 每次运行都会重建这份夹具）。
const DEMO_FLOW = '.harness/demo/ppx_probe/_sandbox/struct.json'

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
  elemCount: document.querySelector('#elemCount'),
}

let commands = []
let flows = []
let currentPath = null
let cards = []
let selected = null // 当前选中的卡片
let fields = [] // 该节点的可编辑字段（服务端算的，含未设置的声明字段）
let groups = [] // 分组定义（顺序 + 折叠），服务端给
let panelMode = 'param' // param | struct —— 决定保存走哪个 RPC
let baseline = {} // 首次渲染时的原文，用于判断是否改动 / 撤销
let formError = null
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
      row.addEventListener('click', () => insertCommand(c))
      el.cmds.append(row)
    }
  }
  el.cmdCount.textContent = `${list.length} 条`
}

/** 插入一个真节点。落点由服务端按「anchor 所属分支末尾」决定，前端只出 anchor。 */
async function insertCommand(command) {
  if (!currentPath) return
  const anchor = selected?.id || ''
  const t0 = performance.now()
  try {
    const res = await ppx.call('rpa.insertNode', {
      path: currentPath,
      command: command.id,
      anchor_id: anchor,
    })
    if (!res.ok) {
      setStatus(`插入失败 ${res.code}：${res.error}`)
      return
    }
    cards = res.flow.cards
    const placed = res.placed
    renderCards()
    renderCompile(res.compile)
    setStatus(`插入 ${command.label} → ${placed.parent}/${placed.branch}[${placed.index}] · ${res.bytes[0]}→${res.bytes[1]} B · ${res.ms} ms（anchor ${placed.anchor || 'root'}）`)
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
      const group = mk('div', 'card group')
      // 组行也要参与缩进与竖线，否则「则执行 / 否则 / 异常」会漂在父节点左边的同一列
      group.dataset.depth = String(depth)
      group.style.setProperty('--depth-n', String(depth))
      group.append(mk('span', 'gtag', '↳'), mk('span', 'gname', card.label))
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
    row.dataset.depth = String(depth)
    row.style.setProperty('--depth-n', String(depth))
    row.style.setProperty('--accent', DEPTH[depth % DEPTH.length])
    row.append(mk('span', 'no', String(card.no)), mk('span', 'name', card.label))
    if (card.summary) row.append(mk('span', 'sum', card.summary))
    if (card.command) row.append(mk('span', 'badge', card.command.split('.')[0]))
    if (missingNodeIds.has(card.id)) row.append(mk('span', 'cap', '⚠ 待补'))
    if (!card.draft) {
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

  let control
  if (field.enum.length) {
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
  const note = field.description || (!field.set && field.required ? '必填但当前未设置' : '')
  if (note) {
    const desc = mk('div', 'desc', note)
    // manifest 的 description 动辄四五句（browserType 那段 90 字），全铺开会把 340px 的
    // 侧栏撑成一大片留白。收成两行，全文进 tooltip——想读的人有地方读。
    desc.title = note
    wrap.append(desc)
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

function renderForm() {
  el.form.replaceChildren()
  if (formError) el.form.append(mk('div', 'formError', formError))
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
    baseline = Object.fromEntries(fields.map((f) => [f.key, f.valueText]))
    renderForm()
    el.detail.textContent = JSON.stringify(res.node ?? res, null, 2)
  } catch (error) {
    fields = []
    groups = []
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
    const res = await ppx.call(rpc, {
      path: currentPath,
      node_id: selected.id,
      params,
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
    baseline = Object.fromEntries(fields.map((f) => [f.key, f.valueText]))
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
  if (!fields.length) return
  for (const field of fields) {
    const node = el.form.querySelector(`[data-key="${CSS.escape(field.key)}"]`)
    if (node) node.value = baseline[field.key]
  }
  refreshDirty()
}

/* ---- 流程与编译 -------------------------------------------------------- */

async function openFlow(path) {
  const t0 = performance.now()
  try {
    const flow = await ppx.call('rpa.openFlow', { path })
    currentPath = path
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

/* ---- 元素资产（AI 生成工作流的待补元素 + 捕获入口） -----------------------
 * AI 生成的工作流把交互元素写成 elementRefs 占位（模型不知道真实选择器），运行期解析不到
 * 即 ELEMENT_REF_MISSING。这里把「缺哪个元素、在第几步」摊开，并接上 main 的
 * `rpa-core capture` 复用捕获器。捕获落盘即用同名 = 自动绑定（引用模型无需单独绑定步骤）。 */

// 用户可见文案的唯一来源（M66）。静态判据扫这张表的值：出现实现名词即判红。
// 动态内容一律用 {name} / {seconds} 占位，避免把拼接藏进源码里绕过判据。
const PICK_TEXT = {
  main: '补一下',
  mainHint: '补这个元素，系统自己判断怎么抓',
  alternate: '换个方式',
  alternateHint: '通常不用改；这次选的只对这一个元素生效',
  auto: '自动判断',
  page: '在网页上',
  app: '在桌面软件里',
  wherePage: '网页',
  whereApp: '桌面软件',
  checking: '检查环境…',
  ready: '准备好了，马上去点一下要用的元素',
  readyCount: '{seconds} 秒后自动开始',
  startNow: '现在开始',
  blocked: '现在还不能开始：{fix}',
  offlineHint: '浏览器还没连上，可以先手动填写',
  waiting: '等你点一下要用的元素',
  waitingDefault: '去点一下要用的元素',
  left: '还剩 {seconds} 秒',
  cancelled: '已取消，随时可以重来',
  timeout: '时间到了，没收到这次点选',
  failed: '这次没成：{reason}',
  bound: '第 {step} 步已就绪 · {name}',
  manual: '手动填写',
  manualHint: '不用点元素，直接写一个选择器（键盘也能用）',
  manualPlaceholder: '例如 #username',
  manualSubmit: '写好并绑定',
  manualDone: '已写好并绑定 · {name}',
  manualBad: '没写上：{reason}',
  cancel: '取消',
  retry: '再试一次',
  view: '查看这一步',
  none: '本流程没有元素，不用补',
  loadFail: '元素没加载出来：{reason}',
  headMissing: '待补元素',
  headDone: '已就绪',
  countDone: '{count} 已就绪',
  countMissing: '{count} 待补',
  stepFallback: '这一步',
}

// 状态符号：与文字、颜色三者并列，任何一条通道失效都还能读出状态。
const PICK_ICON = {
  checking: '◌', ready: '◉', waiting: '◌', bound: '✓',
  blocked: '!', cancelled: '–', timeout: '!', failed: '!',
}

function pickText(key, vars = {}) {
  return (PICK_TEXT[key] || key).replace(/\{(\w+)\}/g, (_, k) => String(vars[k] ?? ''))
}

let pickMissing = []
let pickElements = []
let pickCapabilities = null
let pickState = null // { name, path, override, state, step, nodeId, deadlineAt, hint, reason }
let pickTimer = null
let pickPollTimer = null

function stepOf(nodeId) {
  const card = cards.find((c) => c.id === nodeId)
  return card && card.no != null ? String(card.no) : PICK_TEXT.stepFallback
}

function whereOf(m) {
  const cmd = String(m.command || '')
  if (cmd.startsWith('browser.')) return PICK_TEXT.wherePage
  if (cmd.startsWith('desktop.')) return PICK_TEXT.whereApp
  return m.decision && m.decision.target === 'browser' ? PICK_TEXT.wherePage : PICK_TEXT.whereApp
}

async function refreshElements() {
  if (!currentPath) {
    el.elemBody.replaceChildren(mk('div', 'hint', pickText('none')))
    el.elemCount.textContent = ''
    return
  }
  try {
    const [miss, list] = await Promise.all([
      ppx.call('rpa.missingElements', { path: currentPath }),
      ppx.call('rpa.listElements', { path: currentPath }),
    ])
    pickMissing = miss.missing || []
    pickElements = list.elements || []
    pickCapabilities = miss.capabilities || null
    missingNodeIds = new Set(pickMissing.map((m) => m.nodeId).filter(Boolean))
    renderCards()
    renderElementsPanel()
  } catch (error) {
    el.elemBody.replaceChildren(
      mk('div', 'hint', pickText('loadFail', { reason: `${error.code}: ${error.message}` })),
    )
  }
}

function renderElementsPanel() {
  el.elemBody.replaceChildren()
  el.elemCount.textContent =
    `${pickText('countDone', { count: pickElements.length })} · ` +
    pickText('countMissing', { count: pickMissing.length })
  paintPickBar()
  if (!pickMissing.length && !pickElements.length) {
    el.elemBody.append(mk('div', 'hint', pickText('none')))
    return
  }
  if (pickMissing.length) {
    el.elemBody.append(mk('div', 'elemHead', pickText('headMissing')))
    for (const m of pickMissing) el.elemBody.append(pickRow(m))
  }
  if (pickElements.length) {
    el.elemBody.append(mk('div', 'elemHead', pickText('headDone')))
    for (const e of pickElements) {
      const row = mk('div', 'elemRow')
      row.append(mk('span', 'elemName', e.name))
      row.append(mk('span', 'elemMeta', `${e.kind || '?'} · ${e.selector || ''}`))
      el.elemBody.append(row)
    }
  }
}

function pickRow(m) {
  const row = mk('div', 'elemRow missing')
  row.dataset.pickName = m.name
  row.append(mk('span', 'elemName', m.name))
  row.append(mk('span', 'elemMeta', `第 ${stepOf(m.nodeId)} 步 · ${whereOf(m)}`))

  // 唯一主操作：怎么抓由后端 resolve_target 决定，界面不提供技术选择题。
  const main = mk('button', 'pickMain', pickText('main'))
  main.title = pickText('mainHint')
  main.addEventListener('click', () => startPickFlow(m, null))
  row.append(main)

  const alt = mk('details', 'pickAlt')
  alt.append(mk('summary', null, pickText('alternate')))
  const box = mk('div', 'pickAltBox')
  for (const [label, value] of [
    [pickText('auto'), 'auto'],
    [pickText('page'), 'browser'],
    [pickText('app'), 'desktop'],
  ]) {
    const btn = mk('button', 'pickAltBtn', label)
    btn.dataset.override = value
    btn.addEventListener('click', () => startPickFlow(m, value === 'auto' ? null : value))
    box.append(btn)
  }
  box.append(mk('div', 'pickHint', pickText('alternateHint')))
  box.append(manualForm(m.name))
  alt.append(box)
  row.append(alt)
  return row
}

function manualForm(name) {
  const wrap = mk('div', 'pickManualRow')
  wrap.append(mk('span', 'pickManualLabel', pickText('manual')))
  const input = mk('input', 'pickManualInput')
  input.placeholder = pickText('manualPlaceholder')
  input.setAttribute('aria-label', pickText('manualHint'))
  const submit = mk('button', 'pickManualSubmit', pickText('manualSubmit'))
  submit.addEventListener('click', () => submitManual(name, input.value))
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') submitManual(name, input.value)
  })
  wrap.append(input, submit)
  wrap.append(mk('span', 'pickManualHint', pickText('manualHint')))
  return wrap
}

function paintPickBar() {
  const bar = mk('div', 'pickBar')
  bar.setAttribute('role', 'status')
  const live = mk('div', 'pickLive')
  live.setAttribute('aria-live', 'polite')
  const detail = mk('div', 'pickDetail')
  const actions = mk('div', 'pickActions')

  if (!pickState) {
    if (!pickCapabilities || !pickCapabilities.fix) return
    bar.dataset.state = 'offline'
    live.append(mk('span', 'pickDot', '!'), mk('span', 'pickLiveText', pickText('offlineHint')))
    detail.textContent = pickCapabilities.fix
    el.elemBody.append(bar)
    bar.append(live, detail)
    return
  }

  const s = pickState
  bar.dataset.state = s.state
  live.append(
    mk('span', 'pickDot', PICK_ICON[s.state] || '·'),
    mk('span', 'pickLiveText', pickText(s.state, {
      name: s.name,
      step: s.step,
      fix: s.reason,
      reason: s.reason,
    })),
  )
  if (s.state === 'waiting' || s.state === 'ready') {
    detail.append(mk('span', 'pickHintText', s.hint || pickText('waitingDefault')))
    const seconds = mk('span', 'pickSeconds')
    detail.append(seconds)
  } else {
    detail.textContent = s.hint || ''
  }

  if (s.state === 'ready') {
    const go = mk('button', 'pickAction', pickText('startNow'))
    go.addEventListener('click', () => beginCapture())
    actions.append(go)
  }
  if (s.state === 'checking' || s.state === 'ready' || s.state === 'waiting') {
    const cancel = mk('button', 'pickAction', pickText('cancel'))
    cancel.addEventListener('click', () => cancelPick())
    actions.append(cancel)
  }
  if (s.state !== 'checking' && s.state !== 'bound') {
    const retry = mk('button', 'pickAction', pickText('retry'))
    retry.addEventListener('click', () => {
      const m = pickMissing.find((x) => x.name === s.name)
      if (m) startPickFlow(m, s.override)
    })
    actions.append(retry)
  }
  if (s.state === 'bound') {
    const view = mk('button', 'pickAction', pickText('view'))
    view.addEventListener('click', () => focusNode(s.nodeId))
    actions.append(view)
  }
  if (s.state !== 'bound') {
    const manual = mk('details', 'pickBarManual')
    manual.append(mk('summary', null, pickText('manual')))
    manual.append(manualForm(s.name))
    actions.append(manual)
  }
  bar.append(live, detail, actions)
  el.elemBody.append(bar)
  updateSeconds()
}

function updateSeconds() {
  const node = document.querySelector('.pickSeconds')
  if (!node || !pickState) return
  const end = pickState.state === 'ready' ? pickState.readyAt : pickState.deadlineAt
  if (!end) return
  const left = Math.max(0, Math.ceil(end - Date.now() / 1000))
  node.textContent = pickState.state === 'ready'
    ? pickText('readyCount', { seconds: left })
    : pickText('left', { seconds: left })
}

function setPickState(next) {
  pickState = next
  renderElementsPanel()
  if (pickTimer) clearInterval(pickTimer)
  pickTimer = null
  if (next && (next.state === 'ready' || next.state === 'waiting')) {
    pickTimer = setInterval(updateSeconds, 1000)
    if (next.state === 'ready') {
      setTimeout(() => {
        if (pickState && pickState.state === 'ready') beginCapture()
      }, 10000)
    }
  }
}

async function startPickFlow(m, override) {
  if (!currentPath) return
  if (pickState && ['checking', 'ready', 'waiting'].includes(pickState.state)) await cancelPick()
  const base = {
    name: m.name,
    path: currentPath,
    override,
    step: stepOf(m.nodeId),
    nodeId: m.nodeId,
    reason: null,
    hint: null,
  }
  setPickState({ ...base, state: 'checking' })
  let plan
  try {
    plan = await ppx.call('rpa.pickPlan', {
      path: currentPath,
      name: m.name,
      override,
    })
  } catch (error) {
    setPickState({ ...base, state: 'blocked', reason: error.message })
    return
  }
  pickCapabilities = plan.capabilities
  // ★ 能力闸在后端 pickPlan 算（与 startCapture 同一个函数），前端不重复判断，
  //   也不在能力不在线时进等待态让用户空等一分钟。
  if (plan.blocked) {
    setPickState({ ...base, state: 'blocked', reason: plan.fix || '' })
    return
  }
  setPickState({
    ...base,
    state: 'ready',
    hint: '',
    readyAt: Date.now() / 1000 + 10,
  })
}

async function beginCapture() {
  const s = pickState
  if (!s || s.state !== 'ready') return
  let res
  try {
    res = await ppx.call('rpa.startCapture', {
      path: s.path,
      name: s.name,
      override: s.override,
    })
  } catch (error) {
    setPickState({ ...s, state: 'failed', reason: error.message })
    return
  }
  if (!res.ok) {
    setPickState({ ...s, state: 'blocked', reason: res.fix || res.error || '' })
    return
  }
  setPickState({
    ...s,
    state: 'waiting',
    hint: res.instructions,
    nodeId: res.nodeId || s.nodeId,
    deadlineAt: res.deadlineAt,
  })
  pollCapture()
}

async function pollCapture() {
  if (pickPollTimer) clearTimeout(pickPollTimer)
  const tick = async () => {
    const s = pickState
    if (!s || s.state !== 'waiting') return
    let res
    try {
      res = await ppx.call('rpa.capturePoll', { path: s.path, name: s.name })
    } catch (error) {
      setPickState({ ...s, state: 'failed', reason: error.message })
      return
    }
    if (!res.ok) {
      setPickState({ ...s, state: 'failed', reason: res.error })
      return
    }
    if (res.state === 'bound') {
      setPickState({ ...s, state: 'bound', nodeId: res.boundNodeId || s.nodeId })
      focusNode(res.boundNodeId || s.nodeId)
      await refreshElements()
      return
    }
    if (res.state === 'waiting') {
      pickPollTimer = setTimeout(tick, 1000)
      return
    }
    setPickState({ ...s, state: res.state, reason: res.reason })
  }
  pickPollTimer = setTimeout(tick, 800)
}

async function cancelPick() {
  const s = pickState
  if (!s) return
  if (pickPollTimer) clearTimeout(pickPollTimer)
  pickPollTimer = null
  if (s.state === 'waiting') {
    try {
      await ppx.call('rpa.cancelCapture', { path: s.path, name: s.name })
    } catch {
      // 取消失败也要退出等待态，否则界面会一直假装在等。
    }
  }
  setPickState({ ...s, state: 'cancelled' })
}

async function submitManual(name, selector) {
  let res
  try {
    res = await ppx.call('rpa.writeElement', { path: currentPath, name, selector })
  } catch (error) {
    setPickState({ ...(pickState || {}), state: 'failed', name, reason: error.message })
    return
  }
  if (!res.ok) {
    setPickState({ ...(pickState || {}), state: 'failed', name, reason: res.error })
    return
  }
  if (pickState) setPickState({ ...pickState, state: 'bound', name, nodeId: res.boundNodeId })
  await refreshElements()
}

function focusNode(nodeId) {
  if (!nodeId) return
  const card = Array.from(document.querySelectorAll('.card')).find(
    (n) => n.dataset.nodeId === nodeId,
  )
  if (!card) return
  card.scrollIntoView({ block: 'nearest' })
  card.classList.add('pickFlash')
  setTimeout(() => card.classList.remove('pickFlash'), 1200)
}

document.addEventListener('keydown', (event) => {
  if (event.key !== 'Escape' || !pickState) return
  if (['checking', 'ready', 'waiting'].includes(pickState.state)) cancelPick()
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
