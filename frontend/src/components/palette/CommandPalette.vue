<template>
  <div class="palette-panel">
    <div class="palette-header">
      <span class="palette-title">指令</span>
    </div>
    <div class="palette-search">
      <span class="search-icon">🔍</span>
      <input
        class="search-input"
        v-model="filter"
        placeholder="搜索指令"
      />
    </div>
    <div class="palette-list">
      <div v-if="filteredGroups.length === 0" class="palette-empty">
        无匹配指令
      </div>
      <div v-for="group in filteredGroups" :key="group.label" class="palette-group">
        <div
          class="group-header"
          @click="group.open = !group.open"
        >
          <span class="group-icon">{{ group.icon }}</span>
          <span class="group-label">{{ group.label }}</span>
          <span class="group-arrow" :class="{ open: group.open }">›</span>
        </div>
        <div v-show="group.open" class="group-items">
          <div
            v-for="cmd in group.items"
            :key="cmd.id"
            class="command-item"
            draggable="true"
            @dragstart="onDragStart($event, cmd)"
          >
            <span class="cmd-icon" :style="{ background: group.color + '15', color: group.color }">
              {{ group.iconText }}
            </span>
            <div class="cmd-info">
              <span class="cmd-name">{{ cmd.name }}</span>
              <span class="cmd-id">{{ cmd.id }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useWorkflowStore } from '../../stores/workflow'

const store = useWorkflowStore()
const filter = ref('')

const GROUPS = [
  {
    label: '浏览器操作',
    icon: '🌐',
    iconText: '🌐',
    color: '#3b82f6',
    open: true,
    filter: (cmd) => cmd.id.startsWith('browser.') && !cmd.id.includes('Element'),
  },
  {
    label: '浏览器元素操作',
    icon: '🎯',
    iconText: '🎯',
    color: '#06b6d4',
    open: false,
    filter: (cmd) => cmd.id.includes('Element') || cmd.id === 'browser.click' || cmd.id === 'browser.input' || cmd.id === 'browser.getText' || cmd.id === 'browser.select' || cmd.id === 'browser.hover',
  },
  {
    label: '变量及日志',
    icon: '📝',
    iconText: '📝',
    color: '#10b981',
    open: false,
    filter: (cmd) => cmd.id.startsWith('data.'),
  },
  {
    label: 'AI 调用',
    icon: '🤖',
    iconText: '🤖',
    color: '#ec4899',
    open: false,
    filter: (cmd) => cmd.id.includes('ai') || cmd.id.includes('gpt'),
  },
  {
    label: '循环',
    icon: '🔄',
    iconText: '🔄',
    color: '#f59e0b',
    open: false,
    filter: (cmd) => cmd.id.includes('forEach') || cmd.id.includes('loop'),
  },
  {
    label: '条件判断',
    icon: '❓',
    iconText: '❓',
    color: '#8b5cf6',
    open: false,
    filter: (cmd) => cmd.id.includes('if') || cmd.id.includes('condition'),
  },
  {
    label: '文件处理',
    icon: '📁',
    iconText: '📁',
    color: '#06b6d4',
    open: false,
    filter: (cmd) => cmd.id.includes('file') || cmd.id.includes('upload') || cmd.id.includes('download'),
  },
  {
    label: '异常处理',
    icon: '⚠️',
    iconText: '⚠️',
    color: '#ef4444',
    open: false,
    filter: (cmd) => cmd.id.includes('try') || cmd.id.includes('catch'),
  },
  {
    label: '桌面操作',
    icon: '🖥️',
    iconText: '🖥️',
    color: '#8b5cf6',
    open: true,
    filter: (cmd) => cmd.id.startsWith('desktop.'),
  },
  {
    label: 'data',
    icon: '📊',
    iconText: '📊',
    color: '#10b981',
    open: false,
    filter: (cmd) => cmd.id.startsWith('data.'),
  },
]

const CONTROL_NODES = [
  { id: '__sequence', name: '顺序执行', iconText: '≡', isControl: true, type: 'sequence', color: '#64748b' },
  { id: '__if', name: '条件分支', iconText: '❓', isControl: true, type: 'if', color: '#8b5cf6' },
  { id: '__forEach', name: '循环', iconText: '🔄', isControl: true, type: 'forEach', color: '#f59e0b' },
  { id: '__try', name: '异常捕获', iconText: '⚠️', isControl: true, type: 'try', color: '#ef4444' },
]

const grouped = computed(() => {
  const groups = []
  const controlGroup = {
    label: '控制流',
    icon: '⚙️',
    iconText: '⚙️',
    color: '#64748b',
    open: true,
    items: CONTROL_NODES,
  }
  if (!filter.value || '控制流'.includes(filter.value.toLowerCase())) {
    groups.push(controlGroup)
  }
  for (const groupDef of GROUPS) {
    const items = store.catalog
      .filter(cmd => groupDef.filter(cmd))
      .map(cmd => ({
        id: cmd.id,
        name: store.commandName(cmd.id),
        iconText: groupDef.iconText,
        color: groupDef.color,
      }))
    if (items.length > 0) {
      groups.push({ ...groupDef, items })
    }
  }
  return groups
})

const filteredGroups = computed(() => {
  if (!filter.value) return grouped.value
  const q = filter.value.toLowerCase()
  return grouped.value
    .map(g => ({
      ...g,
      items: g.items.filter(i => i.id.toLowerCase().includes(q) || i.name.includes(q)),
    }))
    .filter(g => g.items.length > 0)
})

function onDragStart(e, cmd) {
  if (cmd.isControl) {
    e.dataTransfer.setData('application/x-control-type', cmd.type)
  } else {
    e.dataTransfer.setData('application/x-command', cmd.id)
  }
  e.dataTransfer.effectAllowed = 'copy'
}
</script>

<style scoped>
.palette-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--bg-secondary);
}
.palette-header {
  padding: 12px 16px 8px;
}
.palette-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}
.palette-search {
  padding: 0 12px 12px;
  position: relative;
}
.search-icon {
  position: absolute;
  left: 22px;
  top: 50%;
  transform: translateY(-50%);
  font-size: 12px;
  color: var(--text-muted);
}
.search-input {
  width: 100%;
  height: 32px;
  padding: 0 10px 0 30px;
  font-size: 12px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-surface);
  color: var(--text-primary);
}
.search-input::placeholder {
  color: var(--text-muted);
}
.palette-list {
  flex: 1;
  overflow-y: auto;
  padding: 0 8px 8px;
}
.palette-empty {
  font-size: 12px;
  color: var(--text-muted);
  text-align: center;
  padding: 16px;
}
.palette-group {
  margin-bottom: 4px;
}
.group-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px;
  border-radius: 6px;
  cursor: pointer;
  user-select: none;
}
.group-header:hover {
  background: var(--bg-hover);
}
.group-icon {
  font-size: 14px;
}
.group-label {
  flex: 1;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
}
.group-arrow {
  font-size: 14px;
  color: var(--text-muted);
  transition: transform 0.2s;
}
.group-arrow.open {
  transform: rotate(90deg);
}
.group-items {
  padding: 0 0 4px 8px;
}
.command-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-radius: 6px;
  cursor: grab;
  margin-bottom: 2px;
}
.command-item:hover {
  background: var(--bg-hover);
}
.command-item:active {
  cursor: grabbing;
  background: var(--bg-active);
}
.cmd-icon {
  width: 28px;
  height: 28px;
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  flex-shrink: 0;
}
.cmd-info {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.cmd-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.cmd-id {
  font-size: 11px;
  color: var(--text-muted);
  font-family: Consolas, monospace;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
