<script setup>
import { computed, ref, watch } from 'vue'
import { api } from '../../../api.js'
import { useAction } from '../../../composables/useResource.js'
import { clone, numberOrBlank, same } from '../../forms.js'
import SettingSection from '../../components/SettingSection.vue'
import AdvancedFields from '../../components/AdvancedFields.vue'

const props = defineProps({ snapshot: { type: Object, required: true } })
const emit = defineEmits(['saved', 'dirty'])
const saved = computed(() => props.snapshot.saved.panel)
const draft = ref(null), password = ref('')
const save = useAction()
watch(() => JSON.stringify(saved.value), () => { draft.value = clone(saved.value); password.value = '' }, { immediate: true })
const dirty = computed(() => password.value !== '' || !same(draft.value, saved.value))
watch(dirty, value => emit('dirty', value), { immediate: true })

async function submit() {
  const result = await save.run(() => api('/api/host/settings/panel', {
    method: 'PUT', body: JSON.stringify({ ...draft.value, password: password.value || null }),
  }))
  if (result) emit('saved', result)
}
</script>

<template>
  <SettingSection v-if="draft" title="面板账号" description="登录这个管理面板用的账号。"
    :dirty="dirty" :saving="save.busy.value" :error="save.error.value" @save="submit">
    <div class="form-grid">
      <v-text-field v-model="draft.username" label="用户名" autocomplete="username" />
      <v-text-field v-model="password" type="password" autocomplete="new-password" label="新密码" placeholder="留空保持不变" />
    </div>
    <AdvancedFields>
      <v-text-field v-model="draft.host" label="面板监听地址" hint="只在本机访问时填 127.0.0.1" persistent-hint />
      <v-text-field :model-value="draft.port" type="number" label="面板端口" @update:model-value="value => draft.port = numberOrBlank(value)" />
      <v-switch v-model="draft.cookie_secure" label="仅通过 HTTPS 登录" hint="面板放在 HTTPS 反向代理后面时打开" persistent-hint />
    </AdvancedFields>
  </SettingSection>
</template>
