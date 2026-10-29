import type { Topic, TopicSettings } from '../api'

/**
 * The topic list as it was last shown, for one project: coming back from a topic, the list is there
 * at once — with the pages loaded by «Показать ещё» — and scrolls back to where the topic was opened.
 */
export const topicsCache: {
  project: number | null
  items: Topic[] | null
  hasMore: boolean
  settings: TopicSettings | null
  scrollY: number | null
} = { project: null, items: null, hasMore: false, settings: null, scrollY: null }

/** A topic hidden from its page: gone from the list before the list asks the server again. */
export function forgetTopic(id: number) {
  if (topicsCache.items) topicsCache.items = topicsCache.items.filter((t) => t.id !== id)
}
