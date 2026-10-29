/**
 * One server-sent events connection shared by every screen: the server says "posts changed"
 * and subscribers refetch what they show. EventSource reconnects by itself after errors.
 */
type Listener = () => void

const listeners = new Set<Listener>()
let source: EventSource | null = null
let closeTimer: number | undefined

export function subscribeLive(fn: Listener): () => void {
  listeners.add(fn)
  window.clearTimeout(closeTimer)
  if (!source) {
    source = new EventSource('/api/live')
    source.addEventListener('posts', () => listeners.forEach((l) => l()))
  }
  return () => {
    listeners.delete(fn)
    // switching screens unsubscribes and resubscribes right away — keep the connection
    if (listeners.size === 0) {
      closeTimer = window.setTimeout(() => {
        source?.close()
        source = null
      }, 5000)
    }
  }
}
