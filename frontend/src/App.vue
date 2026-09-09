<script setup>
import { onMounted, ref } from 'vue'
import { useWorkflowStore } from './stores/workflow'
import { useKeyboard } from './composables/useKeyboard'
import MainLayout from './components/layout/MainLayout.vue'

const store = useWorkflowStore()
useKeyboard()

const showWorkflowList = ref(false)

onMounted(async () => {
  await store.loadCatalog()
  await store.loadWorkflowList()
  // 尝试加载最近的工作流
  if (store.workflowList.length > 0) {
    await store.loadWorkflow(store.workflowList[store.workflowList.length - 1])
  } else {
    store.newWorkflow()
  }
})

function openWorkflow(name) {
  store.loadWorkflow(name)
  showWorkflowList.value = false
}

function createNew() {
  store.newWorkflow()
  showWorkflowList.value = false
}
</script>

<template>
  <MainLayout />
  <!-- 工作流选择弹窗 -->
  <Teleport to="body">
    <div v-if="showWorkflowList" class="modal-overlay" @click="showWorkflowList = false">
      <div class="modal-content" @click.stop>
        <div class="modal-header">
          <h3>选择工作流</h3>
          <button class="modal-close" @click="showWorkflowList = false">✕</button>
        </div>
        <div class="modal-body">
          <div class="workflow-new" @click="createNew">
            <span class="new-icon">📄</span>
            <span>新建工作流</span>
          </div>
          <div
            v-for="name in store.workflowList"
            :key="name"
            class="workflow-item"
            :class="{ active: name === store.workflowName }"
            @click="openWorkflow(name)"
          >
            <span class="wf-icon">📋</span>
            <span class="wf-name">{{ name }}</span>
          </div>
          <div v-if="!store.workflowList.length" class="empty-hint">
            暂无工作流
          </div>
        </div>
      </div>
    </div>
  </Teleport>
  <!-- 浮动按钮 -->
  <button class="fab-workflows" @click="showWorkflowList = true" title="工作流列表">
    📋
  </button>
</template>

<style scoped>
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 10000;
}
.modal-content {
  background: #fff;
  border-radius: 12px;
  width: 360px;
  max-height: 480px;
  overflow: hidden;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2);
}
.modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid var(--border);
}
.modal-header h3 {
  margin: 0;
  font-size: 15px;
}
.modal-close {
  width: 24px;
  height: 24px;
  border: none;
  background: transparent;
  cursor: pointer;
  font-size: 14px;
  color: var(--muted);
  border-radius: 4px;
}
.modal-close:hover {
  background: #f0f0f0;
}
.modal-body {
  overflow-y: auto;
  padding: 8px;
}
.workflow-new {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border: 2px dashed var(--border);
  border-radius: 8px;
  cursor: pointer;
  margin-bottom: 8px;
  font-size: 13px;
  color: var(--muted);
}
.workflow-new:hover {
  border-color: var(--accent);
  color: var(--accent);
}
.new-icon {
  font-size: 16px;
}
.workflow-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border-radius: 8px;
  cursor: pointer;
  font-size: 13px;
}
.workflow-item:hover {
  background: #f5f5f5;
}
.workflow-item.active {
  background: #e0ecff;
  color: var(--accent);
  font-weight: 500;
}
.wf-icon {
  font-size: 14px;
}
.wf-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.empty-hint {
  text-align: center;
  padding: 24px;
  color: #ccc;
  font-size: 13px;
}
.fab-workflows {
  position: fixed;
  bottom: 40px;
  left: 16px;
  width: 44px;
  height: 44px;
  border-radius: 50%;
  border: none;
  background: #fff;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
  font-size: 20px;
  cursor: pointer;
  z-index: 100;
  display: flex;
  align-items: center;
  justify-content: center;
}
.fab-workflows:hover {
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
}
</style>
