import { motion } from 'framer-motion'
import { ChevronLeft, Loader2 } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, type ChannelStats, type StatsPeriod } from '../api'
import { BarChart, LineChart } from '../components/Charts'
import { Page } from '../components/Page'
import { Segmented } from '../components/Segmented'
import { compact, exact, percent, plural } from '../lib/format'

const PERIODS = [
  { value: '7d', label: 'Неделя' },
  { value: '30d', label: 'Месяц' },
  { value: '90d', label: '3 месяца' },
] as const

const RATIOS = [
  { value: 'reach', label: 'Охват' },
  { value: 'er', label: 'ER' },
  { value: 'fr', label: 'FR' },
] as const
type Ratio = (typeof RATIOS)[number]['value']
const RATIO_HINTS: Record<Ratio, string> = {
  reach: 'Охват — средние просмотры поста к числу подписчиков',
  er: 'ER — реакции, комментарии и репосты к просмотрам',
  fr: 'FR — репосты к просмотрам: какая доля читателей поделилась постом',
}

const dayFmt = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short' })
const weekdayFmt = new Intl.DateTimeFormat('ru-RU', { weekday: 'short', day: 'numeric', month: 'short' })
const timeFmt = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
const dayLabel = (iso: string) => dayFmt.format(new Date(`${iso}T12:00:00`))
const signed = (n: number) => (n > 0 ? `+${exact(n)}` : n < 0 ? `−${exact(-n)}` : '0')
const pct = (n: number | null) => (n === null ? '—' : percent(n))

/** A chart card in the manner of System Performance: a title, the big number (now or under the finger), the chart. */
function Card({
  title,
  when,
  value,
  children,
  footer,
}: {
  title: string
  when: string
  value: string
  children: React.ReactNode
  footer?: string
}) {
  return (
    <div className="section">
      <div className="group perf-card">
        <div className="perf-head">
          <div className="perf-title">{title}</div>
          <div className="perf-when">{when}</div>
        </div>
        <div className="perf-value">{value}</div>
        {children}
      </div>
      {footer && <div className="section-footer">{footer}</div>}
    </div>
  )
}

/** «Статистика» of the project's own channel: subscribers, publications, views and what works best. */
export function ChannelStatsScreen({ id, onBack }: { id: number; onBack: () => void }) {
  const [period, setPeriod] = useState<StatsPeriod>('30d')
  const [data, setData] = useState<ChannelStats | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [ratio, setRatio] = useState<Ratio>('reach')
  const [hover, setHover] = useState<Record<string, number | null>>({})
  const on = (key: string) => (i: number | null) => setHover((h) => ({ ...h, [key]: i }))

  const reload = useCallback(async () => {
    try {
      setData(await api.channelStats(id, period))
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [id, period])

  useEffect(() => {
    reload()
  }, [reload])

  const shown = data?.period === period ? data : null
  const every = period === '7d' ? 1 : period === '30d' ? 7 : 21
  const subs = useMemo(() => (shown?.subscribers ?? []).map((p) => ({ t: new Date(p.t).getTime(), v: p.v })), [shown])
  const best = useMemo(() => {
    const ranked = (shown?.hours ?? []).filter((h) => h.avg_views !== null && h.posts >= 2)
    ranked.sort((a, b) => (b.avg_views ?? 0) - (a.avg_views ?? 0))
    return new Set(ranked.slice(0, 3).map((h) => h.hour))
  }, [shown])

  const days = shown?.days ?? []
  const pickDay = (key: string) => (hover[key] != null ? days[hover[key]!] : null)
  const s = shown?.summary

  return (
    <Page
      title="Статистика"
      onRefresh={reload}
      left={
        <motion.button className="nav-button" onClick={onBack} whileTap={{ opacity: 0.4 }} style={{ paddingLeft: 0 }}>
          <ChevronLeft size={30} strokeWidth={2.2} style={{ marginRight: -4 }} />
          Канал
        </motion.button>
      }
      accessory={<Segmented id="stats-period" options={[...PERIODS]} value={period} onChange={setPeriod} />}
    >
      {error && <div className="error-banner">{error}</div>}
      {!shown && !error && (
        <div className="loader">
          <Loader2 size={22} className="spinner" />
        </div>
      )}
      {shown && s && (
        <motion.div key={period} initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.2 }}>
          <div className="stat-tiles">
            <div className="stat-tile">
              <span>Подписчики</span>
              <b>{s.subscribers !== null ? exact(s.subscribers) : '—'}</b>
              {s.subs_delta !== null && (
                <i className={s.subs_delta >= 0 ? 'up' : 'down'}>{signed(s.subs_delta)} за период</i>
              )}
            </div>
            <div className="stat-tile">
              <span>Постов в день</span>
              <b>{String(s.posts_per_day).replace('.', ',')}</b>
              <i>
                {exact(s.posts)} {plural(s.posts, 'пост', 'поста', 'постов')}
              </i>
            </div>
            <div className="stat-tile">
              <span>Ср. просмотры</span>
              <b>{s.avg_views !== null ? compact(s.avg_views) : '—'}</b>
              <i>охват {pct(s.reach)}</i>
            </div>
            <div className="stat-tile">
              <span>ER · FR</span>
              <b>{pct(s.er)}</b>
              <i>FR {pct(s.fr)}</i>
            </div>
          </div>

          <Card
            title="Подписчики"
            when={hover.subs != null && subs[hover.subs!] ? timeFmt.format(subs[hover.subs!].t) : 'сейчас'}
            value={
              hover.subs != null && subs[hover.subs!]
                ? exact(subs[hover.subs!].v)
                : s.subscribers !== null
                  ? exact(s.subscribers)
                  : '—'
            }
          >
            {subs.length > 1 ? (
              <LineChart
                points={subs}
                from={Date.now() - (period === '7d' ? 7 : period === '30d' ? 30 : 90) * 86_400_000}
                to={Date.now()}
                color="var(--accent)"
                format={(v) => compact(Math.round(v))}
                hover={hover.subs ?? null}
                onHover={on('subs')}
                gap={period === '7d' ? 3 * 3_600_000 : 3 * 86_400_000}
                ticks={days
                  .filter((_, i) => i % every === 0)
                  .map((d) => ({ at: new Date(`${d.day}T12:00:00`).getTime(), label: dayLabel(d.day) }))}
              />
            ) : (
              <div className="perf-empty">
                Число подписчиков записывается каждый час — график появится совсем скоро.
              </div>
            )}
          </Card>

          <Card
            title="Прирост подписчиков"
            when={pickDay('delta') ? weekdayFmt.format(new Date(`${pickDay('delta')!.day}T12:00:00`)) : 'за период'}
            value={
              pickDay('delta')
                ? pickDay('delta')!.subs_delta === null
                  ? '—'
                  : signed(pickDay('delta')!.subs_delta!)
                : s.subs_delta !== null
                  ? signed(s.subs_delta)
                  : '—'
            }
          >
            <BarChart
              bars={days.map((d) => ({ label: dayLabel(d.day), value: d.subs_delta }))}
              color={(v) => (v < 0 ? 'var(--red)' : 'var(--green)')}
              format={(v) => exact(v)}
              hover={hover.delta ?? null}
              onHover={on('delta')}
              every={every}
              signed={days.some((d) => (d.subs_delta ?? 0) < 0)}
            />
          </Card>

          <Card
            title="Публикации"
            when={pickDay('posts') ? weekdayFmt.format(new Date(`${pickDay('posts')!.day}T12:00:00`)) : 'за период'}
            value={pickDay('posts') ? exact(pickDay('posts')!.posts) : exact(s.posts)}
          >
            <BarChart
              bars={days.map((d) => ({ label: dayLabel(d.day), value: d.posts }))}
              color="var(--indigo)"
              format={(v) => exact(v)}
              hover={hover.posts ?? null}
              onHover={on('posts')}
              every={every}
            />
          </Card>

          <Card
            title="Средние просмотры поста"
            when={pickDay('views') ? weekdayFmt.format(new Date(`${pickDay('views')!.day}T12:00:00`)) : 'за период'}
            value={
              pickDay('views')
                ? pickDay('views')!.avg_views === null
                  ? '—'
                  : exact(pickDay('views')!.avg_views!)
                : s.avg_views !== null
                  ? exact(s.avg_views)
                  : '—'
            }
            footer="Посты считаются через сутки после выхода, когда просмотры почти перестают расти."
          >
            <BarChart
              bars={days.map((d) => ({ label: dayLabel(d.day), value: d.avg_views }))}
              color="var(--orange)"
              format={(v) => compact(v)}
              hover={hover.views ?? null}
              onHover={on('views')}
              every={every}
            />
          </Card>

          <div className="section">
            <div className="group perf-card">
              <div className="perf-head">
                <div className="perf-title">Вовлечённость</div>
                <div className="perf-when">
                  {pickDay('ratio') ? weekdayFmt.format(new Date(`${pickDay('ratio')!.day}T12:00:00`)) : 'за период'}
                </div>
              </div>
              <div className="perf-value">{pct(pickDay('ratio') ? pickDay('ratio')![ratio] : s[ratio])}</div>
              <div className="ratio-switch">
                <Segmented id="stats-ratio" options={[...RATIOS]} value={ratio} onChange={setRatio} />
              </div>
              <BarChart
                bars={days.map((d) => ({ label: dayLabel(d.day), value: d[ratio] }))}
                color="var(--green)"
                format={(v) => percent(v)}
                hover={hover.ratio ?? null}
                onHover={on('ratio')}
                every={every}
              />
            </div>
            <div className="section-footer">{RATIO_HINTS[ratio]}.</div>
          </div>

          <Card
            title="Лучшее время публикаций"
            when={
              hover.hours != null
                ? `${String(hover.hours).padStart(2, '0')}:00–${String(hover.hours + 1).padStart(2, '0')}:00`
                : 'ср. просмотры'
            }
            value={(() => {
              const h = hover.hours != null ? shown.hours[hover.hours] : null
              if (h)
                return h.avg_views === null
                  ? '—'
                  : `${exact(h.avg_views)} · ${h.posts} ${plural(h.posts, 'пост', 'поста', 'постов')}`
              return best.size
                ? [...best]
                    .sort((x, y) => x - y)
                    .map((x) => `${x}:00`)
                    .join(', ')
                : '—'
            })()}
            footer="Средние просмотры постов по часу публикации, по вашему времени. Яркие столбцы — лучшие часы (где было хотя бы два поста)."
          >
            <BarChart
              bars={shown.hours.map((h) => ({ label: `${h.hour}`, value: h.avg_views }))}
              color="var(--accent)"
              format={(v) => compact(v)}
              hover={hover.hours ?? null}
              onHover={on('hours')}
              every={3}
              strong={best}
            />
          </Card>

          <div className="section-footer" style={{ padding: '0 32px 24px' }}>
            {shown.collecting_since
              ? `Статистика канала собирается с ${dayFmt.format(new Date(shown.collecting_since))}: подписчики — каждый час, цифры постов — через 1, 6, 24 и 48 часов после выхода.`
              : 'Статистика канала начнёт собираться в ближайший час.'}
          </div>
        </motion.div>
      )}
    </Page>
  )
}
