<template>
  <footer class="status-bar">
    <div class="status-left">
      <span class="status-item" v-if="workflowName">
        <span class="status-icon">📄</span>
        {{ workflowName }}
      </span>
      <span class="status-item dirty" v-if="dirty">
        <span class="status-dot"></span>
        未保存
      </span>
    </div>
    <div class="status-center">
      <span class="status-item" v-if="nodeCount > 0">
        共 {{ nodeCount }} 个节点
      </span>
      <span class="status-item" v-if="selectedNode">
        选中: {{ selectedLabel }}
      </span>
    </div>
    <div class="status-right">
      <span class="status-item hint">
        Ctrl+Z 撤销 | Delete 删除 | 右键菜单
      </span>
    </div>
  </footer>
</template>

<script setup>
import { computed } from 'vue'
import { useWorkflowStore } from '../../stores/workflow'

const store = useWorkflowStore()

const workflowName = computed(() => store.workflowName || store.workflow?.name || '')
const dirty = computed(() => store.dirty)

const nodeCount = computed(() => {
  if (!store.workflow?.root) return 0
  let count = 0
  const walk = (node) => {
    count++
    const lists = []
    if (Array.isArray(node.children)) lists.push(node.children)
    if (Array.isArray(node.then)) lists.push(node.then)
    if (Array.isArray(node.else)) lists.push(node.else)
    if (Array.isArray(node.catch)) lists.push(node.catch)
    for (const list of lists) {
      for (const child of list) walk(child)
    }
  }
  walk(store.workflow.root)
  return count
})

const selectedNode = computed(() => {
  if (!store.selected) return null
  return store.findNode(store.selected)
})

const selectedLabel = computed(() => {
  if (!selectedNode.value) return ''
  const n = selectedNode.value
  if (n.type === 'action') return store.commandName(n.command)
  const labels = { sequence: '顺序', if: '条件', forEach: '循环', try: '异常', return: '返回' }
  return labels[n.type] || n.type
})
</script>

<style scoped>
.status-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 28px;
  padding: 0 12px;
  background: var(--bg-secondary);
  border-top: 1px solid var(--border);
  font-size: 11px;
  color: var(--text-muted);
  flex-shrink: 0;
}
.status-left, .status-center, .status-right {
  display: flex;
  align-items: center;
  gap: 12px;
}
.status-item {
  display: flex;
  align-items: center;
  gap: 4px;
}
.status-icon {
  font-size: 12px;
}
.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--bad);
}
.dirty .status-dot {
  animation: pulse 1.5s infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}
.hint {
  color: var(--text-muted);
}
</style>
