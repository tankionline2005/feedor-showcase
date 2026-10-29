import { motion } from 'framer-motion'
import { ChevronLeft, ChevronRight, Loader2, Sparkles } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { api, type AiOperation, type ServiceAi } from '../api'
import { Stepper } from '../components/Controls'
import { Page } from '../components/Page'
import { Sheet } from '../components/Sheet'
import { useToast } from '../components/Toast'
import { plural } from '../lib/format'
import { usd } from '../lib/money'
import { ModelPicker } from './AiSettings'

const MARKUPS = [100, 125, 150, 175, 200, 250, 300, 400, 500]
const shortModel = (id: string) => id.split('/').pop() ?? id

/** What OpenRouter sent the admin back with: "#/service-ai?connected=1" or "#/service-ai?error=…". */
function takeReturn(): { connected: boolean; error: string | null } {
  const query = new URLSearchParams(location.hash.split('?')[1] ?? '')
  if (query.size) history.replaceState(null, '', '#/service-ai')
  return { connected: query.get('connected') === '1', error: query.get('error') }
}

/**
 * Admin → «ИИ и оплата»: the service's OpenRouter (the AI of every plan but dev:service:plan), its models and
 * markup, what the month cost and brought, and opening the payments.
 */
export function ServiceAiScreen({ onBack }: { onBack: () => void }) {
  const [state, setState] = useState<ServiceAi | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [picking, setPicking] = useState<AiOperation | null>(null)
  const [connecting, setConnecting] = useState(false)
  const [live, setLive] = useState(false)
  const toast = useToast()
  const [returned] = useState(takeReturn)

  const reload = useCallback(async () => {
    try {
      setState(await api.serviceAi())
      setError(returned.error)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [returned])
  useEffect(() => {
    reload()
  }, [reload])
  useEffect(() => {
    if (returned.connected) toast('OpenRouter сервиса подключён')
  }, [returned, toast])

  const save = async (values: Parameters<typeof api.patchServiceAi>[0]) => {
    try {
      setState(await api.patchServiceAi(values))
    } catch (e) {
      toast((e as Error).message)
    }
  }
  const connect = async () => {
    setConnecting(true)
    try {
      location.href = (await api.connectServiceAi()).url
    } catch (e) {
      toast((e as Error).message)
      setConnecting(false)
    }
  }
  const unlink = async () => {
    if (!confirm('Отключить OpenRouter сервиса? ИИ всех тарифов, кроме dev:service:plan, остановится.')) return
    try {
      setState(await api.unlinkServiceAi())
    } catch (e) {
      toast((e as Error).message)
    }
  }

  return (
    <Page
      title="ИИ и оплата"
      onRefresh={reload}
      left={
        <motion.button className="nav-button" onClick={onBack} whileTap={{ opacity: 0.4 }} style={{ paddingLeft: 0 }}>
          <ChevronLeft size={30} strokeWidth={2.2} style={{ marginRight: -4 }} />
          Настройки
        </motion.button>
      }
    >
      {error && <div className="error-banner">{error}</div>}
      {!state && !error && (
        <div className="loader">
          <Loader2 size={22} className="spinner" />
        </div>
      )}
      {state && (
        <>
          <div className="section">
            <div className="group balance-card">
              <div className="balance-label">OpenRouter сервиса</div>
              <div className="balance-value">
                {state.linked ? (state.balance?.credits_left != null ? usd(state.balance.credits_left) : '—') : 'Не подключён'}
              </div>
              <div className="balance-sub">
                {state.paused
                  ? state.paused_reason === 'no_credits'
                    ? 'Закончились средства — ИИ пользователей стоит. Пополните OpenRouter, и он продолжит сам.'
                    : 'OpenRouter не принимает ключ — подключите заново.'
                  : state.linked
                    ? 'Остаток на балансе OpenRouter. Бот предупредит, когда останется меньше $5.'
                    : 'Без него ИИ всех тарифов, кроме dev:service:plan, не работает.'}
              </div>
            </div>
            <div className="group" style={{ marginTop: 12 }}>
              <button className="cell accent" onClick={connect} disabled={connecting}>
                <div className="cell-main accent-text">{state.linked ? 'Подключить заново' : 'Подключить OpenRouter'}</div>
                {connecting && <Loader2 size={18} className="spinner" />}
              </button>
              {state.linked && (
                <button className="cell destructive" onClick={unlink}>
                  Отключить
                </button>
              )}
            </div>
          </div>

          <div className="section">
            <div className="section-header">Модели</div>
            <div className="group">
              {state.models.map((m) => (
                <button key={m.operation} className="cell" onClick={() => setPicking(m.operation)}>
                  <div className="cell-main">
                    <div className="cell-title">{m.title}</div>
                    <div className="cell-sub mono-sub">
                      {shortModel(m.model)}
                      {m.model === m.default ? ' · по умолчанию' : ''}
                    </div>
                  </div>
                  <ChevronRight size={18} color="var(--text-3)" />
                </button>
              ))}
            </div>
            <div className="section-footer">Для всех пользователей, кроме dev:service:plan: они выбирают модели сами.</div>
          </div>

          <div className="section">
            <div className="group">
              <div className="cell">
                <div className="cell-main">
                  <div className="cell-title">Наценка на ИИ</div>
                  <div className="cell-sub">×{(state.markup_pct / 100).toLocaleString('ru-RU')} к цене OpenRouter</div>
                </div>
                <Stepper value={state.markup_pct} steps={MARKUPS} onChange={(v) => save({ markup_pct: v })} />
              </div>
            </div>
            <div className="section-footer">
              Пользователи видят расход уже с наценкой. Новая наценка — для новых запросов, прошлые не пересчитываются.
            </div>
          </div>

          <div className="section">
            <div className="section-header">Этот месяц</div>
            <div className="group">
              <div className="cell">
                <div className="cell-main">ИИ обошёлся сервису</div>
                <span className="cell-value">{usd(state.month.cost)}</span>
              </div>
              <div className="cell">
                <div className="cell-main">Пользователи заплатили за ИИ</div>
                <span className="cell-value">{usd(state.month.charged)}</span>
              </div>
              <div className="cell">
                <div className="cell-main">Куплено тарифов</div>
                <span className="cell-value">{usd(state.month.plans)}</span>
              </div>
            </div>
          </div>

          {state.owners.length > 0 && (
            <div className="section">
              <div className="section-header">Кто больше тратит на ИИ</div>
              <div className="group">
                {state.owners.map((o) => (
                  <div key={o.user_id} className="cell">
                    <div className="cell-main">
                      <div className="cell-title">{o.name || (o.username ? `@${o.username}` : `#${o.user_id}`)}</div>
                      <div className="cell-sub">
                        {o.requests} {plural(o.requests, 'запрос', 'запроса', 'запросов')} · себестоимость {usd(o.cost)}
                      </div>
                    </div>
                    <span className="cell-value">{usd(o.charged)}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="section">
            <div className="group">
              <button className="cell" style={{ ['--inset' as string]: '57px' }} disabled={state.payments_live} onClick={() => setLive(true)}>
                <div className="cell-icon" style={{ background: state.payments_live ? 'var(--green)' : 'var(--orange)' }}>
                  <Sparkles size={17} />
                </div>
                <div className="cell-main">{state.payments_live ? 'Оплата запущена' : 'Запустить оплату'}</div>
                {!state.payments_live && <ChevronRight size={18} color="var(--text-3)" />}
              </button>
            </div>
            <div className="section-footer">
              {state.payments_live
                ? 'Пробные периоды идут, замороженные проекты удаляются через 7 дней.'
                : `Пока оплата не запущена, пробный период не кончается и ничего не удаляется. Ждут запуска: ${state.trials_waiting} ${plural(state.trials_waiting, 'проект', 'проекта', 'проектов')}.`}
            </div>
          </div>

          <ModelPicker
            operation={picking}
            state={state}
            onClose={() => setPicking(null)}
            onPicked={async (op, model) => {
              await save({ models: { [op]: model } })
              setPicking(null)
            }}
          />
          <LiveSheet
            open={live}
            waiting={state.trials_waiting}
            onClose={() => setLive(false)}
            onDone={(next) => {
              setState(next)
              setLive(false)
            }}
          />
        </>
      )}
    </Page>
  )
}

/** «Запустить оплату»: once, never undone — the trials start counting, frozen projects start going. */
function LiveSheet({
  open,
  waiting,
  onClose,
  onDone,
}: {
  open: boolean
  waiting: number
  onClose: () => void
  onDone: (next: ServiceAi) => void
}) {
  const [busy, setBusy] = useState(false)
  const toast = useToast()
  const go = async () => {
    setBusy(true)
    try {
      const next = await api.goLive()
      toast(`Оплата запущена: ${next.trials_started} пробных периодов начались`)
      onDone(next)
    } catch (e) {
      toast((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Sheet
      open={open}
      onClose={onClose}
      title="Запустить оплату"
      left={
        <button className="nav-button" onClick={onClose}>
          Отмена
        </button>
      }
    >
      <div className="section">
        <div className="delete-prompt">
          У {waiting} {plural(waiting, 'проекта', 'проектов', 'проектов')} на Free Trial начнутся 30 дней. Проекты, у которых
          тариф закончился, будут удаляться через 7 дней заморозки. Отменить запуск нельзя.
        </div>
        <button className="button" disabled={busy} onClick={go}>
          {busy ? <Loader2 size={20} className="spinner" /> : 'Запустить'}
        </button>
      </div>
    </Sheet>
  )
}
