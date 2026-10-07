<script setup>
import { computed, ref, watch } from 'vue'
import { api } from '../../../api.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { notify, readPendingRestart } from '../../store.js'
import { clone, numberOrBlank, same } from '../../forms.js'
import SettingSection from '../../ui/SettingSection.vue'
import ResourceState from '../../ui/ResourceState.vue'

const emit = defineEmits(['dirty'])
const settings = useResource(() => api('/api/host/settings'))
const read = ref(null), search = ref(null), fakeIp = ref(''), publicDns = ref('')
const saveRead = useAction(), saveSearch = useAction(), saveNetwork = useAction()
const saved = computed(() => settings.data.value?.saved)
watch(() => JSON.stringify(saved.value?.web_read), () => { if (saved.value) read.value = clone(saved.value.web_read) }, { immediate: true })
watch(() => JSON.stringify(saved.value?.web_search), () => { if (saved.value) search.value = clone(saved.value.web_search) }, { immediate: true })
watch(() => JSON.stringify(saved.value?.network), () => {
  if (saved.value) {
    fakeIp.value = saved.value.network.fake_ip_networks.join('\n')
    publicDns.value = saved.value.network.public_dns_url
  }
}, { immediate: true })
const fakeIpList = computed(() => fakeIp.value.split('\n').map(line => line.trim()).filter(Boolean))
const networkDirty = computed(() => Boolean(saved.value) && (!same(fakeIpList.value, saved.value.network.fake_ip_networks)
  || publicDns.value !== saved.value.network.public_dns_url))
const readDirty = computed(() => Boolean(saved.value) && !same(read.value, saved.value.web_read))
const searchDirty = computed(() => Boolean(saved.value) && !same(search.value, saved.value.web_search))
watch(() => readDirty.value || searchDirty.value || networkDirty.value, value => emit('dirty', value), { immediate: true })

async function submit(kind) {
  const [action, body] = {
    'web-read': [saveRead, { web_read: read.value }],
    'web-search': [saveSearch, { web_search: search.value }],
    network: [saveNetwork, { network: { fake_ip_networks: fakeIpList.value, public_dns_url: publicDns.value } }],
  }[kind]
  const result = await action.run(() => api(`/api/host/settings/${kind}`, { method: 'PUT', body: JSON.stringify(body) }))
  if (result) { settings.data.value = result; readPendingRestart(); notify('已保存') }
}
</script>

<template>
  <ResourceState :resource="settings" error-title="读取网页服务设置失败">
    <SettingSection title="读网页" description="Bot 可以打开群友发的链接，读取网页正文。"
      :dirty="readDirty" :saving="saveRead.busy.value" :error="saveRead.error.value" @save="submit('web-read')">
      <v-switch :model-value="read !== null" label="允许读网页" @update:model-value="value => read = value ? { timeout_seconds: 20 } : null" />
      <v-text-field v-if="read" :model-value="read.timeout_seconds" type="number" label="读取超时（秒）"
        @update:model-value="value => read.timeout_seconds = numberOrBlank(value)" />
    </SettingSection>
    <SettingSection title="搜索网页" description="Bot 可以用必应搜索查资料。"
      :dirty="searchDirty" :saving="saveSearch.busy.value" :error="saveSearch.error.value" @save="submit('web-search')">
      <v-switch :model-value="search !== null" label="允许搜索网页"
        @update:model-value="value => search = value ? { provider: 'bing_rss', timeout_seconds: 15, max_results: 5 } : null" />
      <div v-if="search" class="form-grid">
        <v-text-field :model-value="search.max_results" type="number" label="每次最多几条结果"
          @update:model-value="value => search.max_results = numberOrBlank(value)" />
        <v-text-field :model-value="search.timeout_seconds" type="number" label="搜索超时（秒）"
          @update:model-value="value => search.timeout_seconds = numberOrBlank(value)" />
      </div>
    </SettingSection>
    <SettingSection title="代理的假 IP 网段" description="开着 fake-ip 时，填入代理的假 IP 网段。Bot 会查询真实公网地址再连接，私网和本机地址不会放行。"
      :dirty="networkDirty" :saving="saveNetwork.busy.value" :error="saveNetwork.error.value" @save="submit('network')">
      <v-textarea v-model="fakeIp" rows="2" auto-grow label="假 IP 网段" placeholder="198.18.0.0/15"
        hint="每行一个，以代理软件设置里的网段为准；没开 fake-ip 就留空。" persistent-hint />
      <v-text-field v-if="fakeIpList.length" v-model="publicDns" label="真实 DNS 查询地址"
        hint="使用支持 DNS JSON 的解析服务；查询失败会返回错误，不改用其他服务。" persistent-hint />
    </SettingSection>
  </ResourceState>
</template>
