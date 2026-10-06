import 'vuetify/styles'
import { createVuetify } from 'vuetify'
import { aliases, mdi } from 'vuetify/iconsets/mdi-svg'
import { zhHans } from 'vuetify/locale'
import { vuetifyColors } from '../styles/theme.js'

const field = { variant: 'outlined', density: 'comfortable', hideDetails: 'auto', rounded: 'lg', color: 'primary' }

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
    // Switches that are on use the brand pink; it is one of the few places the accent appears.
    VSwitch: { color: 'brand', density: 'compact', hideDetails: 'auto', inset: true },
    VAlert: { density: 'compact', rounded: 'lg', variant: 'tonal' },
    VChip: { size: 'small', variant: 'tonal' },
    VProgressLinear: { color: 'primary', rounded: true },
    VProgressCircular: { color: 'primary' },
    VMenu: { transition: 'fade-transition' },
    // Drawn like the page tabs (gray track, white selected pill) by styles/base.css.
    VBtnToggle: { density: 'compact', variant: 'text', rounded: 'lg' },
    VDialog: { transition: 'fade-transition' },
    VExpansionPanels: { variant: 'accordion' },
  },
})
