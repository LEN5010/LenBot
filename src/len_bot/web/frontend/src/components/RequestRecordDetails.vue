<script setup>
import { computed, ref, watch } from 'vue'
import EntityLink from './EntityLink.vue'
import ResourceViewer from './ResourceViewer.vue'

const props = defineProps({ record: Object, sceneId: String })
const visibleCount = ref(25)
const rawOpen = ref(false)
watch(() => props.record, () => { visibleCount.value = 25; rawOpen.value = false })
const supported = computed(() => [1, 2, 3, 4, 5].includes(props.record?.format_version))
const messages = computed(() => supported.value ? props.record.messages : [])
const components = computed(() => [2, 3, 4, 5].includes(props.record?.format_version)
  ? messages.value.flatMap(message => message.prompt_components.map(component => ({ ...component, messageIndex: message.index }))) : [])
const retainedTools = computed(() => [2, 3, 4, 5].includes(props.record?.format_version)
  ? props.record.tools.filter(tool => tool.definition.status === 'retained').length : 0)
const images = computed(() => messages.value.flatMap(message => message.images))
const omitted = computed(() => messages.value.filter(message => message.omitted === true).length)
const omissionUnknown = computed(() => messages.value.filter(message => message.omitted === null).length)
const toolChoice = computed(() => typeof props.record.tool_choice === 'string'
  ? props.record.tool_choice : `${props.record.tool_choice.type} · ${props.record.tool_choice.name || '未记录名称'}`)
const gaps = {
  message_bodies: '消息正文及动态提示', prompt_versions: '提示版本',
  tool_definition_versions: '工具完整定义及版本', tool_arguments: '工具调用参数',
  media_bodies: '媒体正文', provider_wire_body: '客户端序列化后的请求正文',
  dynamic_message_bodies: '动态消息正文（人格、配置与插件指令等）',
  undeclared_prompt_components: '未单独登记的提示组件及版本',
  undeclared_tool_definitions: '未单独登记的工具完整定义及版本',
  dynamic_plugin_tool_definitions: '插件动态工具完整定义（归属版本另列）',
}
</script>

<template>
  <section class="request-record" aria-label="调用登记时的请求材料">
    <h3>调用登记时的请求材料</h3>
    <p v-if="!record" class="request-note">未保存本次调用的独立材料记录。旧调用和未接入此记录的调用入口不回填，也不以轮次最终清单替代。</p>
    <template v-else-if="supported">
      <p class="request-note">记录取自最终装配之后、进入客户端之前，与本次调用一同登记。登记不证明请求已经发出或材料已被模型接收；执行结果见调用状态与传输记录。</p>
      <dl class="request-facts">
        <div><dt>消息位置</dt><dd>{{ messages.length }}</dd></div>
        <div><dt>省略标记</dt><dd>{{ omitted }}；{{ omissionUnknown }} 个位置未记录该标记</dd></div>
        <div><dt>图像块</dt><dd>{{ images.length }}；{{ images.filter(image => !image.asset_id).length }} 个缺少资产定位</dd></div>
        <div><dt>工具定义数量</dt><dd>{{ record.tools.length }}</dd></div>
        <div><dt>最大输出 tokens</dt><dd>{{ record.settings.max_completion_tokens }}</dd></div>
        <div><dt>工具选择</dt><dd>{{ toolChoice }}</dd></div>
      </dl>
      <p v-if="record.request_order?.scope === 'binding_instance_preparation'" class="request-note">同一绑定实例的第 {{ record.request_order.sequence }} 次请求准备（{{ record.request_order.sequence === 1 ? '首次' : '后续' }}）。实例 {{ record.request_order.binding_id }}；不是群首问、段首轮或供应商重试序号。此前准备若未登记，序号可能不连续。</p>
      <p v-else class="request-note">未记录本次请求在绑定实例中的先后顺序。</p>
      <p class="request-note">未留存：{{ record.not_retained.map(key => gaps[key] || key).join('、') }}。清单格式版本 {{ record.format_version }} 不是提示或插件版本；来源定位不能逐字还原请求。</p>
      <template v-if="[2, 3, 4, 5].includes(record.format_version)">
        <h4>固定提示组件</h4>
        <p class="request-note">只保留下列固定片段；同一消息中的人格配置、表达偏好及插件动态指令未留存。组件修订号是声明版本，具体内容以本次快照为准。</p>
        <p v-if="!components.length" class="request-note">本次没有单独登记的固定提示组件。</p>
        <details v-for="component in components" :key="`${component.messageIndex}:${component.component_id}`">
          <summary>位置 {{ component.messageIndex + 1 }} · {{ component.component_id }} · 修订 {{ component.revision }} · {{ component.status === 'retained' ? '已留存' : '声明后内容有变化，未留存' }}</summary>
          <template v-if="component.status === 'retained'">
            <p class="request-note">该消息中的字符范围 [{{ component.text_range.start }}, {{ component.text_range.end }})；字符从 0 开始计数。</p>
            <ResourceViewer title="本次固定提示片段" :content="component.snapshot_text" />
          </template>
        </details>
        <p class="request-note">工具定义快照已留存 {{ retainedTools }} / {{ record.tools.length }}。未留存完整定义的工具不能用当前 Schema 还原；插件归属版本如有记录则单独列出，不等于完整定义版本。</p>
      </template>
      <details><summary>当次工具顺序与定义</summary><ol class="request-tools"><li v-for="tool in record.tools" :key="tool.index">
        {{ tool.name || '未记录名称' }} · {{ tool.type }}
        <template v-if="[2, 3, 4, 5].includes(record.format_version)">
          <p v-if="tool.definition.plugin" class="request-note">声明来源：{{ tool.definition.plugin.id }} · 插件 v{{ tool.definition.plugin.version }} · 接口世代 {{ tool.definition.plugin.api_version }}。仅定位所属插件，不证明 Schema 恒定或工具已执行。</p>
          <details v-if="tool.definition.status === 'retained'">
            <summary>{{ tool.definition.component_id }} · 修订 {{ tool.definition.revision }} · 查看本次定义</summary>
            <ResourceViewer title="本次工具定义" :content="tool.definition.snapshot_json" />
          </details>
          <p v-else-if="tool.definition.status === 'origin_recorded'" class="request-note">已登记插件归属，动态工具完整定义未留存。</p>
          <p v-else class="request-note">{{ tool.definition.status === 'changed_after_declaration' ? '定义在声明后有变化，未将声明来源或快照作为本次完整定义依据。' : '未保存该工具的定义版本。' }}</p>
        </template>
      </li></ol><p v-if="!record.tools.length" class="request-note">该调用的工具列表为空。</p></details>
      <p class="request-note">资料页范围只说明该次最终请求保留了对应原文或宿主投影，不证明模型已经收到或已读。未匹配到资料页不等于工具未执行；原始结果和执行状态仍以所属记录为准。</p>
      <p v-if="record.format_version >= 4" class="request-note">若有上段资料位置，只记录本次请求中保留的编号和目录装配时可用性；它不是正文阅读，也不证明资料现在仍可用。</p>
      <p v-if="record.format_version >= 5" class="request-note">历史摘要只登记本次最终保留的批次 ID、版本和覆盖范围，不复制摘要正文；来源后来可能失效，登记不授予精确原话已读资格。</p>
      <p v-if="images.length" class="request-note">图像定位按消息和内容块展开，位置均从 1 开始显示。资产编号只表示本次请求记录中的关联，不证明模型看到像素、文件仍可用或平台已收到；展开不会加载图片。</p>
      <div class="request-table-wrap"><table>
        <caption>消息顺序与来源定位（从第 1 个位置开始显示）</caption>
        <thead><tr><th scope="col">位置 / 角色</th><th scope="col">类别</th><th scope="col">来源与范围</th><th scope="col">省略 / 图像</th></tr></thead>
        <tbody><tr v-for="message in messages.slice(0,visibleCount)" :key="message.index">
          <th scope="row">{{ message.index + 1 }} · {{ message.role }}</th>
          <td>{{ message.section || '未记录类别' }}</td>
          <td><EntityLink v-if="message.event_id" type="event" :id="message.event_id" :scene-id="sceneId" label="查看原始事件" /><span v-else>未记录直接事件来源</span>
            <p v-if="message.text_range">原文字符 [{{ message.text_range.start }}, {{ message.text_range.end }}) / {{ message.text_range.total }}</p>
            <p v-if="message.tool_call_id">工具调用 {{ message.tool_call_id }}</p>
            <p v-if="message.tool_call_id && message.tool_presentations == null">本次请求未单独记录资料页范围；整轮已读范围见原轨迹。</p>
            <ul v-if="message.tool_presentations?.length"><li v-for="(page,pageIndex) in message.tool_presentations" :key="pageIndex">
              <EntityLink type="result" :id="page.result_id" :scene-id="sceneId" :span="page" label="查看本次保留的资料范围" />
              <p>[{{ page.start }}, {{ page.end }}) / {{ page.total }} · {{ page.coordinate_unit === 'records' ? '记录坐标' : '字符坐标' }}<span v-if="page.evidence_ref"> · {{ page.evidence_ref }}</span></p>
            </li></ul>
            <p v-if="message.result_locator_status === 'changed_after_declaration'">旧资料目录在声明后有变化，编号未登记为本次保留位置。</p>
            <ul v-if="message.result_locator_status === 'retained' && message.result_locators?.length"><li v-for="item in message.result_locators" :key="item.ref">
              {{ item.ref }} · {{ item.tool || '原工具未记录' }} · <EntityLink v-if="item.result_id" type="result" :id="item.result_id" :scene-id="sceneId" label="查看旧资料" /><span v-else>装配时不可用</span>
            </li></ul>
            <p v-if="message.summary_ref_status === 'changed_after_declaration'">历史摘要消息在声明后变化，未把原批次清单登记为本次保留内容。</p>
            <ul v-if="message.summary_ref_status === 'retained' && message.summary_refs?.length"><li v-for="item in message.summary_refs" :key="item.batch_id">
              批次 <code>{{ item.batch_id }}</code> · 版本 {{ item.generation_version }} · 覆盖 [{{ item.range[0] }}:{{ item.range[1] }}, {{ item.range[2] }}:{{ item.range[3] }})
            </li></ul>
          </td>
          <td>{{ message.omitted === null ? '省略状态未记录' : message.omitted ? '标记省略' : '未标记省略' }}<p v-if="message.omitted">{{ message.omission_reason || '省略原因未单独记录' }}</p><p>图像块 {{ message.images.length }}</p>
            <details v-if="message.images.length" class="request-images">
              <summary>消息 {{ message.index + 1 }} 的图像定位</summary>
              <ul><li v-for="image in message.images" :key="image.part_index">
                内容块 {{ image.part_index + 1 }} · {{ image.type }}
                <p v-if="image.asset_id">资产 <code>{{ image.asset_id }}</code></p>
                <p v-else>未记录资产定位；不从当前资料反推。</p>
              </li></ul>
            </details>
          </td>
        </tr></tbody>
      </table></div>
      <v-btn v-if="visibleCount < messages.length" variant="text" size="small" @click="visibleCount += 25">继续显示 25 个位置（已显示 {{ visibleCount }} / {{ messages.length }}）</v-btn>
      <details :open="rawOpen" @toggle="rawOpen=$event.target.open"><summary>完整定位字段</summary><ResourceViewer v-if="rawOpen" title="该调用已保存的材料记录" :content="record" /></details>
    </template>
    <p v-else class="request-note">已有材料记录，但当前页面不支持其格式版本 {{ record.format_version }}；未将它解释成空清单。</p>
  </section>
</template>

<style scoped>
.request-record{min-width:0}.request-record h3{font-size:15px;margin:0 0 12px}.request-note{font-size:12px;color:var(--muted);line-height:1.7;overflow-wrap:anywhere}.request-facts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;font-size:12px}.request-facts dt{color:var(--muted)}.request-facts dd{margin:4px 0 0;overflow-wrap:anywhere}.request-record summary{cursor:pointer;font-size:12px;line-height:1.7}.request-record details{margin:12px 0}.request-tools{font-size:12px;line-height:1.7;overflow-wrap:anywhere}.request-table-wrap{overflow-x:auto;margin-top:12px}.request-record table{width:100%;border-collapse:collapse;font-size:12px;text-align:left}.request-record caption{text-align:left;color:var(--muted);padding:8px 0}.request-record th,.request-record td{padding:10px 8px;border-bottom:1px solid var(--line);vertical-align:top;overflow-wrap:anywhere;min-width:90px}.request-record td p{margin:5px 0 0;color:var(--muted)}.request-record th{font-weight:500}.request-record :deep(.entity-link){max-width:180px;font-size:12px}
@media(max-width:600px){.request-facts{grid-template-columns:minmax(0,1fr)}}
.request-images summary{padding:2px 0}.request-images ul{margin:8px 0;padding-left:16px}.request-images li+li{margin-top:8px}.request-images code{white-space:normal;overflow-wrap:anywhere}
</style>
