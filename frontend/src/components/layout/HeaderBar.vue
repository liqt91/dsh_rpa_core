<template>
  <header class="header-bar">
    <div class="header-left">
      <span class="brand-icon">⚡</span>
      <input
        class="workflow-name-input"
        v-model="workflowName"
        @blur="saveName"
        @keydown.enter="$event.target.blur()"
        placeholder="未命名工作流"
      />
    </div>
    <div class="header-center">
      <button class="header-btn" @click="doSave" title="保存">
        <span class="btn-icon">💾</span> 保存
      </button>
      <button class="header-btn" @click="doExportPython" title="导出 Python">
        <span class="btn-icon">🐍</span> 导出 Python
      </button>
      <button class="header-btn" @click="doExport" title="导出流程">
        <span class="btn-icon">📤</span> 导出流程
      </button>
      <button class="header-btn" @click="doImport" title="导入流程">
        <span class="btn-icon">📥</span> 导入流程
      </button>
      <span class="header-separator"></span>
      <span class="extension-status" :class="{ connected: extensionConnected }">
        <span class="status-dot"></span>
        {{ extensionConnected ? '扩展已连接' : '扩展未连接' }}
      </span>
      <span class="header-separator"></span>
      <button
        v-if="!store.isRunning"
        class="run-btn"
        @click="store.simulateRun()"
        :disabled="!store.workflow?.root?.children?.length"
      >
        <span class="btn-icon">▶</span> 运行
      </button>
      <button
        v-else
        class="run-btn stop"
        @click="store.stopRun()"
      >
        <span class="btn-icon">⏹</span> 停止
      </button>
    </div>
    <div class="header-right">
      <button class="header-btn ghost" @click="store.undo()" :disabled="!store.undoStack.length" title="撤销">
        <span class="btn-icon">↩</span>
      </button>
      <button class="header-btn ghost" @click="store.redo()" :disabled="!store.redoStack.length" title="重做">
        <span class="btn-icon">↪</span>
      </button>
    </div>
  </header>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useWorkflowStore } from '../../stores/workflow'

const store = useWorkflowStore()
const extensionConnected = ref(false)

const workflowName = computed({
  get: () => store.workflowName || '',
  set: (val) => {
    if (store.workflow) {
      store.workflow.name = val
    }
  }
})

function saveName() {
  // 名称会在保存时一并保存
}

async function doSave() {
  if (!store.workflow) return
  const name = store.workflowName || store.workflow.name
  if (!name) {
    const input = prompt('输入工作流名称:')
    if (!input) return
    await store.saveWorkflow(input)
  } else {
    await store.saveWorkflow()
  }
}

function doExport() {
  if (!store.workflow) return
  const blob = new Blob([JSON.stringify(store.workflow, null, 2)], { type: 'application/json' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = (store.workflowName || 'workflow') + '.json'
  a.click()
}

function doExportPython() {
  alert('导出 Python 功能开发中')
}

function doImport() {
  const input = document.createElement('input')
  input.type = 'file'
  input.accept = '.json'
  input.onchange = async (e) => {
    const file = e.target.files[0]
    if (!file) return
    try {
      const text = await file.text()
      const data = JSON.parse(text)
      store.workflow = data
      store.workflowName = file.name.replace('.json', '')
      store.dirty = true
    } catch (err) {
      alert('导入失败: ' + err.message)
    }
  }
  input.click()
}
</script>

<style scoped>
.header-bar {
  display: flex;
  align-items: center;
  height: var(--header-height);
  padding: 0 16px;
  background: var(--bg-primary);
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
  gap: 16px;
}
.header-left {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 200px;
}
.brand-icon {
  font-size: 20px;
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--accent);
  color: #fff;
  border-radius: 8px;
}
.workflow-name-input {
  background: transparent;
  border: 1px solid transparent;
  color: var(--text-primary);
  font-size: 14px;
  font-weight: 600;
  padding: 4px 8px;
  border-radius: 4px;
  width: 180px;
}
.workflow-name-input:hover {
  border-color: var(--border);
}
.workflow-name-input:focus {
  border-color: var(--accent);
  background: var(--bg-surface);
}
.header-center {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
}
.header-btn {
  height: 32px;
  padding: 0 12px;
  font-size: 12px;
  font-weight: 500;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-surface);
  color: var(--text-secondary);
  display: flex;
  align-items: center;
  gap: 6px;
}
.header-btn:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}
.header-btn.ghost {
  border: none;
  background: transparent;
}
.header-btn.ghost:hover {
  background: var(--bg-hover);
}
.btn-icon {
  font-size: 12px;
}
.header-separator {
  width: 1px;
  height: 20px;
  background: var(--border);
  margin: 0 4px;
}
.extension-status {
  font-size: 12px;
  color: var(--text-muted);
  display: flex;
  align-items: center;
  gap: 6px;
}
.extension-status.connected {
  color: var(--ok);
}
.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--bad);
}
.extension-status.connected .status-dot {
  background: var(--ok);
}
.run-btn {
  height: 34px;
  padding: 0 20px;
  font-size: 13px;
  font-weight: 600;
  border: none;
  border-radius: 8px;
  background: var(--ok);
  color: #fff;
  display: flex;
  align-items: center;
  gap: 6px;
}
.run-btn:hover:not(:disabled) {
  background: #059669;
}
.run-btn:disabled {
  opacity: 0.4;
  cursor: default;
}
.run-btn.stop {
  background: var(--bad);
}
.run-btn.stop:hover {
  background: #dc2626;
}
.header-right {
  display: flex;
  align-items: center;
  gap: 4px;
}
</style>
