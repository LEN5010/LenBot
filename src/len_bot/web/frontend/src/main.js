import { createApp } from 'vue'
import App from './App.vue'
import router from './router/index.js'
import vuetify from './plugins/vuetify.js'
import './styles/tokens.css'
import './styles/base.css'

createApp(App).use(vuetify).use(router).mount('#app')
