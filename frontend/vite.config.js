import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [vue()],
  base: '/static/',
  build: {
    outDir: import.meta.dirname + '/../src/rpa_core/devserver/static',
    // 迁移中间态：线上编辑器仍是零构建 app.js（index.html 引用 app.js），
    // 严禁清空 outDir——那会删掉 app.js / i18n.js / styles.css 等线上文件。
    // Vue 正式切换入口时再改为 true 并清理旧 hash 产物。
    emptyOutDir: false,
  },
  server: {
    proxy: {
      '/api': 'http://localhost:8080',
    }
  }
})
