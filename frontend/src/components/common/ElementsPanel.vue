<template>
  <div class="elements-panel">
    <div class="elements-header">
      <span class="elements-count">{{ elements.length }} 个元素</span>
      <div class="elements-actions">
        <button class="action-btn" @click="captureElement">🎯 捕获元素</button>
      </div>
    </div>
    <div class="elements-list">
      <div v-if="!elements.length" class="empty-elements">
        暂无元素，点击"捕获元素"添加
      </div>
      <div
        v-for="el in elements"
        :key="el.id"
        class="element-item"
        :class="{ active: selectedElement === el.id }"
        @click="selectedElement = el.id"
      >
        <span class="el-icon">🎯</span>
        <span class="el-name">{{ el.name || el.id }}</span>
      </div>
    </div>
    <div class="elements-detail" v-if="selectedElement">
      <div class="detail-header">元素详情</div>
      <div class="detail-content">
        <pre>{{ JSON.stringify(getElement(selectedElement), null, 2) }}</pre>
      </div>
    </div>
    <div class="elements-detail empty-detail" v-else>
      选择左侧元素查看详情
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'

const elements = ref([])
const selectedElement = ref(null)

function getElement(id) {
  return elements.value.find(el => el.id === id) || null
}

function captureElement() {
  // TODO: 调用扩展 API 捕获元素
  alert('捕获元素功能需要浏览器扩展支持')
}
</script>

<style scoped>
.elements-panel {
  display: flex;
  height: 100%;
}
.elements-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  border-bottom: 1px solid var(--border);
}
.elements-count {
  font-size: 12px;
  color: var(--text-muted);
}
.elements-actions {
  display: flex;
  gap: 8px;
}
.action-btn {
  height: 28px;
  padding: 0 12px;
  font-size: 12px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-surface);
  color: var(--text-primary);
}
.action-btn:hover {
  background: var(--bg-hover);
}
.elements-list {
  width: 200px;
  border-right: 1px solid var(--border);
  overflow-y: auto;
  padding: 8px;
}
.empty-elements {
  font-size: 12px;
  color: var(--text-muted);
  text-align: center;
  padding: 16px;
}
.element-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
}
.element-item:hover {
  background: var(--bg-hover);
}
.element-item.active {
  background: rgba(59, 130, 246, 0.1);
  color: var(--accent);
}
.el-icon {
  font-size: 14px;
}
.el-name {
  flex: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.elements-detail {
  flex: 1;
  display: flex;
  flex-direction: column;
  padding: 12px;
}
.detail-header {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
  margin-bottom: 8px;
}
.detail-content {
  flex: 1;
  overflow: auto;
}
.detail-content pre {
  font-size: 11px;
  font-family: Consolas, monospace;
  color: var(--text-secondary);
  margin: 0;
}
.empty-detail {
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-muted);
  font-size: 13px;
}
</style>
