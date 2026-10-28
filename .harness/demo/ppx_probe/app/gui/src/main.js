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

function mk(tag, cls, text) {
  const n = document.createElement(tag)
  if (cls) n.className = cls
  if (text != null) n.textContent = text
  return n
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
      el.status.textContent = `插入失败 ${res.code}：${res.error}`
      return
    }
    cards = res.flow.cards
    const placed = res.placed
    renderCards()
    renderCompile(res.compile)
    el.status.textContent = `插入 ${command.label} → ${placed.parent}/${placed.branch}[${placed.index}] · ${res.bytes[0]}→${res.bytes[1]} B · ${res.ms} ms（anchor ${placed.anchor || 'root'}）`
    // 新节点 with 是空的（与 Qt 一致），参数全未设置 ⇒ 直接跳到它，省一次点击
    const fresh = cards.find((c) => c.id === res.newNodeId)
    if (fresh) await selectCard(fresh)
  } catch (error) {
    el.status.textContent = `${error.code}: ${error.message}`
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
      el.status.textContent = `删除失败 ${res.code}：${res.error}`
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
    el.status.textContent = `删除 ${res.removed.id}${extra} · ${res.bytes[0]}→${res.bytes[1]} B · ${res.ms} ms`
  } catch (error) {
    el.status.textContent = `${error.code}: ${error.message}`
  }
}

function renderCards() {
  el.cards.replaceChildren()
  for (const card of cards) {
    if (card.kind === 'group') {
      el.cards.append(mk('div', 'card group', card.label))
      continue
    }
    const row = mk(
      'div',
      'card' + (card.draft ? ' draft' : '') + (selected && card.id === selected.id ? ' selected' : ''),
    )
    row.style.setProperty('--depth', DEPTH[card.depth % DEPTH.length])
    row.append(mk('span', 'grip', '⋮'), mk('span', 'no', String(card.no)), mk('span', 'name', card.label))
    if (card.summary) row.append(mk('span', 'sum', card.summary))
    if (card.command) row.append(mk('span', 'badge', card.command.split('.')[0]))
    if (!card.draft) {
      const del = mk('button', 'del', '×')
      del.title = '删除这个节点（容器会连同里面的一起删）'
      del.addEventListener('click', (event) => {
        event.stopPropagation()
        removeCard(card)
      })
      row.append(del)
      row.addEventListener('click', () => selectCard(card))
    }
    el.cards.append(row)
  }
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
  renderCards()
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
    el.status.textContent = `保存 ${selected ? selected.label : keepId} · ${bytes}${delta}${warned} · sha ${res.sha} · ${(performance.now() - t0).toFixed(0)} ms`
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
      mk('small', null, `${path} · ${flow.cards.length} 行 · sha ${flow.sha} · ${(performance.now() - t0).toFixed(1)} ms`),
    )
    renderCards()
    // 打开流程后直接落到第一张可编辑卡片：右栏空着会让用户以为「这东西没参数」
    const firstEditable = cards.find((card) => card.kind !== 'group' && card.id)
    if (firstEditable) await selectCard(firstEditable)
  } catch (error) {
    el.title.replaceChildren(mk('span', null, `打开失败：${path}`))
    el.cards.replaceChildren(mk('div', 'card group', `${error.code}: ${error.message}`))
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

el.save.addEventListener('click', saveParams)
el.revert.addEventListener('click', revertParams)

el.search.addEventListener('input', () => {
  const q = el.search.value.trim().toLowerCase()
  const list = q ? commands.filter((c) => c.id.toLowerCase().includes(q) || c.label.includes(q)) : commands
  renderCommands(list)
})

async function boot() {
  const t0 = performance.now()
  try {
    const info = await ppx.call('rpa.bootstrap')
    commands = info.commands
    flows = info.flows
    el.status.textContent = `catalog ${info.commands.length} 条 · digest ${info.catalogDigest} · Python ${info.python} · 首帧→数据 ${(performance.now() - t0).toFixed(0)} ms（catalog ${info.catalogMs} ms）`
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
    el.status.textContent = `${error.code}: ${error.message}`
  }
}

boot()
