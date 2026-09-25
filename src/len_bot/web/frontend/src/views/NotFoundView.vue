<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import PageHeader from '../components/PageHeader.vue'
import { returnTarget } from '../router/navigation.js'
const route=useRoute(),router=useRouter()
const origin=computed(()=>{
  const target=returnTarget(route.query.return_to)
  return target&&router.resolve(target).name!=='not-found'?target:''
})
const directory=computed(()=>({
  scenes:{name:'scenes',label:'群聊目录'},groups:{name:'scenes',label:'群聊目录'},
  jobs:{name:'jobs',label:'工作列表'},tasks:{name:'tasks',label:'提醒与等待'},
  memories:{name:'memories',label:'认识与记忆'},skills:{name:'skills',label:'方法列表'},
  media:{name:'media',label:'媒体与素材'},plugins:{name:'plugins',label:'插件列表'},
  activity:{name:'activity',label:'运行记录'},models:{name:'models',label:'模型设置'},
  agent:{name:'agent-settings',label:'人格与参与'},settings:{name:'settings',label:'系统设置'},
}[route.path.split('/')[1]] || {name:'overview',label:'运行概览'}))
</script>
<template>
  <div class="page-stack">
    <PageHeader title="页面未找到" description="这个地址没有对应页面；不会为此修改配置或创建对象。" />
    <v-card>
      <v-card-text class="empty-state">
        <p>404 · 未知页面</p>
        <v-btn v-if="origin" color="primary" :to="origin">返回来源页面</v-btn>
        <v-btn v-else color="primary" :to="{name:directory.name}">返回{{ directory.label }}</v-btn>
      </v-card-text>
    </v-card>
  </div>
</template>
