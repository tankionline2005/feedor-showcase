import { AnimatePresence, motion } from 'framer-motion'
import { ChevronLeft, ChevronRight, Loader2, Search, UserRound, X } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, type Channel, type WebProject, type WebUser } from '../api'
import { Avatar } from '../components/Avatar'
import { Switch } from '../components/Controls'
import { Page } from '../components/Page'
import { ActionSheet, Sheet } from '../components/Sheet'
import { useToast } from '../components/Toast'
import { dayLabel, longDate, plural, relative } from '../lib/format'
import { cents } from '../lib/money'
import { spring } from '../lib/motion'
import { channelSubtitle } from './Channels'

const PLAN_CHOICES = [
  { id: 'trial', title: 'Free Trial' },
  { id: 'starter', title: 'Starter' },
  { id: 'pro', title: 'Pro' },
  { id: 'studio', title: 'Studio' },
  { id: 'agency', title: 'Agency' },
  { id: 'dev', title: 'dev:service:plan' },
]
/** given only forever */
const FOREVER = new Set(['dev'])

/** "2026-10-26" for a date input, in local time. */
function dayInput(iso: string): string {
  const d = new Date(iso)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

const displayName = (u: WebUser) => u.name || (u.username ? `@${u.username}` : `Пользователь ${u.id}`)

/** "Pro · 12 каналов" for one project, "2 проекта · 30 каналов" for several. */
function projectsLine(u: WebUser): string {
  const channels = u.projects.reduce((sum, p) => sum + p.channels, 0)
  const head =
    u.projects.length === 1
      ? (u.projects[0].plan?.title ?? 'без тарифа')
      : `${u.projects.length} ${plural(u.projects.length, 'проект', 'проекта', 'проектов')}`
  return `${head} · ${channels} ${plural(channels, 'канал', 'канала', 'каналов')}`
}

/** The admin's list of people who signed in through the bot. */
export function Users({ onBack, openUserId }: { onBack: () => void; openUserId?: number }) {
  const [users, setUsers] = useState<WebUser[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<WebUser | null>(null)

  const reload = useCallback(async () => {
    try {
      setUsers((await api.users()).items)
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

  // came here for one person: their card opens as soon as the list is in
  const [opened, setOpened] = useState(false)
  useEffect(() => {
    if (opened || !openUserId || !users) return
    setOpened(true)
    const user = users.find((u) => u.id === openUserId)
    if (user) setSelected(user)
  }, [opened, openUserId, users])

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase().replace(/^@/, '')
    if (!q) return users ?? []
    return (users ?? []).filter((u) => [u.name, u.username, String(u.tg_user_id)].some((s) => s?.toLowerCase().includes(q)))
  }, [users, query])

  return (
    <Page
      title="Пользователи"
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
          <input placeholder="Имя или @username" value={query} onChange={(e) => setQuery(e.target.value)} />
          {query && (
            <button aria-label="Очистить" onClick={() => setQuery('')} style={{ display: 'flex' }}>
              <X size={17} />
            </button>
          )}
        </label>
      }
    >
      {error && <div className="error-banner">{error}</div>}

      {users && users.length === 0 && (
        <motion.div className="empty" style={{ paddingTop: 24 }} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={spring}>
          <div className="empty-icon">
            <UserRound size={32} strokeWidth={1.6} />
          </div>
          <div className="empty-title">Пока никого</div>
          <div>Здесь появятся люди, которые вошли на сайт через Telegram</div>
        </motion.div>
      )}

      {users && users.length > 0 && (
        <>
          <div className="section-header" style={{ marginLeft: 16 }}>
            {query
              ? `Найдено: ${visible.length}`
              : `${users.length} ${plural(users.length, 'пользователь', 'пользователя', 'пользователей')}`}
          </div>
          <div className="section">
            {visible.length > 0 ? (
              <div className="group">
                <AnimatePresence initial={false}>
                  {visible.map((u) => (
                    <motion.button
                      key={u.id}
                      className="cell"
                      style={{ ['--inset' as string]: '68px', minHeight: 60 }}
                      onClick={() => setSelected(u)}
                      layout="position"
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      exit={{ opacity: 0 }}
                      transition={spring}
                    >
                      <Avatar title={displayName(u)} seed={u.id} size={40} />
                      <div className="cell-main">
                        <div className="cell-title">{displayName(u)}</div>
                        <div className="cell-sub">
                          {u.blocked ? (
                            <span style={{ color: 'var(--red)' }}>заблокирован</span>
                          ) : (
                            <>
                              {projectsLine(u)} · {u.last_seen_at ? `был ${relative(u.last_seen_at)}` : 'ещё не заходил'}
                            </>
                          )}
                        </div>
                      </div>
                      <ChevronRight size={18} color="var(--text-3)" />
                    </motion.button>
                  ))}
                </AnimatePresence>
              </div>
            ) : (
              <div className="empty" style={{ padding: '24px 16px' }}>
                <div className="empty-title">Ничего не найдено</div>
              </div>
            )}
            <div className="section-footer">Пользователи входят через бота сами, регистрация открыта.</div>
          </div>
        </>
      )}

      <UserSheet user={selected} onClose={() => setSelected(null)} onChanged={reload} />
    </Page>
  )
}

function UserSheet({ user, onClose, onChanged }: { user: WebUser | null; onClose: () => void; onChanged: () => Promise<void> }) {
  const [confirm, setConfirm] = useState(false)
  const [money, setMoney] = useState(false)
  const toast = useToast()

  // keep the last user rendered while the sheet animates out
  const [shown, setShown] = useState<WebUser | null>(null)
  useEffect(() => {
    if (user) setShown(user)
  }, [user])
  // after a change the fresh copy from the server wins over the one the list opened the sheet with
  const u = user && shown?.id === user.id ? shown : (user ?? shown)

  const onPatched = async (fresh: WebUser) => {
    setShown(fresh)
    await onChanged()
  }

  const setBlocked = async (blocked: boolean) => {
    if (!u) return
    try {
      setShown(await api.patchUser(u.id, { blocked }))
      toast(blocked ? 'Пользователь заблокирован' : 'Пользователь разблокирован')
      await onChanged()
    } catch (e) {
      toast((e as Error).message)
    }
  }

  return (
    <>
      <Sheet
        open={user !== null}
        onClose={onClose}
        title={u ? displayName(u) : 'Пользователь'}
        right={
          <button className="nav-button" style={{ fontWeight: 600 }} onClick={onClose}>
            Готово
          </button>
        }
      >
        {u && (
          <>
            <div className="hero" style={{ paddingTop: 0 }}>
              <Avatar title={displayName(u)} seed={u.id} size={72} />
              {u.username && (
                <a className="hero-sub" style={{ marginTop: 8, color: 'var(--accent)' }} href={`https://t.me/${u.username}`} target="_blank" rel="noreferrer">
                  @{u.username}
                </a>
              )}
            </div>

            <div className="section">
              <div className="group">
                <div className="cell">
                  <div className="cell-main">Баланс</div>
                  <span className="cell-value">{cents(u.balance)}</span>
                </div>
                <button className="cell accent" onClick={() => setMoney(true)}>
                  Начислить или списать
                </button>
              </div>
            </div>

            {u.projects.map((p) => (
              <ProjectPlan key={p.id} userId={u.id} project={p} onPatched={onPatched} />
            ))}

            <div className="section">
              <div className="group">
                <div className="cell">
                  <div className="cell-main">Зарегистрирован</div>
                  <span className="cell-value">{dayLabel(u.created_at)}</span>
                </div>
                <div className="cell">
                  <div className="cell-main">Последний раз на сайте</div>
                  <span className="cell-value">{u.last_seen_at ? relative(u.last_seen_at) : '—'}</span>
                </div>
                <div className="cell">
                  <div className="cell-main">Telegram ID</div>
                  <span className="cell-value" style={{ fontVariantNumeric: 'tabular-nums' }}>
                    {u.tg_user_id}
                  </span>
                </div>
              </div>
            </div>

            <div className="section">
              <div className="group">
                {u.blocked ? (
                  <button className="cell accent" onClick={() => setBlocked(false)}>
                    Разблокировать
                  </button>
                ) : (
                  <button className="cell destructive" onClick={() => setConfirm(true)}>
                    Заблокировать
                  </button>
                )}
              </div>
              <div className="section-footer">
                {u.blocked
                  ? 'Пользователь не может войти на сайт, его ключ API не работает.'
                  : 'Заблокированный пользователь сразу выходит с сайта и не может войти снова, его ключ API перестаёт работать.'}
              </div>
            </div>
          </>
        )}
      </Sheet>
      <ActionSheet
        open={confirm}
        onClose={() => setConfirm(false)}
        message="Пользователь выйдет с сайта и не сможет войти снова, пока вы его не разблокируете."
        actions={[{ label: 'Заблокировать', destructive: true, onClick: () => setBlocked(true) }]}
      />
      <MoneySheet
        user={money ? u : null}
        onClose={() => setMoney(false)}
        onDone={async (balance) => {
          setMoney(false)
          if (u) setShown({ ...u, balance })
          await onChanged()
        }}
      />
    </>
  )
}

/** The admin adds money to an owner's balance by hand (or takes it), with a note they see in their history. */
function MoneySheet({
  user,
  onClose,
  onDone,
}: {
  user: WebUser | null
  onClose: () => void
  onDone: (balance: number) => Promise<void>
}) {
  const [amount, setAmount] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const toast = useToast()
  useEffect(() => {
    if (user) {
      setAmount('')
      setNote('')
      setError(null)
    }
  }, [user])
  const value = Number(amount.replace(',', '.').replace('−', '-'))
  const valid = Number.isFinite(value) && value !== 0 && note.trim().length > 0
  const save = async () => {
    if (!user) return
    setBusy(true)
    setError(null)
    try {
      const { balance } = await api.adminMoney(user.id, value, note.trim())
      toast(`${value > 0 ? 'Начислено' : 'Списано'} ${cents(Math.abs(value))}`)
      await onDone(balance)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Sheet
      open={user !== null}
      onClose={onClose}
      title="Баланс"
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
            placeholder="Сумма, $ — с минусом, чтобы списать"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            autoFocus
          />
          <input
            className="field"
            placeholder="Комментарий — его увидит пользователь"
            value={note}
            maxLength={200}
            onChange={(e) => setNote(e.target.value)}
          />
        </div>
        <div className="section-footer">Сейчас на балансе {user ? cents(user.balance) : ''}.</div>
      </div>
      {error && <div className="error-banner">{error}</div>}
    </Sheet>
  )
}

/** One project of the user: its plan, the plan's end, and how many channels it tracks. */
function ProjectPlan({
  userId,
  project: p,
  onPatched,
}: {
  userId: number
  project: WebProject
  onPatched: (u: WebUser) => Promise<void>
}) {
  const [choosing, setChoosing] = useState(false)
  const [showChannels, setShowChannels] = useState(false)
  const toast = useToast()

  const patch = async (body: Parameters<typeof api.patchProject>[2], message: string) => {
    try {
      await onPatched(await api.patchProject(userId, p.id, body))
      toast(message)
    } catch (e) {
      toast((e as Error).message)
    }
  }

  // the end of the chosen day, in the admin's time zone
  const setUntil = (day: string) =>
    day && patch({ expires_at: new Date(`${day}T23:59:59`).toISOString() }, `Тариф действует до ${longDate(`${day}T12:00:00`)}`)

  return (
    <div className="section">
      <div className="section-header">{p.title}</div>
      <div className="group">
        <button className="cell" onClick={() => setChoosing(true)}>
          <div className="cell-main">Тариф</div>
          <span className="cell-value">{p.plan ? p.plan.title : 'нет'}</span>
          <ChevronRight size={18} color="var(--text-3)" />
        </button>
        {p.plan && !FOREVER.has(p.plan.id) && (
          <>
            <div className="cell">
              <div className="cell-main">Бессрочно</div>
              <Switch
                label="Бессрочно"
                on={p.plan.expires_at === null}
                onChange={(forever) =>
                  forever
                    ? patch({ expires_at: null }, 'Тариф бессрочный')
                    : setUntil(dayInput(new Date(Date.now() + 30 * 86_400_000).toISOString()))
                }
              />
            </div>
            {p.plan.expires_at && (
              <label className="cell">
                <div className="cell-main">Действует до</div>
                <input
                  type="date"
                  className="cell-input"
                  style={{ textAlign: 'right', flex: 'none' }}
                  value={dayInput(p.plan.expires_at)}
                  min={dayInput(new Date().toISOString())}
                  onChange={(e) => setUntil(e.target.value)}
                />
              </label>
            )}
          </>
        )}
        <button className="cell" disabled={p.channels === 0} onClick={() => setShowChannels(true)}>
          <div className="cell-main">Каналы</div>
          <span className="cell-value">
            {p.channels}
            {p.plan ? (p.plan.channels === null ? ' · без ограничений' : ` из ${p.plan.channels}`) : ''}
          </span>
          {p.channels > 0 && <ChevronRight size={18} color="var(--text-3)" />}
        </button>
        <div className="cell">
          <div className="cell-main">Сотрудники</div>
          <span className="cell-value">
            {p.members}
            {p.plan ? (p.plan.members === null ? ' · без ограничений' : ` из ${p.plan.members}`) : ''}
          </span>
        </div>
      </div>
      {p.expired_plan?.expires_at && (
        <div className="section-footer" style={{ color: 'var(--orange)' }}>
          Тариф {p.expired_plan.title} закончился {longDate(p.expired_plan.expires_at)}
        </div>
      )}
      <ActionSheet
        open={choosing}
        onClose={() => setChoosing(false)}
        title={`Тариф · ${p.title}`}
        message="Тариф начнётся сейчас; срок — тот же, если он ещё не прошёл, иначе 30 дней. dev:service:plan — только бессрочно."
        actions={[
          ...PLAN_CHOICES.map((c) => ({
            label: c.title,
            onClick: () => {
              const left = p.plan?.expires_at && new Date(p.plan.expires_at).getTime() > Date.now() ? p.plan.expires_at : null
              const until = FOREVER.has(c.id)
                ? null
                : (left ?? (p.plan && !p.plan.expires_at ? null : new Date(Date.now() + 30 * 86_400_000).toISOString()))
              return patch({ plan: c.id, expires_at: until }, `Тариф ${c.title}`)
            },
          })),
          { label: 'Без тарифа', destructive: true, onClick: () => patch({ plan: '' }, 'Тариф снят') },
        ]}
      />
      <ProjectChannelsSheet userId={userId} project={showChannels ? p : null} onClose={() => setShowChannels(false)} />
    </div>
  )
}

/** The channels a user's project tracks; a channel opens its page. */
function ProjectChannelsSheet({
  userId,
  project,
  onClose,
}: {
  userId: number
  project: WebProject | null
  onClose: () => void
}) {
  const [items, setItems] = useState<(Channel & { own: boolean })[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [shown, setShown] = useState<WebProject | null>(null)

  useEffect(() => {
    if (!project) return
    setShown(project)
    setItems(null)
    api.userProjectChannels(userId, project.id).then(
      (res) => setItems(res.items),
      (e) => setError((e as Error).message),
    )
  }, [userId, project])

  return (
    <Sheet
      open={project !== null}
      onClose={onClose}
      title={shown ? `Каналы · ${shown.title}` : 'Каналы'}
      right={
        <button className="nav-button" style={{ fontWeight: 600 }} onClick={onClose}>
          Готово
        </button>
      }
    >
      {error && <div className="error-banner">{error}</div>}
      <div className="section">
        <div className="group">
          {!items ? (
            <div className="cell" style={{ justifyContent: 'center' }}>
              <Loader2 size={20} className="spinner" />
            </div>
          ) : (
            items.map((ch) => (
              <button
                key={ch.id}
                className="cell"
                style={{ ['--inset' as string]: '62px' }}
                onClick={() => {
                  onClose()
                  location.hash = `/channel/${ch.id}`
                }}
              >
                <Avatar title={ch.title || ch.link} seed={ch.id} src={ch.avatar_url} size={36} />
                <div className="cell-main">
                  <div className="cell-title">
                    {ch.title || ch.link}
                    {ch.own && <span className="status-pill own" style={{ marginLeft: 6 }}>канал проекта</span>}
                  </div>
                  <div className="cell-sub">{channelSubtitle(ch)}</div>
                </div>
                <ChevronRight size={18} color="var(--text-3)" />
              </button>
            ))
          )}
        </div>
      </div>
    </Sheet>
  )
}
