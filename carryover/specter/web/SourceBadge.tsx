import { tokens } from './tokens'
import type { EdgeKind } from './tokens'

export interface SourceBadgeProps {
  source: EdgeKind
}

// The dash glyph is rendered as an inline SVG so the visual carries on
// greyscale next to the label (per §12 redundant-signal contract).
export function SourceBadge({ source }: SourceBadgeProps) {
  const { stroke, dash, label } = tokens.edge[source]
  const dashArray = dash === 'dashed' ? '6 4' : undefined
  return (
    <span
      role="img"
      aria-label={`${label} edge badge`}
      // Focusable so the legend strip in the shell is tab-reachable; see ADR-020.
      // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex
      tabIndex={0}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: tokens.space[2],
        padding: `${tokens.space[1]}px ${tokens.space[3]}px`,
        background: tokens.color.surface,
        color: tokens.color.text,
        border: `1px solid ${stroke}`,
        borderRadius: tokens.radius.md,
        fontFamily: tokens.font.sans,
        fontWeight: 500,
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
      <svg width={28} height={10} aria-hidden>
        <line
          x1={1}
          y1={5}
          x2={27}
          y2={5}
          stroke={stroke}
          strokeWidth={2}
          strokeDasharray={dashArray}
        />
      </svg>
      {label}
    </span>
  )
}
