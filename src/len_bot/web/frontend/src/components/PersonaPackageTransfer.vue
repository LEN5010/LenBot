<script setup>
import { computed, ref } from 'vue'
import { api } from '../api.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'
import { useUnsavedChanges } from '../composables/useUnsavedChanges.js'

const props = defineProps({ scene: { type: String, required: true }, unsavedRole: Boolean })
const name = ref(''), upload = ref(null), input = ref(null)
const importing = ref(false), error = ref(''), imported = ref(null)
const downloading = ref(false), downloadError = ref(''), downloadNotice = ref('')
const draftPresent = computed(() => name.value !== '' || upload.value !== null || importing.value)
useUnsavedChanges(draftPresent)
const beginImport = useRequestGuard()
const beginDownload = useRequestGuard()

async function downloadPackage() {
  if (!props.scene || downloading.value) return
  const scene = props.scene, fresh = beginDownload()
  downloading.value = true
  downloadError.value = ''
  downloadNotice.value = ''
  try {
    const blob = await api(`/api/host/scenes/${encodeURIComponent(scene)}/persona-package`, {}, 'blob')
    if (!fresh()) return
    const url = URL.createObjectURL(blob)
    try {
      const link = document.createElement('a')
      link.href = url
      link.download = `persona-${scene.replace(':','-')}.zip`
      document.body.appendChild(link)
      link.click()
      link.remove()
    } finally {
      URL.revokeObjectURL(url)
    }
    downloadNotice.value = `已收到 ${scene} 的已保存角色包并交给浏览器下载；最终保存位置由浏览器决定。`
  } catch (problem) {
    if (fresh()) downloadError.value = `${scene} 导出失败：${problem.message}`
  } finally {
    if (fresh()) downloading.value = false
  }
}

function choose(event) {
  upload.value = event.target.files[0] ?? null
  error.value = ''
}

async function importPackage() {
  if (importing.value) return
  error.value = ''
  if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(name.value)) {
    error.value = '新目录名须为 1–64 个英文字母、数字、下划线或短横线，并以字母或数字开头。'
    return
  }
  if (!upload.value) {
    error.value = '请先选择原生角色 ZIP 文件。'
    return
  }
  if (upload.value.size > 64 * 1024 * 1024) {
    error.value = 'ZIP 文件不能超过 64 MiB。'
    return
  }
  const targetName = name.value
  const form = new FormData()
  form.append('name', targetName)
  form.append('file', upload.value)
  const fresh = beginImport()
  importing.value = true
  imported.value = null
  try {
    const result = await api('/api/host/personas/import', { method: 'POST', body: form })
    if (!fresh()) return
    imported.value = result
    name.value = ''
    upload.value = null
    input.value.value = ''
  } catch (problem) {
    if (!fresh()) return
    error.value = problem.status >= 400 && problem.status < 500
      ? `导入未被接受：${problem.message}`
      : `导入结果未确认：${problem.message} 请核对实例根目录 personas/${targetName} 是否已生成；不会自动重试或覆盖。`
  } finally {
    if (fresh()) importing.value = false
  }
}
</script>

<template>
  <section class="surface package-transfer" aria-labelledby="role-package-title">
    <div class="section-heading">
      <div><h2 id="role-package-title">角色包导入与导出</h2>
        <p class="muted">原生 ZIP 保留角色原文、完整样例、知识与表情原件。导入只创建新目录，不绑定场景、不替换当前人格。</p></div>
      <v-btn v-if="scene" variant="outlined" :loading="downloading" :disabled="downloading" @click="downloadPackage">下载已保存角色包</v-btn>
    </div>
    <p v-if="unsavedRole" class="muted">当前编辑器有未保存的原文。下载只读取根配置指向的磁盘角色包，不包含此草稿或旧运行快照。</p>
    <v-alert v-if="error" type="error" variant="tonal" role="alert">{{ error }}</v-alert>
    <v-alert v-if="downloadError" type="error" variant="tonal" role="alert">{{ downloadError }}</v-alert>
    <v-alert v-if="downloadNotice" type="info" variant="tonal" role="status">{{ downloadNotice }}</v-alert>
    <v-alert v-if="imported" type="success" variant="tonal" role="status">
      <strong>{{ imported.persona.name }} · {{ imported.persona.id }} 已创建为独立包</strong>
      <p>{{ imported.notice }}</p>
      <p>配置路径：<code>{{ imported.config_path }}</code></p>
      <p>实际保存目录：<code>{{ imported.path }}</code></p>
      <p>{{ imported.files }} 个文件；{{ imported.examples }} 条完整样例；{{ imported.knowledge }} 篇知识；{{ imported.stickers }} 张已索引表情。</p>
      <RouterLink v-if="scene" :to="{name:'host-settings',query:{scene}}">到场景设置明确选择角色路径</RouterLink>
    </v-alert>
    <form @submit.prevent="importPackage" novalidate>
      <fieldset :disabled="importing">
        <legend>上传为新的独立角色包</legend>
        <v-text-field v-model="name" label="新目录名（不是包内角色 ID）" placeholder="my-role" hide-details="auto" :disabled="importing" />
        <label class="file-field">原生角色 ZIP
          <input ref="input" type="file" accept=".zip,application/zip" :disabled="importing" @change="choose" />
        </label>
        <p class="muted">四个角色文件必须直接位于 ZIP 根；可含 knowledge/、stickers/、avatar.png、README.md、LICENSE、NOTICE。64 MiB 上传、128 MiB 解压、最多 2048 项。不自动识别其他角色卡格式，已有目录不会覆盖。</p>
        <v-btn type="submit" color="primary" :loading="importing" :disabled="importing">上传并创建独立包</v-btn>
      </fieldset>
    </form>
  </section>
</template>

<style scoped>
.section-heading{display:flex;justify-content:space-between;align-items:flex-start;gap:14px;flex-wrap:wrap}
.section-heading p{margin:8px 0 16px}.package-transfer h2{font-size:18px;margin:0}
fieldset{border:1px solid var(--line);border-radius:10px;padding:16px;min-width:0;margin:16px 0 0;display:grid;gap:14px}
legend{padding:0 8px;font-weight:700}.file-field{display:grid;gap:8px;font-weight:600}.file-field input{max-width:100%;min-height:44px;font:inherit}
p,code{overflow-wrap:anywhere;white-space:pre-wrap}form .v-btn{justify-self:start;min-height:44px}
</style>
