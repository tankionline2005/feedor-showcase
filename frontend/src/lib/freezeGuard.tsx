import { useIsPresent } from 'framer-motion'
import { type CSSProperties, type ReactNode, useEffect, useState } from 'react'

/*
 * The page that stops scrolling, now and then, on any screen.
 *
 * Windows over the page (sheets, a photo, a video, the phone's post editor, the camera) stop the page scrolling
 * under them. Each used to remember the page's overflow and put it back when it went: two windows going in
 * another order than they came — a photo still fading out as a sheet opens — put back the «hidden» of the other
 * one, and the page stayed locked with nothing open. Now the windows hold a lock each and the page scrolls again
 * when the last one lets go.
 *
 * And a guard looks at every tap, wheel and swipe: a lock with no window open, or an invisible layer over the
 * page catching the taps (a window whose fading out never ended), is undone at once — and the server hears what
 * it was, with what was on the screen, so the cause can be found.
 */

let locks = 0

const HIDE_AFTER = 700 // the longest of the windows' leaving animations, and some

/**
 * How a window over the page leaves: it lets taps and swipes through the moment it starts fading, and is hidden
 * after the fading had time to end — even if the animation library never takes it away. framer-motion forgets
 * to when a layout animation runs on the page just then (a post's text unfolding, a list's rows moving, a live
 * number rolling): the window stayed over the page, invisible, catching every tap and swipe — the page «froze».
 * For a window inside AnimatePresence; its style while it stays.
 */
export function useLeavingStyle(): CSSProperties | undefined {
  const present = useIsPresent()
  const [gone, setGone] = useState(false)
  useEffect(() => {
    if (present) return
    const timer = window.setTimeout(() => setGone(true), HIDE_AFTER)
    return () => window.clearTimeout(timer)
  }, [present])
  return present ? undefined : gone ? { pointerEvents: 'none', display: 'none' } : { pointerEvents: 'none' }
}

/** The same for windows of several layers (a backdrop and a sheet): the style handed to them. */
export function Leaving({ children }: { children: (style: CSSProperties | undefined) => ReactNode }) {
  return <>{children(useLeavingStyle())}</>
}

function apply() {
  document.body.style.overflow = locks > 0 ? 'hidden' : ''
}

/** The page does not scroll while `active` (a window is open over it). */
export function useScrollLock(active: boolean) {
  useEffect(() => {
    if (!active) return
    locks++
    apply()
    return () => {
      locks = Math.max(0, locks - 1)
      apply()
    }
  }, [active])
}

const REPORT_EVERY = 5 * 60_000 // one report of a kind in this long
const reported: Record<string, number> = {}

/** What was on the screen: enough to tell which window froze the page. */
function snapshot(extra: Record<string, unknown> = {}) {
  const cx = innerWidth / 2
  const cy = innerHeight / 2
  const layers = document
    .elementsFromPoint(cx, cy)
    .slice(0, 6)
    .map((el) => {
      const s = getComputedStyle(el)
      return `${el.tagName.toLowerCase()}.${String((el as HTMLElement).className || '').slice(0, 40)} pos=${s.position} op=${s.opacity} pe=${s.pointerEvents}`
    })
  const mains = [...document.querySelectorAll('main')].map((m) => {
    const s = getComputedStyle(m)
    return `op=${s.opacity} tf=${s.transform.slice(0, 40)}`
  })
  return {
    route: location.hash,
    locks,
    body_overflow: document.body.style.overflow,
    html_overflow: getComputedStyle(document.documentElement).overflow,
    scroll: `${Math.round(scrollY)} of ${document.documentElement.scrollHeight - innerHeight}`,
    viewport: `${innerWidth}x${innerHeight}`,
    visible: document.visibilityState,
    mains,
    layers,
    ...extra,
  }
}

function report(kind: string, extra?: Record<string, unknown>) {
  const now = Date.now()
  if (now - (reported[kind] ?? 0) < REPORT_EVERY) return
  reported[kind] = now
  try {
    const body = JSON.stringify({ kind, detail: snapshot(extra) })
    navigator.sendBeacon?.('/api/client-log', new Blob([body], { type: 'application/json' })) ||
      fetch('/api/client-log', {
        method: 'POST',
        body,
        headers: { 'Content-Type': 'application/json' },
        keepalive: true,
      })
  } catch {
    /* the report is a nicety */
  }
}

/** The fixed layer over most of the screen that `el` belongs to, if it cannot be seen. */
function invisibleLayer(el: Element | null): HTMLElement | null {
  for (let node = el; node && node !== document.body; node = node.parentElement) {
    const s = getComputedStyle(node)
    if (s.position !== 'fixed') continue
    const r = node.getBoundingClientRect()
    if (r.width * r.height < innerWidth * innerHeight * 0.6) return null
    let opacity = 1
    for (let up: Element | null = node; up; up = up.parentElement) opacity *= Number(getComputedStyle(up).opacity)
    return opacity < 0.05 ? (node as HTMLElement) : null
  }
  return null
}

function look(x: number, y: number) {
  // 1. locked with no window holding it
  if (document.body.style.overflow === 'hidden' && locks === 0) {
    report('scroll-lock-left')
    document.body.style.overflow = ''
  }
  // 2. an invisible layer catching the taps: given a moment (a window may be just starting to fade in)
  const layer = invisibleLayer(document.elementFromPoint(x, y))
  if (layer) {
    window.setTimeout(() => {
      if (!layer.isConnected || invisibleLayer(layer) !== layer) return
      report('invisible-layer', { layer: `${layer.tagName.toLowerCase()}.${layer.className}` })
      layer.style.pointerEvents = 'none'
    }, 700)
  }
}

/** Is the page itself what a swipe or the wheel should have scrolled (not a list inside it, not a window)? */
function pageScrolls(target: EventTarget | null) {
  for (let node = target as Element | null; node && node !== document.body; node = node.parentElement) {
    const s = getComputedStyle(node)
    if (s.position === 'fixed') return false
    if (/(auto|scroll)/.test(s.overflowY) && node.scrollHeight > node.clientHeight) return false
  }
  return document.documentElement.scrollHeight - innerHeight > 4
}

/** 3. A swipe or the wheel that should have scrolled the page and did not: the server hears it (not undone). */
function watchStuck(target: EventTarget | null, down: boolean, from = scrollY) {
  if (!pageScrolls(target)) return
  const room = down ? document.documentElement.scrollHeight - innerHeight - from : from
  if (room < 8) return
  window.setTimeout(() => {
    if (Math.abs(scrollY - from) < 1 && document.visibilityState === 'visible') report('no-scroll', { down })
  }, 400)
}

let started = false

/** Started once, as the app opens. */
export function startFreezeGuard() {
  if (started) return
  started = true
  window.addEventListener('pointerdown', (e) => look(e.clientX, e.clientY), true)
  window.addEventListener(
    'wheel',
    (e) => {
      look(e.clientX, e.clientY)
      if (Math.abs(e.deltaY) > 30 && Math.abs(e.deltaY) > Math.abs(e.deltaX)) watchStuck(e.target, e.deltaY > 0)
    },
    { capture: true, passive: true },
  )
  let start: { x: number; y: number; scroll: number; target: EventTarget | null } | null = null
  window.addEventListener(
    'touchstart',
    (e) => {
      const t = e.touches[0]
      look(t.clientX, t.clientY)
      start = e.touches.length === 1 ? { x: t.clientX, y: t.clientY, scroll: scrollY, target: e.target } : null
    },
    { capture: true, passive: true },
  )
  window.addEventListener(
    'touchend',
    (e) => {
      const t = e.changedTouches[0]
      if (!start || !t) return
      const dy = t.clientY - start.y
      // a clear swipe up or down (not a tap, not a sideways swipe of a row)
      if (Math.abs(dy) > 60 && Math.abs(dy) > Math.abs(t.clientX - start.x) * 2)
        watchStuck(start.target, dy < 0, start.scroll)
      start = null
    },
    { capture: true, passive: true },
  )
}
