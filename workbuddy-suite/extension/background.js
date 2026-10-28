/**
 * WorkBuddy Studio · Element Picker —— service worker。
 *
 * 它刻意**不是**一个独立的抓取工具：不存采集数据、不做回放、不自己跑流程。
 * 它只做三件事：
 *   1. 把 `content.js` 注入当前标签页（activeTab，按需，不常驻）；
 *   2. 把拾取结果 POST 给本地会话服务（Agent 正在那里等）；
 *   3. 用徽标告诉用户「Agent 在不在线 / 是不是正在等一个元素」。
 *
 * 这样设计的好处：用户关掉 Agent、关掉窗口，扩展就退化成一个小按钮，不会在后台
 * 悄悄采数据；而 Agent 那边拿到的是结构化选择器，不是一张截图。
 */

const DEFAULT_PORT = 7788
const HEALTH_TIMEOUT = 1200
const SEND_TIMEOUT = 4000
const RECENT_LIMIT = 8

const runtime = {
  port: DEFAULT_PORT,
  online: false,
  waiting: false,
  lastError: '',
}

async function loadConfig() {
  const stored = await chrome.storage.local.get({ port: DEFAULT_PORT })
  runtime.port = Number(stored.port) || DEFAULT_PORT
  return runtime.port
}

function endpoint(path) {
  return `http://127.0.0.1:${runtime.port}${path}`
}

async function fetchJson(path, { method = 'GET', body = null, timeout = SEND_TIMEOUT } = {}) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeout)
  try {
    const response = await fetch(endpoint(path), {
      method,
      body: body ? JSON.stringify(body) : null,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      signal: controller.signal,
      cache: 'no-store',
    })
    if (!response.ok) {
      return { ok: false, code: `HTTP_${response.status}`, error: `本地服务返回 ${response.status}` }
    }
    return await response.json()
  } catch (error) {
    const aborted = error?.name === 'AbortError'
    return {
      ok: false,
      code: aborted ? 'TIMEOUT' : 'SESSION_DOWN',
      error: aborted ? '本地会话超时' : '连不上本地会话（先运行 studio serve）',
    }
  } finally {
    clearTimeout(timer)
  }
}

function paintBadge() {
  const text = runtime.online ? (runtime.waiting ? '●' : 'ON') : 'OFF'
  const color = runtime.online ? (runtime.waiting ? '#d29922' : '#2ea043') : '#4b5563'
  chrome.action.setBadgeText({ text }).catch(() => {})
  chrome.action.setBadgeBackgroundColor({ color }).catch(() => {})
  const title = runtime.online
    ? runtime.waiting
      ? 'WorkBuddy Studio · Agent 正在等一个元素'
      : 'WorkBuddy Studio · 会话在线'
    : 'WorkBuddy Studio · 会话离线（运行 studio serve）'
  chrome.action.setTitle({ title }).catch(() => {})
}

async function refreshHealth() {
  const result = await fetchJson('/health', { timeout: HEALTH_TIMEOUT })
  runtime.online = Boolean(result?.ok)
  runtime.waiting = Number(result?.waiting?.capture || 0) > 0
  runtime.lastError = runtime.online ? '' : result?.error || ''
  paintBadge()
  return { ...runtime, version: result?.version || null }
}

async function injectPicker(tabId) {
  try {
    await chrome.scripting.executeScript({ target: { tabId, allFrames: false }, files: ['content.js'] })
    await chrome.tabs.sendMessage(tabId, { type: 'wb-start' }).catch(() => {})
    return { ok: true }
  } catch (error) {
    return { ok: false, code: 'INJECT_FAILED', error: String(error?.message || error) }
  }
}

async function pickFromActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true })
  if (!tab?.id) return { ok: false, code: 'NO_TAB', error: '没有可操作的标签页' }
  const blocked = /^(chrome|edge|about|chrome-extension|devtools):/i.test(tab.url || '')
  if (blocked) {
    return { ok: false, code: 'RESTRICTED_PAGE', error: '浏览器内部页面不允许注入脚本，换一个普通网页再试' }
  }
  return injectPicker(tab.id)
}

async function saveRecent(entry) {
  const stored = await chrome.storage.local.get({ recent: [] })
  const recent = [entry, ...(stored.recent || [])].slice(0, RECENT_LIMIT)
  await chrome.storage.local.set({ recent })
  return recent
}

chrome.runtime.onInstalled.addListener(async () => {
  await loadConfig()
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: 'wb-pick',
      title: '把此元素交给 WorkBuddy Agent',
      contexts: ['all'],
    })
  })
  chrome.alarms.create('wb-health', { periodInMinutes: 0.5 })
  refreshHealth()
})

chrome.runtime.onStartup.addListener(() => {
  loadConfig().then(refreshHealth)
})

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === 'wb-health') refreshHealth()
})

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId === 'wb-pick' && tab?.id) injectPicker(tab.id)
})

chrome.commands.onCommand.addListener((command) => {
  if (command === 'pick-element') pickFromActiveTab()
})

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message || typeof message !== 'object') return false

  if (message.type === 'wb-capture') {
    const payload = message.payload || {}
    ;(async () => {
      const result = await fetchJson('/capture', { method: 'POST', body: payload })
      const entry = {
        selector: payload.selector,
        text: payload.text || '',
        tag: payload.tag || '',
        at: Date.now(),
        ok: Boolean(result?.ok),
      }
      await saveRecent(entry)
      sendResponse({ ok: Boolean(result?.ok), ...result })
    })()
    return true // 异步响应
  }

  if (message.type === 'wb-status') {
    ;(async () => {
      const status = await refreshHealth()
      sendResponse({ ok: true, ...status })
    })()
    return true
  }

  if (message.type === 'wb-pick-here') {
    pickFromActiveTab().then(sendResponse)
    return true
  }

  return false
})
