import { AnimatePresence, motion } from 'framer-motion'
import { ChevronLeft, ChevronRight, Loader2, Network, Plus, Radio, RotateCw, Search, Trash2, X } from 'lucide-react'
import { Fragment, type ReactNode, useCallback, useEffect, useMemo, useState } from 'react'
import { api, type Proxy } from '../api'
import { Stepper, Switch } from '../components/Controls'
import { Page } from '../components/Page'
import { Chips } from '../components/Segmented'
import { ActionSheet, Sheet } from '../components/Sheet'
import { SwipeRow, type SwipeAction } from '../components/SwipeRow'
import { useToast } from '../components/Toast'
import { plural, relative } from '../lib/format'
import { spring } from '../lib/motion'

type Filter = 'all' | 'ok' | 'dead' | 'off'

const KIND_LABEL: Record<Proxy['kind'], string> = { socks5: 'SOCKS5', mtproto: 'MTProto' }
const LIMIT_STEPS = Array.from({ length: 15 }, (_, i) => i + 1)

/** Channels the server takes new proxies from (backend/app/worker/proxy_feed.py, FEED_CHANNELS). */
const FEED_CHANNELS = ['ProxyMTProto', 'TProxyRU', 'ProxyFree_Ru']

export function proxyStatus(p: Pick<Proxy, 'enabled' | 'status'> & { latency_ms?: number | null }): [string, string] {
  if (!p.enabled) return ['выключен', 'var(--text-2)']
  if (p.status === 'ok') return [p.latency_ms != null ? `работает · ${p.latency_ms} мс` : 'работает', 'var(--green)']
  if (p.status === 'dead') return ['не работает', 'var(--red)']
  return ['проверяется', 'var(--orange)']
}

export function Proxies({ onBack }: { onBack: () => void }) {
  const [proxies, setProxies] = useState<Proxy[] | null>(null)
  const [limit, setLimit] = useState<number | null>(null)
  const [feed, setFeed] = useState<boolean | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState<Filter>('all')
  const [adding, setAdding] = useState(false)
  const [selected, setSelected] = useState<Proxy | null>(null)
  const [confirm, setConfirm] = useState<Proxy | null>(null)
  const [checking, setChecking] = useState<Set<number>>(new Set())
  const toast = useToast()

  const reload = useCallback(async () => {
    try {
      const [p, s] = await Promise.all([api.proxies(), api.settings()])
      setProxies(p.items)
      setLimit(s.accounts_per_proxy)
      setFeed(s.proxy_feed_enabled)
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])

  useEffect(() => {
    reload()
    const timer = window.setInterval(reload, 30_000)
    return () => window.clearInterval(timer)
  }, [reload])

  const counts = useMemo(() => {
    const c = { ok: 0, dead: 0, off: 0 }
    proxies?.forEach((p) => {
      if (!p.enabled) c.off++
      else if (p.status === 'ok') c.ok++
      else if (p.status === 'dead') c.dead++
    })
    return c
  }, [proxies])

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase()
    return (proxies ?? []).filter(
      (p) =>
        (filter === 'all' ||
          (filter === 'off' ? !p.enabled : p.enabled && p.status === filter)) &&
        (!q || p.label.toLowerCase().includes(q) || (p.username ?? '').toLowerCase().includes(q)),
    )
  }, [proxies, query, filter])

  const filters = [
    { value: 'all' as const, label: `Все ${proxies?.length ?? ''}`.trim() },
    { value: 'ok' as const, label: `Работают ${counts.ok}` },
    ...(counts.dead ? [{ value: 'dead' as const, label: `Не работают ${counts.dead}` }] : []),
    ...(counts.off ? [{ value: 'off' as const, label: `Выключены ${counts.off}` }] : []),
  ]

  const saveFeed = async (value: boolean) => {
    setFeed(value)
    try {
      await api.saveSettings({ proxy_feed_enabled: value })
      toast(value ? 'Новые прокси из каналов будут добавляться сами' : 'Автодобавление выключено')
    } catch (e) {
      toast((e as Error).message)
      reload()
    }
  }

  const saveLimit = async (value: number) => {
    setLimit(value)
    try {
      await api.saveSettings({ accounts_per_proxy: value })
    } catch (e) {
      toast((e as Error).message)
      reload()
    }
  }

  const checkNow = async (p: Proxy) => {
    setChecking((s) => new Set(s).add(p.id))
    try {
      const updated = await api.checkProxy(p.id)
      setProxies((list) => list?.map((x) => (x.id === p.id ? updated : x)) ?? null)
      setSelected((cur) => (cur?.id === p.id ? updated : cur))
      toast(updated.status === 'ok' ? `${p.label} работает` : `${p.label} не отвечает`)
    } catch (e) {
      toast((e as Error).message)
    } finally {
      setChecking((s) => {
        const next = new Set(s)
        next.delete(p.id)
        return next
      })
    }
  }

  const remove = async (p: Proxy) => {
    try {
      await api.deleteProxy(p.id)
      toast(p.accounts ? 'Прокси удалён, аккаунты переедут на другие' : 'Прокси удалён')
      setSelected(null)
      await reload()
    } catch (e) {
      toast((e as Error).message)
    }
  }

  const rowActions = (p: Proxy): SwipeAction[] => [
    { label: 'Проверить', icon: RotateCw, color: 'var(--accent)', onClick: () => checkNow(p) },
    { label: 'Удалить', icon: Trash2, color: 'var(--red)', onClick: () => setConfirm(p) },
  ]

  return (
    <Page
      title="Прокси"
      onRefresh={reload}
      left={
        <motion.button className="nav-button" onClick={onBack} whileTap={{ opacity: 0.4 }} style={{ paddingLeft: 0 }}>
          <ChevronLeft size={30} strokeWidth={2.2} style={{ marginRight: -4 }} />
          Настройки
        </motion.button>
      }
      accessory={
        <>
          <label className="search">
            <Search size={17} />
            <input placeholder="Адрес или логин" value={query} onChange={(e) => setQuery(e.target.value)} />
            {query && (
              <button aria-label="Очистить" onClick={() => setQuery('')} style={{ display: 'flex' }}>
                <X size={17} />
              </button>
            )}
          </label>
          {filters.length > 2 && <Chips id="proxy-filter" options={filters} value={filter} onChange={setFilter} />}
        </>
      }
    >
      {error && <div className="error-banner">{error}</div>}

      <div className="section" style={{ marginBottom: 24 }}>
        <div className="group">
          <button className="cell accent" onClick={() => setAdding(true)}>
            <div className="cell-icon" style={{ background: 'var(--accent)' }}>
              <Plus size={20} />
            </div>
            Добавить прокси
          </button>
          {limit !== null && (
            <div className="cell">
              <div className="cell-main" style={{ whiteSpace: 'normal' }}>
                Максимум аккаунтов на один прокси: <b style={{ fontVariantNumeric: 'tabular-nums' }}>{limit}</b>
              </div>
              <Stepper value={limit} steps={LIMIT_STEPS} onChange={saveLimit} />
            </div>
          )}
        </div>
        <div className="section-footer">
          Аккаунты подключаются только через прокси из списка: каждый получает свой автоматически и держится за него.
          Если прокси перестаёт работать, его аккаунты переезжают на другие, а если свободных рабочих нет — ждут.
          Напрямую с IP сервера аккаунты не подключаются никогда.
        </div>
      </div>

      {feed !== null && (
        <div className="section">
          <div className="group">
            <div className="cell" style={{ ['--inset' as string]: '57px' }}>
              <div className="cell-icon" style={{ background: feed ? 'var(--green)' : 'var(--text-2)' }}>
                <Radio size={18} />
              </div>
              <div className="cell-main">Автодобавление из каналов</div>
              <Switch label="Автодобавление из каналов" on={feed} onChange={saveFeed} />
            </div>
          </div>
          <div className="section-footer">
            Раз в 5 минут сервер смотрит открытые страницы каналов{' '}
            {FEED_CHANNELS.map((ch, i) => (
              <Fragment key={ch}>
                {i > 0 && (i === FEED_CHANNELS.length - 1 ? ' и ' : ', ')}
                <a href={`https://t.me/${ch}`} target="_blank" rel="noopener noreferrer">
                  @{ch}
                </a>
              </Fragment>
            ))}{' '}
            и добавляет прокси из новых постов — без участия аккаунтов. Прокси с fake-TLS пропускаются: их не
            поддерживает библиотека Telegram. Нерабочие из добавленных так прокси удаляются через сутки, если на них
            нет аккаунтов.
          </div>
        </div>
      )}

      {proxies && proxies.length === 0 && (
        <motion.div className="empty" style={{ paddingTop: 16 }} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={spring}>
          <div className="empty-icon">
            <Network size={32} strokeWidth={1.6} />
          </div>
          <div className="empty-title">Нет прокси</div>
          <div>Вставьте список SOCKS5 или ссылки MTProto — по одному в строке</div>
        </motion.div>
      )}

      {proxies && proxies.length > 0 && (
        <>
          <div className="section-header" style={{ marginLeft: 16 }}>
            {query || filter !== 'all'
              ? `Найдено: ${visible.length}`
              : `${proxies.length} ${plural(proxies.length, 'прокси', 'прокси', 'прокси')}`}
          </div>
          <div className="section">
            {visible.length > 0 ? (
              <div className="group">
                <AnimatePresence initial={false}>
                  {visible.map((p) => {
                    const [text, color] = proxyStatus(p)
                    return (
                      <motion.div
                        key={p.id}
                        className="list-item"
                        style={{ ['--inset' as string]: '90px' }}
                        layout="position"
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        transition={spring}
                      >
                        <SwipeRow actions={rowActions(p)}>
                          <button className="cell" style={{ minHeight: 58 }} onClick={() => setSelected(p)}>
                            <span className="kind-badge">{KIND_LABEL[p.kind]}</span>
                            <div className="cell-main">
                              <div className="cell-title" style={{ fontVariantNumeric: 'tabular-nums' }}>
                                {p.label}
                              </div>
                              <div className="cell-sub">
                                {checking.has(p.id) ? (
                                  <span style={{ color: 'var(--orange)' }}>проверяется…</span>
                                ) : (
                                  <span style={{ color }}>{text}</span>
                                )}
                                {p.source && ` · ${p.source}`}
                              </div>
                            </div>
                            <span className="cell-value" title="Аккаунтов на прокси">
                              {p.accounts}/{limit ?? '–'}
                            </span>
                            <ChevronRight size={18} color="var(--text-3)" />
                          </button>
                        </SwipeRow>
                      </motion.div>
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

      <AddProxiesSheet open={adding} onClose={() => setAdding(false)} onAdded={reload} />
      <ProxySheet
        proxy={selected}
        limit={limit}
        checking={selected ? checking.has(selected.id) : false}
        onClose={() => setSelected(null)}
        onCheck={checkNow}
        onDelete={(p) => setConfirm(p)}
        onToggle={async (p, enabled) => {
          try {
            const updated = await api.patchProxy(p.id, enabled)
            setSelected(updated)
            toast(enabled ? 'Прокси включён' : 'Прокси выключен, его аккаунты переедут на другие')
            await reload()
          } catch (e) {
            toast((e as Error).message)
          }
        }}
      />
      <ActionSheet
        open={confirm !== null}
        onClose={() => setConfirm(null)}
        title={confirm?.label}
        message={confirm?.accounts ? 'Аккаунты на этом прокси переедут на другие рабочие прокси' : undefined}
        actions={confirm ? [{ label: 'Удалить прокси', destructive: true, onClick: () => remove(confirm) }] : []}
      />
    </Page>
  )
}

function ProxySheet({
  proxy,
  limit,
  checking,
  onClose,
  onCheck,
  onDelete,
  onToggle,
}: {
  proxy: Proxy | null
  limit: number | null
  checking: boolean
  onClose: () => void
  onCheck: (p: Proxy) => void
  onDelete: (p: Proxy) => void
  onToggle: (p: Proxy, enabled: boolean) => void
}) {
  // keep the last proxy rendered while the sheet animates out
  const [shown, setShown] = useState<Proxy | null>(null)
  useEffect(() => {
    if (proxy) setShown(proxy)
  }, [proxy])
  const p = proxy ?? shown
  const [statusText, statusColor] = p ? proxyStatus(p) : ['', '']

  return (
    <Sheet
      open={proxy !== null}
      onClose={onClose}
      title={p?.label}
      right={
        <button className="nav-button" style={{ fontWeight: 600 }} onClick={onClose}>
          Готово
        </button>
      }
    >
      {p && (
        <>
          <div className="section">
            <div className="group">
              <Row label="Тип" value={KIND_LABEL[p.kind]} />
              <Row label="Адрес" value={p.label} />
              {p.username && <Row label="Логин" value={p.username} />}
              {p.source && <Row label="Добавлен" value={`автоматически из ${p.source}`} />}
              <Row label="Статус" value={<span style={{ color: statusColor }}>{statusText}</span>} />
              <Row label="Проверен" value={p.checked_at ? relative(p.checked_at) : 'ещё нет'} />
              <Row label="Аккаунты" value={`${p.accounts} из ${limit ?? '–'}`} />
              <div className="cell">
                <div className="cell-main">Включён</div>
                <Switch label="Включён" on={p.enabled} onChange={(v) => onToggle(p, v)} />
              </div>
            </div>
            {p.last_error && p.status !== 'ok' && (
              <div className="section-footer" style={{ color: 'var(--red)' }}>
                {p.last_error}
              </div>
            )}
          </div>

          <div className="section">
            <div className="group">
              <button className="cell accent" disabled={checking} onClick={() => onCheck(p)}>
                {checking ? <Loader2 size={18} className="spinner" /> : <RotateCw size={18} />}
                {checking ? 'Проверяется…' : 'Проверить сейчас'}
              </button>
            </div>
          </div>

          <div className="section">
            <div className="group">
              <button className="cell destructive" onClick={() => onDelete(p)}>
                Удалить прокси
              </button>
            </div>
          </div>
        </>
      )}
    </Sheet>
  )
}

function Row({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="cell">
      <div className="cell-main">{label}</div>
      <span className="cell-value" style={{ fontVariantNumeric: 'tabular-nums' }}>
        {value}
      </span>
    </div>
  )
}

function AddProxiesSheet({ open, onClose, onAdded }: { open: boolean; onClose: () => void; onAdded: () => void }) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const toast = useToast()

  useEffect(() => {
    if (open) setError(null)
  }, [open])

  const submit = async () => {
    setBusy(true)
    setError(null)
    try {
      const res = await api.addProxies(text)
      const n = res.added.length
      const problems = res.invalid.map((i) => `${i.line} — ${i.error}`)
      if (n === 0) {
        setError(
          [res.duplicates.length ? `Уже в списке: ${res.duplicates.length}` : '', ...problems].filter(Boolean).join('\n') ||
            'Ничего не добавлено',
        )
        return
      }
      toast(`Добавлено ${n}, работают ${res.working}${res.duplicates.length ? `, повторов ${res.duplicates.length}` : ''}`)
      if (problems.length) {
        // keep the sheet open with the lines that need fixing
        setText(res.invalid.map((i) => i.line).join('\n'))
        setError(`Не добавлены:\n${problems.join('\n')}`)
        onAdded()
        return
      }
      setText('')
      onAdded()
      onClose()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Sheet
      open={open}
      onClose={onClose}
      title="Новые прокси"
      left={
        <button className="nav-button" onClick={onClose}>
          Отмена
        </button>
      }
      right={
        <button className="nav-button" style={{ fontWeight: 600 }} disabled={busy || !text.trim()} onClick={submit}>
          {busy ? <Loader2 size={20} className="spinner" /> : 'Добавить'}
        </button>
      }
    >
      <div className="section">
        <div className="group">
          <textarea
            className="field mono"
            autoFocus
            spellCheck={false}
            autoCapitalize="none"
            autoCorrect="off"
            placeholder={'1.2.3.4:1080:user:pass\nsocks5://user:pass@1.2.3.4:1080\ntg://proxy?server=…&port=…&secret=…'}
            value={text}
            onChange={(e) => setText(e.target.value)}
            style={{ minHeight: 170 }}
          />
        </div>
        <div className="section-footer" style={error ? { color: 'var(--red)', whiteSpace: 'pre-line' } : undefined}>
          {busy
            ? 'Проверяем прокси — это займёт до 15 секунд…'
            : error ??
              'По одному в строке. SOCKS5: host:port:user:pass, user:pass@host:port, socks5://… или host:port. MTProto: ссылки tg://proxy или t.me/proxy (кроме fake-TLS с секретом ee). Каждый прокси сразу проверяется.'}
        </div>
      </div>
    </Sheet>
  )
}
