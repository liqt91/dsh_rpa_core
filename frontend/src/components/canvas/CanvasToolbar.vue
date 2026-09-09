<template>
  <div class="canvas-toolbar">
    <div class="toolbar-group">
      <button @click="zoomIn" title="放大" :disabled="scale >= 2">
        <span>🔍+</span>
      </button>
      <span class="zoom-label">{{ Math.round(scale * 100) }}%</span>
      <button @click="zoomOut" title="缩小" :disabled="scale <= 0.5">
        <span>🔍−</span>
      </button>
      <button @click="zoomReset" title="重置缩放">
        <span>1:1</span>
      </button>
    </div>
    <div class="toolbar-separator"></div>
    <div class="toolbar-group">
      <button @click="fitToView" title="适应视图">
        <span>⊞</span>
      </button>
      <button @click="collapseAll" title="折叠全部">
        <span>⊟</span>
      </button>
      <button @click="expandAll" title="展开全部">
        <span>⊞</span>
      </button>
    </div>
    <div class="toolbar-separator"></div>
    <div class="toolbar-group">
      <button @click="clearCanvas" title="清空画布" class="danger">
        <span>🗑</span>
      </button>
    </div>
  </div>
</template>

<script setup>
const props = defineProps({
  scale: { type: Number, default: 1 },
})

const emit = defineEmits(['zoom-in', 'zoom-out', 'zoom-reset', 'fit', 'collapse', 'expand', 'clear'])

function zoomIn() { emit('zoom-in') }
function zoomOut() { emit('zoom-out') }
function zoomReset() { emit('zoom-reset') }
function fitToView() { emit('fit') }
function collapseAll() { emit('collapse') }
function expandAll() { emit('expand') }
function clearCanvas() { emit('clear') }
</script>

<style scoped>
.canvas-toolbar {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 8px;
  background: var(--bg);
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}
.toolbar-group {
  display: flex;
  align-items: center;
  gap: 2px;
}
.toolbar-group button {
  width: 28px;
  height: 28px;
  padding: 0;
  font-size: 13px;
  border: none;
  border-radius: 4px;
  background: transparent;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
}
.toolbar-group button:hover:not(:disabled) {
  background: rgba(0, 0, 0, 0.06);
}
.toolbar-group button:disabled {
  opacity: 0.3;
  cursor: default;
}
.toolbar-group button.danger:hover {
  background: #fef2f2;
  color: var(--bad);
}
.toolbar-separator {
  width: 1px;
  height: 16px;
  background: var(--border);
  margin: 0 4px;
}
.zoom-label {
  font-size: 11px;
  color: var(--muted);
  min-width: 32px;
  text-align: center;
  font-family: Consolas, monospace;
}
</style>
