import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { i18n } from '../i18n'

// 容器节点的子列表键
const CONTAINER_LISTS = {
  sequence: ['children'],
  if: ['then', 'else'],
  forEach: ['children'],
  try: ['children', 'catch'],
}

export const useWorkflowStore = defineStore('workflow', () => {
  // --- State ---
  const workflow = ref(null)
  const workflowName = ref(null) // 当前工作流名称（用于 API 调用）
  const workflowList = ref([]) // 工作流列表
  const selected = ref(null)
  const multi = ref([])
  const dirty = ref(false)
  const catalog = ref([])
  const undoStack = ref([])
  const redoStack = ref([])
  const clipboard = ref(null)
  const clipboardMode = ref(null) // 'copy' | 'cut'

  // --- Execution state ---
  const nodeStates = ref({}) // { [nodeId]: 'idle' | 'running' | 'done' | 'error' }
  const isRunning = ref(false)
  const runLog = ref([]) // [{ time, nodeId, level, message }]
  const currentNodeId = ref(null)
  const runId = ref(null)
  const runEvents = ref([])

  // --- Catalog ---
  async function loadCatalog() {
    const res = await fetch('/api/catalog')
    const data = await res.json()
    catalog.value = data.commands || []
  }

  function commandName(id) {
    return i18n.commands[id] || id
  }

  function manifestOf(commandId) {
    return catalog.value.find(c => c.id === commandId)
  }

  // --- Workflow List ---
  async function loadWorkflowList() {
    try {
      const res = await fetch('/api/workflows')
      const data = await res.json()
      workflowList.value = data.workflows || []
    } catch (e) {
      console.error('加载工作流列表失败:', e)
      workflowList.value = []
    }
  }

  async function loadWorkflow(name) {
    try {
      const res = await fetch(`/api/workflows/${encodeURIComponent(name)}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      workflow.value = data
      workflowName.value = name
      selected.value = null
      multi.value = []
      dirty.value = false
      undoStack.value = []
      redoStack.value = []
      resetNodeStates()
      return true
    } catch (e) {
      console.error('加载工作流失败:', e)
      return false
    }
  }

  async function saveWorkflow(name) {
    const target = name || workflowName.value
    if (!target || !workflow.value) return false
    try {
      const res = await fetch(`/api/workflows/${encodeURIComponent(target)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(workflow.value),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      workflowName.value = target
      dirty.value = false
      await loadWorkflowList()
      return true
    } catch (e) {
      console.error('保存工作流失败:', e)
      return false
    }
  }

  // --- Workflow ---
  function newWorkflow() {
    pushUndo()
    workflow.value = {
      schema_version: '1.0',
      id: 'untitled',
      name: '未命名',
      inputs: {},
      timeout_seconds: 3600,
      root: { type: 'sequence', id: 'root_seq', children: [] },
    }
    selected.value = null
    multi.value = []
    dirty.value = false
  }

  // --- Node operations ---
  function appendNode(commandId, targetId) {
    if (!workflow.value) newWorkflow()
    pushUndo()
    const node = createNode(commandId)
    if (targetId) {
      const target = findNode(targetId)
      if (target && isContainer(target)) {
        getListForAppend(target).push(node)
      }
    } else {
      getListForAppend(workflow.value.root).push(node)
    }
    dirty.value = true
    selected.value = node.id
    return node
  }

  function removeNode(id) {
    if (!workflow.value) return
    const parent = findParent(workflow.value.root, id)
    if (!parent) return
    pushUndo()
    const list = getListForChild(parent, id)
    if (list) {
      const idx = list.findIndex(n => n.id === id)
      if (idx !== -1) list.splice(idx, 1)
    }
    if (selected.value === id) selected.value = null
    multi.value = multi.value.filter(m => m !== id)
    dirty.value = true
  }

  function moveNode(nodeId, targetId, position) {
    // position: 'before' | 'after' | 'inside'
    if (!workflow.value) return
    const node = findNode(nodeId)
    const target = findNode(targetId)
    if (!node || !target || nodeId === targetId) return
    // 不能移动到自己的子树中
    if (position === 'inside' && isDescendant(targetId, nodeId)) return
    pushUndo()
    // 从原位置移除
    const parent = findParent(workflow.value.root, nodeId)
    if (parent) {
      const list = getListForChild(parent, nodeId)
      if (list) {
        const idx = list.findIndex(n => n.id === nodeId)
        if (idx !== -1) list.splice(idx, 1)
      }
    }
    // 插入到新位置
    if (position === 'inside') {
      const targetNode = findNode(targetId)
      if (targetNode && isContainer(targetNode)) {
        getListForAppend(targetNode).push(node)
      }
    } else {
      const targetParent = findParent(workflow.value.root, targetId)
      if (targetParent) {
        const list = getListForChild(targetParent, targetId)
        if (list) {
          const idx = list.findIndex(n => n.id === targetId)
          if (position === 'before') {
            list.splice(idx, 0, node)
          } else {
            list.splice(idx + 1, 0, node)
          }
        }
      }
    }
    dirty.value = true
  }

  function findNode(id) {
    if (!workflow.value?.root) return null
    return findNodeInTree(workflow.value.root, id)
  }

  function findParent(node, childId) {
    const lists = []
    if (Array.isArray(node.children)) lists.push(node.children)
    if (Array.isArray(node.then)) lists.push(node.then)
    if (Array.isArray(node.else)) lists.push(node.else)
    if (Array.isArray(node.catch)) lists.push(node.catch)
    for (const list of lists) {
      for (const child of list) {
        if (child.id === childId) return node
        const found = findParent(child, childId)
        if (found) return found
      }
    }
    return null
  }

  function selectNode(id, ctrlKey = false) {
    if (ctrlKey) {
      const idx = multi.value.indexOf(id)
      if (idx === -1) {
        multi.value.push(id)
      } else {
        multi.value.splice(idx, 1)
      }
    } else {
      selected.value = id
      multi.value = []
    }
  }

  function isContainer(node) {
    return ['sequence', 'if', 'forEach', 'try'].includes(node.type)
  }

  function isDescendant(parentId, childId) {
    const parent = findNode(parentId)
    if (!parent) return false
    const check = (node) => {
      if (node.id === childId) return true
      const lists = []
      if (Array.isArray(node.children)) lists.push(node.children)
      if (Array.isArray(node.then)) lists.push(node.then)
      if (Array.isArray(node.else)) lists.push(node.else)
      if (Array.isArray(node.catch)) lists.push(node.catch)
      for (const list of lists) {
        for (const child of list) {
          if (check(child)) return true
        }
      }
      return false
    }
    return check(parent)
  }

  // --- Clipboard ---
  function deepClone(node) {
    return JSON.parse(JSON.stringify(node))
  }

  function copyNode(id) {
    const node = findNode(id)
    if (!node) return
    clipboard.value = deepClone(node)
    clipboardMode.value = 'copy'
  }

  function cutNode(id) {
    const node = findNode(id)
    if (!node) return
    clipboard.value = deepClone(node)
    clipboardMode.value = 'cut'
    removeNode(id)
  }

  function pasteNode(targetId) {
    if (!clipboard.value) return
    if (!workflow.value) newWorkflow()
    pushUndo()
    const cloned = deepClone(clipboard.value)
    // 重新分配 ID 避免冲突
    reassignIds(cloned)
    if (targetId) {
      const target = findNode(targetId)
      if (target && isContainer(target)) {
        getListForAppend(target).push(cloned)
      }
    } else if (selected.value) {
      const target = findNode(selected.value)
      if (target && isContainer(target)) {
        getListForAppend(target).push(cloned)
      } else {
        // 在选中节点之后插入
        const parent = findParent(workflow.value.root, selected.value)
        if (parent) {
          const list = getListForChild(parent, selected.value)
          if (list) {
            const idx = list.findIndex(n => n.id === selected.value)
            list.splice(idx + 1, 0, cloned)
          }
        }
      }
    } else {
      getListForAppend(workflow.value.root).push(cloned)
    }
    dirty.value = true
    selected.value = cloned.id
    // 剪切模式下清空剪贴板
    if (clipboardMode.value === 'cut') {
      clipboard.value = null
      clipboardMode.value = null
    }
  }

  function reassignIds(node) {
    node.id = uniqueId(node.type || 'node')
    const lists = []
    if (Array.isArray(node.children)) lists.push(node.children)
    if (Array.isArray(node.then)) lists.push(node.then)
    if (Array.isArray(node.else)) lists.push(node.else)
    if (Array.isArray(node.catch)) lists.push(node.catch)
    for (const list of lists) {
      for (const child of list) {
        reassignIds(child)
      }
    }
  }

  function getListForAppend(node) {
    if (node.type === 'sequence') return node.children
    if (node.type === 'forEach') return node.children
    if (node.type === 'try') return node.children
    if (node.type === 'if') return node.then
    return node.children || (node.children = [])
  }

  function getListForChild(parent, childId) {
    const keys = CONTAINER_LISTS[parent.type] || []
    for (const key of keys) {
      if (Array.isArray(parent[key])) {
        if (parent[key].some(n => n.id === childId)) return parent[key]
      }
    }
    return null
  }

  // --- Undo/Redo ---
  function pushUndo() {
    if (!workflow.value) return
    undoStack.value.push(JSON.parse(JSON.stringify(workflow.value)))
    if (undoStack.value.length > 50) undoStack.value.shift()
    redoStack.value = []
  }

  function undo() {
    if (undoStack.value.length === 0) return
    redoStack.value.push(JSON.parse(JSON.stringify(workflow.value)))
    workflow.value = undoStack.value.pop()
    selected.value = null
  }

  function redo() {
    if (redoStack.value.length === 0) return
    undoStack.value.push(JSON.parse(JSON.stringify(workflow.value)))
    workflow.value = redoStack.value.pop()
    selected.value = null
  }

  // --- Helpers ---
  function createControlNode(type) {
    const templates = {
      sequence: { type: 'sequence', id: uniqueId('seq'), children: [] },
      if: { type: 'if', id: uniqueId('if'), condition: '', then: [], else: [] },
      forEach: { type: 'forEach', id: uniqueId('forEach'), items: [], item_var: 'item', children: [] },
      try: { type: 'try', id: uniqueId('try'), children: [], catch: [], error_var: 'error' },
      return: { type: 'return', id: uniqueId('return'), value: '' },
    }
    return templates[type] || { type: 'sequence', id: uniqueId('seq'), children: [] }
  }

  function appendControlNode(type, targetId) {
    if (!workflow.value) newWorkflow()
    pushUndo()
    const node = createControlNode(type)
    if (targetId) {
      const target = findNode(targetId)
      if (target && isContainer(target)) {
        getListForAppend(target).push(node)
      }
    } else {
      getListForAppend(workflow.value.root).push(node)
    }
    dirty.value = true
    selected.value = node.id
    return node
  }

  function createNode(commandId) {
    const manifest = manifestOf(commandId)
    const kind = manifest?.kind || 'action'
    if (kind === 'lifecycle') {
      // 生命周期命令创建对应的容器
      if (commandId.includes('forEach') || commandId.includes('loop')) {
        return { type: 'forEach', id: uniqueId('forEach'), items: [], item_var: 'item', children: [] }
      }
    }
    return {
      type: 'action',
      id: uniqueId(commandId.split('.').pop()),
      command: commandId,
      with: withDefaults(commandId),
    }
  }

  function withDefaults(commandId) {
    const manifest = manifestOf(commandId)
    if (!manifest?.input_schema?.properties) return {}
    const result = {}
    for (const [key, schema] of Object.entries(manifest.input_schema.properties)) {
      if (schema.default !== undefined) result[key] = schema.default
    }
    return result
  }

  let _idCounter = 0
  function uniqueId(base) {
    _idCounter++
    return `${base}_${Date.now().toString(36)}_${_idCounter}`
  }

  function findNodeInTree(node, id) {
    if (node.id === id) return node
    const lists = []
    if (Array.isArray(node.children)) lists.push(node.children)
    if (Array.isArray(node.then)) lists.push(node.then)
    if (Array.isArray(node.else)) lists.push(node.else)
    if (Array.isArray(node.catch)) lists.push(node.catch)
    for (const list of lists) {
      for (const child of list) {
        const found = findNodeInTree(child, id)
        if (found) return found
      }
    }
    return null
  }

  // --- Execution ---
  function resetNodeStates() {
    nodeStates.value = {}
    runLog.value = []
    currentNodeId.value = null
    runId.value = null
    runEvents.value = []
  }

  function setNodeState(nodeId, state) {
    nodeStates.value[nodeId] = state
  }

  function addLog(nodeId, level, message) {
    runLog.value.push({
      time: new Date().toLocaleTimeString(),
      nodeId,
      level,
      message,
    })
  }

  // 真实运行：调用 API 启动执行，轮询事件
  async function simulateRun() {
    if (isRunning.value || !workflow.value?.root) return
    // 先保存工作流
    if (dirty.value && workflowName.value) {
      await saveWorkflow()
    }
    if (!workflowName.value) {
      addLog(null, 'error', '请先保存工作流')
      return
    }

    isRunning.value = true
    resetNodeStates()
    addLog(null, 'info', '启动工作流执行...')

    try {
      // 启动运行
      const res = await fetch('/api/runs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ workflowName: workflowName.value }),
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.message || `HTTP ${res.status}`)
      }
      const data = await res.json()
      runId.value = data.runId
      addLog(null, 'info', `运行已启动: ${runId.value.slice(0, 8)}...`)

      // 轮询事件
      await pollRunEvents()
    } catch (e) {
      addLog(null, 'error', `启动失败: ${e.message}`)
    } finally {
      isRunning.value = false
      currentNodeId.value = null
    }
  }

  async function pollRunEvents() {
    const rid = runId.value
    if (!rid) return

    let lastSeq = 0
    const maxWait = 300000 // 5 分钟超时
    const startTime = Date.now()

    while (isRunning.value && (Date.now() - startTime) < maxWait) {
      try {
        const res = await fetch(`/api/runs/${rid}/events`)
        if (!res.ok) break
        const data = await res.json()
        const events = data.events || []

        // 处理新事件
        for (const evt of events) {
          if (evt.seq <= lastSeq) continue
          lastSeq = evt.seq
          processEvent(evt)
        }

        // 检查是否已结束
        const lastEvt = events[events.length - 1]
        if (lastEvt?.type === 'runFinished') break

        // 等待 500ms 再轮询
        await sleep(500)
      } catch (e) {
        addLog(null, 'error', `轮询失败: ${e.message}`)
        break
      }
    }
  }

  function processEvent(evt) {
    const { type, node_id, payload } = evt

    switch (type) {
      case 'runStarted':
        addLog(null, 'info', `工作流开始执行`)
        break
      case 'stepStarted':
        setNodeState(node_id, 'running')
        currentNodeId.value = node_id
        addLog(node_id, 'info', `${commandName(payload.command)} 开始执行`)
        break
      case 'stepAttemptStarted':
        addLog(node_id, 'info', `第 ${payload.attempt} 次尝试 (超时 ${payload.timeout}s)`)
        break
      case 'stepCompleted':
        setNodeState(node_id, 'done')
        addLog(node_id, 'info', `执行成功`)
        break
      case 'stepFailed':
        setNodeState(node_id, 'error')
        addLog(node_id, 'error', `执行失败: ${payload.error || '未知错误'}`)
        break
      case 'runFinished':
        if (payload.status === 'succeeded') {
          addLog(null, 'info', '工作流执行完成')
        } else {
          addLog(null, 'error', `工作流执行失败: ${payload.error || '未知错误'}`)
        }
        break
    }
  }

  async function stopRun() {
    if (!runId.value) {
      isRunning.value = false
      return
    }
    try {
      await fetch(`/api/runs/${runId.value}/cancel`, { method: 'POST' })
      addLog(null, 'warn', '已发送停止请求')
    } catch (e) {
      addLog(null, 'error', `停止失败: ${e.message}`)
    }
    isRunning.value = false
  }

  function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms))
  }

  return {
    workflow, workflowName, workflowList, selected, multi, dirty, catalog, undoStack, redoStack, clipboard, clipboardMode,
    nodeStates, isRunning, runLog, currentNodeId, runId,
    loadCatalog, commandName, manifestOf,
    loadWorkflowList, loadWorkflow, saveWorkflow,
    newWorkflow, appendNode, appendControlNode, removeNode, moveNode,
    findNode, findParent, selectNode, isContainer,
    copyNode, cutNode, pasteNode,
    createControlNode, createActionNode: createNode,
    pushUndo, undo, redo,
    resetNodeStates, setNodeState, addLog, simulateRun, stopRun,
  }
})
