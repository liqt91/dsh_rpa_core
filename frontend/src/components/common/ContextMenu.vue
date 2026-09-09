<template>
  <div v-if="visible" class="context-menu" :style="{ left: x + 'px', top: y + 'px' }" @click.stop ref="menuRef">
    <div class="menu-item" @click="emit('insert-before')">
      <span class="menu-icon">⬆️</span> 在前插入
    </div>
    <div class="menu-item" @click="emit('insert-after')">
      <span class="menu-icon">⬇️</span> 在后插入
    </div>
    <div class="menu-item" @click="emit('insert-inside')">
      <span class="menu-icon">➡️</span> 插入内部
    </div>
    <div class="menu-divider"></div>
    <div class="menu-item" @click="emit('copy')">
      <span class="menu-icon">📋</span> 复制 <span class="menu-shortcut">Ctrl+C</span>
    </div>
    <div class="menu-item" @click="emit('cut')">
      <span class="menu-icon">✂️</span> 剪切 <span class="menu-shortcut">Ctrl+X</span>
    </div>
    <div class="menu-item" @click="emit('paste')">
      <span class="menu-icon">📌</span> 粘贴 <span class="menu-shortcut">Ctrl+V</span>
    </div>
    <div class="menu-divider"></div>
    <div class="menu-item danger" @click="emit('delete')">
      <span class="menu-icon">🗑️</span> 删除 <span class="menu-shortcut">Del</span>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'

const props = defineProps({
  visible: Boolean,
  x: Number,
  y: Number,
  nodeId: String,
})

const emit = defineEmits(['insert-before', 'insert-after', 'insert-inside', 'copy', 'cut', 'paste', 'delete', 'close'])
const menuRef = ref(null)

function onDocClick(e) {
  if (menuRef.value && !menuRef.value.contains(e.target)) {
    emit('close')
  }
}

onMounted(() => document.addEventListener('click', onDocClick))
onUnmounted(() => document.removeEventListener('click', onDocClick))
</script>

<style scoped>
.context-menu {
  position: fixed;
  z-index: 1000;
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 4px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);
  min-width: 180px;
}
.menu-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border-radius: 6px;
  font-size: 13px;
  color: var(--text-primary);
  cursor: pointer;
}
.menu-item:hover {
  background: var(--bg-hover);
}
.menu-item.danger {
  color: var(--bad);
}
.menu-item.danger:hover {
  background: rgba(239, 68, 68, 0.08);
}
.menu-icon {
  font-size: 14px;
  width: 20px;
  text-align: center;
}
.menu-shortcut {
  margin-left: auto;
  font-size: 11px;
  color: var(--text-muted);
  font-family: Consolas, monospace;
}
.menu-divider {
  height: 1px;
  background: var(--border);
  margin: 4px 8px;
}
</style>
