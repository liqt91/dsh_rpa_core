<template>
  <div class="app-layout">
    <HeaderBar />
    <div class="main-area">
      <aside class="left-panel" :style="{ width: leftWidth + 'px' }">
        <CommandPalette />
      </aside>
      <div
        class="resize-v"
        :class="{ active: resizingLeft }"
        @mousedown="startResizeLeft"
      />
      <section class="canvas-area">
        <CanvasView />
      </section>
      <div
        class="resize-v"
        :class="{ active: resizingRight }"
        @mousedown="startResizeRight"
      />
      <aside class="right-panel" :style="{ width: rightWidth + 'px' }">
        <PropsPanel />
      </aside>
    </div>
    <BottomPanel />
  </div>
</template>

<script setup>
import { ref, onUnmounted } from 'vue'
import HeaderBar from './HeaderBar.vue'
import CommandPalette from '../palette/CommandPalette.vue'
import CanvasView from '../canvas/CanvasView.vue'
import PropsPanel from '../props/PropsPanel.vue'
import BottomPanel from '../common/BottomPanel.vue'

const leftWidth = ref(260)
const rightWidth = ref(320)
const resizingLeft = ref(false)
const resizingRight = ref(false)

let startX = 0
let startWidth = 0

function startResizeLeft(e) {
  resizingLeft.value = true
  startX = e.clientX
  startWidth = leftWidth.value
  document.addEventListener('mousemove', onResizeLeft)
  document.addEventListener('mouseup', stopResizeLeft)
  document.body.classList.add('resizing-panel')
}

function onResizeLeft(e) {
  leftWidth.value = Math.max(200, Math.min(400, startWidth + (e.clientX - startX)))
}

function stopResizeLeft() {
  resizingLeft.value = false
  document.removeEventListener('mousemove', onResizeLeft)
  document.removeEventListener('mouseup', stopResizeLeft)
  document.body.classList.remove('resizing-panel')
}

function startResizeRight(e) {
  resizingRight.value = true
  startX = e.clientX
  startWidth = rightWidth.value
  document.addEventListener('mousemove', onResizeRight)
  document.addEventListener('mouseup', stopResizeRight)
  document.body.classList.add('resizing-panel')
}

function onResizeRight(e) {
  rightWidth.value = Math.max(260, Math.min(480, startWidth - (e.clientX - startX)))
}

function stopResizeRight() {
  resizingRight.value = false
  document.removeEventListener('mousemove', onResizeRight)
  document.removeEventListener('mouseup', stopResizeRight)
  document.body.classList.remove('resizing-panel')
}

onUnmounted(() => {
  document.removeEventListener('mousemove', onResizeLeft)
  document.removeEventListener('mouseup', stopResizeLeft)
  document.removeEventListener('mousemove', onResizeRight)
  document.removeEventListener('mouseup', stopResizeRight)
})
</script>

<style scoped>
.app-layout {
  display: flex;
  flex-direction: column;
  height: 100vh;
  overflow: hidden;
}
.main-area {
  flex: 1;
  display: flex;
  min-height: 0;
}
.left-panel {
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}
.right-panel {
  border-left: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}
.canvas-area {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.resize-v {
  width: 4px;
  cursor: col-resize;
  background: transparent;
  transition: background 0.15s;
  flex-shrink: 0;
}
.resize-v:hover,
.resize-v.active {
  background: var(--accent);
}
</style>
