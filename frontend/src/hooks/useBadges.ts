import { useEffect, useSyncExternalStore } from 'react'
import { api, type Badges } from '../api'
import { subscribeLive } from '../lib/live'

/**
 * The blue badges (see backend web/badges_api.py): new posts of the feed, new topics and the AI's posts by topics since
 * the person last looked, chats with an unread answer. One copy for the whole app; asked again every
 * half a minute, when posts change, when an answer comes, and when the tab is looked at again.
 */
const EMPTY: Badges = { feed: 0, topics: 0, posts: 0, chats: 0, triggers: 0, most: 99 }
const EVERY_MS = 30_000

let state: Badges = EMPTY
let asking: Promise<void> | null = null
let started = false
const listeners = new Set<() => void>()

function set(next: Badges) {
  // the same numbers again: nothing to draw (asked every few seconds while posts come in)
  if (JSON.stringify(next) === JSON.stringify(state)) return
  state = next
  listeners.forEach((fn) => fn())
}

export function reloadBadges(): Promise<void> {
  asking ??= api
    .badges()
    .then(set)
    .catch(() => {})
    .finally(() => {
      asking = null
    })
  return asking
}

/** Something counted may have changed (an answer came, a run ended): ask again. */
export function badgesChanged() {
  window.dispatchEvent(new Event('badges'))
}

function start() {
  if (started) return
  started = true
  let timer: number | undefined
  const soon = () => {
    window.clearTimeout(timer)
    timer = window.setTimeout(() => document.visibilityState === 'visible' && reloadBadges(), 800)
  }
  subscribeLive(soon)
  window.addEventListener('badges', soon)
  document.addEventListener('visibilitychange', () => document.visibilityState === 'visible' && reloadBadges())
  window.setInterval(() => document.visibilityState === 'visible' && reloadBadges(), EVERY_MS)
  reloadBadges()
}

/** The badges; `enabled` is false for the admin, who has none. */
export function useBadges(enabled = true): Badges {
  const value = useSyncExternalStore(
    (fn) => {
      listeners.add(fn)
      return () => listeners.delete(fn)
    },
    () => state,
  )
  useEffect(() => {
    if (enabled) start()
  }, [enabled])
  return enabled ? value : EMPTY
}

/**
 * While a list is in front of the person (the feed, the topics, the publications), what it counts is seen:
 * at once when it opens, and again whenever something new is counted while it stays open.
 */
export function useSeen(kind: 'feed' | 'topics' | 'posts', active = true) {
  const count = useBadges(active)[kind]
  useEffect(() => {
    if (!active) return
    const look = () => {
      if (document.visibilityState !== 'visible') return
      set({ ...state, [kind]: 0 })
      api.badgeSeen(kind).catch(() => {})
    }
    look()
    document.addEventListener('visibilitychange', look)
    return () => document.removeEventListener('visibilitychange', look)
  }, [kind, active])
  useEffect(() => {
    if (active && count > 0 && document.visibilityState === 'visible') {
      set({ ...state, [kind]: 0 })
      api.badgeSeen(kind).catch(() => {})
    }
  }, [kind, active, count])
}

/** Signed out: the next person must not see these. */
export function resetBadges() {
  set(EMPTY)
}

/** «12», or «99+» past what is counted. */
export const badgeText = (n: number, most = state.most) => (n > most ? `${most}+` : String(n))
