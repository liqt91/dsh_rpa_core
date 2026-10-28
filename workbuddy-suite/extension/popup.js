/* 弹窗只做「看一眼 + 一个动作」：状态、拾取、最近结果。
   没有任何采集列表、没有回放 —— 它不是一个独立工具。 */

const el = {
  status: document.querySelector('#status'),
  pick: document.querySelector('#pick'),
  hint: document.querySelector('#hint'),
  port: document.querySelector('#port'),
  portSave: document.querySelector('#portSave'),
  recentList: document.querySelector('#recentList'),
  clear: document.querySelector('#clear'),
}

function send(message) {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage(message, (response) => {
      resolve(chrome.runtime.lastError ? { ok: false, error: chrome.runtime.lastError.message } : response)
    })
  })
}

function paintStatus(status) {
  const online = Boolean(status?.online)
  const waiting = Boolean(status?.waiting)
  el.status.className = `pill${online ? (waiting ? ' waiting' : ' online') : ''}`
  el.status.textContent = online ? (waiting ? 'Agent 在等元素' : '会话在线') : '会话离线'
  el.hint.textContent = online
    ? waiting
      ? 'Agent 已经进入等待状态，点下面的按钮，再在页面上点目标元素。'
      : '在页面上点一个元素，选择器会直接交给正在等待的 Agent。'
    : '连不上本地会话。先让 Agent 运行 studio serve，再回来。'
}

function paintRecent(items) {
  el.recentList.replaceChildren()
  if (!items || !items.length) {
    const empty = document.createElement('li')
    empty.className = 'empty'
    empty.textContent = '还没有记录'
    el.recentList.append(empty)
    return
  }
  for (const item of items) {
    const row = document.createElement('li')
    if (!item.ok) row.className = 'bad'
    const code = document.createElement('code')
    code.textContent = item.selector
    code.title = `${item.tag || ''} ${item.text || ''}`
    const when = document.createElement('span')
    when.textContent = new Date(item.at).toLocaleTimeString('zh-CN', { hour12: false })
    row.append(code, when)
    row.addEventListener('click', async () => {
      await navigator.clipboard.writeText(item.selector)
      when.textContent = '已复制'
    })
    el.recentList.append(row)
  }
}

async function refresh() {
  const status = await send({ type: 'wb-status' })
  paintStatus(status)
  const stored = await chrome.storage.local.get({ port: 7788 })
  el.port.value = stored.port
}

el.pick.addEventListener('click', async () => {
  const result = await send({ type: 'wb-pick-here' })
  if (!result?.ok) {
    el.hint.textContent = `注入失败：${result?.error || result?.code || '未知原因'}`
    return
  }
  window.close()
})

el.portSave.addEventListener('click', async () => {
  const port = Number(el.port.value)
  if (!Number.isInteger(port) || port < 1024 || port > 65535) {
    el.hint.textContent = '端口要在 1024–65535 之间'
    return
  }
  await chrome.storage.local.set({ port })
  const status = await send({ type: 'wb-status' })
  paintStatus(status)
  el.hint.textContent = status?.online ? `已切到端口 ${port}，会话在线` : `已切到端口 ${port}，但连不上`
})

el.clear.addEventListener('click', async () => {
  await chrome.storage.local.set({ recent: [] })
  paintRecent([])
})

chrome.storage.onChanged.addListener((changes) => {
  if (changes.recent) paintRecent(changes.recent.newValue)
})

refresh()
chrome.storage.local.get({ recent: [] }).then((stored) => paintRecent(stored.recent))

// 弹窗打开期间每 2 秒刷一次状态：Agent 开始等待时这里要立刻变色。
setInterval(refresh, 2000)
