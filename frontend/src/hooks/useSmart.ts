import { useEffect, useSyncExternalStore } from 'react'
import { api, type SmartState } from '../api'
import { useMe } from './useMe'

// One copy for the feed and its «AI» sheet; per project (the key), reloaded when it changes.
let state: { key: string; value: SmartState } | null = null
let loading: Promise<void> | null = null
const listeners = new Set<() => void>()

function set(next: typeof state) {
  state = next
  listeners.forEach((fn) => fn())
}

export function setSmart(key: string, value: SmartState) {
  set({ key, value })
}

function load(key: string): Promise<void> {
  loading ??= api
    .smart()
    .then((value) => set({ key, value }))
    .catch(() => {})
    .finally(() => {
      loading = null
    })
  return loading
}

/** The smart feed the viewer sees; null for the admin and until loaded. */
export function useSmart(): { state: SmartState | null; key: string; reload: () => Promise<void> } {
  const me = useMe()
  const key = `${me.role}:${me.project?.id ?? ''}`
  const enabled = me.role !== 'admin' && (me.role === 'user' || me.ai_linked)
  const value = useSyncExternalStore(
    (fn) => {
      listeners.add(fn)
      return () => listeners.delete(fn)
    },
    () => state,
  )
  useEffect(() => {
    if (enabled && state?.key !== key) load(key)
  }, [enabled, key])
  return { state: enabled && value?.key === key ? value.value : null, key, reload: () => load(key) }
}
