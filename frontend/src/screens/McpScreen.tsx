import { AnimatePresence, motion } from 'framer-motion'
import { ChevronDown, ChevronLeft, Copy, Link2, Loader2, Plug, ShieldCheck, Unplug } from 'lucide-react'
import { type ReactNode, useCallback, useEffect, useState } from 'react'
import { api, type McpConnection, type McpRequest, type McpTool } from '../api'
import { Page } from '../components/Page'
import { ActionSheet } from '../components/Sheet'
import { useToast } from '../components/Toast'
import { useIsAdmin, useMe } from '../hooks/useMe'
import { copyText } from '../lib/clipboard'
import { longDate, plural, relative } from '../lib/format'
import { openIntro, useFirstVisit } from '../lib/intros'
import { spring } from '../lib/motion'

export function McpScreen({ onBack }: { onBack: () => void }) {
  useFirstVisit('mcp')
  const toast = useToast()
  const admin = useIsAdmin()
  const me = useMe()
  const url = `${window.location.origin}/mcp`
  const [connections, setConnections] = useState<McpConnection[] | null>(null)
  const [tools, setTools] = useState<McpTool[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [unplugging, setUnplugging] = useState<McpConnection | null>(null)

  const load = useCallback(async () => {
    try {
      const [t, c] = await Promise.all([api.mcpTools(), admin ? Promise.resolve({ items: [] }) : api.mcpConnections()])
      setTools(t.items)
      setConnections(c.items)
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [admin])

  useEffect(() => {
    load()
  }, [load])

  const copy = async (text: string, what: string) => {
    toast((await copyText(text)) ? `${what} скопирован` : 'Не удалось скопировать — выделите текст вручную')
  }

  const unplug = async (c: McpConnection) => {
    try {
      await api.mcpDisconnect(c.id)
      setConnections((list) => list?.filter((x) => x.id !== c.id) ?? null)
      toast(`«${c.client_name}» отключён`)
    } catch (e) {
      toast((e as Error).message)
    }
  }

  return (
    <Page
      onHelp={() => openIntro('mcp')}
      title="MCP-коннектор"
      onRefresh={load}
      left={
        <motion.button className="nav-button" onClick={onBack} whileTap={{ opacity: 0.4 }} style={{ paddingLeft: 0 }}>
          <ChevronLeft size={30} strokeWidth={2.2} style={{ marginRight: -4 }} />
          Настройки
        </motion.button>
      }
    >
      {error && <div className="error-banner">{error}</div>}
      {!admin && !me.plan?.api && (
        <div className="error-banner">
          {me.plan
            ? `Тариф ${me.plan.title} не включает MCP-коннектор: он есть во всех тарифах, начиная со Starter.`
            : 'Без тарифа коннектор не работает: подключить его не получится. Выберите тариф в настройках — он заработает сразу.'}
        </div>
      )}

      <div className="section-header" style={{ marginLeft: 16 }}>
        Адрес коннектора
      </div>
      <div className="section">
        <div className="group">
          <button className="cell" style={{ ['--inset' as string]: '57px' }} onClick={() => copy(url, 'Адрес')}>
            <div className="cell-icon" style={{ background: 'var(--indigo)' }}>
              <Link2 size={17} />
            </div>
            <div className="cell-main api-key">{url}</div>
            <Copy size={17} color="var(--text-2)" />
          </button>
        </div>
        <div className="section-footer">
          {admin
            ? 'Подключайте по ключу API из раздела «API» — в заголовке X-API-Key (Claude Code, Cursor и другие программы). ИИ получит всё, что вы делаете на сайте.'
            : me.role === 'member'
              ? 'Вставьте этот адрес в Claude, ChatGPT или другое ИИ-приложение как свой коннектор: оно попросит войти и разрешить доступ. ИИ сможет делать то же, что вы на сайте, — с правами, которые дал вам владелец. Программам, которые подключают по заголовку, передайте ключ из раздела «API» в X-API-Key.'
              : 'Вставьте этот адрес в Claude, ChatGPT или другое ИИ-приложение как свой коннектор: оно попросит войти и разрешить доступ. ИИ сможет делать то же, что вы на сайте: читать посты и темы, писать и публиковать посты, вести рубрики и настройки. Программам, которые подключают по заголовку, передайте ключ из раздела «API» в X-API-Key.'}
        </div>
      </div>

      {!admin && (
        <>
          <div className="section-header" style={{ marginLeft: 16 }}>
            Подключённые приложения
          </div>
          <div className="section">
            <div className="group">
              {connections === null ? (
                <div className="cell" style={{ justifyContent: 'center' }}>
                  <Loader2 size={18} className="spinner" />
                </div>
              ) : connections.length === 0 ? (
                <div className="cell">
                  <div className="cell-main" style={{ color: 'var(--text-2)' }}>
                    Пока ничего не подключено
                  </div>
                </div>
              ) : (
                <AnimatePresence initial={false}>
                  {connections.map((c) => (
                    <motion.div
                      key={c.id}
                      className="cell"
                      style={{ ['--inset' as string]: '57px' }}
                      initial={false}
                      exit={{ opacity: 0, height: 0 }}
                      transition={spring}
                    >
                      <div className="cell-icon" style={{ background: 'var(--green)' }}>
                        <Plug size={17} />
                      </div>
                      <div className="cell-main">
                        <div className="cell-title">{c.client_name}</div>
                        <div className="cell-sub">
                          {[c.redirect_host, `с ${longDate(c.created_at)}`, c.last_used_at && relative(c.last_used_at)]
                            .filter(Boolean)
                            .join(' • ')}
                        </div>
                      </div>
                      <button
                        className="nav-button"
                        style={{ color: 'var(--red)' }}
                        onClick={() => setUnplugging(c)}
                        aria-label={`Отключить ${c.client_name}`}
                        title="Отключить"
                      >
                        <Unplug size={19} />
                      </button>
                    </motion.div>
                  ))}
                </AnimatePresence>
              )}
            </div>
            <div className="section-footer">
              Приложения, которым вы разрешили доступ. Отключите любое — его доступ пропадёт сразу.
            </div>
          </div>
        </>
      )}

      <div className="section-header" style={{ marginLeft: 16 }}>
        Инструменты
      </div>
      <div className="docs">
        {tools === null ? (
          <section className="docs-loading">
            <Loader2 size={18} className="spinner" />
          </section>
        ) : (
          groupsOf(tools).map(([group, items]) => (
            <Fold key={group} title={group} count={items.length}>
              {items.map((t) => (
                <div key={t.name} className="docs-manage">
                  <div className="docs-endpoint">
                    <span className={`method-badge ${t.read_only ? '' : 'post'}`}>{t.read_only ? 'ЧТЕНИЕ' : 'ДЕЙСТВИЕ'}</span>
                    <code>{t.name}</code>
                  </div>
                  <p>{t.description}</p>
                </div>
              ))}
            </Fold>
          ))
        )}
      </div>
      <div className="section-footer" style={{ margin: '-26px 16px 34px' }}>
        {tools
          ? `ИИ получит ${tools.length} ${plural(tools.length, 'инструмент', 'инструмента', 'инструментов')} — ровно то, что доступно вам на сайте.`
          : ''}
      </div>

      <ActionSheet
        open={unplugging !== null}
        onClose={() => setUnplugging(null)}
        title={`Отключить «${unplugging?.client_name ?? ''}»?`}
        message="Приложение сразу потеряет доступ. Чтобы вернуть его, коннектор нужно будет подключить заново."
        actions={[{ label: 'Отключить', destructive: true, onClick: () => unplugging && unplug(unplugging) }]}
      />
    </Page>
  )
}

function groupsOf(tools: McpTool[]): [string, McpTool[]][] {
  const out = new Map<string, McpTool[]>()
  for (const t of tools) out.set(t.group, [...(out.get(t.group) ?? []), t])
  return [...out]
}

/** A folded section, as the API's reference has them. */
function Fold({ title, count, children }: { title: string; count?: number; children: ReactNode }) {
  const [open, setOpen] = useState(false)
  return (
    <section className="docs-group">
      <button className="docs-group-head" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <h3>{title}</h3>
        {count !== undefined && <span className="docs-group-count">{count}</span>}
        <motion.span animate={{ rotate: open ? 180 : 0 }} transition={spring} style={{ display: 'flex' }}>
          <ChevronDown size={18} color="var(--text-2)" />
        </motion.span>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={spring}
            style={{ overflow: 'hidden' }}
          >
            {children}
          </motion.div>
        )}
      </AnimatePresence>
    </section>
  )
}

/** #/connect/<request>: an AI app asks to be let in — which app, as whom; «Разрешить» sends the person back to it. */
export function ConnectScreen({ request, onDone }: { request: string; onDone: () => void }) {
  const [info, setInfo] = useState<McpRequest | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<'allow' | 'deny' | null>(null)

  useEffect(() => {
    api.mcpRequest(request).then(setInfo, (e) => setError((e as Error).message))
  }, [request])

  const answer = async (allow: boolean) => {
    setBusy(allow ? 'allow' : 'deny')
    try {
      const { redirect } = await api.mcpApprove(request, allow)
      window.location.href = redirect
    } catch (e) {
      setError((e as Error).message)
      setBusy(null)
    }
  }

  return (
    <Page title="Подключение">
      <div className="connect">
        <motion.div
          className="connect-icon"
          initial={{ scale: 0.6, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          transition={spring}
        >
          <Plug size={34} />
        </motion.div>
        {error ? (
          <>
            <h2 className="connect-title">Не получилось</h2>
            <p className="connect-text">{error}</p>
            <button className="button secondary" onClick={onDone}>
              К MCP-коннектору
            </button>
          </>
        ) : !info ? (
          <Loader2 size={22} className="spinner" />
        ) : (
          <>
            <h2 className="connect-title">«{info.client_name}» просит доступ</h2>
            <p className="connect-text">
              К проекту «{info.project_title}» — {info.role === 'member' ? `от имени сотрудника ${info.login}` : 'от вашего имени'}.
            </p>
            <div className="connect-points">
              <div>
                <ShieldCheck size={18} />
                <span>
                  {info.role === 'member'
                    ? 'ИИ сможет делать то же, что вы на сайте, — только то, что разрешил вам владелец.'
                    : 'ИИ сможет делать то же, что вы на сайте: читать посты и темы, писать и публиковать посты, менять настройки.'}
                </span>
              </div>
              <div>
                <Unplug size={18} />
                <span>Отключить его можно в любой момент: Настройки → MCP-коннектор.</span>
              </div>
            </div>
            {!info.has_api && <div className="error-banner">Нужен тариф с API и MCP — начиная со Starter. Выберите его в настройках.</div>}
            <p className="connect-host">После ответа вы вернётесь на {info.redirect_host}</p>
            <div className="connect-actions">
              <motion.button
                className="button"
                whileTap={{ scale: 0.97 }}
                disabled={busy !== null || !info.has_api}
                onClick={() => answer(true)}
              >
                {busy === 'allow' ? <Loader2 size={18} className="spinner" /> : 'Разрешить'}
              </motion.button>
              <button className="button secondary" disabled={busy !== null} onClick={() => answer(false)}>
                {busy === 'deny' ? <Loader2 size={18} className="spinner" /> : 'Отклонить'}
              </button>
            </div>
          </>
        )}
      </div>
    </Page>
  )
}
