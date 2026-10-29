import { useEffect, useSyncExternalStore } from 'react'
import { api, type GroupsState } from '../api'

// One copy for the whole app: every post card's send button needs the project's groups.
let state: GroupsState | null = null
let loading: Promise<void> | null = null
let generation = 0 // a new one for each project opened
const listeners = new Set<() => void>()

function set(next: GroupsState) {
  state = next
  listeners.forEach((fn) => fn())
}

export function setGroups(next: GroupsState) {
  set(next)
}

export function reloadGroups(): Promise<void> {
  const asked = generation
  loading ??= api
    .groups()
    // an answer for the project before another was opened is dropped
    .then((next) => {
      if (asked === generation) set(next)
    })
    .catch(() => {})
    .finally(() => {
      loading = null
    })
  return loading
}

/** The current project's groups («Группы проекта»); loaded on first use. `enabled` is false for the admin. */
export function useGroups(enabled = true): GroupsState | null {
  const value = useSyncExternalStore(
    (fn) => {
      listeners.add(fn)
      return () => listeners.delete(fn)
    },
    () => state,
  )
  useEffect(() => {
    if (enabled && state === null) reloadGroups()
  }, [enabled])
  return enabled ? value : null
}

/** Signed out: the next person must not see these. */
export function resetGroups() {
  state = null
  listeners.forEach((fn) => fn())
}

/** Another project opened: its own groups, at once for whoever shows them. */
export function projectChangedGroups() {
  generation++
  loading = null
  resetGroups()
  if (listeners.size) reloadGroups()
}
