import { createApp } from 'vue'
import App from './App.vue'
import router from './router/index.js'
import vuetify from './plugins/vuetify.js'
import { applyPalette } from './styles/theme.js'
import './styles/tokens.css'
import './styles/base.css'
import './styles/shell.css'

applyPalette()
createApp(App).use(vuetify).use(router).mount('#app')
