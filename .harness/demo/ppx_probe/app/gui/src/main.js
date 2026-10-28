import { ppx } from 'ppx-js'
import './style.css'

const DEPTH = ['#0969da', '#1a7f37', '#9a6700', '#8250df', '#bc4c00', '#0598bc']
// 默认打开试验台副本，不碰 examples/ 里的既有流程
const DEMO_FLOW = '.harness/demo/ppx_probe/_sandbox/demo.json'

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
}

let commands = []
let flows = []
let currentPath = null
let cards = []
let selected = null // 当前选中的卡片
let fields = [] // 该节点的可编辑字段（服务端算的）
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
      row.addEventListener('click', () => appendDraft(c))
      el.cmds.append(row)
    }
  }
  el.cmdCount.textContent = `${list.length} 条`
}

function appendDraft(command) {
  if (!currentPath) return
  cards.push({
    kind: 'action',
    draft: true,
    no: cards.filter((c) => c.kind !== 'group').length + 1,
    depth: 0,
    command: command.id,
    label: command.label,
    summary: '（原型：仅演示插入观感，不改真实 AST）',
  })
  renderCards()
  el.cards.lastElementChild?.scrollIntoView({ block: 'nearest' })
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
    if (!card.draft) row.addEventListener('click', () => selectCard(card))
    el.cards.append(row)
  }
}

/* ---- 参数表单 ---------------------------------------------------------- */

function currentValues() {
  const values = {}
  for (const field of fields) {
    const node = el.form.querySelector(`[data-key="${CSS.escape(field.key)}"]`)
    if (node) values[field.key] = node.value
  }
  return values
}

function isDirty() {
  if (!fields.length) return false
  const values = currentValues()
  return fields.some((field) => (values[field.key] ?? field.valueText) !== field.valueText)
}

function refreshDirty() {
  const dirty = isDirty()
  el.dirty.textContent = dirty ? '● 未保存' : ''
  el.save.disabled = !dirty
  el.revert.disabled = !dirty
}

function buildControl(field) {
  const wrap = mk('div', 'field')
  const row = mk('div', 'labelRow')
  row.append(mk('span', 'key', field.key))
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
    const known = new Set(field.enum.map((o) => String(o.value)))
    if (!known.has(String(field.value))) {
      const option = document.createElement('option')
      option.value = String(field.value)
      option.textContent = `${field.value}（当前值，不在枚举内）`
      control.append(option)
    }
    for (const option of field.enum) {
      const item = document.createElement('option')
      item.value = String(option.value)
      item.textContent = option.label
      control.append(item)
    }
    control.value = String(field.value)
  } else if (field.type === 'boolean') {
    control = document.createElement('select')
    for (const text of ['true', 'false']) {
      const item = document.createElement('option')
      item.value = text
      item.textContent = text === 'true' ? '是（true）' : '否（false）'
      control.append(item)
    }
    control.value = String(field.value)
  } else if (field.type === 'array' || field.type === 'object') {
    control = document.createElement('textarea')
    control.value = field.valueText
    control.spellcheck = false
  } else {
    control = document.createElement('input')
    control.type = 'text'
    control.value = field.valueText
    control.spellcheck = false
  }
  control.dataset.key = field.key
  control.addEventListener('input', refreshDirty)
  control.addEventListener('change', refreshDirty)
  wrap.append(control)
  if (field.description) wrap.append(mk('div', 'desc', field.description))
  return wrap
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
          ? `${selected.label} 没有可改的参数（文件里 with 为空）`
          : `${selected.label} 是结构节点，S5 只改 action 参数`,
      ),
    )
  } else {
    for (const field of fields) el.form.append(buildControl(field))
  }
  refreshDirty()
}

async function selectCard(card) {
  if (isDirty() && !window.confirm('当前改动还没保存，切换会丢弃。继续？')) return
  selected = card
  formError = null
  renderCards()
  const t0 = performance.now()
  try {
    const res = await ppx.call('rpa.nodeDetail', { path: currentPath, node_id: card.id })
    el.rpcMs.textContent = `${(performance.now() - t0).toFixed(1)} ms`
    fields = res.fields || []
    baseline = Object.fromEntries(fields.map((f) => [f.key, f.valueText]))
    renderForm()
    el.detail.textContent = JSON.stringify(res.node, null, 2)
  } catch (error) {
    fields = []
    renderForm()
    el.detail.textContent = `${error.code}: ${error.message}`
  }
}

async function saveParams() {
  if (!selected || !fields.length) return
  const params = currentValues()
  el.save.disabled = true
  el.revert.disabled = true
  const t0 = performance.now()
  try {
    const res = await ppx.call('rpa.saveNodeParams', {
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
    const detail = await ppx.call('rpa.nodeDetail', { path: currentPath, node_id: keepId })
    fields = detail.editable ? detail.fields : []
    baseline = Object.fromEntries(fields.map((f) => [f.key, f.valueText]))
    renderCards()
    renderForm()
    renderCompile(res.compile)
    const bytes = res.changed ? `${res.bytes[0]} → ${res.bytes[1]} B` : '内容未变，未写盘'
    el.status.textContent = `保存 ${selected ? selected.label : keepId} · ${bytes} · sha ${res.sha} · ${(performance.now() - t0).toFixed(0)} ms`
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
    formError = null
    renderForm()
    el.detail.textContent = ''
    el.rpcMs.textContent = ''
    el.title.replaceChildren(
      mk('span', null, flow.name),
      mk('small', null, `${path} · ${flow.cards.length} 行 · sha ${flow.sha} · ${(performance.now() - t0).toFixed(1)} ms`),
    )
    renderCards()
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
