<template>
  <div class="log-panel">
    <div class="log-list" ref="logList">
      <div v-if="!logs.length" class="log-empty">暂无日志</div>
      <div
        v-for="(log, i) in logs"
        :key="i"
        class="log-item"
        :class="log.level"
      >
        <span class="log-time">{{ log.time }}</span>
        <span class="log-level" :class="log.level">{{ levelIcon(log.level) }}</span>
        <span class="log-node" v-if="log.nodeId" @click="selectNode(log.nodeId)">
          {{ shortId(log.nodeId) }}
        </span>
        <span class="log-msg">{{ log.message }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, nextTick } from 'vue'
import { useWorkflowStore } from '../../stores/workflow'

const store = useWorkflowStore()
const logList = ref(null)

const logs = store.runLog

function levelIcon(level) {
  const icons = { info: 'ℹ', warn: '⚠', error: '✕', success: '✓' }
  return icons[level] || '·'
}

function shortId(id) {
  if (!id) return ''
  return id.length > 12 ? id.slice(0, 12) + '…' : id
}

function selectNode(id) {
  store.selectNode(id)
}

watch(() => logs.length, async () => {
  await nextTick()
  if (logList.value) {
    logList.value.scrollTop = logList.value.scrollHeight
  }
})
</script>

<style scoped>
.log-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
}
.log-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
  font-size: 12px;
  font-family: Consolas, monospace;
}
.log-empty {
  color: var(--text-muted);
  text-align: center;
  padding: 16px;
}
.log-item {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 4px 8px;
  border-radius: 4px;
  margin-bottom: 2px;
}
.log-item:hover {
  background: var(--bg-hover);
}
.log-item.error {
  color: var(--bad);
}
.log-item.warn {
  color: var(--warn);
}
.log-item.success {
  color: var(--ok);
}
.log-time {
  color: var(--text-muted);
  flex-shrink: 0;
}
.log-level {
  flex-shrink: 0;
  width: 16px;
  text-align: center;
}
.log-level.error {
  color: var(--bad);
}
.log-level.warn {
  color: var(--warn);
}
.log-node {
  color: var(--accent);
  cursor: pointer;
  flex-shrink: 0;
}
.log-node:hover {
  text-decoration: underline;
}
.log-msg {
  flex: 1;
  word-break: break-all;
  color: var(--text-secondary);
}
</style>
