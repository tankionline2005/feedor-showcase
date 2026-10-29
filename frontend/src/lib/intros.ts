import { useEffect, useSyncExternalStore } from 'react'
import { api } from '../api'
import { anyOpen } from './layers'

/**
 * The sections' welcome cards (components/Intro.tsx): which ones this person has seen — the server keeps them, so a
 * phone and a computer agree — and the one open now. A section's cards open by themselves on its first visit, once;
 * the «?» by its title opens them again.
 */
export type IntroKey =
  | 'feed'
  | 'triggers'
  | 'top'
  | 'chat'
  | 'topics'
  | 'publications'
  | 'channels'
  | 'groups'
  | 'team'
  | 'api'
  | 'mcp'

let seen = new Set<string>()
let off = true // until who is signed in is known — and for the admin, who has none of these sections
let current: IntroKey | null = null
const listeners = new Set<() => void>()
const tell = () => listeners.forEach((l) => l())

/** Who signed in: what they have seen already; `enabled` false for the admin. */
export function initIntros(list: string[], enabled: boolean) {
  seen = new Set(list)
  off = !enabled
}

export function openIntro(key: IntroKey) {
  current = key
  tell()
}

if (import.meta.env.DEV) (window as unknown as { openIntro: typeof openIntro }).openIntro = openIntro

/** The cards are closed — by «Продолжить», «Пропустить» or a swipe down: that section's are seen. */
export function closeIntro() {
  const key = current
  current = null
  tell()
  if (key && !seen.has(key)) {
    seen.add(key)
    api.introSeen(key).catch(() => {})
  }
}

export function useOpenIntro(): IntroKey | null {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l)
      return () => listeners.delete(l)
    },
    () => current,
  )
}

/** One-time tips about something new (backend auth.INTRO_KEYS), kept with the cards seen. */
export type TipKey = 'covers' | 'billing-news'

/** The tip is for this person still: not closed yet (and never for the admin). */
export function useTip(key: TipKey): [boolean, () => void] {
  const shown = useSyncExternalStore(
    (l) => {
      listeners.add(l)
      return () => listeners.delete(l)
    },
    () => !off && !seen.has(key),
  )
  const close = () => {
    if (seen.has(key)) return
    seen.add(key)
    tell()
    api.introSeen(key).catch(() => {})
  }
  return [shown, close]
}

/** A section shown: its cards open by themselves the first time, a moment after it appears (its transition done) —
 * or, if the person has already opened a window there, once it is closed. */
export function useFirstVisit(key: IntroKey, when = true) {
  useEffect(() => {
    if (!when || off || seen.has(key)) return
    let timer = 0
    const later = (ms: number) => {
      timer = window.setTimeout(() => {
        if (off || seen.has(key) || current !== null) return
        if (anyOpen()) later(700)
        else openIntro(key)
      }, ms)
    }
    later(550)
    return () => window.clearTimeout(timer)
  }, [key, when])
}
