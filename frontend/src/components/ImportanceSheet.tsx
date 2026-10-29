import { motion } from 'framer-motion'
import { useEffect, useState } from 'react'
import { api, type ImportanceBand } from '../api'
import { spring } from '../lib/motion'
import { Sheet } from './Sheet'

/** The bands of the scale in plain words, with their colour. */
const BANDS: Record<string, { title: string; text: string; color: string }> = {
  '1-3': {
    title: 'Мелочь',
    text: 'Узкая или проходная новость: интересна немногим, пишет о ней один канал.',
    color: '#8e8e93',
  },
  '4-6': {
    title: 'Заметная новость',
    text: 'Подходит вашему каналу, о ней пишут несколько каналов, но это не главное за день.',
    color: 'var(--accent)',
  },
  '7-8': {
    title: 'Важная',
    text: 'Её обсуждают многие каналы, читатели живо реагируют.',
    color: '#ff9500',
  },
  '9-10': {
    title: 'Главное событие дня',
    text: 'Об этом сегодня будут говорить все.',
    color: '#ff3b30',
  },
}

export const importanceLabel = (n: number) => (n <= 1 ? 'Все темы' : `От ${n}`)

/**
 * «Минимальная важность»: what the AI's score of a topic means — the scale in four bands, each shown on the project's
 * own recent topics — and the least one to take, chosen on a row of ten.
 */
export function ImportanceSheet({
  open,
  title,
  what,
  value,
  onClose,
  onChange,
}: {
  open: boolean
  title: string
  /** what the choice keeps out, for the line under the row: «посты», «автопостинг» */
  what: string
  value: number
  onClose: () => void
  onChange: (n: number) => void
}) {
  const [bands, setBands] = useState<ImportanceBand[] | null>(null)

  useEffect(() => {
    if (!open) return
    let alive = true
    api.importanceExamples().then(
      (r) => alive && setBands(r.bands),
      () => alive && setBands([]),
    )
    return () => {
      alive = false
    }
  }, [open])

  return (
    <Sheet
      open={open}
      onClose={onClose}
      title={title}
      right={
        <button className="nav-button" style={{ fontWeight: 600 }} onClick={onClose}>
          Готово
        </button>
      }
    >
      <div className="section">
        <div className="importance-intro">
          Каждой теме ИИ ставит важность от 1 до 10 — насколько она горячая для вашего канала прямо сейчас: подходит ли
          под ваши критерии, сколько каналов о ней пишут, как реагируют читатели и насколько она свежая.
        </div>
        <div className="importance-row" role="group" aria-label="Минимальная важность">
          {Array.from({ length: 10 }, (_, i) => i + 1).map((n) => (
            <button
              key={n}
              className={`importance-dot${n >= value ? ' on' : ''}${n === value ? ' chosen' : ''}`}
              aria-pressed={n === value}
              aria-label={`От ${n}`}
              onClick={() => onChange(n)}
            >
              {n}
            </button>
          ))}
        </div>
        <div className="section-footer" style={{ textAlign: 'center' }}>
          {value <= 1
            ? `Важность не ограничивает ${what}: подходят все темы.`
            : `${what[0].toUpperCase()}${what.slice(1)} — только для тем с важностью от ${value}.`}
        </div>
      </div>

      <div className="section">
        <div className="section-header">Что значат цифры</div>
        <div className="group">
          {(bands ?? Object.keys(BANDS).map((k) => ({ from: +k.split('-')[0], to: +k.split('-')[1], examples: [] }))).map(
            (b) => {
              const band = BANDS[`${b.from}-${b.to}`]
              const out = b.to < value
              return (
                <motion.div
                  key={b.from}
                  className="cell importance-band"
                  animate={{ opacity: out ? 0.4 : 1 }}
                  transition={spring}
                >
                  <span className="importance-badge" style={{ background: band.color }}>
                    {b.from}–{b.to}
                  </span>
                  <div className="cell-main">
                    <div className="cell-title">
                      {band.title}
                      {out && <span className="importance-out"> · не подходит</span>}
                    </div>
                    <div className="cell-sub importance-text">{band.text}</div>
                    {b.examples.length > 0 && (
                      <ul className="importance-examples">
                        {b.examples.map((e) => (
                          <li key={e.title}>
                            <span className="importance-score">{e.importance}</span>
                            {e.title}
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                </motion.div>
              )
            },
          )}
        </div>
        <div className="section-footer">
          {bands && bands.some((b) => b.examples.length)
            ? 'Примеры — темы вашего проекта за последнюю неделю.'
            : 'Когда соберутся темы, здесь появятся примеры из вашего проекта.'}
        </div>
      </div>
    </Sheet>
  )
}
