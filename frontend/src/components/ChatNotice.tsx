import { AnimatePresence, motion, type PanInfo } from 'framer-motion'
import { AlertCircle, Sparkles, X } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { useWide } from '../hooks/useMedia'
import { badgesChanged } from '../hooks/useBadges'
import { markDone, watched } from '../lib/chatWatch'
import { plainText } from '../lib/markdown'
import { spring } from '../lib/motion'

const POLL_MS = 2500
const SHOWN_MS = 10_000
// the page asks for an answer and says so to the notice at the same moment: the server may not have
// begun it when first asked. Not busy yet is not done — unless it stays so this long (nothing began)
const START_GRACE_MS = 20_000

type Notice = { key: string; id: number; title: string; text: string; failed: boolean }

/**
 * «ИИ ответил»: wherever the person is on the site, when an answer they wait on is done (lib/chatWatch).
 * Not on the chat itself, where the answer is seen anyway. Goes by itself after 10 seconds; closed with
 * the cross, or on a phone by swiping it away; «Перейти в чат» opens the chat.
 */
export function ChatNotice() {
  const wide = useWide()
  const [notice, setNotice] = useState<Notice | null>(null)
  // per answer (chat and when it began): told about already, or seen still being written by this tab
  const told = useRef(new Set<string>())
  const seenBusy = useRef(new Set<string>())
  const timer = useRef<number | undefined>(undefined)

  const close = useCallback(() => {
    window.clearTimeout(timer.current)
    setNotice(null)
  }, [])

  const arm = useCallback(() => {
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => setNotice(null), SHOWN_MS)
  }, [])

  useEffect(() => {
    let stopped = false
    const tick = async () => {
      const ask: { id: number; key: string; at: number }[] = []
      for (const [id, w] of Object.entries(watched())) {
        const key = `${id}:${w.at}`
        if (told.current.has(key)) continue
        // done before this tab knew of it (a page opened later): nothing to tell here
        if (w.done && !seenBusy.current.has(key)) {
          told.current.add(key)
          continue
        }
        ask.push({ id: Number(id), key, at: w.at })
      }
      if (!ask.length) return
      let items
      try {
        items = (await api.chatsStatus(ask.map((a) => a.id))).items
      } catch {
        return // asked again on the next tick
      }
      if (stopped) return
      for (const item of items) {
        const { key, at } = ask.find((a) => a.id === item.id)!
        if (item.busy) {
          seenBusy.current.add(key)
          continue
        }
        // not begun on the server yet (the question is still on its way): not an answer to tell of
        if (!seenBusy.current.has(key) && Date.now() - at < START_GRACE_MS) continue
        markDone(item.id)
        badgesChanged() // an unread answer for the badges
        // a tab out of sight tells once it is looked at again
        if (document.visibilityState !== 'visible') {
          seenBusy.current.add(key)
          continue
        }
        told.current.add(key)
        if (location.hash.split('?')[0] === `#/ai/chat/${item.id}`) continue // the answer is in front of them
        setNotice({ key, id: item.id, title: item.title, text: plainText(item.last), failed: item.failed })
        arm()
      }
    }
    tick()
    const every = window.setInterval(tick, POLL_MS)
    const now = () => void tick()
    window.addEventListener('chat-watch', now)
    document.addEventListener('visibilitychange', now)
    return () => {
      stopped = true
      window.clearInterval(every)
      window.removeEventListener('chat-watch', now)
      document.removeEventListener('visibilitychange', now)
    }
  }, [arm])

  useEffect(() => () => window.clearTimeout(timer.current), [])

  const open = (id: number) => {
    close()
    location.hash = `/ai/chat/${id}`
  }

  const onDragEnd = (_: unknown, info: PanInfo) => {
    if (
      Math.abs(info.offset.x) > 80 ||
      info.offset.y < -30 ||
      Math.abs(info.velocity.x) > 500 ||
      info.velocity.y < -400
    )
      close()
    else arm()
  }

  return (
    <AnimatePresence>
      {notice && (
        <motion.div
          key={notice.key}
          className={`chat-notice${wide ? ' wide' : ''}`}
          role="status"
          initial={{ opacity: 0, y: -24, scale: 0.96 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={{ opacity: 0, y: -16, scale: 0.97, transition: { duration: 0.18 } }}
          transition={spring}
          drag={wide ? false : true}
          dragSnapToOrigin
          dragElastic={0.6}
          onDragStart={() => window.clearTimeout(timer.current)}
          onDragEnd={onDragEnd}
          onHoverStart={() => wide && window.clearTimeout(timer.current)}
          onHoverEnd={() => wide && arm()}
        >
          <div className={`chat-notice-icon${notice.failed ? ' failed' : ''}`}>
            {notice.failed ? <AlertCircle size={17} /> : <Sparkles size={17} />}
          </div>
          <div className="chat-notice-main">
            <div className="chat-notice-head">{notice.failed ? 'ИИ не смог ответить' : 'ИИ ответил'}</div>
            <div className="chat-notice-title">{notice.title}</div>
            {notice.text && <div className="chat-notice-text">{notice.text}</div>}
            <button className="chat-notice-go" onClick={() => open(notice.id)}>
              Перейти в чат
            </button>
          </div>
          <button className="chat-notice-close" onClick={close} aria-label="Скрыть уведомление">
            <X size={16} />
          </button>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
