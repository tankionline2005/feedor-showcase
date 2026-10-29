import { AnimatePresence, motion } from 'framer-motion'
import { ChevronRight, ChevronsUpDown, Clock, MinusCircle, Plus, Repeat, Trash2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import type { AiSchedule, ScheduleInfo, ScheduleRule } from '../api'
import { useMe } from '../hooks/useMe'
import { plural } from '../lib/format'
import { spring } from '../lib/motion'
import { Switch } from './Controls'
import { Segmented } from './Segmented'
import { Sheet } from './Sheet'
import { useToast } from './Toast'

const EVERY: Record<number, string> = {
  15: 'Каждые 15 минут',
  30: 'Каждые 30 минут',
  60: 'Каждый час',
  120: 'Каждые 2 часа',
  180: 'Каждые 3 часа',
  360: 'Каждые 6 часов',
  720: 'Каждые 12 часов',
  1440: 'Раз в сутки',
}

const GAP: Record<number, string> = { 15: '15 минут', 30: '30 минут', 60: 'Час', 180: '3 часа' }
const DAYS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
const DAY_SHORT = ['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс']
const WEEKDAY_AT = ['в понедельник', 'во вторник', 'в среду', 'в четверг', 'в пятницу', 'в субботу', 'в воскресенье']
const ALL_DAYS = [0, 1, 2, 3, 4, 5, 6]
const SAVE_AFTER = 400

const zone = () => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'Europe/Moscow'
  } catch {
    return 'Europe/Moscow'
  }
}

const isTime = (v: string) => /^\d{2}:\d{2}$/.test(v)

const hhmm = (d: Date) => `${d.getHours()}:${String(d.getMinutes()).padStart(2, '0')}`

/** «09:00» → «9:00» */
const hm = (t: string) => (t.startsWith('0') && t[1] !== '0' ? t.slice(1) : t.startsWith('00') ? '0' + t.slice(2) : t)

/** How many whole days from today: 0 today, 1 tomorrow… */
function dayOffset(at: Date): number {
  const now = new Date()
  return Math.round(
    (new Date(at.getFullYear(), at.getMonth(), at.getDate()).getTime() -
      new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()) /
      86_400_000,
  )
}

function dayWord(at: Date): string {
  const days = dayOffset(at)
  if (days === 0) return 'сегодня'
  if (days === 1) return 'завтра'
  if (days < 7) return WEEKDAY_AT[(at.getDay() + 6) % 7]
  return at.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' })
}

/** When the next run goes, said the way people say it: «сегодня в 19:00», «в пятницу в 9:00». */
export function nextRunLabel(iso: string): string {
  const at = new Date(iso)
  if (at.getTime() - Date.now() < 60_000) return 'в ближайшую минуту'
  return `${dayWord(at)} в ${hhmm(at)}`
}

/** The next runs, grouped by day: «сегодня в 22:00; завтра в 0:00, 2:00 и 4:00». */
function upcomingText(list: string[]): string {
  const groups: { day: string; times: string[] }[] = []
  for (const iso of list) {
    const at = new Date(iso)
    const soon = at.getTime() - Date.now() < 60_000
    const day = soon ? '' : dayWord(at)
    const time = soon ? 'в ближайшую минуту' : hhmm(at)
    const last = groups[groups.length - 1]
    if (last && last.day === day && day) last.times.push(time)
    else groups.push({ day, times: [time] })
  }
  const join = (items: string[]) =>
    items.length === 1 ? items[0] : `${items.slice(0, -1).join(', ')} и ${items[items.length - 1]}`
  return groups.map((g) => (g.day ? `${g.day} в ${join(g.times)}` : join(g.times))).join('; ')
}

function perDayText(perDay: number): string {
  if (perDay >= 1) {
    const n = Math.round(perDay)
    return `около ${n} ${plural(n, 'запуска', 'запусков', 'запусков')} в день`
  }
  const week = Math.round(perDay * 7)
  return week >= 1 ? `около ${week} ${plural(week, 'запуска', 'запусков', 'запусков')} в неделю` : 'реже раза в неделю'
}

function daysText(days: number[]): string {
  if (days.length === 7) return 'ежедневно'
  if (days.join() === '0,1,2,3,4') return 'по будням'
  if (days.join() === '5,6') return 'по выходным'
  return 'по ' + days.map((d) => DAY_SHORT[d]).join(', ')
}

/** A rule's line in the list: «Каждый час» — «с 10:00 до 23:00 · ежедневно». */
function ruleTitle(rule: ScheduleRule): string {
  if (rule.kind === 'times') {
    const times = (rule.times ?? []).map(hm)
    return 'В ' + (times.length === 1 ? times[0] : `${times.slice(0, -1).join(', ')} и ${times[times.length - 1]}`)
  }
  const every = rule.every_min ?? 60
  return EVERY[every] ?? `Каждые ${every} мин`
}

function ruleSub(rule: ScheduleRule): string {
  const when = rule.kind === 'every' ? (rule.from ? `с ${hm(rule.from)} до ${hm(rule.to!)}` : 'весь день') : null
  return [when, daysText(rule.days)].filter(Boolean).join(' · ')
}

const newRule = (): ScheduleRule => ({ kind: 'every', days: ALL_DAYS, from: '10:00', to: '23:00', every_min: 60 })

/** «Расписание» in the topics settings: rules — each on its days, every so often within hours or at set times —
 * that make one schedule together, and an extra run when many posts come at once; the next runs under them. */
export function ScheduleEditor({
  info,
  waiting,
  postsWord,
  mode = 'topics',
  onSave,
}: {
  info: ScheduleInfo
  /** whose schedule: the topics' (with an extra run on a flood of posts), or a rubric's issues */
  mode?: 'topics' | 'rubric'
  /** the extra run by posts: posts waiting now */
  waiting?: number | null
  /** what the runs look at, for the footer: «постов в каналах» */
  postsWord: string
  onSave: (schedule: AiSchedule) => Promise<unknown>
}) {
  const toast = useToast()
  const { ai_own: own } = useMe()
  const [draft, setDraft] = useState<AiSchedule>(info.schedule)
  // the rule open in its sheet: its place in the list, or 'new'
  const [editing, setEditing] = useState<number | 'new' | null>(null)
  const timer = useRef(0)
  const pending = useRef(false)

  // the server's word, unless a change is waiting to be saved
  // the server's schedule, when it really changed (the list reloads now and then with the same one)
  const serverSchedule = JSON.stringify(info.schedule)
  useEffect(() => {
    if (!pending.current) setDraft(JSON.parse(serverSchedule))
  }, [serverSchedule])
  useEffect(() => () => window.clearTimeout(timer.current), [])

  /** Saves the schedule; with `at`, after a pause (a switch flicked twice saves once). False: it was refused. */
  const save = (next: AiSchedule, at = 0): Promise<boolean> => {
    setDraft(next)
    window.clearTimeout(timer.current)
    pending.current = true
    return new Promise((resolve) => {
      timer.current = window.setTimeout(async () => {
        try {
          await onSave({ ...next, tz: zone() })
          resolve(true)
        } catch (e) {
          toast((e as Error).message)
          setDraft(info.schedule)
          resolve(false)
        } finally {
          pending.current = false
        }
      }, at)
    })
  }

  const opts = info.schedule_options
  const s = draft
  const posts = s.posts

  let footer = ''
  const rubric = mode === 'rubric'
  if (info.upcoming.length) {
    footer = `${rubric ? 'Ближайшие выпуски' : 'Ближайшие сборы'} — ${upcomingText(info.upcoming)}.`
  } else if (posts) {
    footer =
      `Следующий сбор — когда наберётся ${posts.count} новых постов` + (waiting != null ? `, сейчас ${waiting}.` : '.')
  }
  if (info.per_day !== null && !rubric) {
    footer +=
      ` ${!s.rules.length ? 'При нынешнем потоке ' + postsWord + ' — ' : 'Это '}` +
      `${perDayText(info.per_day)}. ` +
      (own
        ? 'Дневной лимит бесплатных моделей OpenRouter общий для всех функций ИИ; с платной моделью каждый запуск оплачивается с вашего баланса.'
        : 'Каждый сбор оплачивается из ИИ тарифа: чем чаще сборы, тем быстрее он расходуется.')
  }

  const open = editing === null ? null : editing === 'new' ? null : s.rules[editing]

  return (
    <div className="section">
      <div className="section-header">Расписание</div>
      <div className="group">
        <AnimatePresence initial={false}>
          {s.rules.map((rule, i) => (
            <motion.button
              key={i}
              className="cell"
              layout="position"
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: 'auto' }}
              exit={{ opacity: 0, height: 0 }}
              transition={spring}
              onClick={() => setEditing(i)}
            >
              <div className="cell-icon" style={{ background: rule.kind === 'times' ? '#ff9500' : 'var(--accent)' }}>
                {rule.kind === 'times' ? <Clock size={17} /> : <Repeat size={17} />}
              </div>
              <div className="cell-main">
                <div className="cell-title">{ruleTitle(rule)}</div>
                <div className="cell-sub">{ruleSub(rule)}</div>
              </div>
              <ChevronRight size={18} color="var(--text-3)" />
            </motion.button>
          ))}
          {s.rules.length < opts.max_rules && (
            <motion.button key="add" layout="position" className="cell accent" onClick={() => setEditing('new')}>
              <Plus size={20} />
              <div className="cell-main">Добавить правило</div>
            </motion.button>
          )}
        </AnimatePresence>
      </div>
      <div className="section-footer">
        {rubric ? (
          <>
            Выпуски выходят по всем правилам вместе. Например: по будням в 9:00 и по выходным в 11:00 — два правила.
            {footer && (
              <>
                <br />
                <br />
                {footer}
              </>
            )}
          </>
        ) : (
          'Темы собираются по всем правилам вместе. Например: каждый час днём и каждые 2 часа ночью — два правила. Промежуток может переходить через полночь, например с 23:00 до 10:00.'
        )}
      </div>

      {!rubric && (
      <>
      <div className="group" style={{ marginTop: 16 }}>
        <div className="cell">
          <div className="cell-main">Внеочередной сбор</div>
          <Switch
            label="Внеочередной сбор"
            on={!!posts}
            onChange={(on) => {
              if (!on && !s.rules.length) {
                toast('Сначала добавьте правило — иначе темы не будут собираться совсем')
                return
              }
              save({ ...s, posts: on ? { count: 50, gap_min: 30 } : null }, SAVE_AFTER)
            }}
          />
        </div>
        <AnimatePresence initial={false}>
          {posts && (
            <motion.div
              key="posts"
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: 'auto' }}
              exit={{ opacity: 0, height: 0 }}
              transition={spring}
              style={{ overflow: 'hidden' }}
            >
              <Choice
                label="Когда наберётся"
                value={posts.count}
                options={opts.posts}
                labels={Object.fromEntries(
                  opts.posts.map((n) => [n, `${n} ${plural(n, 'новый пост', 'новых поста', 'новых постов')}`]),
                )}
                onChange={(count) => save({ ...s, posts: { ...posts, count } })}
              />
              <Choice
                label="Не чаще чем раз в"
                value={posts.gap_min}
                options={opts.gap_min}
                labels={GAP}
                onChange={(gap_min) => save({ ...s, posts: { ...posts, gap_min } })}
              />
            </motion.div>
          )}
        </AnimatePresence>
      </div>
      <div className="section-footer">
        {s.rules.length
          ? 'Если новостей вдруг становится очень много, темы соберутся раньше, не дожидаясь расписания.'
          : 'Правил нет: темы собираются только когда наберётся нужное число новых постов.'}
        {footer && (
          <>
            <br />
            <br />
            {footer}
          </>
        )}
      </div>
      </>
      )}

      <RuleSheet
        open={editing !== null}
        rule={open}
        maxTimes={opts.max_times}
        everyOptions={opts.every_min}
        canDelete={editing !== null && editing !== 'new' && (s.rules.length > 1 || !!posts)}
        onClose={() => setEditing(null)}
        onDone={async (rule) => {
          const rules =
            editing === 'new' ? [...s.rules, rule] : s.rules.map((r, j) => (j === editing ? rule : r))
          if (await save({ ...s, rules })) setEditing(null)
        }}
        onDelete={async () => {
          if (await save({ ...s, rules: s.rules.filter((_, j) => j !== editing) })) setEditing(null)
        }}
      />
    </div>
  )
}

/** One rule, in its own sheet: regular within hours, or at set times; on its days. */
function RuleSheet({
  open,
  rule,
  maxTimes,
  everyOptions,
  canDelete,
  onClose,
  onDone,
  onDelete,
}: {
  open: boolean
  /** null: a new one */
  rule: ScheduleRule | null
  maxTimes: number
  everyOptions: number[]
  canDelete: boolean
  onClose: () => void
  onDone: (rule: ScheduleRule) => Promise<void>
  onDelete: () => Promise<void>
}) {
  const [r, setR] = useState<ScheduleRule>(rule ?? newRule())
  const [busy, setBusy] = useState(false)

  // filled when the window opens, not again while it is open: the edits in it stay through the page's reloads
  useEffect(() => {
    if (open) setR(rule ?? newRule())
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  const set = (patch: Partial<ScheduleRule>) => setR((x) => ({ ...x, ...patch }))
  const toggleDay = (d: number) => {
    const days = r.days.includes(d) ? r.days.filter((x) => x !== d) : [...r.days, d].sort()
    if (days.length) set({ days })
  }
  const times = r.times ?? []
  const valid =
    r.kind === 'every'
      ? !r.from || (isTime(r.from) && isTime(r.to ?? '') && r.from !== r.to)
      : times.length > 0 && times.every(isTime)
  const overnight = r.kind === 'every' && !!r.from && !!r.to && r.from > r.to

  const done = async () => {
    setBusy(true)
    try {
      await onDone(
        r.kind === 'every'
          ? { kind: 'every', days: r.days, from: r.from ?? null, to: r.from ? (r.to ?? null) : null, every_min: r.every_min ?? 60 }
          : { kind: 'times', days: r.days, times: [...new Set(times)].sort() },
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <Sheet
      open={open}
      onClose={onClose}
      title={rule ? 'Правило' : 'Новое правило'}
      left={
        <button className="nav-button" onClick={onClose}>
          Отмена
        </button>
      }
      right={
        <button className="nav-button" style={{ fontWeight: 600 }} disabled={!valid || busy} onClick={done}>
          Готово
        </button>
      }
    >
      <div className="schedule-modes">
        <Segmented
          id="schedule-rule-kind"
          options={[
            { value: 'every', label: 'Регулярно' },
            { value: 'times', label: 'В точное время' },
          ]}
          value={r.kind}
          onChange={(kind) =>
            set(
              kind === 'every'
                ? { kind, every_min: r.every_min ?? 60, from: r.from ?? '10:00', to: r.to ?? '23:00' }
                : { kind, times: times.length ? times : ['09:00', '19:00'] },
            )
          }
        />
      </div>

      {r.kind === 'every' ? (
        <div className="section">
          <div className="group">
            <Choice
              label="Как часто"
              value={r.every_min ?? 60}
              options={everyOptions}
              labels={EVERY}
              onChange={(every_min) => set({ every_min })}
            />
            <div className="cell">
              <div className="cell-main">Весь день</div>
              <Switch
                label="Весь день"
                on={!r.from}
                onChange={(on) => set(on ? { from: null, to: null } : { from: '10:00', to: '23:00' })}
              />
            </div>
            <AnimatePresence initial={false}>
              {r.from && (
                <motion.div
                  key="hours"
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: 'auto' }}
                  exit={{ opacity: 0, height: 0 }}
                  transition={spring}
                  style={{ overflow: 'hidden' }}
                >
                  <div className="cell">
                    <div className="cell-main">С</div>
                    <input
                      type="time"
                      className="time-input"
                      aria-label="С какого времени"
                      value={r.from}
                      onChange={(e) => set({ from: e.target.value })}
                    />
                  </div>
                  <div className="cell">
                    <div className="cell-main">До</div>
                    <input
                      type="time"
                      className="time-input"
                      aria-label="До какого времени"
                      value={r.to ?? ''}
                      onChange={(e) => set({ to: e.target.value })}
                    />
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </div>
          <div className="section-footer">
            {r.from && r.from === r.to
              ? 'Начало и конец совпадают — выберите разное время.'
              : overnight
                ? `Через полночь: с ${hm(r.from!)} вечера до ${hm(r.to!)} утра следующего дня.`
                : 'Сборы идут с выбранной частотой внутри промежутка.'}
          </div>
        </div>
      ) : (
        <div className="section">
          <div className="group">
            <AnimatePresence initial={false}>
              {times.map((t, i) => (
                <motion.div
                  key={i}
                  className="cell schedule-time"
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: 'auto' }}
                  exit={{ opacity: 0, height: 0 }}
                  transition={spring}
                >
                  {times.length > 1 && (
                    <button
                      className="schedule-remove"
                      aria-label={`Убрать ${t}`}
                      onClick={() => set({ times: times.filter((_, j) => j !== i) })}
                    >
                      <MinusCircle size={20} />
                    </button>
                  )}
                  <div className="cell-main">Сбор</div>
                  <input
                    type="time"
                    className="time-input"
                    aria-label="Время сбора"
                    value={t}
                    onChange={(e) => set({ times: times.map((x, j) => (j === i ? e.target.value : x)) })}
                  />
                </motion.div>
              ))}
              {times.length < maxTimes && (
                <motion.button
                  key="add"
                  layout="position"
                  className="cell accent"
                  onClick={() => set({ times: [...times, nextFreeTime(times)] })}
                >
                  <Plus size={20} />
                  <div className="cell-main">Добавить время</div>
                </motion.button>
              )}
            </AnimatePresence>
          </div>
        </div>
      )}

      <div className="section">
        <div className="section-header">Дни недели</div>
        <div className="schedule-days" role="group" aria-label="Дни недели">
          {DAYS.map((label, d) => (
            <button
              key={d}
              className={`schedule-day${r.days.includes(d) ? ' on' : ''}`}
              aria-pressed={r.days.includes(d)}
              onClick={() => toggleDay(d)}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="section-footer">{daysText(r.days).replace(/^./, (c) => c.toUpperCase())}.</div>
      </div>

      {canDelete && (
        <div className="section">
          <div className="group">
            <button className="cell destructive" disabled={busy} onClick={onDelete}>
              <Trash2 size={18} />
              Удалить правило
            </button>
          </div>
        </div>
      )}
    </Sheet>
  )
}

/** The hour after the last time listed, for «Добавить время». */
function nextFreeTime(times: string[]): string {
  const last = [...times].filter(isTime).sort().pop()
  if (!last) return '09:00'
  const hour = (Number(last.slice(0, 2)) + 3) % 24
  return `${String(hour).padStart(2, '0')}:${last.slice(3)}`
}

/** A setting with a few choices: its value, a tap opens the system's list (the wheel on iPhone). */
export function Choice({
  label,
  value,
  options,
  labels,
  onChange,
}: {
  label: string
  value: number
  options: number[]
  labels: Record<number, string>
  onChange: (v: number) => void
}) {
  return (
    <label className="cell schedule-choice">
      <div className="cell-main">{label}</div>
      <span className="cell-value">
        {labels[value] ?? value}
        <ChevronsUpDown size={15} />
      </span>
      <select value={value} aria-label={label} onChange={(e) => onChange(Number(e.target.value))}>
        {options.map((o) => (
          <option key={o} value={o}>
            {labels[o] ?? o}
          </option>
        ))}
      </select>
    </label>
  )
}
