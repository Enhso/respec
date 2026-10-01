import type { GraphKind } from '../../theme/tokens'
import { formatEventDate } from '../landing/eventDate'
import { observationSummary } from './observationSummary'

export type TimelineItemKind = 'event' | 'span' | 'document'

interface TimelineOtherNode {
  id: string
  label: string
  name: string
  graph: GraphKind
  event_date_from?: string | null
  event_date_to?: string | null
  event_date_precision?: string | null
  event_location_name?: string | null
  document_date?: string | null
}

interface TimelineObservation {
  date_from?: string | null
  date_to?: string | null
  date_precision: string
  confidence: number
  observed_at: string
}

interface TimelineEdge {
  id: string
  type: string
  temporal_observations: TimelineObservation[]
}

export interface TimelineIncidentEdge {
  other_node: TimelineOtherNode
  edge: TimelineEdge
  direction: 'outgoing' | 'incoming'
}

export interface TimelineItem {
  kind: TimelineItemKind
  key: string
  nodeId: string
  label: string
  dateLabel: string | null
  sortKey: string | null
  ranged: boolean
  graph: GraphKind
  locationName?: string | null
}

export interface TimelineGroups {
  years: { year: string; items: TimelineItem[] }[]
  undated: TimelineItem[]
  documentCount: number
}

const KIND_ORDER: Record<TimelineItemKind, number> = { event: 0, span: 1, document: 2 }

function compareItems(a: TimelineItem, b: TimelineItem): number {
  const aKey = a.sortKey ?? ''
  const bKey = b.sortKey ?? ''
  if (aKey !== bKey) return aKey < bKey ? 1 : -1
  if (a.kind !== b.kind) return KIND_ORDER[a.kind] - KIND_ORDER[b.kind]
  return a.label.localeCompare(b.label)
}

function considerDedup(map: Map<string, TimelineItem>, nodeId: string, item: TimelineItem): void {
  const existing = map.get(nodeId)
  if (!existing || (existing.sortKey === null && item.sortKey !== null)) {
    map.set(nodeId, item)
  }
}

/**
 * Fold an entity's adjacency payload into a year-grouped timeline.
 *
 * Every rendered date is stored data: an item can only land in a year
 * group when it carries a date; otherwise it lands in the undated bucket
 * (Event/Document only) or is not created at all (dateless relationship
 * observations).
 *
 * @param relationships - Incident edges from `GET /entities/{id}/relationships`.
 * @param opts.includeDocuments - Whether Document items are included.
 */
export function buildTimeline(
  relationships: TimelineIncidentEdge[],
  opts: { includeDocuments: boolean },
): TimelineGroups {
  const eventItems = new Map<string, TimelineItem>()
  const documentItems = new Map<string, TimelineItem>()
  const spanItems: TimelineItem[] = []

  for (const { other_node: node, edge, direction } of relationships) {
    if (node.label === 'Event' && (edge.type === 'INVOLVED' || edge.type === 'PARTICIPATED_IN')) {
      const sortKey = node.event_date_from ?? null
      const dateLabel = sortKey
        ? formatEventDate({
            date_from: node.event_date_from as string,
            date_to: node.event_date_to ?? null,
            date_precision: (node.event_date_precision ?? 'day') as
              | 'exact'
              | 'day'
              | 'month'
              | 'year'
              | 'range',
          })
        : null

      considerDedup(eventItems, node.id, {
        kind: 'event',
        key: node.id,
        nodeId: node.id,
        label: node.name,
        dateLabel,
        sortKey,
        ranged: node.event_date_to != null && node.event_date_to !== node.event_date_from,
        graph: node.graph,
        locationName: node.event_location_name ?? null,
      })
      continue
    }

    if (node.label === 'Document') {
      const sortKey = node.document_date ?? null
      const dateLabel = sortKey
        ? `reported ${formatEventDate({ date_from: sortKey, date_precision: 'day' })}`
        : null

      considerDedup(documentItems, node.id, {
        kind: 'document',
        key: node.id,
        nodeId: node.id,
        label: node.name,
        dateLabel,
        sortKey,
        ranged: false,
        graph: node.graph,
      })
      continue
    }

    edge.temporal_observations.forEach((obs, index) => {
      if (!obs.date_from) return
      spanItems.push({
        kind: 'span',
        key: `${edge.id}:${index}`,
        nodeId: node.id,
        label: `${edge.type} ${direction === 'outgoing' ? '->' : '<-'} ${node.name}`,
        dateLabel: observationSummary([obs]),
        sortKey: obs.date_from,
        ranged: obs.date_to != null && obs.date_to !== obs.date_from,
        graph: node.graph,
      })
    })
  }

  const documentCount = documentItems.size

  const allItems: TimelineItem[] = [
    ...eventItems.values(),
    ...(opts.includeDocuments ? documentItems.values() : []),
    ...spanItems,
  ]

  const dated = allItems.filter((item) => item.sortKey !== null)
  const undated = allItems.filter((item) => item.sortKey === null).sort(compareItems)

  const yearMap = new Map<string, TimelineItem[]>()
  for (const item of dated) {
    const year = (item.sortKey as string).slice(0, 4)
    const bucket = yearMap.get(year)
    if (bucket) bucket.push(item)
    else yearMap.set(year, [item])
  }

  const years = [...yearMap.entries()]
    .sort((a, b) => (a[0] < b[0] ? 1 : a[0] > b[0] ? -1 : 0))
    .map(([year, items]) => ({ year, items: [...items].sort(compareItems) }))

  return { years, undated, documentCount }
}
