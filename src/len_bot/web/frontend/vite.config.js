import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import vuetify from 'vite-plugin-vuetify'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

// Keep original npm notices next to every built panel, including wheels and
// custom outDir builds. The inventory includes build-only dependencies.
function frontendNotices() {
  let config
  return {
    name: 'frontend-notices', apply: 'build',
    configResolved(value) { config = value },
    closeBundle() {
      const collector = fileURLToPath(new URL('../../../../scripts/collect_frontend_licenses.cjs', import.meta.url))
      execFileSync(process.execPath, [collector, path.join(config.root, 'node_modules'),
        path.resolve(config.root, config.build.outDir, config.build.assetsDir, 'licenses/frontend')], { stdio: 'inherit' })
    },
  }
}

// Build output is served by FastAPI (see web/app.py). Dev mode proxies /api
// (cookies included) to the running len-bot control plane.
export default defineConfig({
  plugins: [vue(), vuetify({ autoImport: true }), frontendNotices()],
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
