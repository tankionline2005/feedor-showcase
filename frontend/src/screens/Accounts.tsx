import { AnimatePresence, motion } from 'framer-motion'
import { ChevronLeft, ChevronRight, Loader2, Plus, Search, Shuffle, Users, X } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, type Account } from '../api'
import { Avatar } from '../components/Avatar'
import { Switch } from '../components/Controls'
import { Page } from '../components/Page'
import { ActionSheet, Sheet } from '../components/Sheet'
import { useToast } from '../components/Toast'
import { plural, time } from '../lib/format'
import { spring } from '../lib/motion'
import { AddAccountSheet } from './AddAccountSheet'
import { proxyStatus } from './Proxies'

const ACCOUNT_STATUS: Record<Account['status'], [string, string]> = {
  new: ['подключается', 'var(--orange)'],
  active: ['в сети', 'var(--green)'],
  flood: ['лимит Telegram', 'var(--orange)'],
  waiting: ['ждёт прокси', 'var(--orange)'],
  error: ['ошибка', 'var(--red)'],
  dead: ['сессия недействительна', 'var(--red)'],
}

export function accountStatus(acc: Account): [string, string] {
  if (!acc.enabled) return ['выключен', 'var(--text-2)']
  return ACCOUNT_STATUS[acc.status]
}

export function Accounts({ onBack }: { onBack: () => void }) {
  const [accounts, setAccounts] = useState<Account[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [adding, setAdding] = useState(false)
  const [selected, setSelected] = useState<Account | null>(null)

  const reload = useCallback(async () => {
    try {
      const list = await api.accounts()
      setAccounts(list.items)
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])

  useEffect(() => {
    reload()
  }, [reload])

  const connecting = accounts?.some((a) => a.status === 'new' && a.enabled) ?? false
  useEffect(() => {
    const timer = window.setInterval(reload, connecting ? 4_000 : 30_000)
    return () => window.clearInterval(timer)
  }, [reload, connecting])

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase().replace(/[\s()-]/g, '')
    if (!q) return accounts ?? []
    return (accounts ?? []).filter((a) =>
      [a.name, a.username, a.phone].some((s) => s?.toLowerCase().replace(/[\s()-]/g, '').includes(q)),
    )
  }, [accounts, query])

  return (
    <Page
      title="Аккаунты"
      onRefresh={reload}
      left={
        <motion.button className="nav-button" onClick={onBack} whileTap={{ opacity: 0.4 }} style={{ paddingLeft: 0 }}>
          <ChevronLeft size={30} strokeWidth={2.2} style={{ marginRight: -4 }} />
          Настройки
        </motion.button>
      }
      accessory={
        <label className="search">
          <Search size={17} />
          <input placeholder="Имя, @username или номер" value={query} onChange={(e) => setQuery(e.target.value)} />
          {query && (
            <button aria-label="Очистить" onClick={() => setQuery('')} style={{ display: 'flex' }}>
              <X size={17} />
            </button>
          )}
        </label>
      }
    >
      {error && <div className="error-banner">{error}</div>}

      <div className="section" style={{ marginBottom: 24 }}>
        <div className="group">
          <button className="cell accent" onClick={() => setAdding(true)}>
            <div className="cell-icon" style={{ background: 'var(--accent)' }}>
              <Plus size={20} />
            </div>
            Добавить аккаунт
          </button>
        </div>
      </div>

      {accounts && accounts.length === 0 && (
        <motion.div className="empty" style={{ paddingTop: 24 }} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={spring}>
          <div className="empty-icon">
            <Users size={32} strokeWidth={1.6} />
          </div>
          <div className="empty-title">Нет аккаунтов</div>
          <div>Аккаунты читают каналы и пересылают посты в группу-агрегатор</div>
        </motion.div>
      )}

      {accounts && accounts.length > 0 && (
        <>
          <div className="section-header" style={{ marginLeft: 16 }}>
            {query
              ? `Найдено: ${visible.length}`
              : `${accounts.length} ${plural(accounts.length, 'аккаунт', 'аккаунта', 'аккаунтов')}`}
          </div>
          <div className="section">
            {visible.length > 0 ? (
              <div className="group">
                <AnimatePresence initial={false}>
                  {visible.map((acc) => {
                    const [text, color] = accountStatus(acc)
                    return (
                      <motion.button
                        key={acc.id}
                        className="cell"
                        style={{ ['--inset' as string]: '68px', minHeight: 60 }}
                        onClick={() => setSelected(acc)}
                        layout="position"
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        transition={spring}
                      >
                        <Avatar title={acc.name || acc.phone || '?'} seed={acc.id} size={40} />
                        <div className="cell-main">
                          <div className="cell-title">{acc.name || acc.phone || `Аккаунт ${acc.id}`}</div>
                          <div className="cell-sub">
                            <span style={{ color }}>{text}</span>
                            {' · '}
                            {acc.channels} / {acc.max_channels} {plural(acc.max_channels, 'канал', 'канала', 'каналов')}
                          </div>
                        </div>
                        <ChevronRight size={18} color="var(--text-3)" />
                      </motion.button>
                    )
                  })}
                </AnimatePresence>
              </div>
            ) : (
              <div className="empty" style={{ padding: '24px 16px' }}>
                <div className="empty-title">Ничего не найдено</div>
              </div>
            )}
          </div>
        </>
      )}

      <AddAccountSheet open={adding} onClose={() => setAdding(false)} onAdded={reload} />
      <AccountSheet account={selected} onClose={() => setSelected(null)} onChanged={reload} />
    </Page>
  )
}

function AccountSheet({
  account,
  onClose,
  onChanged,
}: {
  account: Account | null
  onClose: () => void
  onChanged: () => Promise<void>
}) {
  const [enabled, setEnabled] = useState(true)
  const [confirm, setConfirm] = useState(false)
  const [moving, setMoving] = useState(false)
  const toast = useToast()

  useEffect(() => {
    if (account) setEnabled(account.enabled)
  }, [account])

  // keep the last account rendered while the sheet animates out
  const [shown, setShown] = useState<Account | null>(null)
  useEffect(() => {
    if (account) setShown(account)
  }, [account])
  const acc = account ?? shown

  const patch = async (body: Parameters<typeof api.patchAccount>[1], message: string) => {
    if (!acc) return
    try {
      await api.patchAccount(acc.id, body)
      toast(message)
      await onChanged()
    } catch (e) {
      toast((e as Error).message)
    }
  }

  const changeProxy = async () => {
    if (!acc) return
    setMoving(true)
    try {
      const updated = await api.changeAccountProxy(acc.id)
      setShown(updated)
      toast(`Аккаунт переключён на ${updated.proxy?.label}, он переподключится`)
      await onChanged()
    } catch (e) {
      toast((e as Error).message)
    } finally {
      setMoving(false)
    }
  }

  const remove = async () => {
    if (!acc) return
    try {
      await api.deleteAccount(acc.id)
      toast('Аккаунт удалён')
      onClose()
      await onChanged()
    } catch (e) {
      toast((e as Error).message)
    }
  }

  const [statusText, statusColor] = acc ? accountStatus(acc) : ['', '']

  return (
    <>
      <Sheet
        open={account !== null}
        onClose={onClose}
        title={acc?.name || acc?.phone || 'Аккаунт'}
        right={
          <button className="nav-button" style={{ fontWeight: 600 }} onClick={onClose}>
            Готово
          </button>
        }
      >
        {acc && (
          <>
            <div className="hero" style={{ paddingTop: 0 }}>
              <Avatar title={acc.name || acc.phone || '?'} seed={acc.id} size={72} />
              <div className="hero-sub" style={{ marginTop: 8 }}>
                {[acc.phone, acc.username && `@${acc.username}`].filter(Boolean).join(' · ')}
              </div>
            </div>

            <div className="section">
              <div className="group">
                <div className="cell">
                  <div className="cell-main">Статус</div>
                  <span className="cell-value" style={{ color: statusColor }}>
                    {statusText}
                  </span>
                </div>
                <div className="cell">
                  <div className="cell-main">Каналы</div>
                  <span className="cell-value">
                    {acc.channels} из {acc.max_channels}
                  </span>
                </div>
                {acc.flood_until && acc.status === 'flood' && (
                  <div className="cell">
                    <div className="cell-main">Пауза до</div>
                    <span className="cell-value">{time(acc.flood_until)}</span>
                  </div>
                )}
                <div className="cell">
                  <div className="cell-main">Включён</div>
                  <Switch
                    label="Включён"
                    on={enabled}
                    onChange={(v) => {
                      setEnabled(v)
                      patch({ enabled: v }, v ? 'Аккаунт включён' : 'Аккаунт выключен, его каналы перейдут другим')
                    }}
                  />
                </div>
              </div>
              {acc.status_detail && (
                <div className="section-footer" style={{ color: acc.status === 'active' ? undefined : 'var(--red)' }}>
                  {acc.status_detail}
                </div>
              )}
            </div>

            <div className="section-header" style={{ marginLeft: 16 }}>Прокси</div>
            <div className="section">
              <div className="group">
                <div className="cell">
                  <div className="cell-main">
                    <div className="cell-title" style={{ fontVariantNumeric: 'tabular-nums' }}>
                      {acc.proxy ? acc.proxy.label : 'Ждёт прокси'}
                    </div>
                    {acc.proxy && (
                      <div className="cell-sub">
                        <span style={{ color: proxyStatus({ enabled: true, status: acc.proxy.status })[1] }}>
                          {proxyStatus({ enabled: true, status: acc.proxy.status })[0]}
                        </span>
                        {' · '}
                        {acc.proxy.kind === 'mtproto' ? 'MTProto' : 'SOCKS5'}
                      </div>
                    )}
                  </div>
                </div>
                {acc.proxy && (
                  <button className="cell accent" disabled={moving} onClick={changeProxy}>
                    {moving ? <Loader2 size={18} className="spinner" /> : <Shuffle size={18} />}
                    Сменить прокси
                  </button>
                )}
              </div>
              <div className="section-footer">
                {acc.proxy
                  ? 'Прокси выдаётся автоматически из пула. Если он перестанет работать, аккаунт сам переедет на другой.'
                  : 'Аккаунт ждёт свободный рабочий прокси из пула — напрямую он не подключится.'}
              </div>
            </div>

            <div className="section">
              <div className="group">
                <button className="cell destructive" onClick={() => setConfirm(true)}>
                  Удалить аккаунт
                </button>
              </div>
            </div>
          </>
        )}
      </Sheet>
      <ActionSheet
        open={confirm}
        onClose={() => setConfirm(false)}
        message="Каналы этого аккаунта перейдут другим аккаунтам. Сама сессия в Telegram останется активной."
        actions={[{ label: 'Удалить аккаунт', destructive: true, onClick: remove }]}
      />
    </>
  )
}
