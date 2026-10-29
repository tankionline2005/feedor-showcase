import { useCallback, useEffect, useState } from 'react'
import { api, type Channel } from '../api'

/** Channel list, re-polled while some channels are still joining or being removed. */
export function useChannels() {
  const [items, setItems] = useState<Channel[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const reload = useCallback(async () => {
    try {
      const res = await api.channels()
      setItems(res.items)
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])

  useEffect(() => {
    reload()
  }, [reload])

  const busy = items?.some((c) => c.status === 'pending') ?? false
  useEffect(() => {
    const timer = window.setInterval(reload, busy ? 5_000 : 60_000)
    return () => window.clearInterval(timer)
  }, [reload, busy])

  return { items, setItems, error, reload }
}
