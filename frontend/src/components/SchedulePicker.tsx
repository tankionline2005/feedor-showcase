import { useEffect, useState } from 'react'
import { whenLabel } from '../lib/format'
import { Sheet } from './Sheet'

const MIN_AHEAD = 2 * 60_000
const MAX_AHEAD = 365 * 86_400_000

/** "2026-10-25T10:00": what an <input type="datetime-local"> holds, in the device's own time. */
function inputValue(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** An hour from now, on the next five minutes. */
function nextHour(): Date {
  const d = new Date(Date.now() + 3_600_000)
  d.setSeconds(0, 0)
  d.setMinutes(Math.ceil(d.getMinutes() / 5) * 5)
  return d
}

/** «Отложенная публикация»: the date and time the post comes out, in the device's own time — the phone's wheels,
 * the computer's calendar. */
export function SchedulePicker({
  open,
  channel,
  initial,
  onClose,
  onPick,
}: {
  open: boolean
  channel: string
  /** the time already chosen, when it is changed */
  initial: string | null
  onClose: () => void
  onPick: (at: Date) => void
}) {
  const [value, setValue] = useState('')
  useEffect(() => {
    if (open) setValue(inputValue(initial && new Date(initial).getTime() > Date.now() ? new Date(initial) : nextHour()))
  }, [open, initial])

  const at = value ? new Date(value) : null
  const ahead = at ? at.getTime() - Date.now() : 0
  const problem = !at
    ? 'Выберите дату и время'
    : ahead < MIN_AHEAD
      ? 'Выберите время хотя бы на пару минут позже, чем сейчас'
      : ahead > MAX_AHEAD
        ? 'Запланировать можно не больше чем на год вперёд'
        : null

  return (
    <Sheet
      open={open}
      onClose={onClose}
      title="Отложенная публикация"
      left={
        <button className="nav-button" onClick={onClose}>
          Отмена
        </button>
      }
    >
      <div className="schedule-picker">
        <input
          className="schedule-input"
          type="datetime-local"
          value={value}
          min={inputValue(new Date(Date.now() + MIN_AHEAD))}
          max={inputValue(new Date(Date.now() + MAX_AHEAD))}
          onChange={(e) => setValue(e.target.value)}
          aria-label="Дата и время публикации"
        />
        <div className={`schedule-note${problem && value ? ' bad' : ''}`}>
          {problem && value
            ? problem
            : `Бот опубликует пост в канале «${channel}». До этого пост можно править — выйдет последняя версия.`}
        </div>
        <button className="button schedule-submit" disabled={Boolean(problem)} onClick={() => at && onPick(at)}>
          {at && !problem ? `Запланировать на ${whenLabel(at.toISOString())}` : 'Запланировать'}
        </button>
      </div>
    </Sheet>
  )
}
