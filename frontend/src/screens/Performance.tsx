import { motion } from 'framer-motion'
import { ChevronLeft, Loader2 } from 'lucide-react'
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { api, type Perf, type PerfMetric, type PerfPeriod } from '../api'
import { Page } from '../components/Page'
import { Segmented } from '../components/Segmented'

const PERIODS = [
  { value: '1h', label: 'Час' },
  { value: '24h', label: 'Сутки' },
  { value: '7d', label: 'Неделя' },
] as const

interface Series {
  key: PerfMetric
  label: string
  color: string
}

type Point = Perf['points'][number]

const CHART_H = 132
const PAD_TOP = 16

/** 1 → "1 Б/с", 123456 → "121 КБ/с", 2.5e6 → "2,4 МБ/с" */
function rate(n: number): string {
  const units: [string, number][] = [
    ['ГБ/с', 1024 ** 3],
    ['МБ/с', 1024 ** 2],
    ['КБ/с', 1024],
  ]
  for (const [unit, scale] of units) {
    if (n >= scale) {
      const v = n / scale
      return `${(v >= 100 ? Math.round(v).toString() : v.toFixed(1)).replace('.', ',').replace(',0', '')} ${unit}`
    }
  }
  return `${Math.round(n)} Б/с`
}

/** 953 МБ, 1,9 ГБ */
const memBytes = (n: number) =>
  n < 1024 ** 3 ? `${Math.round(n / 1024 ** 2)} МБ` : `${(n / 1024 ** 3).toFixed(1).replace('.', ',')} ГБ`

const pct = (n: number) => `${n < 10 ? n.toFixed(1).replace('.', ',').replace(',0', '') : Math.round(n)}%`

/** The top of the chart: a round number of the 1-2-5 kind just above the peak (1024-based for bytes). */
function niceMax(peak: number, base: number): number {
  if (peak <= 0) return base
  const scale = base ** Math.floor(Math.log(peak) / Math.log(base))
  const steps = base === 1024 ? [1, 2, 5, 10, 20, 50, 100, 200, 500, 1024] : [1, 2, 5, 10]
  const m = steps.find((s) => s * scale >= peak * 1.08)
  return (m ?? base) * scale
}

const timeFmt = new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit' })
const dayFmt = new Intl.DateTimeFormat('ru-RU', { weekday: 'short', day: 'numeric' })
const fullFmt = new Intl.DateTimeFormat('ru-RU', {
  weekday: 'short',
  day: 'numeric',
  month: 'short',
  hour: '2-digit',
  minute: '2-digit',
})

/** Ticks under the chart: every 15 minutes, 6 hours or a day — on round times. */
function ticks(period: PerfPeriod, from: number, to: number): { at: number; label: string }[] {
  const out: { at: number; label: string }[] = []
  if (period === '7d') {
    const d = new Date(from)
    d.setHours(0, 0, 0, 0)
    for (d.setDate(d.getDate() + 1); d.getTime() < to; d.setDate(d.getDate() + 1)) {
      out.push({ at: d.getTime(), label: dayFmt.format(d) })
    }
    return out
  }
  const every = period === '1h' ? 15 * 60_000 : 6 * 3_600_000
  const offset = new Date(from).getTimezoneOffset() * 60_000
  for (let t = Math.ceil((from - offset) / every) * every + offset; t < to; t += every) {
    out.push({ at: t, label: timeFmt.format(t) })
  }
  return out
}

interface ChartProps {
  data: Perf
  series: Series[]
  max: number
  format: (n: number) => string
  hover: number | null
  onHover: (index: number | null) => void
}

/** A line chart in the Health / Screen Time manner: soft area, thin grid, scrubbing by finger or mouse. */
function Chart({ data, series, max, format, hover, onHover }: ChartProps) {
  const box = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)

  useLayoutEffect(() => {
    const el = box.current
    if (!el) return
    const observer = new ResizeObserver(() => setWidth(el.clientWidth))
    observer.observe(el)
    setWidth(el.clientWidth)
    return () => observer.disconnect()
  }, [])

  const from = new Date(data.since).getTime()
  const to = new Date(data.until).getTime()
  const times = data.points.map((p) => new Date(p.t).getTime() + (data.step * 1000) / 2)
  const x = (t: number) => ((t - from) / (to - from)) * width
  const y = (v: number) => PAD_TOP + (1 - Math.min(v, max) / max) * (CHART_H - PAD_TOP)

  // runs of points without a gap: the worker was down in between, the line breaks there
  const runs: number[][] = []
  times.forEach((t, i) => {
    if (i === 0 || t - times[i - 1] > data.step * 1000 * 2.5) runs.push([])
    runs[runs.length - 1].push(i)
  })

  const line = (key: PerfMetric, run: number[]) =>
    run.map((i, n) => `${n ? 'L' : 'M'}${x(times[i]).toFixed(1)},${y(data.points[i][key]).toFixed(1)}`).join('')
  const area = (key: PerfMetric, run: number[]) =>
    run.length > 1
      ? `${line(key, run)}L${x(times[run[run.length - 1]]).toFixed(1)},${CHART_H}L${x(times[run[0]]).toFixed(1)},${CHART_H}Z`
      : ''

  const pick = (clientX: number) => {
    const el = box.current
    if (!el || !times.length) return
    const at = from + ((clientX - el.getBoundingClientRect().left) / width) * (to - from)
    let best = 0
    times.forEach((t, i) => {
      if (Math.abs(t - at) < Math.abs(times[best] - at)) best = i
    })
    onHover(best)
  }

  const gradient = (key: string) => `perf-${key}`

  return (
    <div
      ref={box}
      className="perf-chart"
      onPointerMove={(e) => pick(e.clientX)}
      onPointerDown={(e) => pick(e.clientX)}
      onPointerLeave={() => onHover(null)}
      onPointerCancel={() => onHover(null)}
    >
      {width > 0 && (
        <svg width={width} height={CHART_H + 22} role="img">
          <defs>
            {series.map((s) => (
              <linearGradient key={s.key} id={gradient(s.key)} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0" stopColor={s.color} stopOpacity={series.length > 1 ? 0.18 : 0.28} />
                <stop offset="1" stopColor={s.color} stopOpacity={0} />
              </linearGradient>
            ))}
          </defs>
          {[0, 0.5, 1].map((f) => (
            <g key={f}>
              <line className="perf-grid" x1={0} x2={width} y1={y(max * f)} y2={y(max * f)} />
              {f > 0 && (
                <text className="perf-label" x={width - 2} y={y(max * f) - 4} textAnchor="end">
                  {format(max * f)}
                </text>
              )}
            </g>
          ))}
          {ticks(data.period, from, to).map((t) => (
            <g key={t.at}>
              <line className="perf-grid dashed" x1={x(t.at)} x2={x(t.at)} y1={PAD_TOP} y2={CHART_H} />
              <text className="perf-label" x={x(t.at)} y={CHART_H + 16} textAnchor="middle">
                {t.label}
              </text>
            </g>
          ))}
          {series.map((s) =>
            runs.map((run) => (
              <path key={`a-${s.key}-${run[0]}`} d={area(s.key, run)} fill={`url(#${gradient(s.key)})`} />
            )),
          )}
          {series.map((s) =>
            runs.map((run) =>
              run.length > 1 ? (
                <path key={`l-${s.key}-${run[0]}`} d={line(s.key, run)} className="perf-line" stroke={s.color} />
              ) : (
                <circle
                  key={`l-${s.key}-${run[0]}`}
                  cx={x(times[run[0]])}
                  cy={y(data.points[run[0]][s.key])}
                  r={1.8}
                  fill={s.color}
                />
              ),
            ),
          )}
          {hover !== null && times[hover] !== undefined && (
            <g>
              <line className="perf-cursor" x1={x(times[hover])} x2={x(times[hover])} y1={0} y2={CHART_H} />
              {series.map((s) => (
                <circle
                  key={s.key}
                  cx={x(times[hover])}
                  cy={y(data.points[hover][s.key])}
                  r={4}
                  fill={s.color}
                  className="perf-dot"
                />
              ))}
            </g>
          )}
        </svg>
      )}
    </div>
  )
}

interface CardProps {
  title: string
  data: Perf
  series: Series[]
  max: number
  format: (n: number) => string
  /** the big value: the load now or at the scrubbed point */
  value?: (pick: (key: PerfMetric) => number | null) => string
}

function PerfCard({ title, data, series, max, format, value }: CardProps) {
  const [hover, setHover] = useState<number | null>(null)
  const point: Point | null = hover !== null ? (data.points[hover] ?? null) : null
  const pick = (key: PerfMetric) => (point ? point[key] : data.summary[key].now)
  const empty = data.points.length === 0

  return (
    <div className="section">
      <div className="group perf-card">
        <div className="perf-head">
          <div className="perf-title">{title}</div>
          <div className="perf-when">{point ? fullFmt.format(new Date(point.t)) : empty ? '' : 'сейчас'}</div>
        </div>
        {value && <div className="perf-value">{empty ? '—' : value(pick)}</div>}
        {series.length > 1 && (
          <div className="perf-legend">
            {series.map((s) => {
              const v = pick(s.key)
              return (
                <span key={s.key}>
                  <i style={{ background: s.color }} />
                  {s.label} <b>{v === null ? '—' : format(v)}</b>
                </span>
              )
            })}
          </div>
        )}
        {empty ? (
          <div className="perf-empty">Данных за этот период пока нет: замеры идут каждые 30 секунд.</div>
        ) : (
          <Chart data={data} series={series} max={max} format={format} hover={hover} onHover={setHover} />
        )}
      </div>
      {!empty && (
        <div className="section-footer">
          {series
            .map((s) => {
              const { avg, max: peak } = data.summary[s.key]
              const name = series.length > 1 ? `${s.label}: ` : ''
              return `${name}в среднем ${avg === null ? '—' : format(avg)}, пик ${peak === null ? '—' : format(peak)}`
            })
            .join(' · ')}
        </div>
      )}
    </div>
  )
}

/** Settings → System Performance: CPU, RAM, disk and network of the server, sampled every 30 seconds. */
export function Performance({ onBack }: { onBack: () => void }) {
  const [period, setPeriod] = useState<PerfPeriod>('1h')
  const [data, setData] = useState<Perf | null>(null)
  const [error, setError] = useState<string | null>(null)

  const reload = useCallback(async () => {
    try {
      setData(await api.perf(period))
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [period])

  useEffect(() => {
    reload()
    const timer = window.setInterval(reload, 30_000)
    return () => window.clearInterval(timer)
  }, [reload])

  const shown = data && data.period === period ? data : null
  const memTotal = shown?.mem_total ?? 0
  const peak = (...keys: PerfMetric[]) => Math.max(0, ...keys.flatMap((k) => shown?.points.map((p) => p[k]) ?? []))

  return (
    <Page
      title="System Performance"
      onRefresh={reload}
      left={
        <motion.button className="nav-button" onClick={onBack} whileTap={{ opacity: 0.4 }} style={{ paddingLeft: 0 }}>
          <ChevronLeft size={30} strokeWidth={2.2} style={{ marginRight: -4 }} />
          Настройки
        </motion.button>
      }
      accessory={<Segmented id="perf-period" options={[...PERIODS]} value={period} onChange={setPeriod} />}
    >
      {error && <div className="error-banner">{error}</div>}

      {!shown && !error && (
        <div className="loader">
          <Loader2 size={22} className="spinner" />
        </div>
      )}

      {shown && (
        <motion.div key={period} initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.2 }}>
          <PerfCard
            title="Процессор (CPU)"
            data={shown}
            series={[{ key: 'cpu', label: 'Загрузка', color: 'var(--accent)' }]}
            max={100}
            format={pct}
            value={(pick) => {
              const v = pick('cpu')
              return v === null ? '—' : pct(v)
            }}
          />
          <PerfCard
            title="Оперативная память (RAM)"
            data={shown}
            series={[{ key: 'mem_used', label: 'Занято', color: 'var(--green)' }]}
            max={memTotal || niceMax(peak('mem_used'), 1024)}
            format={memBytes}
            value={(pick) => {
              const v = pick('mem_used')
              if (v === null) return '—'
              return memTotal ? `${memBytes(v)} из ${memBytes(memTotal)} · ${pct((v / memTotal) * 100)}` : memBytes(v)
            }}
          />
          <PerfCard
            title="Диск"
            data={shown}
            series={[
              { key: 'disk_read', label: 'Чтение', color: 'var(--accent)' },
              { key: 'disk_write', label: 'Запись', color: 'var(--orange)' },
            ]}
            max={niceMax(peak('disk_read', 'disk_write'), 1024)}
            format={rate}
          />
          <PerfCard
            title="Сеть"
            data={shown}
            series={[
              { key: 'net_in', label: 'Входящий', color: 'var(--green)' },
              { key: 'net_out', label: 'Исходящий', color: 'var(--indigo)' },
            ]}
            max={niceMax(peak('net_in', 'net_out'), 1024)}
            format={rate}
          />
        </motion.div>
      )}
    </Page>
  )
}
