import { motion } from 'framer-motion'
import { ChevronLeft, Loader2 } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { api, type AiOperation, type AiUsage, type AiUsagePeriod, type AiUsageTotal, type BillingState } from '../api'
import { Page } from '../components/Page'
import { Segmented } from '../components/Segmented'
import { exact, longDate, plural } from '../lib/format'
import { usd } from '../lib/money'

const PERIODS = [
  { value: '24h', label: 'Сутки' },
  { value: '7d', label: 'Неделя' },
  { value: '30d', label: 'Месяц' },
] as const

const OPERATION_COLORS: Record<AiOperation, string> = {
  filter: 'var(--accent)',
  vision: 'var(--orange)',
  topics: 'var(--green)',
  advice: 'var(--indigo)',
  chat: 'var(--red)',
}
const OPERATIONS = Object.keys(OPERATION_COLORS) as AiOperation[]

const hourFmt = new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit' })
const dayFmt = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short' })
const weekdayFmt = new Intl.DateTimeFormat('ru-RU', { weekday: 'short', day: 'numeric', month: 'short' })

/** Bars of spending, stacked by operation; a tap or hover shows the bar's numbers above. */
function SpendChart({
  data,
  hover,
  onHover,
}: {
  data: AiUsage
  hover: number | null
  onHover: (i: number | null) => void
}) {
  const top = Math.max(...data.bars.map((b) => b.cost), 0)
  const every = data.period === '24h' ? 6 : data.period === '7d' ? 1 : 5
  return (
    <div className="spend-chart" onPointerLeave={() => onHover(null)}>
      <div className="spend-bars">
        {data.bars.map((b, i) => (
          <div
            key={b.t}
            className={`spend-bar${hover === i ? ' active' : ''}`}
            onPointerEnter={() => onHover(i)}
            onPointerDown={() => onHover(i)}
          >
            <div className="spend-stack" style={{ height: top ? `${(b.cost / top) * 100}%` : 0 }}>
              {OPERATIONS.filter((op) => b.by[op]).map((op) => (
                // shares of the bar: flex-grow below 1 in total would leave it partly empty
                <span key={op} style={{ flexGrow: (b.by[op] ?? 0) / b.cost, background: OPERATION_COLORS[op] }} />
              ))}
            </div>
          </div>
        ))}
      </div>
      <div className="spend-axis">
        {data.bars.map((b, i) => (
          <span key={b.t}>{i % every === 0 ? (data.step === 3600 ? hourFmt : dayFmt).format(new Date(b.t)) : ''}</span>
        ))}
      </div>
      {top > 0 && <div className="spend-top">{usd(top)}</div>}
    </div>
  )
}

function Breakdown({ title, items, total }: { title: string; items: AiUsageTotal[]; total: number }) {
  if (!items.length) return null
  return (
    <div className="section">
      <div className="section-header">{title}</div>
      <div className="group">
        {items.map((item) => (
          <div key={String(item.key)} className="cell share-cell">
            <div className="cell-main">
              <div className="share-head">
                <span className="share-title">{item.title}</span>
                <span className="cell-value">{usd(item.cost)}</span>
              </div>
              <div className="share-bar">
                <span style={{ width: `${total ? Math.max(1, (item.cost / total) * 100) : 0}%` }} />
              </div>
              <div className="cell-sub">
                {exact(item.requests)} {plural(item.requests, 'запрос', 'запроса', 'запросов')}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

/**
 * Settings → OpenRouter.ai → «Расход и остаток»: what the AI cost, on what, and what is left — on the owner's own
 * OpenRouter (dev:service:plan). On the service's: «Расход на ИИ», at its prices, with the plan's AI this month.
 */
export function AiUsageScreen({ onBack, backLabel = 'OpenRouter.ai' }: { onBack: () => void; backLabel?: string }) {
  const [period, setPeriod] = useState<AiUsagePeriod>('7d')
  const [data, setData] = useState<AiUsage | null>(null)
  const [plan, setPlan] = useState<BillingState['ai'] | undefined>(undefined)
  const [error, setError] = useState<string | null>(null)
  const [hover, setHover] = useState<number | null>(null)

  const reload = useCallback(async () => {
    try {
      const usage = await api.aiUsage(period)
      setData(usage)
      if (!usage.own) setPlan((await api.billing()).ai)
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [period])

  useEffect(() => {
    reload()
    const timer = window.setInterval(reload, 60_000)
    return () => window.clearInterval(timer)
  }, [reload])

  const shown = data && data.period === period ? data : null
  const bar = shown && hover !== null ? shown.bars[hover] : null
  const balance = shown?.balance
  const limit = shown?.monthly_limit_usd ?? null
  const own = shown?.own ?? true
  const totals = (kind: 'operation' | 'project' | 'person' | 'model') => shown?.totals[kind] ?? []

  return (
    <Page
      title={own ? 'Расход и остаток' : 'Расход на ИИ'}
      onRefresh={reload}
      left={
        <motion.button className="nav-button" onClick={onBack} whileTap={{ opacity: 0.4 }} style={{ paddingLeft: 0 }}>
          <ChevronLeft size={30} strokeWidth={2.2} style={{ marginRight: -4 }} />
          {backLabel}
        </motion.button>
      }
    >
      {error && <div className="error-banner">{error}</div>}
      {!shown && !error && (
        <div className="loader">
          <Loader2 size={22} className="spinner" />
        </div>
      )}

      {shown && !own && plan && (
        <div className="section">
          <div className="group balance-card">
            <div className="balance-label">ИИ тарифа</div>
            <div className="balance-value">
              {usd(Math.max(0, plan.included - plan.used))}
              <span className="balance-of"> из {usd(plan.included)}</span>
            </div>
            <div className="balance-month">
              <div className={`share-bar${plan.used >= plan.included * 0.9 ? ' warn' : ''}`}>
                <span style={{ width: `${Math.min(100, (plan.used / Math.max(plan.included, 0.000001)) * 100)}%` }} />
              </div>
            </div>
            <div className="balance-sub">
              {plan.extra_used ? `Сверх тарифа — ${usd(plan.extra_used)} с баланса. ` : ''}
              {plan.renews_at ? `Обновится ${longDate(plan.renews_at)}` : 'Пробный период: ИИ даётся один раз'}
            </div>
          </div>
          <div className="section-footer">Все суммы — по ценам сервиса, с учётом всех расходов на ИИ.</div>
        </div>
      )}

      {shown && (
        <>
          {own && (
          <div className="section">
            <div className="group balance-card">
              <div className="balance-label">Остаток на OpenRouter</div>
              <div className="balance-value">{balance?.credits_left != null ? usd(balance.credits_left) : '—'}</div>
              <div className="balance-sub">
                {balance?.credits_total != null && balance.credits_used != null
                  ? `Пополнено ${usd(balance.credits_total)} · потрачено ${usd(balance.credits_used)} за всё время`
                  : balance
                    ? 'OpenRouter не сообщил баланс аккаунта'
                    : 'OpenRouter.ai не подключён'}
                {balance?.key_limit != null &&
                  ` · лимит ключа ${usd(balance.key_limit)}, осталось ${usd(balance.key_limit_left ?? 0)}`}
              </div>
              <div className="balance-month">
                <div className="share-head">
                  <span>В этом месяце</span>
                  <span>
                    {usd(shown.spent_month ?? 0)}
                    {limit !== null ? ` из ${usd(limit)}` : ''}
                  </span>
                </div>
                {limit !== null && (
                  <div className={`share-bar${(shown.spent_month ?? 0) >= limit * 0.9 ? ' warn' : ''}`}>
                    <span style={{ width: `${Math.min(100, ((shown.spent_month ?? 0) / limit) * 100)}%` }} />
                  </div>
                )}
              </div>
            </div>
            <div className="section-footer">
              Остаток — с вашего аккаунта OpenRouter, в том числе траты вне нашего сервиса. Ниже — только то, что
              потратил наш мониторинг.
            </div>
          </div>
          )}

          <Segmented id="ai-usage-period" options={[...PERIODS]} value={period} onChange={setPeriod} />

          <div className="section" style={{ marginTop: 6 }}>
            <div className="group perf-card">
              <div className="perf-head">
                <div className="perf-title">Расход</div>
                <div className="perf-when">
                  {bar
                    ? shown.step === 3600
                      ? hourFmt.format(new Date(bar.t))
                      : weekdayFmt.format(new Date(bar.t))
                    : 'за период'}
                </div>
              </div>
              <div className="perf-value">{usd(bar ? bar.cost : shown.cost)}</div>
              <div className="perf-legend">
                {OPERATIONS.filter((op) => totals('operation').some((t) => t.key === op)).map((op) => (
                  <span key={op}>
                    <i style={{ background: OPERATION_COLORS[op] }} />
                    {totals('operation').find((t) => t.key === op)?.title}
                    {bar ? <b> {usd(bar.by[op] ?? 0)}</b> : null}
                  </span>
                ))}
              </div>
              <SpendChart data={shown} hover={hover} onHover={setHover} />
            </div>
            <div className="section-footer">
              {shown.requests
                ? `${exact(shown.requests)} ${plural(shown.requests, 'запрос', 'запроса', 'запросов')} к ИИ` +
                  (shown.tokens
                    ? ` · токенов на входе ${exact(shown.tokens.prompt)}` +
                      (shown.tokens.cached ? ` (из кэша ${exact(shown.tokens.cached)})` : '') +
                      `, в ответах ${exact(shown.tokens.completion)}`
                    : '')
                : 'За этот период ИИ ничего не потратил.'}
            </div>
          </div>

          <Breakdown title="По задачам" items={totals('operation')} total={shown.cost} />
          <Breakdown title="По проектам" items={totals('project')} total={shown.cost} />
          <Breakdown title="По людям" items={totals('person')} total={shown.cost} />
          <Breakdown title="По моделям" items={totals('model')} total={shown.cost} />
        </>
      )}
    </Page>
  )
}
