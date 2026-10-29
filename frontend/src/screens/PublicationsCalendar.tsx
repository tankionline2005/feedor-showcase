import { AnimatePresence, motion } from 'framer-motion'
import { ChevronLeft, ChevronRight, Loader2, Plus } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, type CalendarItem, type ChatPostOut } from '../api'
import { Alert, type AlertButton } from '../components/Alert'
import { ChatPostCard } from '../components/ChatPostCard'
import { Sheet } from '../components/Sheet'
import { useToast } from '../components/Toast'
import { useMe } from '../hooks/useMe'
import { whenLabel } from '../lib/format'
import { spring } from '../lib/motion'
import { may } from '../lib/who'

const WEEK = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
const MONTHS = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь',
  'Ноябрь', 'Декабрь']
const DAY_NAMES = ['воскресенье', 'понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота']
const STATE_TEXT: Record<CalendarItem['state'], string> = {
  scheduled: 'Запланирован',
  published: 'Опубликован',
  failed: 'Не вышел',
}

const dayStart = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate())
const addDays = (d: Date, n: number) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n)
const sameDay = (a: Date, b: Date) => a.toDateString() === b.toDateString()
/** «28 сентября» */
const dayMonth = (d: Date) => d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' })
const hhmm = (d: Date) => `${d.getHours()}:${String(d.getMinutes()).padStart(2, '0')}`

/** The month's grid: from the Monday on or before its first day, six weeks. */
function gridOf(month: Date): Date[] {
  const first = new Date(month.getFullYear(), month.getMonth(), 1)
  const start = addDays(first, -((first.getDay() + 6) % 7))
  return Array.from({ length: 42 }, (_, i) => addDays(start, i))
}

/** What a post's text says first, for a line of the day. */
const firstLine = (p: ChatPostOut) => {
  const div = document.createElement('div')
  div.innerHTML = p.text
  return (div.textContent ?? '').split('\n').find((l) => l.trim())?.trim() ?? ''
}

const momentOf = (p: ChatPostOut) => p.scheduled_at ?? p.published_at ?? p.publish_error_at ?? p.withdrawn_at ?? ''
const stateOf = (p: ChatPostOut): CalendarItem['state'] =>
  p.scheduled_at ? 'scheduled' : p.published_at ? 'published' : 'failed'

/**
 * «Публикации» → «Календарь»: the month with a dot for each kind of post on its days — scheduled, published, failed
 * to go — and the day chosen under it, hour by hour; a free hour ahead takes one of the AI's suggestions.
 */
export function PublicationsCalendar({ onReloadReady }: { onReloadReady: (fn: () => Promise<unknown>) => void }) {
  const me = useMe()
  const toast = useToast()
  const canPublish = may(me, 'ai_publish')
  const today = dayStart(new Date())
  const [month, setMonth] = useState(() => new Date(today.getFullYear(), today.getMonth(), 1))
  const [dir, setDir] = useState(1)
  const [selected, setSelected] = useState(today)
  const [items, setItems] = useState<CalendarItem[] | null>(null)
  const [day, setDay] = useState<ChatPostOut[] | null>(null)
  const [opened, setOpened] = useState<ChatPostOut | null>(null)
  const [slot, setSlot] = useState<Date | null>(null)
  const turn = useRef(0)
  const dayTurn = useRef(0)

  const grid = useMemo(() => gridOf(month), [month])

  const loadMonth = useCallback(async () => {
    const mine = ++turn.current
    const start = grid[0]
    const end = addDays(grid[grid.length - 1], 1)
    try {
      const res = await api.publicationsCalendar(start.toISOString(), end.toISOString())
      if (mine === turn.current) setItems(res.items)
    } catch (e) {
      if (mine === turn.current) toast((e as Error).message)
    }
  }, [grid, toast])

  const loadDay = useCallback(async () => {
    const mine = ++dayTurn.current
    try {
      const res = await api.publicationsDay(selected.toISOString(), addDays(selected, 1).toISOString())
      if (mine === dayTurn.current) setDay(res.items)
    } catch (e) {
      if (mine === dayTurn.current) toast((e as Error).message)
    }
  }, [selected, toast])

  useEffect(() => {
    setItems(null)
    loadMonth()
  }, [loadMonth])
  useEffect(() => {
    setDay(null)
    loadDay()
  }, [loadDay])
  const reload = useCallback(async () => {
    await Promise.all([loadMonth(), loadDay()])
  }, [loadMonth, loadDay])
  useEffect(() => onReloadReady(reload), [reload, onReloadReady])

  // the month's posts by day
  const byDay = useMemo(() => {
    const map = new Map<string, CalendarItem[]>()
    for (const it of items ?? []) {
      const k = new Date(it.at).toDateString()
      map.set(k, [...(map.get(k) ?? []), it])
    }
    return map
  }, [items])

  const turnMonth = (step: number) => {
    setDir(step)
    setMonth((m) => new Date(m.getFullYear(), m.getMonth() + step, 1))
  }

  const pick = (d: Date) => {
    setSelected(d)
    if (d.getMonth() !== month.getMonth()) turnMonth(d < month ? -1 : 1)
  }

  // the chosen day, hour by hour
  const hours = useMemo(() => {
    const list: { at: Date; posts: ChatPostOut[] }[] = []
    for (let h = 0; h < 24; h++) {
      const at = new Date(selected.getFullYear(), selected.getMonth(), selected.getDate(), h)
      list.push({ at, posts: (day ?? []).filter((p) => new Date(momentOf(p)).getHours() === h) })
    }
    return list
  }, [day, selected])
  const now = Date.now()

  return (
    <div className="calendar">
      <div className="calendar-head">
        <button className="nav-button" onClick={() => turnMonth(-1)} aria-label="Предыдущий месяц">
          <ChevronLeft size={22} />
        </button>
        <AnimatePresence mode="popLayout" initial={false} custom={dir}>
          <motion.div
            key={month.toISOString()}
            className="calendar-title"
            custom={dir}
            initial={{ opacity: 0, x: dir * 24 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: dir * -24 }}
            transition={spring}
          >
            {MONTHS[month.getMonth()]} {month.getFullYear()}
          </motion.div>
        </AnimatePresence>
        <button className="nav-button" onClick={() => turnMonth(1)} aria-label="Следующий месяц">
          <ChevronRight size={22} />
        </button>
      </div>

      <div className="calendar-week">
        {WEEK.map((w) => (
          <span key={w}>{w}</span>
        ))}
      </div>
      <div className="calendar-grid">
        {grid.map((d) => {
          const list = byDay.get(d.toDateString()) ?? []
          const states = (['scheduled', 'published', 'failed'] as const).filter((s) => list.some((i) => i.state === s))
          const out = d.getMonth() !== month.getMonth()
          return (
            <button
              key={d.toISOString()}
              className={`calendar-day${out ? ' out' : ''}${sameDay(d, today) ? ' today' : ''}${sameDay(d, selected) ? ' on' : ''}`}
              onClick={() => pick(d)}
              aria-label={`${dayMonth(d)}${list.length ? `, постов: ${list.length}` : ''}`}
            >
              <span className="calendar-num">{d.getDate()}</span>
              <span className="calendar-dots">
                {states.map((s) => (
                  <i key={s} className={`dot ${s}`} />
                ))}
              </span>
              {list.length > 1 && <span className="calendar-count">{list.length}</span>}
            </button>
          )
        })}
      </div>
      <div className="calendar-legend">
        <span>
          <i className="dot scheduled" /> запланирован
        </span>
        <span>
          <i className="dot published" /> вышел
        </span>
        <span>
          <i className="dot failed" /> не вышел
        </span>
        {items === null && <Loader2 size={14} className="spinner" />}
      </div>

      <div className="section-header calendar-day-title">
        {sameDay(selected, today)
          ? 'Сегодня'
          : `${DAY_NAMES[selected.getDay()]}, ${dayMonth(selected)}`}
      </div>
      <div className="calendar-hours">
        {day === null ? (
          <div className="loader">
            <Loader2 size={20} className="spinner" />
          </div>
        ) : (
          hours.map(({ at, posts }) => {
            const ahead = at.getTime() + 3_600_000 > now + 120_000
            const free = !posts.length && canPublish && ahead
            return (
              <div key={at.getHours()} className={`calendar-hour${posts.length ? ' busy' : ''}`}>
                <span className="calendar-hour-label">{at.getHours()}:00</span>
                <div className="calendar-hour-body">
                  {posts.map((p) => (
                    <button key={p.id} className={`calendar-post ${stateOf(p)}`} onClick={() => setOpened(p)}>
                      <span className="calendar-post-time">{hhmm(new Date(momentOf(p)))}</span>
                      <span className="calendar-post-text">{firstLine(p) || 'Пост'}</span>
                      <span className="calendar-post-state">{STATE_TEXT[stateOf(p)]}</span>
                    </button>
                  ))}
                  {free && (
                    <button className="calendar-free" onClick={() => setSlot(at)} aria-label={`Запланировать на ${hhmm(at)}`}>
                      <Plus size={14} /> Запланировать
                    </button>
                  )}
                </div>
              </div>
            )
          })
        )}
      </div>

      <Sheet open={opened !== null} onClose={() => setOpened(null)} title="Пост">
        {opened && (
          <div className="feed" style={{ paddingTop: 4 }}>
            <ChatPostCard
              post={opened}
              inList
              onChange={(next) => {
                setOpened(next)
                reload()
              }}
              onDelete={() => {
                setOpened(null)
                reload()
              }}
            />
          </div>
        )}
      </Sheet>

      <ScheduleIntoSheet
        at={slot}
        onClose={() => setSlot(null)}
        onDone={() => {
          setSlot(null)
          reload()
        }}
      />
    </div>
  )
}

/** A free hour of the calendar: one of the AI's suggestions goes there. */
function ScheduleIntoSheet({ at, onClose, onDone }: { at: Date | null; onClose: () => void; onDone: () => void }) {
  const toast = useToast()
  const [list, setList] = useState<ChatPostOut[] | null>(null)
  const [busy, setBusy] = useState<number | null>(null)
  const [ask, setAsk] = useState<{ title: string; text?: string; buttons: AlertButton[] } | null>(null)

  useEffect(() => {
    if (!at) return
    setList(null)
    api.publications('suggested').then(
      (r) => setList(r.items),
      (e) => {
        setList([])
        toast((e as Error).message)
      },
    )
  }, [at, toast])

  const when = at && at.getTime() < Date.now() + 180_000 ? new Date(Date.now() + 180_000) : at

  const schedule = async (p: ChatPostOut, partial = false) => {
    if (!when) return
    setBusy(p.id)
    try {
      const res = await api.scheduleChatPost(p.id, when.toISOString(), partial)
      if (!res.scheduled) {
        setAsk({
          title: 'Часть фото не выйдет',
          text: `${(res.skipped ?? []).join('; ')}. Запланировать пост без этого?`,
          buttons: [
            {
              label: 'Запланировать без них',
              strong: true,
              onClick: () => {
                setAsk(null)
                schedule(p, true)
              },
            },
            { label: 'Отмена', onClick: () => setAsk(null) },
          ],
        })
        return
      }
      toast(`Пост выйдет ${whenLabel(res.scheduled_at!)}`)
      onDone()
    } catch (e) {
      toast((e as Error).message)
    } finally {
      setBusy(null)
    }
  }

  return (
    <Sheet open={at !== null} onClose={onClose} title={at ? `На ${hhmm(at)}` : ''}>
      <div className="section">
        <div className="section-header">Предложения ИИ</div>
        {list === null ? (
          <div className="loader">
            <Loader2 size={20} className="spinner" />
          </div>
        ) : list.length === 0 ? (
          <div className="section-footer">
            Свободных предложений нет. Попросите ИИ написать пост в чате или включите «Создавать тематические посты».
          </div>
        ) : (
          <div className="group">
            {list.map((p) => (
              <button key={p.id} className="cell" onClick={() => schedule(p)} disabled={busy !== null}>
                <div className="cell-main">
                  <div className="cell-title calendar-pick-title">{firstLine(p) || 'Пост'}</div>
                  {p.topic && <div className="cell-sub">по теме «{p.topic.title}»</div>}
                </div>
                {busy === p.id && <Loader2 size={18} className="spinner" />}
              </button>
            ))}
          </div>
        )}
        {list && list.length > 0 && (
          <div className="section-footer">Пост выйдет в {when ? hhmm(when) : ''}. Время можно поменять на самом посте.</div>
        )}
      </div>
      <Alert
        open={ask !== null}
        title={ask?.title ?? ''}
        text={ask?.text}
        buttons={ask?.buttons ?? []}
        onClose={() => setAsk(null)}
      />
    </Sheet>
  )
}
