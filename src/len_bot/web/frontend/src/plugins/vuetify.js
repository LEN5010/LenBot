import 'vuetify/styles'
import { createVuetify } from 'vuetify'
import { aliases, mdi } from 'vuetify/iconsets/mdi-svg'
import { zhHans } from 'vuetify/locale'
import { vuetifyColors } from '../styles/theme.js'

const field = { variant: 'outlined', density: 'compact', hideDetails: 'auto' }

export default createVuetify({
  locale: { locale: 'zhHans', messages: { zhHans } },
  icons: { defaultSet: 'mdi', aliases, sets: { mdi } },
  display: { mobileBreakpoint: 1024 },
  theme: { defaultTheme: 'light', themes: { light: { dark: false, colors: vuetifyColors } } },
  defaults: {
    VBtn: { variant: 'flat', rounded: 'lg', elevation: 0 },
    VCard: { elevation: 0, border: true, rounded: 'lg' },
    VTextField: field,
    VTextarea: field,
    VSelect: field,
    VAutocomplete: field,
    VCombobox: field,
    VFileInput: field,
    VCheckbox: { color: 'primary', density: 'compact', hideDetails: 'auto' },
    VSwitch: { color: 'primary', density: 'compact', hideDetails: 'auto', inset: true },
    VAlert: { density: 'compact', rounded: 'lg', variant: 'tonal' },
    VChip: { size: 'small', variant: 'tonal' },
    VBtnToggle: { density: 'compact', color: 'primary', variant: 'outlined', divided: true },
    VExpansionPanels: { variant: 'accordion' },
  },
})
