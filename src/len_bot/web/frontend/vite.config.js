import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import vuetify from 'vite-plugin-vuetify'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

function frontendNotices() {
  let config
  return {
    name: 'frontend-notices', apply: 'build',
    configResolved(value) { config = value },
    writeBundle() {
      const collector = fileURLToPath(new URL('../../../../scripts/collect_frontend_licenses.cjs', import.meta.url))
      execFileSync(process.execPath, [collector, path.join(config.root, 'node_modules'),
        path.resolve(config.root, config.build.outDir, config.build.assetsDir, 'licenses/frontend')], { stdio: 'inherit' })
      const recorder = fileURLToPath(new URL('../../../../scripts/frontend_build.cjs', import.meta.url))
      execFileSync(process.execPath, [recorder, '--record'], { stdio: 'inherit' })
    },
  }
}

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
        ws: true,
      },
    },
  },
})
