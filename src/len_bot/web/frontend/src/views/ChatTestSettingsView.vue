<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'
import ScenePersonaEditor from '../components/ScenePersonaEditor.vue'

const snapshot = ref(null)
const loading = ref(false)
const error = ref('')
const showAllExamples = ref(false)
const beginRequest = useRequestGuard()

const relationships = computed(() => snapshot.value === null ? [] : Object.entries(snapshot.value.scene_persona.relationships))

async function refresh() {
  const fresh = beginRequest()
  loading.value = true
  try {
    const result = await api('/api/chat-test/settings')
    if (!fresh()) return
    snapshot.value = result
    error.value = ''
  } catch (problem) {
    if (fresh()) error.value = problem.message
  } finally {
    if (fresh()) loading.value = false
  }
}

onMounted(refresh)
</script>

<template>
  <div class="page-stack settings-snapshot">
    <header class="page-intro">
      <div>
        <p class="eyebrow">隔离测试</p>
        <h1>场景与角色</h1>
        <p class="muted">查看现在用的角色和场景设置，修改场景补充。保存后重启生效。</p>
      </div>
      <div class="intro-actions">
        <v-btn :to="{name:'chat-test'}" variant="outlined">返回对话测试</v-btn>
        <v-btn variant="outlined" :loading="loading" @click="refresh">重新读取当前实例</v-btn>
      </div>
    </header>

    <v-alert v-if="error" type="error" variant="tonal" role="alert" title="读取场景与角色失败">
      {{ error }}<span v-if="snapshot"> 下面显示的是上次读到的内容。</span>
    </v-alert>
    <div v-if="loading && !snapshot" class="surface empty-state" role="status">正在读取当前实例已加载的场景与角色…</div>

    <ScenePersonaEditor />

    <template v-if="snapshot">
      <v-alert type="info" variant="tonal" class="snapshot-note">
        <strong>{{ error ? '上次读到的设置' : '现在用的设置' }}</strong> · 模拟发送，不会发到 QQ。
      </v-alert>

      <section class="surface" aria-labelledby="scene-title">
        <div class="section-heading"><h2 id="scene-title">当前场景</h2><span class="muted">本测试实例的实际绑定</span></div>
        <dl class="fact-grid">
          <div><dt>场景</dt><dd>{{ snapshot.scene }}</dd></div>
          <div><dt>Bot QQ</dt><dd>{{ snapshot.bot_qq }}</dd></div>
          <div><dt>时区</dt><dd>{{ snapshot.timezone }}</dd></div>
          <div><dt>表达方式</dt><dd>{{ '聊天模型直接表达' }}</dd></div>
          <div><dt>消息出口</dt><dd>{{ snapshot.delivery === 'simulated' ? '模拟发送 · 未发送到 QQ' : snapshot.delivery }}</dd></div>
        </dl>
      </section>

      <section class="surface" aria-labelledby="scene-persona-title">
        <div class="section-heading"><h2 id="scene-persona-title">当前运行的场景补充</h2><span class="muted">现在用的</span></div>
        <div class="content-block">
          <h3>补充称呼</h3>
          <ul v-if="snapshot.scene_persona.persona_aliases.length" class="plain-list chip-list">
            <li v-for="(alias, index) in snapshot.scene_persona.persona_aliases" :key="index"><v-chip size="small" variant="tonal">{{ alias }}</v-chip></li>
          </ul>
          <p v-else class="muted">未设置补充称呼。</p>
        </div>
        <div class="content-block">
          <h3>本场景关系说明</h3>
          <dl v-if="relationships.length" class="relationship-list">
            <div v-for="entry in relationships" :key="entry[0]"><dt>QQ {{ entry[0] }}</dt><dd class="original-text">{{ entry[1] }}</dd></div>
          </dl>
          <p v-else class="muted">未设置关系说明。</p>
        </div>
        <div class="content-block">
          <h3>行为风格补充</h3>
          <p v-if="snapshot.scene_persona.behavior_addendum !== null" class="original-text">{{ snapshot.scene_persona.behavior_addendum }}</p>
          <p v-else class="muted">未设置行为风格补充。</p>
        </div>
      </section>

      <section class="surface" aria-labelledby="persona-title">
        <div class="section-heading"><h2 id="persona-title">已加载角色</h2><span class="muted">现在用的</span></div>
        <div class="persona-name"><strong>{{ snapshot.persona.name }}</strong><span class="muted">{{ snapshot.persona.id }}</span></div>
        <div class="content-block">
          <h3>身份简述</h3>
          <p class="original-text">{{ snapshot.persona.brief }}</p>
        </div>
        <div class="content-block">
          <h3>行为风格 <span class="section-hint">供大脑决定如何参与</span></h3>
          <p class="original-text">{{ snapshot.persona.behavior }}</p>
        </div>
        <div class="paired-blocks">
          <div class="content-block">
            <h3>自称</h3>
            <ul v-if="snapshot.persona.self_reference.length" class="plain-list chip-list">
              <li v-for="(name, index) in snapshot.persona.self_reference" :key="index"><v-chip size="small" variant="tonal">{{ name }}</v-chip></li>
            </ul>
            <p v-else class="muted">未设置。</p>
          </div>
          <div class="content-block">
            <h3>角色称呼</h3>
            <ul v-if="snapshot.persona.aliases.length" class="plain-list chip-list">
              <li v-for="(name, index) in snapshot.persona.aliases" :key="index"><v-chip size="small" variant="tonal">{{ name }}</v-chip></li>
            </ul>
            <p v-else class="muted">未设置。</p>
          </div>
        </div>
        <div class="content-block">
          <h3>说话风格 <span class="section-hint">供表达时组织台词</span></h3>
          <p class="original-text">{{ snapshot.persona.voice }}</p>
        </div>
        <div class="content-block">
          <h3>身份边界</h3>
          <p class="original-text">{{ snapshot.persona.boundaries }}</p>
        </div>
        <div class="content-block">
          <h3>每轮风格变体</h3>
          <ul v-if="snapshot.persona.styles.length" class="plain-list item-list">
            <li v-for="(style, index) in snapshot.persona.styles" :key="index" class="listed-item">
              <strong>{{ style.name }}</strong><span class="muted">每轮概率 {{ style.weight }}</span>
              <p v-if="style.note !== null" class="original-text">{{ style.note }}</p>
            </li>
          </ul>
          <p v-else class="muted">未设置变体；只使用基础说话风格。</p>
        </div>
      </section>

      <section class="surface" aria-labelledby="examples-title">
        <div class="section-heading"><h2 id="examples-title">人工样例与选集</h2><span class="muted">样例不是群聊经历</span></div>
        <p class="muted">角色选集标签：{{ snapshot.persona.example_tags.length ? snapshot.persona.example_tags.join('、') : '未设置；按角色包顺序选前 8 条' }}。</p>
        <h3>当前实际选中的样例 · {{ snapshot.selected_examples.length }} 条</h3>
        <ol v-if="snapshot.selected_examples.length" class="example-list">
          <li v-for="(example, index) in snapshot.selected_examples" :key="index" class="example-item">
            <p class="example-context original-text">{{ example.context }}</p>
            <p class="original-text">{{ example.line }}</p>
            <p class="tag-line">标签：{{ example.tags.length ? example.tags.join('、') : '无' }}</p>
          </li>
        </ol>
        <p v-else class="muted">没有选中的人工样例。</p>
        <v-btn class="examples-toggle" variant="outlined" :aria-expanded="showAllExamples" aria-controls="all-persona-examples" @click="showAllExamples=!showAllExamples">
          {{ showAllExamples ? '收起全部样例' : `查看全部人工样例 · ${snapshot.persona.examples.length} 条` }}
        </v-btn>
        <div v-if="showAllExamples" id="all-persona-examples" class="all-examples">
          <h3>角色包中的全部样例</h3>
          <ol v-if="snapshot.persona.examples.length" class="example-list">
            <li v-for="(example, index) in snapshot.persona.examples" :key="index" class="example-item">
              <p class="example-context original-text">{{ example.context }}</p>
              <p class="original-text">{{ example.line }}</p>
              <p class="tag-line">标签：{{ example.tags.length ? example.tags.join('、') : '无' }}</p>
            </li>
          </ol>
          <p v-else class="muted">角色包中没有人工样例。</p>
        </div>
      </section>

      <section class="surface" aria-labelledby="knowledge-title">
        <div class="section-heading"><h2 id="knowledge-title">角色资料目录</h2><span class="muted">只显示文件名与概况，不是全文</span></div>
        <ul v-if="snapshot.knowledge.length" class="plain-list item-list">
          <li v-for="document in snapshot.knowledge" :key="document.filename" class="listed-item">
            <strong class="original-text">{{ document.filename }}</strong>
            <span class="muted">{{ document.characters }} 字符</span>
            <p class="tag-line">标签：{{ document.tags.length ? document.tags.join('、') : '无' }}</p>
          </li>
        </ul>
        <p v-else class="muted">本角色没有已加载的资料文件。</p>
      </section>

      <section class="surface" aria-labelledby="abilities-title">
        <div class="section-heading"><h2 id="abilities-title">能力</h2><span class="muted"></span></div>
        <p class="muted">这个角色能用的工具。按需工具要在对话里先找到才能用。</p>
        <div class="paired-blocks">
          <div class="content-block">
            <h3>常驻工具</h3>
            <ul v-if="snapshot.tools.core.length" class="plain-list chip-list">
              <li v-for="name in snapshot.tools.core" :key="name"><v-chip size="small" variant="tonal">{{ name }}</v-chip></li>
            </ul>
            <p v-else class="muted">无常驻工具。</p>
          </div>
          <div class="content-block">
            <h3>按需发现工具</h3>
            <ul v-if="snapshot.tools.deferred.length" class="plain-list chip-list">
              <li v-for="name in snapshot.tools.deferred" :key="name"><v-chip size="small" variant="tonal">{{ name }}</v-chip></li>
            </ul>
            <p v-else class="muted">无按需发现工具。</p>
          </div>
        </div>
        <div class="content-block">
          <h3>角色工具许可声明</h3>
          <p v-if="snapshot.persona.tools === 'all'" class="muted">角色允许所有工具，实际能用的见上方名单。</p>
          <ul v-else-if="snapshot.persona.tools.length" class="plain-list chip-list">
            <li v-for="name in snapshot.persona.tools" :key="name"><v-chip size="small" variant="outlined">{{ name }}</v-chip></li>
          </ul>
          <p v-else class="muted">角色没有设置工具。</p>
        </div>
        <div class="content-block">
          <h3>技能声明</h3>
          <p class="muted">角色文件里写的技能。隔离测试里用不了技能。</p>
          <p v-if="snapshot.persona.skills === 'all'" class="muted">全部技能</p>
          <ul v-else-if="snapshot.persona.skills.length" class="plain-list chip-list">
            <li v-for="name in snapshot.persona.skills" :key="name"><v-chip size="small" variant="outlined">{{ name }}</v-chip></li>
          </ul>
          <p v-else class="muted">没有设置技能。</p>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.settings-snapshot{min-width:0}
.page-intro{display:flex;justify-content:space-between;align-items:flex-start;gap:24px;flex-wrap:wrap}
.page-intro>div:first-child{min-width:0;flex:1 1 460px}
.page-intro h1{margin:0 0 8px}
.eyebrow{font-size:12px;letter-spacing:.08em;color:var(--primary);font-weight:700;margin:0 0 6px}
.intro-actions{display:flex;gap:10px;flex-wrap:wrap}
.intro-actions :deep(.v-btn),.examples-toggle{min-height:44px}
.surface{min-width:0}
.section-heading{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:16px}
.section-heading h2{margin:0}
.section-hint{font-size:13px;font-weight:400;color:var(--muted)}
.fact-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,190px),1fr));gap:18px;margin:0}
.fact-grid>div{min-width:0}
.fact-grid dt{font-size:12px;color:var(--muted);margin-bottom:5px}
.fact-grid dd{margin:0;font-weight:600;overflow-wrap:anywhere}
.content-block{min-width:0;margin-top:22px}
.content-block:first-of-type{margin-top:0}
.content-block h3,.all-examples h3{font-size:16px;margin:0 0 10px}
.content-block p,.example-item p{margin:0}
.paired-blocks{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,260px),1fr));gap:16px}
.paired-blocks .content-block{margin-top:22px}
.persona-name{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;min-width:0;overflow-wrap:anywhere}
.persona-name strong{font-size:20px}
.original-text{white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word}
.plain-list{list-style:none;padding:0;margin:0}
.chip-list{display:flex;gap:8px;flex-wrap:wrap}
.chip-list li{min-width:0;max-width:100%}
.chip-list :deep(.v-chip){max-width:100%;height:auto;min-height:32px;white-space:normal;overflow-wrap:anywhere}
.chip-list :deep(.v-chip__content){white-space:pre-wrap;overflow-wrap:anywhere;padding-block:5px}
.relationship-list{margin:0;display:grid;gap:10px}
.relationship-list>div{border-left:2px solid var(--line);padding-left:12px;min-width:0}
.relationship-list dt{font-weight:600;overflow-wrap:anywhere}
.relationship-list dd{margin:4px 0 0}
.item-list{display:grid;gap:10px}
.listed-item{border:1px solid var(--line);border-radius:8px;padding:12px;min-width:0;display:flex;flex-wrap:wrap;gap:4px 12px;align-items:baseline}
.listed-item strong{min-width:0;overflow-wrap:anywhere}
.listed-item p{flex-basis:100%;margin:0}
.example-list{margin:12px 0 18px;padding-left:24px;display:grid;gap:12px}
.example-item{border-left:2px solid var(--line);padding-left:12px;min-width:0}
.example-context{font-weight:600;margin-bottom:6px!important}
.tag-line{font-size:13px;color:var(--muted);overflow-wrap:anywhere}
.example-item .tag-line{margin-top:8px}
.all-examples{border-top:1px solid var(--line);margin-top:18px;padding-top:18px}
.snapshot-note{overflow-wrap:anywhere}
.settings-snapshot :deep(.v-alert),.settings-snapshot .muted{overflow-wrap:anywhere}
@media(max-width:600px){.intro-actions{width:100%}.intro-actions :deep(.v-btn){flex:1 1 auto}.section-heading{align-items:flex-start}}
</style>
