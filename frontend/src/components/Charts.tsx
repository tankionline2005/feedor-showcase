import { useLayoutEffect, useRef, useState } from 'react'

/** The width of a box, kept up to date: charts draw in real pixels so text and lines stay crisp. */
function useWidth() {
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
  return [box, width] as const
}

/** A round number of the 1-2-5 kind at or above n. */
function niceUp(n: number): number {
  if (n <= 0) return 1
  const scale = 10 ** Math.floor(Math.log10(n))
  return ([1, 2, 2.5, 5, 10].find((m) => m * scale >= n) ?? 10) * scale
}

const H = 132
const TOP = 16

interface LineProps {
  points: { t: number; v: number }[]
  from: number
  to: number
  color: string
  format: (v: number) => string
  hover: number | null
  onHover: (i: number | null) => void
  /** ticks under the chart */
  ticks: { at: number; label: string }[]
  /** a gap longer than this (ms) breaks the line */
  gap?: number
}

/** A line over time (e.g. subscribers): the scale hugs the values instead of starting at zero. */
export function LineChart({ points, from, to, color, format, hover, onHover, ticks, gap }: LineProps) {
  const [box, width] = useWidth()
  const values = points.map((p) => p.v)
  let lo = Math.min(...values)
  let hi = Math.max(...values)
  if (lo === hi) {
    lo -= 1
    hi += 1
  }
  const pad = (hi - lo) * 0.15
  lo = Math.max(0, lo - pad)
  hi += pad
  const x = (t: number) => ((t - from) / Math.max(1, to - from)) * width
  const y = (v: number) => TOP + (1 - (v - lo) / (hi - lo)) * (H - TOP)
  const runs: number[][] = []
  points.forEach((p, i) => {
    if (i === 0 || (gap && p.t - points[i - 1].t > gap)) runs.push([])
    runs[runs.length - 1].push(i)
  })
  const path = (run: number[]) =>
    run.map((i, n) => `${n ? 'L' : 'M'}${x(points[i].t).toFixed(1)},${y(points[i].v).toFixed(1)}`).join('')
  const pick = (clientX: number) => {
    const el = box.current
    if (!el || !points.length) return
    const at = from + ((clientX - el.getBoundingClientRect().left) / width) * (to - from)
    let best = 0
    points.forEach((p, i) => {
      if (Math.abs(p.t - at) < Math.abs(points[best].t - at)) best = i
    })
    onHover(best)
  }
  const id = `line-${color.replace(/\W/g, '')}`
  return (
    <div
      ref={box}
      className="perf-chart"
      onPointerMove={(e) => pick(e.clientX)}
      onPointerDown={(e) => pick(e.clientX)}
      onPointerLeave={() => onHover(null)}
    >
      {width > 0 && (
        <svg width={width} height={H + 22}>
          <defs>
            <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor={color} stopOpacity={0.25} />
              <stop offset="1" stopColor={color} stopOpacity={0} />
            </linearGradient>
          </defs>
          {[lo, (lo + hi) / 2, hi].map((v, i) => (
            <g key={i}>
              <line className="perf-grid" x1={0} x2={width} y1={y(v)} y2={y(v)} />
              {i > 0 && (
                <text className="perf-label" x={width - 2} y={y(v) - 4} textAnchor="end">
                  {format(v)}
                </text>
              )}
            </g>
          ))}
          {ticks.map((t) => (
            <text key={t.at} className="perf-label" x={x(t.at)} y={H + 16} textAnchor="middle">
              {t.label}
            </text>
          ))}
          {runs.map((run) =>
            run.length > 1 ? (
              <g key={run[0]}>
                <path
                  d={`${path(run)}L${x(points[run[run.length - 1]].t)},${H}L${x(points[run[0]].t)},${H}Z`}
                  fill={`url(#${id})`}
                />
                <path d={path(run)} className="perf-line" stroke={color} />
              </g>
            ) : (
              <circle key={run[0]} cx={x(points[run[0]].t)} cy={y(points[run[0]].v)} r={2.5} fill={color} />
            ),
          )}
          {hover !== null && points[hover] && (
            <g>
              <line className="perf-cursor" x1={x(points[hover].t)} x2={x(points[hover].t)} y1={0} y2={H} />
              <circle cx={x(points[hover].t)} cy={y(points[hover].v)} r={4} fill={color} className="perf-dot" />
            </g>
          )}
        </svg>
      )}
    </div>
  )
}

interface BarProps {
  bars: { label: string; value: number | null }[]
  color: string | ((v: number) => string)
  format: (v: number) => string
  hover: number | null
  onHover: (i: number | null) => void
  /** label every n-th bar */
  every?: number
  /** bars below zero go down from the middle (e.g. subscribers lost) */
  signed?: boolean
  /** bars drawn stronger (e.g. the best hours) */
  strong?: Set<number>
}

/** Bars, one per day or hour: a tap or hover tells the parent which, to show its number above. */
export function BarChart({ bars, color, format, hover, onHover, every = 1, signed, strong }: BarProps) {
  const values = bars.map((b) => b.value ?? 0)
  const top = niceUp(Math.max(...values.map(Math.abs), 0))
  const fill = (v: number) => (typeof color === 'function' ? color(v) : color)
  return (
    <div className="spend-chart" onPointerLeave={() => onHover(null)}>
      <div className={`spend-bars${signed ? ' signed' : ''}`}>
        {bars.map((b, i) => {
          const v = b.value ?? 0
          const share = `${(Math.abs(v) / top) * (signed ? 50 : 100)}%`
          return (
            <div
              key={i}
              className={`spend-bar${hover === i ? ' active' : ''}`}
              onPointerEnter={() => onHover(i)}
              onPointerDown={() => onHover(i)}
            >
              {b.value !== null && (
                <span
                  className={`bar-fill${v < 0 ? ' below' : ''}`}
                  style={{
                    height: v ? share : 1,
                    background: fill(v),
                    opacity: strong && !strong.has(i) ? 0.45 : 1,
                  }}
                />
              )}
            </div>
          )
        })}
      </div>
      <div className="spend-axis">
        {bars.map((b, i) => (
          <span key={i}>{i % every === 0 ? b.label : ''}</span>
        ))}
      </div>
      <div className="spend-top">{format(top)}</div>
    </div>
  )
}
