import cytoscape from 'cytoscape'
import { useEffect, useRef } from 'react'
import { observationSummary } from '../profile/observationSummary'
import { canvasStylesheet } from './canvasStyles'
import type { EgoEdge, EgoNode } from './types'

interface EgoGraphCanvasProps {
  nodes: EgoNode[]
  edges: EgoEdge[]
  focalId: string
  onNodeClick?: (id: string) => void
  onEdgeClick?: (edge: EgoEdge) => void
}

export const ZOOM_CAP = 1.5
const FIT_PADDING = 30

/** Fit the viewport, capping the automatic zoom so sparse graphs render
 *  at natural size. Never constrains zoom-out or manual zoom. */
export function clampedFit(cy: cytoscape.Core): void {
  cy.fit(undefined, FIT_PADDING)
  if (cy.zoom() > ZOOM_CAP) {
    cy.zoom(ZOOM_CAP)
    cy.center()
  }
}

/** Build Cytoscape elements from ego subgraph data. */
export function buildElements(
  nodes: EgoNode[],
  edges: EgoEdge[],
  focalId: string,
): cytoscape.ElementDefinition[] {
  const nodeEls: cytoscape.ElementDefinition[] = nodes.map((n) => ({
    data: { id: n.id, name: n.name, label: n.label, graph: n.graph },
    classes: n.id === focalId ? 'focal' : '',
  }))

  const edgeEls: cytoscape.ElementDefinition[] = edges.map((e) => ({
    data: {
      id: e.id,
      source: e.from_id,
      target: e.to_id,
      type: e.type,
      source_type: e.source_type,
      displayLabel: `${e.type} · ${observationSummary(e.temporal_observations)}`,
    },
  }))

  return [...nodeEls, ...edgeEls]
}

export function EgoGraphCanvas({
  nodes,
  edges,
  focalId,
  onNodeClick,
  onEdgeClick,
}: EgoGraphCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const edgeMapRef = useRef<Map<string, EgoEdge>>(new Map())

  useEffect(() => {
    if (!containerRef.current) return

    edgeMapRef.current = new Map(edges.map((e) => [e.id, e]))

    const cy = cytoscape({
      container: containerRef.current,
      elements: buildElements(nodes, edges, focalId),
      style: canvasStylesheet,
      layout: { name: 'preset' } as cytoscape.LayoutOptions,
    })

    cy.on('tap', 'node', (event: cytoscape.EventObjectNode) => {
      onNodeClick?.(event.target.id())
    })

    cy.on('tap', 'edge', (event: cytoscape.EventObjectEdge) => {
      const edge = edgeMapRef.current.get(event.target.id())
      if (edge) onEdgeClick?.(edge)
    })

    cy.on('mouseover', 'edge', (event: cytoscape.EventObjectEdge) => {
      event.target.addClass('hovered')
    })
    cy.on('mouseout', 'edge', (event: cytoscape.EventObjectEdge) => {
      event.target.removeClass('hovered')
    })

    const layout = cy.layout({
      name: 'cose',
      randomize: false,
      animate: false,
    })
    layout.one('layoutstop', () => clampedFit(cy))
    layout.run()

    const observer = new ResizeObserver(() => {
      cy.resize()
      clampedFit(cy)
    })
    observer.observe(containerRef.current)

    return () => {
      observer.disconnect()
      cy.destroy()
    }
  }, [nodes, edges, focalId, onNodeClick, onEdgeClick])

  return (
    <div
      ref={containerRef}
      data-testid="ego-canvas"
      style={{ flex: 1, minHeight: 600, background: '#0B0F19' }}
    />
  )
}
