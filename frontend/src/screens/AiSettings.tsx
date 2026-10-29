import { motion } from 'framer-motion'
import {
  Bot,
  Check,
  ChevronLeft,
  ChevronRight,
  CircleDollarSign,
  Gauge,
  Loader2,
  Search,
  Sparkles,
  Unlink,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, type AiModel, type AiOperation, type AiState } from '../api'
import { Alert } from '../components/Alert'
import { Page } from '../components/Page'
import { ActionSheet, Sheet } from '../components/Sheet'
import { useToast } from '../components/Toast'
import { useReloadMe } from '../hooks/useMe'
import { plural, relative } from '../lib/format'
import { spring } from '../lib/motion'
import { usd } from '../lib/money'

/** "anthropic/claude-haiku-5.5" → "claude-haiku-5.5" */
const shortModel = (id: string) => id.split('/').pop() ?? id

const WARNING_WAIT = 5_000
const shownAt = new Map<string, number>() // when the browser keeps nothing (a private window)

/** When the warning on this link was first shown: «Понятно, принимаю» waits a few seconds after that
 * moment only, so it is read at least once; on a later visit the button is ready at once. */
function firstShown(connectedAt: string): number {
  const key = `ai-warning-shown:${connectedAt}`
  try {
    const saved = Number(localStorage.getItem(key))
    if (saved) return saved
    localStorage.setItem(key, String(Date.now()))
  } catch {
    if (!shownAt.has(key)) shownAt.set(key, Date.now())
    return shownAt.get(key)!
  }
  return Date.now()
}

/** What OpenRouter sent the owner back with: "#/openrouter?connected=1" or "#/openrouter?error=…". */
function takeReturn(): { connected: boolean; error: string | null } {
  const query = new URLSearchParams(location.hash.split('?')[1] ?? '')
  if (query.size) history.replaceState(null, '', '#/openrouter')
  return { connected: query.get('connected') === '1', error: query.get('error') }
}

/** Settings → «Настройки OpenRouter.ai»: models, spending and the link itself. */
export function AiSettings({ onBack, onOpenUsage }: { onBack: () => void; onOpenUsage: () => void }) {
  const toast = useToast()
  const reloadMe = useReloadMe()
  const [state, setState] = useState<AiState | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [returned] = useState(takeReturn)
  const [sheet, setSheet] = useState<'models' | 'limit' | 'unlink' | null>(null)
  const [picking, setPicking] = useState<AiOperation | null>(null)
  const [connecting, setConnecting] = useState(false)
  // seconds left before «Понятно, принимаю» can be pressed
  const [wait, setWait] = useState(0)
  const warning = !!state?.linked && !state.warning_accepted
  const connectedAt = state?.connected_at

  useEffect(() => {
    if (!warning || !connectedAt) return
    const until = firstShown(connectedAt) + WARNING_WAIT
    const tick = () => setWait(Math.max(0, Math.ceil((until - Date.now()) / 1000)))
    tick()
    const timer = window.setInterval(tick, 250)
    const stop = window.setTimeout(() => window.clearInterval(timer), until - Date.now() + 300)
    return () => {
      window.clearInterval(timer)
      window.clearTimeout(stop)
    }
  }, [warning, connectedAt])

  const reload = useCallback(async () => {
    try {
      setState(await api.ai())
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])

  useEffect(() => {
    reload()
    if (returned.connected) {
      toast('OpenRouter.ai подключён')
      reloadMe()
    }
  }, [reload, returned, toast, reloadMe])

  const save = async (values: Parameters<typeof api.saveAi>[0], done?: string) => {
    try {
      setState(await api.saveAi(values))
      if (done) toast(done)
      return true
    } catch (e) {
      toast((e as Error).message)
      return false
    }
  }

  const connect = async () => {
    setConnecting(true)
    try {
      location.href = (await api.aiConnect()).url
    } catch (e) {
      toast((e as Error).message)
      setConnecting(false)
    }
  }

  const acceptWarning = async () => {
    try {
      setState(await api.aiAcceptWarning())
    } catch (e) {
      toast((e as Error).message)
    }
  }

  const unlink = async () => {
    try {
      setState(await api.aiUnlink())
      await reloadMe()
      toast('OpenRouter.ai отвязан')
    } catch (e) {
      toast((e as Error).message)
    }
  }

  return (
    <Page
      title="OpenRouter.ai"
      onRefresh={reload}
      left={
        <motion.button className="nav-button" onClick={onBack} whileTap={{ opacity: 0.4 }} style={{ paddingLeft: 0 }}>
          <ChevronLeft size={30} strokeWidth={2.2} style={{ marginRight: -4 }} />
          Настройки
        </motion.button>
      }
    >
      {(error || returned.error) && <div className="error-banner">{error || returned.error}</div>}

      {!state && !error && (
        <div className="loader">
          <Loader2 size={22} className="spinner" />
        </div>
      )}

      {state && !state.linked && (
        <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={spring}>
          <div className="empty" style={{ paddingBottom: 12 }}>
            <div className="empty-icon ai-icon-large">
              <Sparkles size={30} strokeWidth={1.7} />
            </div>
            <div className="empty-title">OpenRouter.ai не подключён</div>
            <div>
              Подключите LLM-агрегатор и больше не пропускайте новости: умная лента, готовые темы и посты для вашего
              канала. Все ваши настройки сохранены и заработают сразу после подключения.
            </div>
          </div>
          <div style={{ padding: '0 16px 32px' }}>
            <button className="button" onClick={connect} disabled={connecting}>
              {connecting ? (
                <Loader2 size={20} className="spinner" style={{ color: '#fff' }} />
              ) : (
                'Подключить OpenRouter.ai'
              )}
            </button>
          </div>
        </motion.div>
      )}

      {state?.linked && (
        <>
          {state.paused && <div className="error-banner">{state.paused}</div>}

          <div className="section">
            <div className="group">
              <button className="cell" style={{ ['--inset' as string]: '57px' }} onClick={() => setSheet('models')}>
                <div className="cell-icon" style={{ background: 'var(--indigo)' }}>
                  <Bot size={18} />
                </div>
                <div className="cell-main">Выбрать ИИ модели</div>
                <ChevronRight size={18} color="var(--text-3)" />
              </button>
            </div>
            <div className="section-footer">
              Для каждой задачи — своя модель: дешёвая для фильтрации ленты, умнее — для тем, общения и постов.
            </div>
          </div>

          <div className="section">
            <div className="group">
              <button className="cell" style={{ ['--inset' as string]: '57px' }} onClick={onOpenUsage}>
                <div className="cell-icon" style={{ background: 'var(--green)' }}>
                  <CircleDollarSign size={18} />
                </div>
                <div className="cell-main">Расход и остаток</div>
                <span className="cell-value">{usd(state.spent_month)} за месяц</span>
                <ChevronRight size={18} color="var(--text-3)" />
              </button>
              <button className="cell" style={{ ['--inset' as string]: '57px' }} onClick={() => setSheet('limit')}>
                <div className="cell-icon" style={{ background: 'var(--red)' }}>
                  <Gauge size={18} />
                </div>
                <div className="cell-main">Месячный лимит</div>
                <span className="cell-value">
                  {state.monthly_limit_usd === null ? 'нет' : usd(state.monthly_limit_usd)}
                </span>
                <ChevronRight size={18} color="var(--text-3)" />
              </button>
            </div>
            <div className="section-footer">
              Всё оплачивается с баланса вашего аккаунта OpenRouter. Когда расходы за месяц дойдут до лимита, ИИ
              остановится до следующего месяца.
            </div>
          </div>

          <div className="section">
            <div className="group">
              <button
                className="cell destructive"
                style={{ ['--inset' as string]: '57px' }}
                onClick={() => setSheet('unlink')}
              >
                <div className="cell-icon" style={{ background: 'var(--red)' }}>
                  <Unlink size={17} />
                </div>
                Отвязать OpenRouter.ai
              </button>
            </div>
            {state.connected_at && <div className="section-footer">Подключён {relative(state.connected_at)}.</div>}
          </div>

          <ModelsSheet
            open={sheet === 'models'}
            state={state}
            onClose={() => setSheet(null)}
            onPick={(op) => setPicking(op)}
          />
          <ModelPicker
            operation={picking}
            state={state}
            onClose={() => setPicking(null)}
            onPicked={async (op, model) => {
              if (await save({ models: { [op]: model } }, 'Модель выбрана')) setPicking(null)
            }}
          />
          <LimitSheet open={sheet === 'limit'} state={state} onClose={() => setSheet(null)} onSave={save} />
        </>
      )}

      {/* after each link, until accepted: closes only by an answer */}
      <Alert
        open={warning && sheet !== 'unlink'}
        wide
        title="Важно перед началом"
        buttons={[
          {
            label: wait ? `Понятно, принимаю (${wait})` : 'Понятно, принимаю',
            onClick: acceptWarning,
            strong: true,
            disabled: wait > 0,
          },
          { label: 'Отвязать OpenRouter.ai', onClick: () => setSheet('unlink'), destructive: true },
        ]}
        onClose={() => {}}
        text={
          <>
            <p>ИИ на сайте работает через ваш аккаунт OpenRouter и за его счёт.</p>
            <p>
              <b>Это временно — на период бета-тестирования.</b> Пока на сайте нет внутреннего баланса и приёма
              платежей, мы не можем сами оплачивать запросы к ИИ и списывать их стоимость с вас, поэтому ИИ подключается
              через OpenRouter. После релиза ИИ станет встроенным: подключать сторонние сервисы будет не нужно, а
              запросы к ИИ будут оплачиваться с баланса прямо на сайте.
            </p>
            <p>
              <b>Расходы — на вашей ответственности.</b> Каждый запрос оплачивается с вашего баланса OpenRouter по цене
              выбранной модели. Мы не управляем этими расходами и не возмещаем их.
            </p>
            <p>
              <b>Выбор моделей — тоже.</b> Не каждая модель подходит для таких задач. Одна справляется отлично, другая с
              той же задачей ошибается, отвечает невпопад или обходится в разы дороже. То, что хорошо работает с одной
              моделью, не обязательно заработает с другой. Сменив модель, проверьте результат и последите за расходами.
            </p>
            <p>
              <b>По умолчанию выбраны бесплатные модели.</b> Они ничего не стоят, но работают заметно хуже платных,
              бывают медленными и ограничены дневным лимитом запросов OpenRouter — общим для всех функций ИИ. Пополнив
              баланс OpenRouter на $10, вы получите 1000 бесплатных запросов в день.
            </p>
            <p>
              <b>Для лучшего результата выберите платные модели</b> в разделе «Выбрать ИИ модели». Модели умнее и дороже
              работают лучше, но и тратят больше: сколько — зависит от числа каналов и постов и от того, как часто ИИ
              запускается по расписанию.
            </p>
            <p>
              Совет: выбрав платные модели, задайте «Месячный лимит» — когда расходы дойдут до него, ИИ остановится и
              лишнего не потратит.
            </p>
          </>
        }
      />
      <ActionSheet
        open={sheet === 'unlink'}
        onClose={() => setSheet(null)}
        title="Отвязать OpenRouter.ai?"
        message="Ключ удалится с нашего сервера, и ИИ перестанет работать. Модели, промпты, умные ленты, темы и публикации сохранятся — после нового подключения всё продолжит работать. Сам ключ можно удалить в настройках OpenRouter."
        actions={[{ label: 'Отвязать', destructive: true, onClick: unlink }]}
      />
    </Page>
  )
}

function ModelsSheet({
  open,
  state,
  onClose,
  onPick,
}: {
  open: boolean
  state: AiState
  onClose: () => void
  onPick: (op: AiOperation) => void
}) {
  return (
    <Sheet
      open={open}
      onClose={onClose}
      title="ИИ модели"
      right={
        <button className="nav-button" style={{ fontWeight: 600 }} onClick={onClose}>
          Готово
        </button>
      }
    >
      <div className="section">
        <div className="group">
          {state.models.map((m) => (
            <button key={m.operation} className="cell" onClick={() => onPick(m.operation)}>
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
        <div className="section-footer">
          «Общение с ИИ и посты» — модель чата: она отвечает в чатах, пишет в них посты для вашего канала, а ещё сама
          пишет тематические посты для «Публикаций» по новым темам. Цены моделей — за миллион токенов, отдельно для
          текста на входе и ответа.
        </div>
      </div>
    </Sheet>
  )
}

const price = (m: AiModel) => `${usd(m.prompt_price)} / ${usd(m.completion_price)}`
const context = (n: number | null) =>
  !n
    ? ''
    : n >= 1_000_000
      ? `${(n / 1_000_000).toFixed(n % 1_000_000 ? 1 : 0).replace('.', ',')}M`
      : `${Math.round(n / 1000)}K`

/** Choosing an operation's model, cheapest first — the owner's own OpenRouter, or the admin's for the service's. */
export function ModelPicker({
  operation,
  state,
  onClose,
  onPicked,
}: {
  operation: AiOperation | null
  state: Pick<AiState, 'models'>
  onClose: () => void
  onPicked: (op: AiOperation, model: string | null) => Promise<void>
}) {
  const [shownOp, setShownOp] = useState<AiOperation | null>(null)
  const [items, setItems] = useState<AiModel[] | null>(null)
  const [query, setQuery] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  useEffect(() => {
    if (!operation) return
    setShownOp(operation)
    setItems(null)
    setQuery('')
    setError(null)
    api.aiModels(operation).then(
      (res) => setItems(res.items),
      (e) => setError((e as Error).message),
    )
  }, [operation])

  const op = operation ?? shownOp
  const current = state.models.find((m) => m.operation === op)
  const found = useMemo(() => {
    const q = query.trim().toLowerCase()
    return (items ?? []).filter((m) => !q || m.id.toLowerCase().includes(q) || m.name.toLowerCase().includes(q))
  }, [items, query])
  const defaultModel = items?.find((m) => m.id === current?.default)

  const pick = async (model: string | null) => {
    if (!op) return
    setBusy(model ?? 'default')
    try {
      await onPicked(op, model)
    } finally {
      setBusy(null)
    }
  }

  return (
    <Sheet
      open={operation !== null}
      onClose={onClose}
      title={current?.title ?? 'Модель'}
      left={
        <button className="nav-button" onClick={onClose}>
          Назад
        </button>
      }
    >
      <div className="section" style={{ marginBottom: 16 }}>
        <div className="group search-group">
          <Search size={17} color="var(--text-2)" />
          <input
            className="field"
            placeholder="Поиск модели"
            aria-label="Поиск модели"
            autoCapitalize="none"
            spellCheck={false}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
      </div>
      {error && <div className="error-banner">{error}</div>}
      {!items && !error && (
        <div className="loader">
          <Loader2 size={22} className="spinner" />
        </div>
      )}
      {items && current && (
        <>
          {!query && (
            <div className="section">
              <div className="group">
                <button className="cell" onClick={() => pick(null)}>
                  <div className="cell-main">
                    <div className="cell-title">По умолчанию</div>
                    <div className="cell-sub mono-sub">
                      {shortModel(current.default)}
                      {defaultModel ? ` · ${price(defaultModel)}` : ''}
                    </div>
                  </div>
                  {busy === 'default' ? (
                    <Loader2 size={18} className="spinner" />
                  ) : (
                    current.model === current.default && <Check size={20} color="var(--accent)" />
                  )}
                </button>
              </div>
            </div>
          )}
          <div className="section">
            <div className="section-header">
              {found.length} {plural(found.length, 'модель', 'модели', 'моделей')} · сначала дешёвые
            </div>
            <div className="group">
              {found.slice(0, 200).map((m) => (
                <button key={m.id} className="cell" onClick={() => pick(m.id)}>
                  <div className="cell-main">
                    <div className="cell-title">{m.name}</div>
                    <div className="cell-sub">
                      <span className="mono-sub">{price(m)}</span>
                      {m.context ? ` · контекст ${context(m.context)}` : ''}
                      {m.caps.includes('image') ? ' · фото' : ''}
                    </div>
                  </div>
                  {busy === m.id ? (
                    <Loader2 size={18} className="spinner" />
                  ) : (
                    current.model === m.id && <Check size={20} color="var(--accent)" />
                  )}
                </button>
              ))}
            </div>
            <div className="section-footer">Цена за миллион токенов: текст на входе / ответ модели.</div>
          </div>
        </>
      )}
    </Sheet>
  )
}

function LimitSheet({
  open,
  state,
  onClose,
  onSave,
}: {
  open: boolean
  state: AiState
  onClose: () => void
  onSave: (values: Parameters<typeof api.saveAi>[0], done?: string) => Promise<boolean>
}) {
  const [value, setValue] = useState('')
  useEffect(() => {
    if (open) setValue(state.monthly_limit_usd === null ? '' : String(state.monthly_limit_usd).replace('.', ','))
  }, [open, state.monthly_limit_usd])
  const amount = Number(value.replace(',', '.').replace(/[^\d.]/g, ''))
  const valid = value.trim() !== '' && amount > 0

  const submit = async () => {
    if (valid && (await onSave({ monthly_limit_usd: amount }, `Лимит — ${usd(amount)} в месяц`))) onClose()
  }

  return (
    <Sheet
      open={open}
      onClose={onClose}
      title="Месячный лимит"
      left={
        <button className="nav-button" onClick={onClose}>
          Отмена
        </button>
      }
      right={
        <button className="nav-button" style={{ fontWeight: 600 }} disabled={!valid} onClick={submit}>
          Сохранить
        </button>
      }
    >
      <form
        onSubmit={(e) => {
          e.preventDefault()
          submit()
        }}
      >
        <div className="section">
          <div className="group">
            <label className="cell">
              <span style={{ flex: 'none' }}>$</span>
              <input
                className="cell-input"
                style={{ textAlign: 'left' }}
                inputMode="decimal"
                placeholder="Например, 10"
                aria-label="Лимит в долларах"
                autoFocus
                value={value}
                onChange={(e) => setValue(e.target.value)}
              />
            </label>
          </div>
          <div className="section-footer">
            Потрачено в этом месяце: {usd(state.spent_month)}. Когда расходы дойдут до лимита, умная лента, темы, посты
            ИИ и чат остановятся до 1-го числа следующего месяца или пока вы не поднимете лимит.
          </div>
        </div>
        {state.monthly_limit_usd !== null && (
          <div className="section">
            <div className="group">
              <button
                type="button"
                className="cell destructive"
                onClick={async () => (await onSave({ clear_monthly_limit: true }, 'Лимит снят')) && onClose()}
              >
                Без лимита
              </button>
            </div>
          </div>
        )}
      </form>
    </Sheet>
  )
}
