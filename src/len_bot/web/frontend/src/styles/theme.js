// The one color table. It becomes both the Vuetify theme and the CSS
// variables (`--ink`, `--error-bg`, …) that scoped styles use.
// `brand` is the LenBot pink; `primary` is a deeper shade of the same pink so
// that white button text and pink links stay readable.
export const palette = {
  brand: '#e799b0',
  'brand-soft': '#fcebf1',
  primary: '#c25579',
  'primary-bg': '#fcebf1',
  'on-primary': '#ffffff',
  ink: '#2d2430',
  muted: '#7d6f7a',
  line: '#f0e2e8',
  'line-strong': '#e4ceda',
  // Shell background, the content canvas inside it, and cards on the canvas.
  page: '#f8eef3',
  canvas: '#fdf9fb',
  surface: '#ffffff',
  // Neutral-pink states: hover, a segmented-control track, and the selected row or tab.
  hover: '#fbf1f5',
  track: '#f6e8ee',
  selected: '#fbe3ec',
  success: '#2f9468',
  'success-bg': '#e5f5ec',
  warning: '#b86e14',
  'warning-bg': '#fcf0dd',
  error: '#cc3d4d',
  'error-bg': '#fde8ea',
  info: '#3f7fc0',
  'info-bg': '#e7f0fa',
  idle: '#cdbfc7',
  'code-bg': '#faf4f7',
}

// Background and letter colors for scene avatars, picked per scene.
export const avatarTints = [['#fde4ec', '#c25579'], ['#f1e6fb', '#8a5cb8'], ['#ffeede', '#b9692c'],
  ['#e3f3ee', '#2f8a68'], ['#e6effb', '#4a78b0'], ['#fff3d6', '#9a7414']]

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
