import { tokens } from './tokens'
import type { GraphKind } from './tokens'

export interface GraphBadgeProps {
  graph: GraphKind
}

export function GraphBadge({ graph }: GraphBadgeProps) {
  const { fill, glyph, label } = tokens.graph[graph]
  return (
    <span
      role="img"
      aria-label={`${label} graph badge`}
      // Focusable so the legend strip in the shell is tab-reachable; see ADR-020.
      // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex
      tabIndex={0}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: tokens.space[2],
        padding: `${tokens.space[1]}px ${tokens.space[3]}px`,
        background: fill,
        color: '#fff',
        borderRadius: tokens.radius.md,
        fontFamily: tokens.font.sans,
        fontWeight: 600,
        fontSize: 13,
        lineHeight: 1,
        outline: 'none',
      }}
      onFocus={(e) => {
        e.currentTarget.style.outline = `2px solid var(--specter-accent)`
        e.currentTarget.style.outlineOffset = '2px'
      }}
      onBlur={(e) => {
        e.currentTarget.style.outline = 'none'
      }}
    >
      <span
        aria-hidden
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          width: 18,
          height: 18,
          borderRadius: '50%',
          background: 'rgba(255,255,255,0.25)',
          fontFamily: tokens.font.mono,
          fontSize: 11,
        }}
      >
        {glyph}
      </span>
      {label}
    </span>
  )
}
