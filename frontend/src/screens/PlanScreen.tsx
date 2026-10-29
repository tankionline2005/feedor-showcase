import { motion } from 'framer-motion'
import {
  CalendarClock,
  ChevronLeft,
  ChevronRight,
  Code2,
  Funnel,
  Loader2,
  Megaphone,
  RefreshCw,
  Sparkles,
  Ticket,
  Users,
  UsersRound,
  Wallet,
} from 'lucide-react'
import { type ReactNode, useCallback, useEffect, useState } from 'react'
import { api, type BillingState, type Plan } from '../api'
import { Switch } from '../components/Controls'
import { Page } from '../components/Page'
import { PlanCard } from '../components/PlanCard'
import { Sheet } from '../components/Sheet'
import { useToast } from '../components/Toast'
import { useChannels } from '../hooks/useChannels'
import { useReloadMe } from '../hooks/useMe'
import { reloadProjects } from '../hooks/useProjects'
import { longDate, planTerm, plural } from '../lib/format'
import { spring } from '../lib/motion'
import { cents, price, usd } from '../lib/money'
import { PromoSheet } from './PromoSheet'

/**
 * Settings → «Тариф»: the current project's plan as its card, what of it is used, its AI this month, renewal, the
 * other plans to move to (from the owner's balance), and the promo code.
 */
export function PlanScreen({
  onBack,
  onOpenBalance,
  onOpenUsage,
}: {
  onBack: () => void
  onOpenBalance: () => void
  onOpenUsage: () => void
}) {
  const [state, setState] = useState<BillingState | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [promo, setPromo] = useState(false)
  const [buying, setBuying] = useState<Plan | null>(null)
  const [capping, setCapping] = useState(false)
  const { items } = useChannels()
  const toast = useToast()
  const reloadMe = useReloadMe()

  const reload = useCallback(async () => {
    try {
      setState(await api.billing())
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])
  useEffect(() => {
    reload()
  }, [reload])

  // the project's staff and trigger words, against the plan's limits
  const [members, setMembers] = useState<number | null>(null)
  const [triggers, setTriggers] = useState<number | null>(null)
  const [groups, setGroups] = useState<number | null>(null)
  const plan = state?.plan ?? null
  useEffect(() => {
    if (!plan) return
    api.team().then(
      (t) => setMembers(t.items.filter((m) => !m.paused).length),
      () => setMembers(null),
    )
    api.triggers().then(
      (t) => setTriggers(t.used),
      () => setTriggers(null),
    )
    api.groups().then(
      (g) => setGroups(g.items.length),
      () => setGroups(null),
    )
  }, [plan])

  const save = async (values: Parameters<typeof api.patchBilling>[0]) => {
    try {
      setState(await api.patchBilling(values))
      reloadMe()
    } catch (e) {
      toast((e as Error).message)
    }
  }

  const backButton = (
    <motion.button className="nav-button" onClick={onBack} whileTap={{ opacity: 0.4 }} style={{ paddingLeft: 0 }}>
      <ChevronLeft size={30} strokeWidth={2.2} style={{ marginRight: -4 }} />
      Настройки
    </motion.button>
  )
  if (!state) {
    return (
      <Page title="Тариф" left={backButton}>
        {error ? (
          <div className="error-banner">{error}</div>
        ) : (
          <div className="loader">
            <Loader2 size={22} className="spinner" />
          </div>
        )}
      </Page>
    )
  }

  // the project's own channel is free: it does not count towards the limit
  const working = items?.filter((c) => !c.own && !c.paused).length
  const others = state.plans.filter((p) => p.buyable && p.id !== plan?.id)
  const ai = state.ai
  const count = (used: number | null | undefined, limit: number | null, one: string, few: string, many: string) =>
    limit === null
      ? `${used ?? '…'} · без ограничений`
      : used !== null && used !== undefined
        ? `${used} из ${limit}`
        : `до ${limit} ${plural(limit, one, few, many)}`

  return (
    <Page title="Тариф" left={backButton} onRefresh={reload}>
      <div className="plan-project">Проект «{state.project}»</div>

      <motion.div style={{ margin: '0 16px' }} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={spring}>
        {plan ? (
          <PlanCard plan={plan} all={state.plans} state="current" />
        ) : (
          <div className="plan-card none">
            <div className="plan-card-title">{state.expired_plan ? `${state.expired_plan.title} закончился` : 'Нет тарифа'}</div>
            <div className="plan-card-sub">
              {state.expired_plan
                ? state.delete_at && state.payments_live
                  ? `Проект заморожен. ${longDate(state.delete_at)} его данные будут удалены без возможности восстановления — выберите тариф ниже.`
                  : 'Проект заморожен: каналы не читаются, ИИ и триггеры стоят, сотрудники не могут войти. Выберите тариф ниже.'
                : 'Чтобы добавлять каналы и пользоваться ИИ, выберите тариф ниже.'}
            </div>
          </div>
        )}
      </motion.div>

      {plan && (
        <div className="section" style={{ marginTop: 24 }}>
          <div className="group">
            <Cell icon={<Megaphone size={17} />} color="var(--accent)" title="Каналы"
              sub={state.paused.channels ? `ещё ${state.paused.channels} на паузе — сверх лимита` : undefined}
              value={count(working, plan.channels, 'канала', 'каналов', 'каналов')} />
            <Cell icon={<Users size={17} />} color="var(--orange)" title="Сотрудники"
              sub={state.paused.members ? `ещё ${state.paused.members} на паузе — не могут войти` : undefined}
              value={plan.members === 0 ? 'нет в тарифе' : count(members, plan.members, 'сотрудника', 'сотрудников', 'сотрудников')} />
            <Cell icon={<Funnel size={17} />} color="var(--accent)" title="Триггер-слова"
              sub={state.paused.triggers ? `ещё ${state.paused.triggers} на паузе` : 'ваши и сотрудников вместе'}
              value={count(triggers, plan.triggers, 'слова', 'слов', 'слов')} />
            <Cell icon={<UsersRound size={17} />} color="var(--indigo)" title="Группы проекта"
              value={count(groups, plan.groups, 'группы', 'групп', 'групп')} />
            <Cell icon={<Code2 size={18} />} color="var(--text-2)" title="API и MCP-коннектор" value={plan.api ? 'есть' : 'нет'} />
            <Cell icon={<CalendarClock size={17} />} color="var(--orange)" title="Срок"
              sub={planTerm(plan.expires_at) ?? (plan.id === 'trial' ? '30 дней — с запуска оплаты' : 'бессрочно')} />
          </div>
          {(state.paused.channels > 0 || state.paused.members > 0) && (
            <div className="section-footer">
              Сверх лимита тарифа каналы, сотрудники и триггер-слова ставятся на паузу, а не удаляются: они вернутся,
              как только тариф позволит. Какие каналы читать, можно выбрать в «Каналах».
            </div>
          )}
        </div>
      )}

      {ai && (
        <div className="section">
          <div className="section-header">ИИ тарифа</div>
          <div className="group">
            <div className="cell ai-meter" style={{ ['--inset' as string]: '57px' }}>
              <div className="cell-icon ai-icon">
                <Sparkles size={17} />
              </div>
              <div className="cell-main">
                <div className="cell-title">
                  Осталось {usd(Math.max(0, ai.included - ai.used))} из {price(ai.included)}
                </div>
                <div className={`share-bar${ai.used >= ai.included * 0.9 ? ' warn' : ''}`} style={{ marginTop: 6 }}>
                  <span style={{ width: `${Math.min(100, (ai.used / Math.max(ai.included, 0.000001)) * 100)}%` }} />
                </div>
                <div className="cell-sub" style={{ marginTop: 4 }}>
                  {ai.renews_at ? `обновится ${longDate(ai.renews_at)}` : 'на весь пробный период'}
                  {ai.extra_used ? ` · сверх тарифа ${usd(ai.extra_used)}` : ''}
                </div>
              </div>
            </div>
            <div className="cell">
              <div className="cell-main">Докупать ИИ с баланса</div>
              <Switch label="Докупать ИИ с баланса" on={ai.extra} onChange={(v) => save({ ai_extra: v })} />
            </div>
            {ai.extra && (
              <button className="cell" onClick={() => setCapping(true)}>
                <div className="cell-main">Лимит докупки в месяц</div>
                <span className="cell-value">{ai.extra_cap === null ? 'без лимита' : price(ai.extra_cap)}</span>
                <ChevronRight size={18} color="var(--text-3)" />
              </button>
            )}
            <button className="cell" onClick={onOpenUsage}>
              <div className="cell-main">Расход на ИИ</div>
              <ChevronRight size={18} color="var(--text-3)" />
            </button>
          </div>
          <div className="section-footer">
            Когда ИИ тарифа закончится, запросы оплачиваются с баланса по цене сервиса.
            {ai.extra ? '' : ' Докупка выключена: ИИ остановится до нового месяца тарифа.'}
          </div>
        </div>
      )}

      <div className="section">
        <div className="group">
          <button className="cell" style={{ ['--inset' as string]: '57px' }} onClick={onOpenBalance}>
            <div className="cell-icon" style={{ background: 'var(--green)' }}>
              <Wallet size={17} />
            </div>
            <div className="cell-main">Баланс</div>
            <span className="cell-value">{cents(state.balance)}</span>
            <ChevronRight size={18} color="var(--text-3)" />
          </button>
          {plan?.buyable && (
            <div className="cell" style={{ ['--inset' as string]: '57px' }}>
              <div className="cell-icon" style={{ background: 'var(--accent)' }}>
                <RefreshCw size={16} />
              </div>
              <div className="cell-main">Автопродление</div>
              <Switch label="Автопродление" on={state.auto_renew} onChange={(v) => save({ auto_renew: v })} />
            </div>
          )}
        </div>
        {plan?.buyable && plan.expires_at && (
          <div className="section-footer">
            {state.next_plan
              ? `${longDate(plan.expires_at)} начнётся ${state.next_plan.title}${state.auto_renew ? ` — с баланса спишется ${price(state.next_plan.price ?? 0)}` : ''}.`
              : state.auto_renew
                ? `${longDate(plan.expires_at)} тариф продлится на 30 дней — с баланса спишется ${price(state.renew_price ?? 0)}.`
                : `Автопродление выключено: ${longDate(plan.expires_at)} проект заморозится.`}
          </div>
        )}
      </div>

      {plan?.id !== 'dev' && others.length > 0 && (
        <div className="section">
          <div className="section-header">{plan ? 'Другие тарифы' : 'Тарифы'}</div>
          <div className="tariffs">
            {others.map((p) => {
              const cheaper = !!plan?.buyable && (p.price ?? 0) < (plan.price ?? 0)
              const next = state.next_plan?.id === p.id
              return (
                <PlanCard
                  key={p.id}
                  plan={p}
                  all={state.plans}
                  state={next ? 'next' : null}
                  footer={
                    !next && (
                      <button className={`button tariff-button${p.id === 'studio' ? '' : ' on-color'}`} onClick={() => setBuying(p)}>
                        {cheaper ? 'Перейти со следующего периода' : `Перейти — ${price(p.price ?? 0)}`}
                      </button>
                    )
                  }
                />
              )
            })}
          </div>
        </div>
      )}

      <div className="section">
        <div className="group">
          <button className="cell accent" style={{ ['--inset' as string]: '57px' }} onClick={() => setPromo(true)}>
            <div className="cell-icon" style={{ background: 'var(--accent)' }}>
              <Ticket size={17} />
            </div>
            Ввести промокод
          </button>
        </div>
        <div className="section-footer">Промокод на тот же тариф продлевает его, на лучший — сразу заменяет текущий.</div>
      </div>

      <PromoSheet
        open={promo}
        onClose={() => {
          setPromo(false)
          reload()
        }}
      />
      <BuySheet
        plan={buying}
        state={state}
        onClose={() => setBuying(null)}
        onDone={async (next) => {
          setBuying(null)
          setState(next)
          await Promise.all([reloadMe(), reloadProjects()])
        }}
      />
      {ai && (
        <CapSheet
          open={capping}
          cap={ai.extra_cap}
          onClose={() => setCapping(false)}
          onSave={async (cap) => {
            await save(cap === null ? { clear_ai_extra_cap: true } : { ai_extra_cap: cap })
            setCapping(false)
          }}
        />
      )}
    </Page>
  )
}

function Cell({ icon, color, title, sub, value }: { icon: ReactNode; color: string; title: string; sub?: string; value?: string }) {
  return (
    <div className="cell" style={{ ['--inset' as string]: '57px' }}>
      <div className="cell-icon" style={{ background: color }}>
        {icon}
      </div>
      <div className="cell-main">
        {sub ? (
          <>
            <div className="cell-title">{title}</div>
            <div className="cell-sub">{sub}</div>
          </>
        ) : (
          title
        )}
      </div>
      {value && <span className="cell-value">{value}</span>}
    </div>
  )
}

/** Moving to another plan: what is taken from the balance (or when the cheaper one starts), and to confirm. */
function BuySheet({
  plan,
  state,
  onClose,
  onDone,
}: {
  plan: Plan | null
  state: BillingState
  onClose: () => void
  onDone: (next: BillingState) => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [shown, setShown] = useState<Plan | null>(plan)
  const toast = useToast()
  useEffect(() => {
    if (plan) {
      setShown(plan)
      setError(null)
    }
  }, [plan])
  const current = state.plan
  const cheaper = !!shown && !!current?.buyable && (shown.price ?? 0) < (current.price ?? 0)

  const buy = async () => {
    if (!shown) return
    setBusy(true)
    setError(null)
    try {
      const next = await api.buyPlan(shown.id)
      toast(next.done.next ? `${shown.title} начнётся со следующего периода` : `Тариф ${shown.title} подключён`)
      onDone(next)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  const topup = async () => {
    try {
      await api.topup()
    } catch (e) {
      toast((e as Error).message)
    }
  }

  return (
    <Sheet
      open={!!plan}
      onClose={onClose}
      title={shown ? `Тариф ${shown.title}` : ''}
      left={
        <button className="nav-button" onClick={onClose}>
          Отмена
        </button>
      }
    >
      {shown && (
        <>
          <div className="tariffs" style={{ marginBottom: 16 }}>
            <PlanCard plan={shown} all={state.plans} />
          </div>
          <div className="section">
            <div className="group">
              <div className="cell">
                <div className="cell-main">{cheaper ? 'Начнётся' : 'Спишется с баланса'}</div>
                <span className="cell-value">
                  {cheaper && current?.expires_at ? longDate(current.expires_at) : price(shown.price ?? 0)}
                </span>
              </div>
              <div className="cell">
                <div className="cell-main">На балансе</div>
                <span className="cell-value">{cents(state.balance)}</span>
              </div>
            </div>
            <div className="section-footer">
              {cheaper
                ? `До конца периода остаётся ${current?.title}. Потом начнётся ${shown.title} за ${price(shown.price ?? 0)} — если включено автопродление и на балансе хватает денег.`
                : current?.buyable
                  ? `${shown.title} начнётся сразу, на 30 дней. Неиспользованные дни тарифа ${current.title} вернутся на баланс.`
                  : `${shown.title} начнётся сразу, на 30 дней.`}
            </div>
          </div>
          {error && <div className="error-banner">{error}</div>}
          <div className="section">
            <button className="button" disabled={busy} onClick={buy}>
              {busy ? <Loader2 size={20} className="spinner" /> : cheaper ? 'Перейти со следующего периода' : `Оплатить ${price(shown.price ?? 0)}`}
            </button>
            {error?.startsWith('Не хватает') && (
              <button className="button secondary" style={{ marginTop: 10 }} onClick={topup}>
                Пополнить баланс
              </button>
            )}
          </div>
        </>
      )}
    </Sheet>
  )
}

/** «Лимит докупки в месяц»: at most this much of the AI from the balance a month, or none. */
function CapSheet({
  open,
  cap,
  onClose,
  onSave,
}: {
  open: boolean
  cap: number | null
  onClose: () => void
  onSave: (cap: number | null) => Promise<void>
}) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    if (open) setText(cap === null ? '' : String(cap))
  }, [open, cap])
  const value = Number(text.replace(',', '.'))
  const valid = text.trim() === '' || (Number.isFinite(value) && value >= 0)
  const save = async () => {
    setBusy(true)
    try {
      await onSave(text.trim() === '' ? null : Math.round(value * 100) / 100)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Sheet
      open={open}
      onClose={onClose}
      title="Лимит докупки"
      left={
        <button className="nav-button" onClick={onClose}>
          Отмена
        </button>
      }
      right={
        <button className="nav-button" style={{ fontWeight: 600 }} disabled={busy || !valid} onClick={save}>
          {busy ? <Loader2 size={20} className="spinner" /> : 'Готово'}
        </button>
      }
    >
      <div className="section">
        <div className="group">
          <input
            className="field"
            inputMode="decimal"
            placeholder="Без лимита"
            value={text}
            onChange={(e) => setText(e.target.value)}
            autoFocus
          />
        </div>
        <div className="section-footer">
          Сколько долларов в месяц ИИ может потратить с баланса сверх тарифа. Пусто — без лимита.
        </div>
      </div>
    </Sheet>
  )
}
