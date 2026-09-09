<template>
  <div
    class="tree-node"
    :class="{
      selected: isSelected,
      'multi-selected': isMultiSelected,
      'drag-over-top': dragPosition === 'top',
      'drag-over-bottom': dragPosition === 'bottom',
      'drag-over-inside': dragPosition === 'inside',
    }"
  >
    <div
      class="node-card"
      @click.stop="onClick"
      @contextmenu.prevent="onContextMenu"
      draggable="true"
      @dragstart="onDragStart"
      @dragover.prevent="onDragOver"
      @dragleave="onDragLeave"
      @drop="onDrop"
    >
      <div class="node-drag-handle" v-if="!isRoot">⠿</div>
      <div class="node-index" :style="{ background: nodeColor + '15', color: nodeColor }">
        {{ index }}
      </div>
      <div class="node-icon" :style="{ background: nodeColor + '15', color: nodeColor }">
        {{ nodeIcon }}
      </div>
      <div class="node-content">
        <div class="node-title">{{ nodeLabel }}</div>
        <div class="node-summary" v-if="nodeSummary">{{ nodeSummary }}</div>
      </div>
      <div class="node-state" v-if="nodeState" :class="nodeState">
        {{ stateIcon }}
      </div>
      <label class="node-toggle" v-if="!isRoot" @click.stop>
        <input type="checkbox" checked />
        <span class="toggle-slider"></span>
      </label>
      <button class="node-delete" v-if="!isRoot" @click.stop="removeNode" title="删除">
        🗑️
      </button>
    </div>
    <div v-if="hasChildren" class="node-children">
      <TreeNode
        v-for="(child, i) in children"
        :key="child.id"
        :node="child"
        :depth="depth + 1"
        :index="i + 1"
        @contextmenu="onContextMenu"
      />
      <div
        v-if="children.length === 0"
        class="empty-container"
        :style="{ paddingLeft: (depth + 1) * 28 + 40 + 'px' }"
      >
        拖拽指令到此处
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useWorkflowStore } from '../../stores/workflow'

const props = defineProps({
  node: { type: Object, required: true },
  depth: { type: Number, default: 0 },
  index: { type: Number, default: 1 },
  isRoot: { type: Boolean, default: false },
})

const emit = defineEmits(['contextmenu'])

const store = useWorkflowStore()
const dragPosition = ref(null)

const isSelected = computed(() => store.selected === props.node.id)
const isMultiSelected = computed(() => store.multi.includes(props.node.id))
const nodeState = computed(() => store.nodeStates[props.node.id] || null)

const stateIcon = computed(() => {
  const icons = { running: '⟳', done: '✓', error: '✕' }
  return icons[nodeState.value] || ''
})

const nodeColor = computed(() => {
  const colors = {
    action: '#3b82f6',
    sequence: '#64748b',
    if: '#8b5cf6',
    forEach: '#f59e0b',
    try: '#ef4444',
    return: '#10b981',
  }
  if (props.node.type === 'action') {
    const cmd = props.node.command || ''
    if (cmd.startsWith('browser.')) return '#3b82f6'
    if (cmd.startsWith('desktop.')) return '#8b5cf6'
    if (cmd.startsWith('data.')) return '#10b981'
  }
  return colors[props.node.type] || '#64748b'
})

const nodeIcon = computed(() => {
  const icons = {
    action: '⚡',
    sequence: '≡',
    if: '❓',
    forEach: '🔄',
    try: '⚠️',
    return: '←',
  }
  if (props.node.type === 'action') {
    const cmd = props.node.command || ''
    if (cmd.startsWith('browser.')) return '🌐'
    if (cmd.startsWith('desktop.')) return '🖥️'
    if (cmd.startsWith('data.')) return '📊'
  }
  return icons[props.node.type] || '⚡'
})

const nodeLabel = computed(() => {
  if (props.node.type === 'action') {
    return store.commandName(props.node.command)
  }
  const labels = {
    sequence: '顺序执行',
    if: '条件分支',
    forEach: '循环',
    try: '异常捕获',
    return: '返回结果',
  }
  return labels[props.node.type] || props.node.type
})

const nodeSummary = computed(() => {
  if (props.node.type !== 'action') return ''
  const w = props.node.with || {}
  const parts = []
  if (w.url) parts.push(`网址: ${w.url}`)
  if (w.selector) parts.push(`选择器: ${w.selector}`)
  if (w.text) parts.push(`文本: ${w.text}`)
  if (w.command) parts.push(`命令: ${w.command}`)
  if (parts.length === 0) {
    const keys = Object.keys(w).filter(k => w[k] !== undefined && w[k] !== '')
    if (keys.length > 0) {
      return keys.slice(0, 2).map(k => {
        const v = w[k]
        if (typeof v === 'string' && v.length > 30) return v.slice(0, 30) + '…'
        return `${k}: ${v}`
      }).join(' | ')
    }
    return ''
  }
  return parts.join(' | ')
})

const hasChildren = computed(() => {
  return ['sequence', 'if', 'forEach', 'try'].includes(props.node.type)
})

const children = computed(() => {
  const n = props.node
  if (n.type === 'sequence') return n.children || []
  if (n.type === 'if') return [...(n.then || []), ...(n.else || [])]
  if (n.type === 'forEach') return n.children || []
  if (n.type === 'try') return [...(n.children || []), ...(n.catch || [])]
  return []
})

function onClick(e) {
  store.selectNode(props.node.id, e.ctrlKey || e.metaKey)
}

function onContextMenu(e) {
  store.selectNode(props.node.id)
  emit('contextmenu', { nodeId: props.node.id, x: e.clientX, y: e.clientY })
}

function removeNode() {
  store.removeNode(props.node.id)
}

function onDragStart(e) {
  e.dataTransfer.setData('application/x-node-id', props.node.id)
  e.dataTransfer.effectAllowed = 'move'
}

function onDragOver(e) {
  e.stopPropagation()
  const rect = e.target.closest('.node-card')?.getBoundingClientRect()
  if (!rect) return
  const y = e.clientY - rect.top
  const h = rect.height
  if (y < h * 0.25) {
    dragPosition.value = 'top'
  } else if (y > h * 0.75) {
    dragPosition.value = 'bottom'
  } else if (store.isContainer(props.node)) {
    dragPosition.value = 'inside'
  } else {
    dragPosition.value = 'bottom'
  }
}

function onDragLeave() {
  dragPosition.value = null
}

function onDrop(e) {
  e.stopPropagation()
  const nodeId = e.dataTransfer.getData('application/x-node-id')
  const cmdId = e.dataTransfer.getData('application/x-command')
  const controlType = e.dataTransfer.getData('application/x-control-type')

  if (nodeId && nodeId !== props.node.id) {
    store.moveNode(nodeId, props.node.id, dragPosition.value || 'after')
  } else if (cmdId || controlType) {
    const targetId = props.node.id
    if (dragPosition.value === 'inside' && store.isContainer(props.node)) {
      if (controlType) {
        store.appendControlNode(controlType, targetId)
      } else {
        store.appendNode(cmdId, targetId)
      }
    } else {
      const parent = store.findParent(store.workflow.root, targetId)
      if (parent) {
        const list = parent.children || parent.then || []
        const idx = list.findIndex(n => n.id === targetId)
        const newNode = controlType
          ? store.createControlNode(controlType)
          : store.createActionNode(cmdId)
        if (dragPosition.value === 'top') {
          list.splice(idx, 0, newNode)
        } else {
          list.splice(idx + 1, 0, newNode)
        }
        store.dirty = true
        store.selected = newNode.id
      }
    }
  }
  dragPosition.value = null
}
</script>

<style scoped>
.tree-node {
  user-select: none;
}
.node-card {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
  margin: 4px 0;
  background: var(--bg-primary);
  border-radius: var(--card-radius);
  border: 1px solid var(--border);
  cursor: pointer;
  transition: all 0.15s;
}
.node-card:hover {
  border-color: var(--border-light);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
}
.selected .node-card {
  border-color: var(--accent);
  background: rgba(59, 130, 246, 0.05);
}
.multi-selected .node-card {
  border-color: #8b5cf6;
  background: rgba(139, 92, 246, 0.05);
}
.node-drag-handle {
  color: var(--text-muted);
  cursor: grab;
  font-size: 14px;
  opacity: 0.5;
}
.node-drag-handle:hover {
  opacity: 1;
}
.node-drag-handle:active {
  cursor: grabbing;
}
.node-index {
  width: 24px;
  height: 24px;
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 600;
  flex-shrink: 0;
}
.node-icon {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  flex-shrink: 0;
}
.node-content {
  flex: 1;
  min-width: 0;
}
.node-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
  margin-bottom: 2px;
}
.node-summary {
  font-size: 11px;
  color: var(--text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.node-state {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 10px;
  flex-shrink: 0;
}
.node-state.running {
  color: var(--accent);
  animation: spin 1s linear infinite;
}
.node-state.done {
  background: rgba(16, 185, 129, 0.15);
  color: var(--ok);
}
.node-state.error {
  background: rgba(239, 68, 68, 0.15);
  color: var(--bad);
}
@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
.node-toggle {
  position: relative;
  width: 36px;
  height: 20px;
  flex-shrink: 0;
  cursor: pointer;
}
.node-toggle input {
  opacity: 0;
  width: 0;
  height: 0;
}
.toggle-slider {
  position: absolute;
  inset: 0;
  background: #d1d5db;
  border-radius: 20px;
  transition: 0.2s;
}
.toggle-slider::before {
  content: '';
  position: absolute;
  height: 16px;
  width: 16px;
  left: 2px;
  bottom: 2px;
  background: #fff;
  border-radius: 50%;
  transition: 0.2s;
}
.node-toggle input:checked + .toggle-slider {
  background: var(--ok);
}
.node-toggle input:checked + .toggle-slider::before {
  transform: translateX(16px);
}
.node-delete {
  width: 28px;
  height: 28px;
  padding: 0;
  font-size: 14px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--text-muted);
  opacity: 0;
  transition: opacity 0.15s;
}
.node-card:hover .node-delete {
  opacity: 1;
}
.node-delete:hover {
  background: rgba(239, 68, 68, 0.1);
  color: var(--bad);
}
.node-children {
  margin-left: 28px;
  padding-left: 20px;
  border-left: 2px solid var(--border);
}
.drag-over-top .node-card {
  border-top: 2px solid var(--accent);
}
.drag-over-bottom .node-card {
  border-bottom: 2px solid var(--accent);
}
.drag-over-inside .node-card {
  background: rgba(59, 130, 246, 0.05);
  border-color: var(--accent);
}
.empty-container {
  font-size: 12px;
  color: var(--text-muted);
  padding: 12px;
  font-style: italic;
}
</style>
