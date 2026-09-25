<script setup>
const props = defineProps({state: {type: Object, required: true}, page: {type: Object, required: true}})
const { busy, currentSaveOutcome, conflicts } = props.page
const { members, membersRestart, membersDirty, addMember, saveMembers } = props.state
</script>
<template>
  <v-card class="pa-5 form-card">
    <div class="section-header">
      <h2>主播与订阅对象</h2>
      <v-btn
        variant="tonal"
        color="primary"
        :disabled="!!currentSaveOutcome||!!busy"
        @click="addMember"
      >添加对象</v-btn>
    </div>
    <p class="muted my-3">这里登记的是 B 站主播与订阅对象，不是群详情里的 QQ 参与者。名称与别名用于查询，B 站 UID 和直播间号确认实际对象。团体署名保持团体含义，不在这里自动展开。</p>
    <v-alert v-if="membersRestart" type="info" variant="tonal" class="mb-4">系统另有已保存配置等待手动重启。本页编辑已保存的订阅对象，不能据全局重启标记断言当前采集已切换；各插件运行状态另行核对。</v-alert>
    <p v-if="!members.length" class="muted py-4">尚未填写主播与订阅对象；动态与开播插件保持未就绪。</p>
    <v-form :disabled="!!currentSaveOutcome||!!busy" @submit.prevent="saveMembers">
      <v-card v-for="(member,index) in members" :key="index" variant="outlined" class="pa-4 mb-4">
        <div class="section-header mb-3">
          <h3>对象 {{ index+1 }}</h3>
          <v-btn
            variant="text"
            color="error"
            :disabled="!!currentSaveOutcome||!!busy"
            @click="members.splice(index,1)"
          >移除</v-btn>
        </div>
        <div class="form-grid">
          <v-text-field v-model="member.name" label="显示名称" required />
          <v-text-field v-model="member.aliasText" label="别名（逗号或顿号分隔）" />
          <v-text-field
            v-model="member.bilibili_uid"
            label="B 站 UID"
            inputmode="numeric"
            required
          />
          <v-text-field v-model="member.room_id" label="直播间号" inputmode="numeric" required />
        </div>
      </v-card>
      <p class="muted mb-4">已被群订阅的对象需先在相应群中取消订阅，再移除或改名。</p>
      <v-btn
        type="submit"
        color="primary"
        :loading="busy==='members'"
        :disabled="!!currentSaveOutcome||!!busy||!!conflicts.entries.members||!membersDirty"
      >保存主播与订阅对象</v-btn>
    </v-form>
  </v-card>
</template>
<style scoped>
.form-card{max-width:1000px;width:100%}
.section-header{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}
.section-header h2,.form-card>h2{font-size:20px}
.form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;align-items:start}
.form-grid>.v-btn{justify-self:start}
.settings-view p{line-height:1.7}
@media(max-width:650px){
  .form-grid{grid-template-columns:minmax(0,1fr)}
  .section-header{align-items:flex-start}
}
</style>
