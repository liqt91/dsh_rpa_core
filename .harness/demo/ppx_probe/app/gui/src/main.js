import { ppx } from 'ppx-js'
import './style.css'

const DEPTH = ['#0969da', '#1a7f37', '#9a6700', '#8250df', '#bc4c00', '#0598bc']
const DEMO_FLOW = 'workflows/test11/workflow.json'

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
}

let commands = []
let flows = []
let currentPath = null
let cards = []
let selectedId = null

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
    const row = mk('div', 'card' + (card.draft ? ' draft' : '') + (card.id === selectedId ? ' selected' : ''))
    row.style.setProperty('--depth', DEPTH[card.depth % DEPTH.length])
    row.append(mk('span', 'grip', '⋮'), mk('span', 'no', String(card.no)), mk('span', 'name', card.label))
    if (card.summary) row.append(mk('span', 'sum', card.summary))
    if (card.command) row.append(mk('span', 'badge', card.command.split('.')[0]))
    if (!card.draft) row.addEventListener('click', () => selectCard(card))
    el.cards.append(row)
  }
}

async function selectCard(card) {
  selectedId = card.id
  renderCards()
  const t0 = performance.now()
  try {
    const res = await ppx.call('rpa.nodeDetail', { path: currentPath, node_id: card.id })
    el.rpcMs.textContent = `${(performance.now() - t0).toFixed(1)} ms`
    el.detail.textContent = JSON.stringify(res.node, null, 2)
  } catch (error) {
    el.detail.textContent = `${error.code}: ${error.message}`
  }
}

async function openFlow(path) {
  const t0 = performance.now()
  try {
    const flow = await ppx.call('rpa.openFlow', { path })
    currentPath = path
    cards = flow.cards
    selectedId = null
    el.detail.textContent = '点击中间卡片查看节点 AST'
    el.rpcMs.textContent = ''
    el.title.replaceChildren(
      mk('span', null, flow.name),
      mk('small', null, `${path} · ${flow.cards.length} 行 · ${(performance.now() - t0).toFixed(1)} ms`),
    )
    renderCards()
  } catch (error) {
    el.title.replaceChildren(mk('span', null, `打开失败：${path}`))
    el.cards.replaceChildren(mk('div', 'card group', `${error.code}: ${error.message}`))
  }
}

function renderCompile(result) {
  el.compileOut.replaceChildren()
  if (result.ok) {
    el.compileOut.append(mk('div', 'ok', `编译通过 · ${result.ms} ms`))
    el.compileOut.append(
      mk('pre', null, `workflow  ${result.workflowId}\ndigest    ${result.digest}\ncaps      ${result.capabilities.length ? result.capabilities.join(', ') : '（无）'}`),
    )
  } else {
    el.compileOut.append(mk('div', 'bad', `编译失败 · ${result.ms} ms`))
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
