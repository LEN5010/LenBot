// The one color table. It becomes both the Vuetify theme and the CSS
// variables (`--ink`, `--error-bg`, …) that scoped styles use.
// `brand` (#e799b0) is an accent only: the mark, the selected navigation bar,
// charts and switches that are on. Buttons and links use `primary`, a deeper
// shade that keeps white text above 4.5:1. Everything else is neutral gray.
export const palette = {
  brand: '#e799b0',
  'brand-soft': '#fbeff3',
  primary: '#b4476a',
  'primary-bg': '#f8e6ec',
  'on-primary': '#ffffff',
  ink: '#1d1b20',
  muted: '#6b6870',
  line: '#e9e9ec',
  'line-strong': '#d9d8dd',
  // Page background behind cards, the sidebar and top bar, and cards.
  page: '#f7f7f8',
  canvas: '#ffffff',
  surface: '#ffffff',
  // Hover, a segmented-control track, and the selected row or tab.
  hover: '#f2f2f4',
  track: '#ededf0',
  selected: '#fbeff3',
  success: '#22875a',
  'success-bg': '#e6f4ec',
  warning: '#a8630f',
  'warning-bg': '#fbf0de',
  error: '#c4323f',
  'error-bg': '#fce9ea',
  info: '#2f6db0',
  'info-bg': '#e8f0f9',
  idle: '#c8c6cc',
  'code-bg': '#f5f5f7',
}

// Background and letter colors for scene avatars, picked per scene.
export const avatarTints = [['#f8e6ec', '#a8405f'], ['#eee8f6', '#6c4f96'], ['#f8ece0', '#93552a'],
  ['#e4f1ec', '#2a7457'], ['#e6edf6', '#3d6596'], ['#f5efdc', '#7d6418']]

export function applyPalette(root = document.documentElement) {
  for (const [name, value] of Object.entries(palette)) root.style.setProperty(`--${name}`, value)
}

export const vuetifyColors = {
  primary: palette.primary,
  brand: palette.brand,
  secondary: palette.muted,
  background: palette.page,
  surface: palette.surface,
  'surface-variant': palette.track,
  'on-surface-variant': palette.muted,
  success: palette.success,
  warning: palette.warning,
  error: palette.error,
  info: palette.info,
  'on-background': palette.ink,
  'on-surface': palette.ink,
}
