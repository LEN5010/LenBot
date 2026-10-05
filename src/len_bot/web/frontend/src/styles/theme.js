// The one color table. It becomes both the Vuetify theme and the CSS
// variables (`--ink`, `--error-bg`, …) that scoped styles use.
export const palette = {
  ink: '#172b46',
  muted: '#63748b',
  line: '#e2e8f0',
  surface: '#ffffff',
  page: '#f4f6f9',
  primary: '#2563eb',
  'primary-bg': '#eef3ff',
  'on-primary': '#ffffff',
  success: '#16845c',
  'success-bg': '#e7f5ef',
  warning: '#a35a12',
  'warning-bg': '#fbeede',
  error: '#b3261e',
  'error-bg': '#fde8e7',
  info: '#326fa8',
  'info-bg': '#e8f0f9',
  idle: '#adb8c7',
  // Neutral states for "where am I": hover and the selected list row or tab.
  hover: '#f5f7fa',
  selected: '#eaeef4',
  'code-bg': '#f6f8fb',
}

export function applyPalette(root = document.documentElement) {
  for (const [name, value] of Object.entries(palette)) root.style.setProperty(`--${name}`, value)
}

export const vuetifyColors = {
  primary: palette.primary,
  secondary: palette.muted,
  background: palette.page,
  surface: palette.surface,
  'surface-variant': palette.selected,
  'on-surface-variant': palette.muted,
  success: palette.success,
  warning: palette.warning,
  error: palette.error,
  info: palette.info,
  'on-background': palette.ink,
  'on-surface': palette.ink,
}
