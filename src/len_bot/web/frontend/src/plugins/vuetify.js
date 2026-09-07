import 'vuetify/styles'
import { createVuetify } from 'vuetify'
import { aliases, mdi } from 'vuetify/iconsets/mdi-svg'
import { zhHans } from 'vuetify/locale'

export default createVuetify({
  locale:{locale:'zhHans',messages:{zhHans}},
  icons:{defaultSet:'mdi',aliases,sets:{mdi}},
  display:{mobileBreakpoint:1024},
  theme:{defaultTheme:'light',themes:{light:{dark:false,colors:{primary:'#2563eb',secondary:'#52647b',background:'#f4f6f9',surface:'#ffffff','surface-variant':'#eef2f7','on-surface-variant':'#63748b',success:'#16845c',warning:'#a86413',error:'#c84040',info:'#326fa8','on-background':'#172b46','on-surface':'#172b46'}}}},
  defaults:{
    VBtn:{variant:'flat',rounded:'lg',elevation:0},
    VCard:{elevation:0,border:true,rounded:'lg'},
    VTextField:{variant:'outlined',density:'compact',hideDetails:'auto'},
    VTextarea:{variant:'outlined',density:'compact',hideDetails:'auto'},
    VSelect:{variant:'outlined',density:'compact',hideDetails:'auto'},
    VAutocomplete:{variant:'outlined',density:'compact',hideDetails:'auto'},
    VCombobox:{variant:'outlined',density:'compact',hideDetails:'auto'},
    VCheckbox:{color:'primary',density:'compact',hideDetails:'auto'},
    VSwitch:{color:'primary',density:'compact',hideDetails:'auto',inset:true},
    VAlert:{density:'compact',rounded:'lg'},
    VDialog:{maxWidth:720},
  },
})
