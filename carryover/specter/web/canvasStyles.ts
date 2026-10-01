import type { StylesheetStyle } from 'cytoscape'
import { tokens } from '../../theme/tokens'

export const canvasStylesheet: StylesheetStyle[] = [
  {
    selector: 'node[graph = "knowledge"]',
    style: {
      'background-color': tokens.graph.knowledge.fill,
      label: 'data(name)',
      color: tokens.color.text,
      'font-size': 9,
      'font-family': tokens.font.sans,
      'text-valign': 'bottom',
      'text-margin-y': 4,
      width: 8,
      height: 8,
    },
  },
  {
    selector: 'node[graph = "hypothesis"]',
    style: {
      'background-color': tokens.graph.hypothesis.fill,
      label: 'data(name)',
      color: tokens.color.text,
      'font-size': 9,
      'font-family': tokens.font.sans,
      'text-valign': 'bottom',
      'text-margin-y': 4,
      width: 8,
      height: 8,
    },
  },
  {
    selector: 'node.focal',
    style: {
      width: 14,
      height: 14,
      'border-width': 3,
      'border-color': '#fff',
    },
  },
  {
    selector: 'edge[source_type = "sourced"]',
    style: {
      ...tokens.cytoEdge.sourced,
      width: 1,
      'font-size': 8,
      color: tokens.color.subtext,
      'text-rotation': 'autorotate',
      'text-background-color': tokens.color.bg,
      'text-background-opacity': 0.7,
      'text-background-padding': '2px',
      'target-arrow-color': tokens.edge.sourced.stroke,
      'target-arrow-shape': 'triangle',
      'arrow-scale': 0.7,
      'curve-style': 'bezier',
    },
  },
  {
    selector: 'edge[source_type = "inferred"]',
    style: {
      ...tokens.cytoEdge.inferred,
      width: 1,
      'font-size': 8,
      color: tokens.color.subtext,
      'text-rotation': 'autorotate',
      'text-background-color': tokens.color.bg,
      'text-background-opacity': 0.7,
      'text-background-padding': '2px',
      'target-arrow-color': tokens.edge.inferred.stroke,
      'target-arrow-shape': 'triangle',
      'arrow-scale': 0.7,
      'curve-style': 'bezier',
    },
  },
  {
    selector: 'edge.hovered, edge:selected',
    style: {
      label: 'data(displayLabel)',
    },
  },
]
