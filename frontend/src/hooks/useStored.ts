import { useState } from 'react'

/** useState remembered in localStorage (per-browser UI preference only). */
export function useStored<T extends string>(key: string, initial: T, allowed: readonly T[]) {
  const [value, setValue] = useState<T>(() => {
    try {
      const saved = localStorage.getItem(key) as T | null
      return saved && allowed.includes(saved) ? saved : initial
    } catch {
      return initial
    }
  })
  const set = (v: T) => {
    setValue(v)
    try {
      localStorage.setItem(key, v)
    } catch {
      /* storage unavailable */
    }
  }
  return [value, set] as const
}
