import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import vuetify from 'vite-plugin-vuetify'

// Build output is served by FastAPI (see web/app.py). Dev mode proxies /api
// (cookies included) to the running len-bot control plane.
export default defineConfig({
  plugins: [vue(), vuetify({ autoImport: true })],
  build: {
    outDir: '../static/dist',
    emptyOutDir: true,
  },
  server: {
    port: 5183,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:11307',
        changeOrigin: true,
      },
    },
  },
})
