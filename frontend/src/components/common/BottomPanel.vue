<template>
  <div class="bottom-panel" :style="{ height: panelHeight + 'px' }">
    <div
      class="resize-handle"
      @mousedown="startResize"
    ></div>
    <div class="panel-header" @click="isCollapsed = !isCollapsed">
      <div class="panel-tabs">
        <button
          v-for="tab in tabs"
          :key="tab.id"
          class="tab-btn"
          :class="{ active: activeTab === tab.id }"
          @click.stop="activeTab = tab.id"
        >
          <span class="tab-icon">{{ tab.icon }}</span>
          {{ tab.label }}
        </button>
      </div>
      <div class="panel-actions">
        <span class="panel-count" v-if="tabCount">{{ tabCount }}</span>
        <button class="collapse-btn" @click.stop="isCollapsed = !isCollapsed">
          {{ isCollapsed ? '↑' : '↓' }}
        </button>
      </div>
    </div>
    <div class="panel-content" v-show="!isCollapsed">
      <div v-if="activeTab === 'elements'" class="tab-content">
        <ElementsPanel />
      </div>
      <div v-if="activeTab === 'data'" class="tab-content">
        <div class="empty-tab">数据表格功能开发中</div>
      </div>
      <div v-if="activeTab === 'logs'" class="tab-content">
        <LogPanel />
      </div>
      <div v-if="activeTab === 'params'" class="tab-content">
        <ParamsPanel />
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onUnmounted } from 'vue'
import { useWorkflowStore } from '../../stores/workflow'
import LogPanel from '../log/LogPanel.vue'
import ElementsPanel from './ElementsPanel.vue'
import ParamsPanel from './ParamsPanel.vue'

const store = useWorkflowStore()
const isCollapsed = ref(false)
const activeTab = ref('logs')
const panelHeight = ref(200)
const isResizing = ref(false)

const tabs = [
  { id: 'elements', label: '元素库', icon: '🎯' },
  { id: 'data', label: '数据表格', icon: '📊' },
  { id: 'logs', label: '运行日志', icon: '📝' },
  { id: 'params', label: '流程参数', icon: '⚙️' },
]

const tabCount = computed(() => {
  if (activeTab.value === 'logs') return store.runLog.length
  if (activeTab.value === 'elements') return 0
  return null
})

let startY = 0
let startHeight = 0

function startResize(e) {
  isResizing.value = true
  startY = e.clientY
  startHeight = panelHeight.value
  document.addEventListener('mousemove', onResize)
  document.addEventListener('mouseup', stopResize)
  document.body.classList.add('resizing-panel')
}

function onResize(e) {
  const delta = startY - e.clientY
  panelHeight.value = Math.max(80, Math.min(500, startHeight + delta))
}

function stopResize() {
  isResizing.value = false
  document.removeEventListener('mousemove', onResize)
  document.removeEventListener('mouseup', stopResize)
  document.body.classList.remove('resizing-panel')
}

onUnmounted(() => {
  document.removeEventListener('mousemove', onResize)
  document.removeEventListener('mouseup', stopResize)
})
</script>

<style scoped>
.bottom-panel {
  background: var(--bg-secondary);
  border-top: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  position: relative;
  flex-shrink: 0;
}
.resize-handle {
  position: absolute;
  top: -4px;
  left: 0;
  right: 0;
  height: 8px;
  cursor: row-resize;
  z-index: 10;
}
.resize-handle:hover {
  background: var(--accent);
  opacity: 0.5;
}
.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 40px;
  padding: 0 12px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}
.panel-tabs {
  display: flex;
  gap: 4px;
}
.tab-btn {
  height: 28px;
  padding: 0 12px;
  font-size: 12px;
  font-weight: 500;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--text-muted);
  display: flex;
  align-items: center;
  gap: 6px;
}
.tab-btn:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}
.tab-btn.active {
  background: var(--bg-card);
  color: var(--text-primary);
}
.tab-icon {
  font-size: 12px;
}
.panel-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
.panel-count {
  font-size: 11px;
  background: var(--accent);
  color: #fff;
  padding: 2px 6px;
  border-radius: 10px;
}
.collapse-btn {
  width: 24px;
  height: 24px;
  padding: 0;
  font-size: 12px;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: var(--text-muted);
}
.collapse-btn:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}
.panel-content {
  flex: 1;
  overflow: hidden;
}
.tab-content {
  height: 100%;
}
.empty-tab {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: var(--text-muted);
  font-size: 13px;
}
</style>
