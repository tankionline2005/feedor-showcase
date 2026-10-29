import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type Post, type PostQuery } from '../api'
import { subscribeLive } from '../lib/live'

const PAGE = 30
/** Safety net in case the live stream is down (proxy, sleep, network). */
const FALLBACK_REFRESH_MS = 120_000
/** Several changes in a row (one per channel) become one request. */
const LIVE_DEBOUNCE_MS = 800
/** Below this scroll offset new posts are inserted right away; further down they wait behind a pill. */
const NEAR_TOP = 120

export function usePosts(query: Omit<PostQuery, 'offset' | 'limit'>) {
  const [items, setItems] = useState<Post[]>([])
  const [hasMore, setHasMore] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  /** New posts held back while the reader is scrolled down a chronological feed. */
  const [pending, setPending] = useState(0)
  const request = useRef(0)
  const itemsRef = useRef(items)
  itemsRef.current = items
  const hasMoreRef = useRef(hasMore)
  hasMoreRef.current = hasMore
  const latest = useRef<{ items: Post[]; hasMore: boolean } | null>(null)
  const key = JSON.stringify(query)
  const chronological = (query.sort ?? 'new') === 'new'

  const fetchPage = useCallback(
    async (offset: number, limit: number) => api.posts({ ...JSON.parse(key), offset, limit }),
    [key],
  )

  // first page whenever the query changes
  useEffect(() => {
    const id = ++request.current
    setLoading(true)
    setError(null)
    setPending(0)
    latest.current = null
    fetchPage(0, PAGE)
      .then((res) => {
        if (id !== request.current) return
        setItems(res.items)
        setHasMore(res.has_more)
      })
      .catch((e: Error) => id === request.current && setError(e.message))
      .finally(() => id === request.current && setLoading(false))
  }, [fetchPage])

  const loadMore = useCallback(async () => {
    if (loading || !hasMore) return
    const id = request.current
    setLoading(true)
    try {
      const res = await fetchPage(itemsRef.current.length, PAGE)
      if (id !== request.current) return
      setItems((prev) => {
        const seen = new Set(prev.map((p) => p.id))
        return [...prev, ...res.items.filter((p) => !seen.has(p.id))]
      })
      setHasMore(res.has_more)
    } catch (e) {
      if (id === request.current) setError((e as Error).message)
    } finally {
      if (id === request.current) setLoading(false)
    }
  }, [fetchPage, hasMore, loading])

  /** Shows the held-back posts and scrolls up to them. */
  const showPending = useCallback(() => {
    if (latest.current) {
      setItems(latest.current.items)
      setHasMore(latest.current.hasMore)
      latest.current = null
    }
    setPending(0)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }, [])

  /**
   * Reloads what is on screen, keeping the scroll position. `live`: triggered by the server,
   * not by the user — in a chronological feed read further down, new posts don't push the
   * text away; the cards on screen update in place and the new ones wait behind a pill.
   */
  const refresh = useCallback(
    async (live = false) => {
      const id = ++request.current
      try {
        const res = await fetchPage(0, Math.min(100, Math.max(PAGE, itemsRef.current.length)))
        if (id !== request.current) return
        setError(null)
        const current = itemsRef.current
        // only the first 100 are refetched; cards loaded further down stay as they were
        const fresh = new Map(res.items.map((p) => [p.id, p]))
        const tail = res.has_more ? current.slice(res.items.length).filter((p) => !fresh.has(p.id)) : []
        const next = { items: [...res.items, ...tail], hasMore: tail.length ? hasMoreRef.current : res.has_more }
        const known = new Set(current.map((p) => p.id))
        const newer = current.length
          ? res.items.filter((p) => !known.has(p.id) && p.posted_at > current[0].posted_at)
          : []
        if (live && chronological && newer.length > 0 && window.scrollY > NEAR_TOP) {
          setItems(current.map((p) => fresh.get(p.id) ?? p))
          latest.current = next
          setPending(newer.length)
          return
        }
        setItems(next.items)
        setHasMore(next.hasMore)
        latest.current = null
        setPending(0)
      } catch (e) {
        if (id === request.current && !live) setError((e as Error).message)
      }
    },
    [fetchPage, chronological],
  )

  // the server says posts changed; a hidden tab catches up when it becomes visible
  useEffect(() => {
    let timer: number | undefined
    let stale = false
    const run = () => {
      if (document.visibilityState === 'visible') refresh(true)
      else stale = true
    }
    const onChange = () => {
      window.clearTimeout(timer)
      timer = window.setTimeout(run, LIVE_DEBOUNCE_MS)
    }
    const onVisible = () => {
      if (document.visibilityState === 'visible' && stale) {
        stale = false
        refresh(true)
      }
    }
    const unsubscribe = subscribeLive(onChange)
    const fallback = window.setInterval(run, FALLBACK_REFRESH_MS)
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      unsubscribe()
      window.clearTimeout(timer)
      window.clearInterval(fallback)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [refresh])

  // scrolling back to the top shows the held-back posts by itself
  useEffect(() => {
    if (!pending) return
    const onScroll = () => window.scrollY <= NEAR_TOP && showPending()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [pending, showPending])

  const manualRefresh = useCallback(() => refresh(false), [refresh])

  /** A post that left this list (the smart feed's decision on it changed): gone at once. */
  const remove = useCallback((id: number) => setItems((prev) => prev.filter((p) => p.id !== id)), [])

  return { items, hasMore, loading, error, pending, showPending, loadMore, refresh: manualRefresh, remove }
}
