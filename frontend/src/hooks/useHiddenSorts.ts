import { useSyncExternalStore } from 'react'

// One list for the Top and a channel's posts: the sort chips the user put away.
const KEY = 'sorts.hidden'
const listeners = new Set<() => void>()

function read(): string[] {
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) ?? '[]')
    return Array.isArray(saved) ? saved.filter((v) => typeof v === 'string') : []
  } catch {
    return []
  }
}

let hidden: string[] = read()

export function setHiddenSorts(next: string[]) {
  hidden = next
  try {
    localStorage.setItem(KEY, JSON.stringify(next))
  } catch {
    /* storage unavailable */
  }
  listeners.forEach((fn) => fn())
}

/** The sorts the user hid (per browser, like the chosen sort itself). */
export function useHiddenSorts(): string[] {
  return useSyncExternalStore(
    (fn) => {
      listeners.add(fn)
      return () => listeners.delete(fn)
    },
    () => hidden,
  )
}
