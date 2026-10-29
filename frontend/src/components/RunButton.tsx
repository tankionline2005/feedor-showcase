import { AnimatePresence, motion } from 'framer-motion'
import { RefreshCw } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import type { AiRun } from '../api'
import type { ErrorInfo } from './ErrorAlert'

const POLL = 1500
const IDLE_LOOK = 15_000
// the page gives up following a run it cannot ask about (the network is gone, the server answers
// errors): the button is unlocked again, never locked for good
const GIVE_UP_AFTER_FAILURES = 8
const LONGEST = 31 * 60_000 // the server takes a run this long for stuck, and so does the page

type Ended = Extract<AiRun, { going: false }>

/** A topics run as the page follows it: locked while one goes — the owner's «сейчас» or
 * the schedule's — with how far it is, and told how it ended. */
export function useAiRun({
  run,
  fetchRun,
  startRun,
  onEnded,
  onFailed,
}: {
  /** what the page loaded with: a run may be going already */
  run: AiRun | null | undefined
  fetchRun: () => Promise<AiRun | null>
  startRun: () => Promise<AiRun | null>
  onEnded: (run: Ended) => void
  onFailed: (error: ErrorInfo) => void
}) {
  const [percent, setPercent] = useState<number | null>(null)
  // asked to start: following waits for the answer, or it could take the last run's end for this one's
  const [starting, setStarting] = useState(false)
  const since = useRef(0)
  const handlers = useRef({ fetchRun, onEnded, onFailed })
  handlers.current = { fetchRun, onEnded, onFailed }

  const follow = useCallback((r: AiRun | null) => {
    if (r?.going) {
      if (!since.current) since.current = Date.now()
      setPercent(r.percent)
    } else {
      since.current = 0
      setPercent(null)
    }
  }, [])

  // a run going when the page was opened, or found by its reload
  useEffect(() => {
    if (run?.going) follow(run)
  }, [run, follow])

  const going = percent !== null
  const following = going && !starting

  // a run the schedule starts while the page is open: looked for now and then, and on coming back
  useEffect(() => {
    if (going || starting) return
    let alive = true
    const look = async () => {
      if (document.visibilityState !== 'visible') return
      try {
        const r = await handlers.current.fetchRun()
        if (alive && r?.going) follow(r)
      } catch {
        // not now: the next look
      }
    }
    const timer = window.setInterval(look, IDLE_LOOK)
    document.addEventListener('visibilitychange', look)
    return () => {
      alive = false
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', look)
    }
  }, [going, starting, follow])

  useEffect(() => {
    if (!following) return
    let failures = 0
    let timer = 0
    let alive = true
    const tick = async () => {
      try {
        const r = await handlers.current.fetchRun()
        if (!alive) return
        failures = 0
        if (r?.going && Date.now() - since.current < LONGEST) {
          setPercent(r.percent)
        } else {
          follow(null)
          if (r && !r.going) handlers.current.onEnded(r)
          return
        }
      } catch {
        if (!alive) return
        if (++failures >= GIVE_UP_AFTER_FAILURES) {
          follow(null)
          return
        }
      }
      timer = window.setTimeout(tick, POLL)
    }
    timer = window.setTimeout(tick, POLL)
    return () => {
      alive = false
      window.clearTimeout(timer)
    }
  }, [following, follow])

  const start = async () => {
    if (going) return
    since.current = Date.now()
    setPercent(0)
    setStarting(true)
    try {
      const r = await startRun()
      if (r?.going) setPercent(r.percent)
      else {
        follow(null)
        if (r) handlers.current.onEnded(r)
      }
    } catch (e) {
      follow(null)
      handlers.current.onFailed({ text: (e as Error).message })
    } finally {
      setStarting(false)
    }
  }

  return { going, percent: percent ?? 0, start }
}

/** «Собрать сейчас» and the like: a refresh icon, and a ring with the percent while the run goes. */
export function RunButton({
  label,
  going,
  percent,
  onClick,
}: {
  label: string
  going: boolean
  percent: number
  onClick: () => void
}) {
  return (
    <button
      className="nav-button run-button"
      onClick={onClick}
      disabled={going}
      title={going ? `${label}: ${percent}%` : label}
      aria-label={going ? `${label}: ${percent}%` : label}
      aria-busy={going}
    >
      <AnimatePresence initial={false} mode="popLayout">
        {going ? (
          <motion.span
            key="ring"
            className="run-ring"
            initial={{ opacity: 0, scale: 0.6 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.6 }}
            transition={{ type: 'spring', stiffness: 500, damping: 32 }}
          >
            <ProgressRing percent={percent} />
          </motion.span>
        ) : (
          <motion.span
            key="icon"
            className="run-icon"
            initial={{ opacity: 0, scale: 0.6, rotate: -90 }}
            animate={{ opacity: 1, scale: 1, rotate: 0 }}
            exit={{ opacity: 0, scale: 0.6 }}
            transition={{ type: 'spring', stiffness: 500, damping: 32 }}
          >
            <RefreshCw size={18} />
          </motion.span>
        )}
      </AnimatePresence>
    </button>
  )
}

const R = 11
const LENGTH = 2 * Math.PI * R

function ProgressRing({ percent }: { percent: number }) {
  // a sliver from the start, so the ring reads as one at 0 too
  const shown = Math.max(percent, 3) / 100
  return (
    <>
      <svg width="28" height="28" viewBox="0 0 28 28" aria-hidden>
        <circle className="run-ring-track" cx="14" cy="14" r={R} />
        <circle
          className="run-ring-arc"
          cx="14"
          cy="14"
          r={R}
          strokeDasharray={LENGTH}
          strokeDashoffset={LENGTH * (1 - shown)}
        />
      </svg>
      <span className="run-ring-number">{percent}</span>
    </>
  )
}
