/**
 * 页面内的拾取器（由 background 按需注入，不常驻）。
 *
 * 真正的难点不是画个高亮框，而是**给出一个明天还能用的选择器**：
 *   - 优先 id / data-testid / aria-label 这类「人写给人看」的属性；
 *   - 类名只取像人写的（跳过 css-1x2y3z 这种构建产物）；
 *   - 再不行才退到 nth-of-type 路径，并如实标出「这个选择器脆」；
 *   - Shadow DOM 与 iframe 不能装作没看见 —— 选择器穿不过去就得说出来。
 *
 * 注入保护：全局只装一份，重复注入只做「重新开始」，不会叠出两个高亮框。
 */

(() => {
  if (window.__wbPicker) {
    window.__wbPicker.start()
    return
  }

  const STYLE = `
    :host { all: initial; }
    .box {
      position: fixed; z-index: 2147483646; pointer-events: none;
      border: 2px solid #4f8cff; border-radius: 3px;
      background: rgba(79, 140, 255, 0.14);
      box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.45), 0 6px 24px rgba(79, 140, 255, 0.25);
      transition: left 40ms linear, top 40ms linear, width 40ms linear, height 40ms linear;
    }
    .tag {
      position: fixed; z-index: 2147483647; pointer-events: none;
      max-width: 70vw; padding: 4px 8px; border-radius: 5px;
      background: #0e1116; color: #e6edf3; border: 1px solid #2a3444;
      font: 12px/1.45 "Cascadia Code", Consolas, monospace;
      white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }
    .hud {
      position: fixed; z-index: 2147483647; left: 50%; bottom: 26px;
      transform: translateX(-50%);
      display: flex; align-items: center; gap: 10px;
      padding: 9px 14px; border-radius: 999px;
      background: rgba(14, 17, 22, 0.94); color: #e6edf3;
      border: 1px solid #2a3444; box-shadow: 0 12px 32px rgba(0, 0, 0, 0.45);
      font: 13px/1.3 "Segoe UI", "Microsoft YaHei UI", sans-serif;
      pointer-events: none;
    }
    .hud b { color: #8ab4ff; font-weight: 600; }
    .hud .ok { color: #3fb950; }
  `

  const host = document.createElement('div')
  host.setAttribute('data-wb-picker', '')
  host.style.cssText = 'all: initial; position: fixed; inset: 0; pointer-events: none; z-index: 2147483645;'
  const shadow = host.attachShadow({ mode: 'open' })
  const style = document.createElement('style')
  style.textContent = STYLE
  const box = document.createElement('div')
  box.className = 'box'
  box.style.display = 'none'
  const tag = document.createElement('div')
  tag.className = 'tag'
  tag.style.display = 'none'
  const hud = document.createElement('div')
  hud.className = 'hud'
  hud.innerHTML = '点击目标元素 · <b>Esc</b> 取消 · 结果会直接交给 WorkBuddy Agent'
  shadow.append(style, box, tag, hud)

  let active = false
  let target = null
  let frame = 0

  const esc = (value) => (window.CSS && CSS.escape ? CSS.escape(value) : String(value).replace(/[^\w-]/g, (m) => `\\${m}`))

  function unique(selector) {
    try {
      return document.querySelectorAll(selector).length === 1
    } catch {
      return false
    }
  }

  function matchCount(selector) {
    try {
      return document.querySelectorAll(selector).length
    } catch {
      return -1
    }
  }

  /** 像是「人写出来的」类名：排除构建产物哈希、状态类、工具类。 */
  function stableClass(name) {
    if (!name || name.length > 32) return false
    if (/^(css|sc|jsx|emotion|styled)[-_]/i.test(name)) return false
    if (/^[_a-z]+-\d+$/i.test(name)) return false
    if (/[a-f0-9]{6,}/i.test(name) && !/^[a-z]+(-[a-z]+)+$/i.test(name)) return false
    if (/^(active|selected|hover|focus|disabled|open|show|hidden|visible)$/i.test(name)) return false
    return /^[A-Za-z][\w-]*$/.test(name)
  }

  function selectorFor(element) {
    const tagName = element.tagName.toLowerCase()

    if (element.id) {
      const candidate = `#${esc(element.id)}`
      if (unique(candidate)) return { selector: candidate, strategy: 'id' }
    }

    const attrs = ['data-testid', 'data-test-id', 'data-test', 'data-qa', 'data-cy', 'name', 'aria-label', 'placeholder', 'title', 'type']
    for (const name of attrs) {
      const value = element.getAttribute(name)
      if (!value || value.length > 80) continue
      const candidate = `${tagName}[${name}="${value.replace(/"/g, '\\"')}"]`
      if (unique(candidate)) return { selector: candidate, strategy: `attr:${name}` }
    }

    const classes = Array.from(element.classList || []).filter(stableClass)
    if (classes.length) {
      for (const count of [classes.length, 2, 1]) {
        const picked = classes.slice(0, count)
        if (!picked.length) continue
        const candidate = `${tagName}${picked.map((name) => `.${esc(name)}`).join('')}`
        if (unique(candidate)) return { selector: candidate, strategy: 'class' }
      }
    }

    const parts = []
    let node = element
    while (node && node.nodeType === 1 && node !== document.documentElement) {
      let part = node.tagName.toLowerCase()
      const parent = node.parentElement
      if (parent) {
        const sameTag = Array.from(parent.children).filter((child) => child.tagName === node.tagName)
        if (sameTag.length > 1) part += `:nth-of-type(${sameTag.indexOf(node) + 1})`
      }
      parts.unshift(part)
      const candidate = parts.join(' > ')
      // ★ 只有一层且只是个裸标签名（span / div / button）时不算数：
      //   整页碰巧只有一个 <span> 就返回 "span"，页面一改就废。
      //   要么至少两级，要么这一级本身就是带下标的精确定位。
      const specific = parts.length >= 2 || /:nth-of-type\(/.test(part)
      if (specific && unique(candidate)) return { selector: candidate, strategy: 'path' }
      node = parent
    }
    return { selector: parts.join(' > '), strategy: 'path' }
  }

  function describe(element) {
    const { selector, strategy } = selectorFor(element)
    const alternatives = []
    if (element.id) alternatives.push(`#${esc(element.id)}`)
    for (const name of ['data-testid', 'data-test', 'data-qa', 'name', 'aria-label']) {
      const value = element.getAttribute(name)
      if (value) alternatives.push(`${element.tagName.toLowerCase()}[${name}="${value.replace(/"/g, '\\"')}"]`)
    }
    const text = (element.innerText || element.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 120)
    const rect = element.getBoundingClientRect()
    const attributes = {}
    for (const name of element.getAttributeNames()) {
      if (name === 'style' || name === 'class') continue
      const value = element.getAttribute(name)
      if (value == null || value.length > 200) continue
      attributes[name] = value
    }
    const root = element.getRootNode()
    const shadow = root instanceof ShadowRoot
    return {
      selector,
      strategy,
      alternatives: alternatives
        .filter((item) => item !== selector)
        .map((item) => ({ selector: item, matches: matchCount(item) }))
        .slice(0, 4),
      matches: matchCount(selector),
      tag: element.tagName.toLowerCase(),
      text,
      shadow,
      iframe: false,
      rect: {
        x: Math.round(rect.left),
        y: Math.round(rect.top),
        w: Math.round(rect.width),
        h: Math.round(rect.height),
      },
      attributes,
      url: location.href,
      title: document.title,
      frame: 'top',
      source: 'extension',
    }
  }

  function paint() {
    frame = 0
    if (!active || !target) return
    const rect = target.getBoundingClientRect()
    box.style.display = 'block'
    box.style.left = `${rect.left}px`
    box.style.top = `${rect.top}px`
    box.style.width = `${rect.width}px`
    box.style.height = `${rect.height}px`

    const info = selectorFor(target)
    tag.style.display = 'block'
    tag.textContent = `${info.selector}  ·  ${Math.round(rect.width)}×${Math.round(rect.height)}`
    const above = rect.top > 26
    tag.style.left = `${Math.max(6, rect.left)}px`
    tag.style.top = above ? `${rect.top - 24}px` : `${rect.bottom + 6}px`
  }

  function onMove(event) {
    if (!active) return
    const next = event.target
    if (!next || next === target || next.nodeType !== 1) return
    target = next
    if (!frame) frame = requestAnimationFrame(paint)
  }

  function onKey(event) {
    if (event.key === 'Escape') {
      event.preventDefault()
      stop('已取消拾取')
    }
  }

  function onClick(event) {
    if (!active) return
    event.preventDefault()
    event.stopPropagation()
    event.stopImmediatePropagation()
    const element = event.target
    if (!element || element.nodeType !== 1) return stop()
    const payload = describe(element)
    stop()
    hud.innerHTML = `<span class="ok">✓</span> 已发送 ${payload.selector}`
    hud.style.display = 'flex'
    setTimeout(() => {
      hud.style.display = 'none'
    }, 2600)
    try {
      chrome.runtime.sendMessage({ type: 'wb-capture', payload }, (response) => {
        if (!response?.ok) {
          hud.innerHTML = `<span style="color:#f85149">✕</span> 没送到 Agent：${response?.error || '本地会话未连接'}`
          hud.style.display = 'flex'
          setTimeout(() => {
            hud.style.display = 'none'
          }, 4200)
        }
      })
    } catch (error) {
      hud.innerHTML = `<span style="color:#f85149">✕</span> 扩展上下文已失效，请刷新页面：${error}`
    }
  }

  function start() {
    if (active) return
    active = true
    target = null
    box.style.display = 'none'
    tag.style.display = 'none'
    hud.innerHTML = '点击目标元素 · <b>Esc</b> 取消 · 结果会直接交给 WorkBuddy Agent'
    hud.style.display = 'flex'
    document.documentElement.append(host)
    document.addEventListener('pointermove', onMove, true)
    document.addEventListener('click', onClick, true)
    document.addEventListener('keydown', onKey, true)
  }

  function stop(message) {
    if (!active) return
    active = false
    document.removeEventListener('pointermove', onMove, true)
    document.removeEventListener('click', onClick, true)
    document.removeEventListener('keydown', onKey, true)
    box.style.display = 'none'
    tag.style.display = 'none'
    if (message) {
      hud.innerHTML = message
      setTimeout(() => {
        hud.style.display = 'none'
      }, 1600)
    } else {
      hud.style.display = 'none'
    }
  }

  window.__wbPicker = { start, stop, selectorFor, describe }

  chrome.runtime.onMessage.addListener((message) => {
    if (message?.type === 'wb-start') start()
  })

  start()
})()
