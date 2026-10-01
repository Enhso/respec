interface DigestOtherNode {
  id: string
  label: string
  name: string
  event_date_from?: string | null
  event_date_to?: string | null
}

interface DigestObservation {
  date_from?: string | null
  date_to?: string | null
}

interface DigestEdge {
  type: string
  display_confidence: number
  temporal_observations: DigestObservation[]
}

export interface DigestInput {
  other_node: DigestOtherNode
  edge: DigestEdge
}

export interface TopConnectedEntry {
  id: string
  name: string
  edgeCount: number
}

export interface RelationshipMixEntry {
  type: string
  count: number
}

export interface Digest {
  connectionCount: number
  documentCount: number
  firstSeen: string | null
  lastSeen: string | null
  topConnected: TopConnectedEntry[]
  relationshipMix: RelationshipMixEntry[]
}

interface NeighborAgg {
  id: string
  name: string
  label: string
  edgeCount: number
  maxConfidence: number
}

/**
 * Fold an entity's adjacency payload into a compact digest.
 *
 * @param relationships - Incident edges from `GET /entities/{id}/relationships`.
 */
export function computeDigest(relationships: DigestInput[]): Digest {
  const neighbors = new Map<string, NeighborAgg>()
  const mixCounts = new Map<string, number>()
  let firstSeen: string | null = null
  let lastSeen: string | null = null

  const considerDate = (d: string | null | undefined): void => {
    if (!d) return
    if (firstSeen === null || d < firstSeen) firstSeen = d
    if (lastSeen === null || d > lastSeen) lastSeen = d
  }

  for (const { other_node: node, edge } of relationships) {
    const existing = neighbors.get(node.id)
    if (existing) {
      existing.edgeCount += 1
      existing.maxConfidence = Math.max(existing.maxConfidence, edge.display_confidence)
    } else {
      neighbors.set(node.id, {
        id: node.id,
        name: node.name,
        label: node.label,
        edgeCount: 1,
        maxConfidence: edge.display_confidence,
      })
    }

    mixCounts.set(edge.type, (mixCounts.get(edge.type) ?? 0) + 1)

    for (const obs of edge.temporal_observations) {
      considerDate(obs.date_from)
      considerDate(obs.date_to)
    }
    considerDate(node.event_date_from)
    considerDate(node.event_date_to)
  }

  const neighborList = [...neighbors.values()]

  const topConnected = [...neighborList]
    .sort((a, b) => {
      if (b.edgeCount !== a.edgeCount) return b.edgeCount - a.edgeCount
      if (b.maxConfidence !== a.maxConfidence) return b.maxConfidence - a.maxConfidence
      return a.name.localeCompare(b.name)
    })
    .slice(0, 3)
    .map((n) => ({ id: n.id, name: n.name, edgeCount: n.edgeCount }))

  const relationshipMix = [...mixCounts.entries()]
    .sort((a, b) => (b[1] !== a[1] ? b[1] - a[1] : a[0].localeCompare(b[0])))
    .map(([type, count]) => ({ type, count }))

  return {
    connectionCount: neighborList.length,
    documentCount: neighborList.filter((n) => n.label === 'Document').length,
    firstSeen,
    lastSeen,
    topConnected,
    relationshipMix,
  }
}
