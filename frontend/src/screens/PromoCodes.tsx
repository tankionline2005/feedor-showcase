import { AnimatePresence, motion } from 'framer-motion'
import { ChevronLeft, Copy, Loader2, Ticket, Trash2 } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, type PromoCode } from '../api'
import { Stepper, Switch } from '../components/Controls'
import { Page } from '../components/Page'
import { Segmented } from '../components/Segmented'
import { SwipeRow } from '../components/SwipeRow'
import { useToast } from '../components/Toast'
import { copyText } from '../lib/clipboard'
import { dayLabel, plural } from '../lib/format'
import { spring } from '../lib/motion'

// the plans a code may give: not the trial, not dev:service:plan
const PLANS = [
  { value: 'starter', label: 'Starter' },
  { value: 'pro', label: 'Pro' },
  { value: 'studio', label: 'Studio' },
  { value: 'agency', label: 'Agency' },
] as const
const DAY_STEPS = [1, 3, 7, 15, 30, 60, 90, 180, 365]
const COUNT_STEPS = [1, 5, 10, 20, 50, 100]

const term = (days: number | null) => (days === null ? 'бессрочно' : `${days} ${plural(days, 'день', 'дня', 'дней')}`)

/** The admin's promo codes: generate a batch for a plan and a term, see who used which. */
export function PromoCodes({ onBack }: { onBack: () => void }) {
  const [codes, setCodes] = useState<PromoCode[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [plan, setPlan] = useState<(typeof PLANS)[number]['value']>('pro')
  const [days, setDays] = useState(15)
  const [forever, setForever] = useState(false)
  const [count, setCount] = useState(20)
  const [busy, setBusy] = useState(false)
  const [fresh, setFresh] = useState<string[]>([])
  const toast = useToast()

  const reload = useCallback(async () => {
    try {
      setCodes((await api.promoCodes()).items)
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])

  useEffect(() => {
    reload()
  }, [reload])

  const generate = async () => {
    setBusy(true)
    try {
      const res = await api.generatePromo({ plan, days: forever ? null : days, count })
      const made = res.items.map((c) => c.code)
      setFresh(made)
      const copied = await copyText(made.join('\n'))
      toast(`Создано ${made.length} ${plural(made.length, 'промокод', 'промокода', 'промокодов')}${copied ? ' и скопировано' : ''}`)
      await reload()
    } catch (e) {
      toast((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const copy = async (text: string, what: string) => toast((await copyText(text)) ? `${what} скопирован` : text)

  const remove = async (code: string) => {
    try {
      await api.deletePromo(code)
      setFresh((f) => f.filter((c) => c !== code))
      await reload()
    } catch (e) {
      toast((e as Error).message)
    }
  }

  const unused = useMemo(() => (codes ?? []).filter((c) => !c.used_by), [codes])
  const used = useMemo(() => (codes ?? []).filter((c) => c.used_by), [codes])

  return (
    <Page
      title="Промокоды"
      onRefresh={reload}
      left={
        <motion.button className="nav-button" onClick={onBack} whileTap={{ opacity: 0.4 }} style={{ paddingLeft: 0 }}>
          <ChevronLeft size={30} strokeWidth={2.2} style={{ marginRight: -4 }} />
          Настройки
        </motion.button>
      }
    >
      {error && <div className="error-banner">{error}</div>}

      <div className="section-header" style={{ marginLeft: 16 }}>Создать</div>
      <div className="section">
        <div style={{ padding: '0 0 10px' }}>
          <Segmented id="promo-plan" options={[...PLANS]} value={plan} onChange={setPlan} />
        </div>
        <div className="group">
          <div className="cell">
            <div className="cell-main">Бессрочно</div>
            <Switch label="Бессрочно" on={forever} onChange={setForever} />
          </div>
          {!forever && (
            <div className="cell">
              <div className="cell-main">Срок: {term(days)}</div>
              <Stepper value={days} steps={DAY_STEPS} onChange={setDays} />
            </div>
          )}
          <div className="cell">
            <div className="cell-main">
              {count} {plural(count, 'промокод', 'промокода', 'промокодов')}
            </div>
            <Stepper value={count} steps={COUNT_STEPS} onChange={setCount} />
          </div>
          <button className="cell accent" disabled={busy} onClick={generate}>
            {busy ? <Loader2 size={18} className="spinner" /> : <Ticket size={18} />}
            Создать
          </button>
        </div>
        <div className="section-footer">
          Каждый промокод срабатывает один раз. Тот же тариф продлевается на срок промокода, лучший заменяет текущий сразу,
          худший не принимается.
        </div>
      </div>

      {fresh.length > 0 && (
        <div className="section">
          <div className="group">
            <button className="cell accent" onClick={() => copy(fresh.join('\n'), 'Список')}>
              <Copy size={18} />
              Скопировать новые ({fresh.length})
            </button>
          </div>
        </div>
      )}

      {codes && codes.length === 0 && (
        <div className="empty" style={{ paddingTop: 8 }}>
          <div className="empty-title">Промокодов пока нет</div>
        </div>
      )}

      {unused.length > 0 && (
        <>
          <div className="section-header" style={{ marginLeft: 16 }}>Свободные · {unused.length}</div>
          <div className="section">
            <div className="group">
              <AnimatePresence initial={false}>
                {unused.map((c) => (
                  <motion.div key={c.code} layout="position" exit={{ opacity: 0, height: 0 }} transition={spring}>
                    <SwipeRow actions={[{ label: 'Удалить', icon: Trash2, color: 'var(--red)', onClick: () => remove(c.code) }]}>
                      <button className="cell" style={{ width: '100%' }} onClick={() => copy(c.code, 'Промокод')}>
                        <div className="cell-main">
                          <div className="cell-title promo-code">{c.code}</div>
                          <div className="cell-sub">
                            {c.plan.title} · {term(c.days)}
                          </div>
                        </div>
                        {fresh.includes(c.code) && <span className="count-badge" style={{ background: 'var(--green)' }}>новый</span>}
                      </button>
                    </SwipeRow>
                  </motion.div>
                ))}
              </AnimatePresence>
            </div>
            <div className="section-footer">Нажмите на промокод, чтобы скопировать его.</div>
          </div>
        </>
      )}

      {used.length > 0 && (
        <>
          <div className="section-header" style={{ marginLeft: 16 }}>Использованные · {used.length}</div>
          <div className="section">
            <div className="group">
              {used.map((c) => (
                <div className="cell" key={c.code}>
                  <div className="cell-main">
                    <div className="cell-title promo-code" style={{ color: 'var(--text-2)' }}>
                      {c.code}
                    </div>
                    <div className="cell-sub">
                      {c.plan.title} · {term(c.days)} ·{' '}
                      {c.used_by?.username ? `@${c.used_by.username}` : c.used_by?.name || 'пользователь'}
                      {c.used_at ? `, ${dayLabel(c.used_at).toLowerCase()}` : ''}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </>
      )}
    </Page>
  )
}
