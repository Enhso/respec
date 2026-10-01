// Single source of truth for the §12 redundant-signal contract.
//
// Knowledge vs Hypothesis nodes carry: colour + leading glyph (K/H) + label.
// Sourced vs Inferred edges carry: colour + line-style (solid/dashed) + label.
// The redundant axes mean the distinction survives greyscale and the two
// common colour-vision deficiencies — see ADR-020 + the panel-*.png assets.
//
// `graph` / `edge` keys are CSS-consumer shapes. `cytoEdge` mirrors `edge`
// in the Cytoscape stylesheet vocabulary (it doesn't read CSS variables).
// They live side by side so a token change touches both at once.

export const tokens = {
  graph: {
    knowledge: { fill: '#3B82F6', glyph: 'K', label: 'Knowledge' },
    hypothesis: { fill: '#F59E0B', glyph: 'H', label: 'Hypothesis' },
  },
  edge: {
    sourced: { stroke: '#10B981', dash: 'solid', label: 'Sourced' },
    inferred: { stroke: '#F59E0B', dash: 'dashed', label: 'Inferred' },
  },
  cytoEdge: {
    sourced: { 'line-color': '#10B981', 'line-style': 'solid' as const },
    inferred: { 'line-color': '#F59E0B', 'line-style': 'dashed' as const },
  },
  color: {
    bg: '#0B0F19',
    surface: '#1A1F2E',
    text: '#E6E8EE',
    subtext: '#9AA3B2',
    accent: '#3B82F6',
    danger: '#EF4444',
  },
  font: {
    sans: '"Inter", system-ui, sans-serif',
    mono: '"JetBrains Mono", ui-monospace, monospace',
  },
  space: { 0: 0, 1: 4, 2: 8, 3: 12, 4: 16, 5: 24, 6: 32 } as const,
  radius: { sm: 4, md: 8, lg: 12 },
} as const

export type Tokens = typeof tokens
export type GraphKind = keyof Tokens['graph']
export type EdgeKind = keyof Tokens['edge']
