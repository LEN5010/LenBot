<script setup>
import { computed } from 'vue'
import { api } from '../api.js'
import { useResource } from '../composables/useResource.js'
import HostPage from '../host/ui/HostPage.vue'
import Panel from '../host/ui/Panel.vue'
import FactList from '../host/ui/FactList.vue'
import Fold from '../host/ui/Fold.vue'
import ObjectList from '../host/ui/ObjectList.vue'
import ObjectRow from '../host/ui/ObjectRow.vue'
import ResourceState from '../host/ui/ResourceState.vue'
import DevOnly from '../host/ui/DevOnly.vue'
import ScenePersonaEditor from '../components/ScenePersonaEditor.vue'

const snapshot = useResource(() => api('/api/chat-test/settings'))
const relationships = computed(() => Object.entries(snapshot.data.value?.scene_persona.relationships || {}))
const tags = list => list.length ? list.join('、') : '无'
</script>

<template>
  <HostPage title="场景与角色" description="这个测试实例正在用的场景和角色。场景补充可以在这里改，保存后重启生效。">
    <template #actions><v-btn variant="outlined" :loading="snapshot.loading.value" @click="snapshot.reload()">重新读取</v-btn></template>
    <ScenePersonaEditor />
    <ResourceState :resource="snapshot" error-title="读取场景与角色失败" v-slot="{ data }">
      <Panel title="当前场景">
        <FactList :items="[['场景', data.scene], ['Bot 账号', data.bot_id], ['时区', data.timezone],
          ['发送方式', data.delivery === 'simulated' ? '模拟发送，不发到 QQ' : data.delivery]]" />
      </Panel>

      <Panel title="正在运行的场景补充" description="重启后才会换成上面保存的内容。">
        <div class="block">
          <h3>补充称呼</h3>
          <div v-if="data.scene_persona.persona_aliases.length" class="chips">
            <v-chip v-for="(alias, index) in data.scene_persona.persona_aliases" :key="index">{{ alias }}</v-chip></div>
          <p v-else class="muted">没有</p>
        </div>
        <div class="block">
          <h3>关系说明</h3>
          <dl v-if="relationships.length" class="pairs">
            <div v-for="[qq, text] in relationships" :key="qq"><dt>{{ qq }}</dt><dd class="readable-copy">{{ text }}</dd></div>
          </dl>
          <p v-else class="muted">没有</p>
        </div>
        <div class="block">
          <h3>行为补充</h3>
          <p v-if="data.scene_persona.behavior_addendum !== null" class="readable-copy">{{ data.scene_persona.behavior_addendum }}</p>
          <p v-else class="muted">没有</p>
        </div>
      </Panel>

      <Panel :title="`角色 · ${data.persona.name}`">
        <DevOnly><p class="muted small">角色编号 {{ data.persona.id }}</p></DevOnly>
        <div class="block"><h3>简介</h3><p class="readable-copy">{{ data.persona.brief }}</p></div>
        <div class="block"><h3>做事方式</h3><p class="readable-copy">{{ data.persona.behavior }}</p></div>
        <div class="pair-grid">
          <div class="block"><h3>自称</h3><p>{{ data.persona.self_reference.join('、') || '没有' }}</p></div>
          <div class="block"><h3>别名</h3><p>{{ data.persona.aliases.join('、') || '没有' }}</p></div>
        </div>
        <div class="block"><h3>说话方式</h3><p class="readable-copy">{{ data.persona.voice }}</p></div>
        <div class="block"><h3>底线</h3><p class="readable-copy">{{ data.persona.boundaries }}</p></div>
        <div class="block">
          <h3>风格变体</h3>
          <ObjectList v-if="data.persona.styles.length" divided>
            <ObjectRow v-for="(style, index) in data.persona.styles" :key="index" :title="style.name" :subtitle="style.note ?? ''">
              <template #meta>每轮概率 {{ style.weight }}</template>
            </ObjectRow>
          </ObjectList>
          <p v-else class="muted">没有，只用基础说话方式</p>
        </div>
      </Panel>

      <Panel title="说话样例" :description="`选集标签：${data.persona.example_tags.length ? data.persona.example_tags.join('、') : '没有设置，按顺序取前 8 条'}`">
        <h3>这次选中的 {{ data.selected_examples.length }} 条</h3>
        <ol v-if="data.selected_examples.length" class="examples">
          <li v-for="(example, index) in data.selected_examples" :key="index">
            <p class="muted readable-copy">{{ example.context }}</p><p class="readable-copy">{{ example.line }}</p>
            <p class="muted small">标签：{{ tags(example.tags) }}</p>
          </li>
        </ol>
        <p v-else class="muted">没有选中的样例</p>
        <Fold v-if="data.persona.examples.length" :label="`全部样例（${data.persona.examples.length} 条）`">
          <ol class="examples">
            <li v-for="(example, index) in data.persona.examples" :key="index">
              <p class="muted readable-copy">{{ example.context }}</p><p class="readable-copy">{{ example.line }}</p>
              <p class="muted small">标签：{{ tags(example.tags) }}</p>
            </li>
          </ol>
        </Fold>
      </Panel>

      <Panel title="角色资料" description="只列出文件名和大小。" flush>
        <ObjectList v-if="data.knowledge.length" divided class="files">
          <ObjectRow v-for="document in data.knowledge" :key="document.filename" :title="document.filename" :subtitle="`标签：${tags(document.tags)}`">
            <template #meta>{{ document.characters }} 字</template>
          </ObjectRow>
        </ObjectList>
        <p v-else class="muted files-empty">这个角色没有资料文件</p>
      </Panel>

      <Panel title="工具与技能" description="按需工具要在对话里先找到才能用；隔离测试里用不了技能。">
        <div class="pair-grid">
          <div class="block"><h3>常驻工具</h3>
            <div v-if="data.tools.core.length" class="chips"><v-chip v-for="name in data.tools.core" :key="name">{{ name }}</v-chip></div>
            <p v-else class="muted">没有</p></div>
          <div class="block"><h3>按需工具</h3>
            <div v-if="data.tools.deferred.length" class="chips"><v-chip v-for="name in data.tools.deferred" :key="name">{{ name }}</v-chip></div>
            <p v-else class="muted">没有</p></div>
        </div>
        <div class="pair-grid">
          <div class="block"><h3>角色允许的工具</h3>
            <p v-if="data.persona.tools === 'all'">全部</p>
            <div v-else-if="data.persona.tools.length" class="chips"><v-chip v-for="name in data.persona.tools" :key="name" variant="outlined">{{ name }}</v-chip></div>
            <p v-else class="muted">没有</p></div>
          <div class="block"><h3>角色写的技能</h3>
            <p v-if="data.persona.skills === 'all'">全部</p>
            <div v-else-if="data.persona.skills.length" class="chips"><v-chip v-for="name in data.persona.skills" :key="name" variant="outlined">{{ name }}</v-chip></div>
            <p v-else class="muted">没有</p></div>
        </div>
      </Panel>
    </ResourceState>
  </HostPage>
</template>

<style scoped>
.block{display:grid;gap:var(--sp-1);min-width:0}
.block p,.examples p{margin:0}
.pair-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,260px),1fr));gap:var(--sp-4)}
.chips{display:flex;gap:var(--sp-2);flex-wrap:wrap}
.pairs{margin:0;display:grid;gap:var(--sp-2)}
.pairs dt{font-weight:600}
.pairs dd{margin:0}
.examples{margin:0;padding-left:var(--sp-5);display:grid;gap:var(--sp-3)}
.files{padding:0 var(--sp-2) var(--sp-2)}
.files-empty{margin:0;padding:0 var(--sp-4) var(--sp-4)}
</style>
