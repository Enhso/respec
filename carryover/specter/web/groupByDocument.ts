import type { components } from '../../api/schema.gen'

type QueueItem = components['schemas']['QueueItem']

/**
 * Group queue items by their document_id.
 *
 * `document_id` is optional and nullable on the wire; both the absent-key
 * and explicit-null shapes normalize into the same `null` group (inference
 * candidates have no source document).
 *
 * @param items - Flat array of queue items from the API.
 * @returns Map keyed by document_id (or `null`) with arrays of items belonging to each group.
 */
export function groupByDocument(items: QueueItem[]): Map<string | null, QueueItem[]> {
  const groups = new Map<string | null, QueueItem[]>()
  for (const item of items) {
    const key = item.document_id ?? null
    const existing = groups.get(key)
    if (existing) {
      existing.push(item)
    } else {
      groups.set(key, [item])
    }
  }
  return groups
}
