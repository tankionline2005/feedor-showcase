import { AnimatePresence, motion } from 'framer-motion'
import { ChevronLeft, ChevronRight, Copy, KeyRound, Loader2, UserPlus, UsersRound } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { api, type MemberRights, type TeamMember, type TeamState } from '../api'
import { Avatar } from '../components/Avatar'
import { Switch } from '../components/Controls'
import { Page } from '../components/Page'
import { ActionSheet, Sheet } from '../components/Sheet'
import { useToast } from '../components/Toast'
import { useMe } from '../hooks/useMe'
import { useCurrentProject } from '../hooks/useProjects'
import { copyText } from '../lib/clipboard'
import { plural, relative } from '../lib/format'
import { usd } from '../lib/money'
import { spring } from '../lib/motion'
import { openIntro, useFirstVisit } from '../lib/intros'

interface Credentials {
  login: string
  password: string
  /** a new account, or a new password for an existing one */
  fresh: boolean
}

/** What a staff member may do besides reading, in a few words for the list. */
const rightsLine = (m: TeamMember) => {
  const parts = [
    m.can_edit_channels && 'каналы',
    m.ai_chat && 'чат с ИИ',
    m.ai_teach && 'обучает ИИ',
    m.ai_configure && 'настройки ИИ',
    m.ai_publish && 'публикует посты',
  ].filter(Boolean)
  return parts.length ? parts.join(', ') : 'только просмотр'
}

const NO_RIGHTS: MemberRights = {
  can_edit_channels: false,
  ai_chat: false,
  ai_chat_limit_usd: null,
  ai_teach: false,
  ai_configure: false,
  ai_publish: false,
}

type Flag = Exclude<keyof MemberRights, 'ai_chat_limit_usd'>

/** One switch, and what it means under it. */
function RightSwitch({
  label,
  on,
  onChange,
  footer,
  children,
}: {
  label: string
  on: boolean
  onChange: (v: boolean) => void
  footer: string
  children?: React.ReactNode
}) {
  return (
    <div className="section">
      <div className="group">
        <div className="cell">
          <div className="cell-main">{label}</div>
          <Switch label={label} on={on} onChange={onChange} />
        </div>
        {children}
      </div>
      <div className="section-footer">{footer}</div>
    </div>
  )
}

/** «Лимит на чат в месяц»: dollars, or empty for none; saved when the field is left. */
function ChatLimit({
  value,
  spent,
  onChange,
}: {
  value: number | null
  spent?: number
  onChange: (v: number | null) => void
}) {
  const [text, setText] = useState(value === null ? '' : String(value))
  useEffect(() => setText(value === null ? '' : String(value)), [value])
  const save = () => {
    const clean = text.trim().replace(',', '.')
    const next = clean === '' ? null : Number(clean)
    if (next !== null && (!Number.isFinite(next) || next < 0)) {
      setText(value === null ? '' : String(value))
      return
    }
    if (next !== value) onChange(next)
  }
  return (
    <div className="cell">
      <div className="cell-main">
        Лимит на чат в месяц
        {spent !== undefined && <div className="cell-sub">потрачено в этом месяце {usd(spent)}</div>}
      </div>
      <span className="limit-field">
        $
        <input
          className="cell-input"
          inputMode="decimal"
          placeholder="нет"
          aria-label="Лимит на чат в месяц, долларов"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onBlur={save}
          onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
        />
      </span>
    </div>
  )
}

/** The switches of what a staff member may do, each with what it means. */
function RightsSwitches({
  rights,
  spent,
  onChange,
}: {
  rights: MemberRights
  /** what their chats cost this month (an existing member) */
  spent?: number
  onChange: (r: Partial<MemberRights>) => void
}) {
  const { ai_linked: aiLinked, ai_own: aiOwn } = useMe()
  const later = aiLinked || !aiOwn ? '' : ' Заработает, когда вы подключите OpenRouter.ai в настройках.'
  const flag = (key: Flag) => ({ on: rights[key], onChange: (v: boolean) => onChange({ [key]: v }) })
  return (
    <>
      <RightSwitch
        label="Разрешить редактирование каналов"
        {...flag('can_edit_channels')}
        footer="Сотрудник сможет добавлять и удалять отслеживаемые каналы проекта."
      />

      <div className="section-header team-ai-header">Искусственный интеллект</div>
      <RightSwitch
        label="Разрешить общаться с ИИ"
        {...flag('ai_chat')}
        footer={
          'Вести чаты с ИИ о проекте, писать посты, и многое другое. Его чаты вы не увидите, но расход на них виден ' +
          'здесь, и в «Расход и остаток». Лимит: когда сотрудник израсходует его, чат станет недоступен до ' +
          'следующего месяца; пустое поле — без лимита.' +
          later
        }
      >
        {rights.ai_chat && (
          <ChatLimit
            value={rights.ai_chat_limit_usd}
            spent={spent}
            onChange={(v) => onChange({ ai_chat_limit_usd: v })}
          />
        )}
      </RightSwitch>
      <RightSwitch
        label="Обучать ИИ проекта"
        {...flag('ai_teach')}
        footer={
          'Скрывать темы, ставить и снимать пометку «Есть в вашем канале», скрывать и показывать посты в вашей умной ' +
          'ленте. ИИ учится на этих решениях для всего проекта; везде видно, кто их принял.'
        }
      />
      <RightSwitch
        label="Настраивать умную ленту и темы"
        {...flag('ai_configure')}
        footer="Включать и выключать умную ленту и темы, менять их промпты и расписание — для всего проекта."
      />
      <RightSwitch
        label="Публиковать в канал посты, сгенерированные ИИ"
        {...flag('ai_publish')}
        footer="Публиковать и откладывать посты ИИ — из своих чатов и из «Публикаций», а там и редактировать их. Без этого права «Публикации» сотрудник только смотрит."
      />
    </>
  )
}

/** "Совместный мониторинг": staff accounts of the current project. */
export function Team({ onBack }: { onBack: () => void }) {
  useFirstVisit('team')
  const project = useCurrentProject()
  const [state, setState] = useState<TeamState | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [selected, setSelected] = useState<TeamMember | null>(null)
  const [credentials, setCredentials] = useState<Credentials | null>(null)

  const reload = useCallback(async () => {
    try {
      setState(await api.team())
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])

  useEffect(() => {
    reload()
  }, [reload])

  // a paused one (over the plan's limit) does not count: they wait for a plan with room
  const count = state?.items.filter((m) => !m.paused).length ?? 0
  const full = state ? state.limit !== null && count >= state.limit : true

  return (
    <Page
      onHelp={() => openIntro('team')}
      title="Совместный мониторинг"
      onRefresh={reload}
      left={
        <motion.button className="nav-button" onClick={onBack} whileTap={{ opacity: 0.4 }} style={{ paddingLeft: 0 }}>
          <ChevronLeft size={30} strokeWidth={2.2} style={{ marginRight: -4 }} />
          Настройки
        </motion.button>
      }
    >
      <div className="section-footer" style={{ padding: '0 16px 16px' }}>
        Аккаунты для вашей команды в проекте{project ? ` «${project.title}»` : ''}: сотрудник видит посты и каналы
        проекта и пользуется API со своим ключом. Других ваших проектов он не видит.
      </div>
      {error && <div className="error-banner">{error}</div>}

      {state && (
        <div className="section">
          <div className="section-header">
            Сотрудники · {state.limit === null ? `${count}, без ограничений` : `${count} из ${state.limit}`}
          </div>
          <div className="group">
            <AnimatePresence initial={false}>
              {state.items.map((m) => (
                <motion.button
                  key={m.id}
                  className="cell"
                  style={{ ['--inset' as string]: '62px', minHeight: 56 }}
                  onClick={() => setSelected(m)}
                  layout="position"
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: 'auto' }}
                  exit={{ opacity: 0, height: 0 }}
                  transition={spring}
                >
                  <Avatar title={m.login} seed={m.id} size={36} />
                  <div className="cell-main">
                    <div className="cell-title">
                      {m.login}
                      {m.paused && <span className="paused-tag">на паузе</span>}
                    </div>
                    <div className="cell-sub">
                      {rightsLine(m)}
                      {m.telegram?.username ? ` · @${m.telegram.username}` : ''} ·{' '}
                      {m.last_seen_at ? `был ${relative(m.last_seen_at)}` : 'ещё не заходил'}
                    </div>
                  </div>
                  <ChevronRight size={18} color="var(--text-3)" />
                </motion.button>
              ))}
            </AnimatePresence>
            <button
              className="cell accent"
              style={{ ['--inset' as string]: '62px' }}
              disabled={full}
              onClick={() => setAdding(true)}
            >
              <span className="team-add-icon">
                <UserPlus size={18} />
              </span>
              Добавить сотрудника
            </button>
          </div>
          <div className="section-footer">
            {!state.has_plan
              ? 'Нужен тариф: без него сотрудников добавить нельзя. Сотрудники ждут, пока тариф не продлят, — войти они не могут.'
              : state.limit === 0
                ? 'Ваш тариф не включает сотрудников — выберите тариф больше в настройках.'
                : full
                  ? `По вашему тарифу — не больше ${state.limit} ${plural(state.limit ?? 0, 'сотрудника', 'сотрудников', 'сотрудников')}.${state.items.some((m) => m.paused) ? ' Сотрудники сверх лимита на паузе: войти они не могут, пока тариф не позволит.' : ''}`
                  : 'Когда тариф проекта закончится, проект заморозится: сотрудники не смогут войти, пока тариф не продлят.'}
          </div>
        </div>
      )}

      {state && state.items.length === 0 && (
        <div className="empty" style={{ paddingTop: 8 }}>
          <div className="empty-icon">
            <UsersRound size={30} strokeWidth={1.6} />
          </div>
          <div>Добавьте редакторов и коллег — каждому свой логин и пароль</div>
        </div>
      )}

      <AddMemberSheet
        open={adding}
        onClose={() => setAdding(false)}
        onCreated={async (c) => {
          setAdding(false)
          setCredentials(c)
          await reload()
        }}
      />
      <MemberSheet
        member={selected}
        onClose={() => setSelected(null)}
        onChanged={async (m) => {
          setSelected(m)
          await reload()
        }}
        onPassword={(c) => {
          setSelected(null)
          setCredentials(c)
        }}
        onDeleted={async () => {
          setSelected(null)
          await reload()
        }}
      />
      <CredentialsSheet credentials={credentials} onClose={() => setCredentials(null)} />
    </Page>
  )
}

function AddMemberSheet({
  open,
  onClose,
  onCreated,
}: {
  open: boolean
  onClose: () => void
  onCreated: (c: Credentials) => void
}) {
  const [login, setLogin] = useState('')
  const [rights, setRights] = useState<MemberRights>(NO_RIGHTS)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (open) {
      setLogin('')
      setRights(NO_RIGHTS)
      setError(null)
    }
  }, [open])

  const create = async () => {
    setBusy(true)
    setError(null)
    try {
      const res = await api.addMember(login, rights)
      onCreated({ login: res.member.login, password: res.password, fresh: true })
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
      title="Новый сотрудник"
      left={
        <button className="nav-button" onClick={onClose}>
          Отмена
        </button>
      }
      right={
        <button
          className="nav-button"
          style={{ fontWeight: 600 }}
          disabled={busy || login.trim().length < 3}
          onClick={create}
        >
          {busy ? <Loader2 size={18} className="spinner" /> : 'Создать'}
        </button>
      }
    >
      <form
        onSubmit={(e) => {
          e.preventDefault()
          if (login.trim().length >= 3) create()
        }}
      >
        <div className="section">
          <div className="group">
            <input
              className="field"
              placeholder="Логин, например anna.editor"
              aria-label="Логин"
              autoCapitalize="none"
              autoComplete="off"
              spellCheck={false}
              autoFocus
              maxLength={32}
              value={login}
              onChange={(e) => setLogin(e.target.value.replace(/\s/g, ''))}
            />
          </div>
          <div className="section-footer" style={error ? { color: 'var(--red)' } : undefined}>
            {error ?? 'Латинские буквы, цифры, точка, дефис или подчёркивание. Пароль мы придумаем сами.'}
          </div>
        </div>
        <RightsSwitches rights={rights} onChange={(r) => setRights({ ...rights, ...r })} />
        <div className="section-footer" style={{ padding: '0 16px 16px' }}>
          Всё это можно поменять позже.
        </div>
      </form>
    </Sheet>
  )
}

function MemberSheet({
  member,
  onClose,
  onChanged,
  onPassword,
  onDeleted,
}: {
  member: TeamMember | null
  onClose: () => void
  onChanged: (m: TeamMember) => Promise<void>
  onPassword: (c: Credentials) => void
  onDeleted: () => Promise<void>
}) {
  const toast = useToast()
  const [confirm, setConfirm] = useState<'password' | 'delete' | null>(null)
  // keep the last member rendered while the sheet animates out
  const [shown, setShown] = useState<TeamMember | null>(null)
  useEffect(() => {
    if (member) setShown(member)
  }, [member])
  const m = member ?? shown

  const setRights = async (rights: Partial<MemberRights>) => {
    if (!m) return
    try {
      await onChanged(await api.patchMember(m.id, rights))
      if ('ai_chat_limit_usd' in rights)
        toast(
          rights.ai_chat_limit_usd === null
            ? 'Лимит на чат снят'
            : `Лимит на чат — ${usd(rights.ai_chat_limit_usd!)} в месяц`,
        )
    } catch (e) {
      toast((e as Error).message)
    }
  }

  const resetPassword = async () => {
    if (!m) return
    try {
      const res = await api.resetMemberPassword(m.id)
      onPassword({ login: res.member.login, password: res.password, fresh: false })
    } catch (e) {
      toast((e as Error).message)
    }
  }

  const remove = async () => {
    if (!m) return
    try {
      await api.deleteMember(m.id)
      toast(`Сотрудник ${m.login} удалён`)
      await onDeleted()
    } catch (e) {
      toast((e as Error).message)
    }
  }

  return (
    <>
      <Sheet
        open={member !== null}
        onClose={onClose}
        title={m?.login ?? 'Сотрудник'}
        right={
          <button className="nav-button" style={{ fontWeight: 600 }} onClick={onClose}>
            Готово
          </button>
        }
      >
        {m && (
          <>
            <div className="hero" style={{ paddingTop: 0 }}>
              <Avatar title={m.login} seed={m.id} size={72} />
              <div className="hero-title mono" style={{ marginTop: 10 }}>
                {m.login}
              </div>
              <div className="hero-sub">
                {m.last_seen_at ? `был на сайте ${relative(m.last_seen_at)}` : 'ещё не заходил'}
              </div>
            </div>
            <RightsSwitches rights={m} spent={m.chat_spent} onChange={setRights} />
            <div className="section">
              <div className="group">
                <div className="cell">
                  <div className="cell-main">Telegram</div>
                  <span className="cell-value">
                    {m.telegram ? (m.telegram.username ? `@${m.telegram.username}` : m.telegram.name) : 'не привязан'}
                  </span>
                </div>
              </div>
              <div className="section-footer">
                Сотрудник привязывает свой Telegram сам, в настройках, — тогда бот присылает ему оригиналы постов.
              </div>
            </div>
            <div className="section">
              <div className="group">
                <button className="cell accent" onClick={() => setConfirm('password')}>
                  <KeyRound size={18} />
                  Сбросить пароль
                </button>
                <button className="cell destructive" onClick={() => setConfirm('delete')}>
                  Удалить сотрудника
                </button>
              </div>
            </div>
          </>
        )}
      </Sheet>
      <ActionSheet
        open={confirm === 'password'}
        onClose={() => setConfirm(null)}
        message="Старый пароль перестанет работать, сотрудник выйдет со всех устройств. Новый пароль покажем один раз."
        actions={[{ label: 'Сбросить пароль', onClick: resetPassword }]}
      />
      <ActionSheet
        open={confirm === 'delete'}
        onClose={() => setConfirm(null)}
        message="Аккаунт и его ключ API удалятся навсегда, сотрудник сразу потеряет доступ."
        actions={[{ label: 'Удалить сотрудника', destructive: true, onClick: remove }]}
      />
    </>
  )
}

/** The login and password, shown once: monospace, each with a copy button, and all of it at once. */
function CredentialsSheet({ credentials, onClose }: { credentials: Credentials | null; onClose: () => void }) {
  const toast = useToast()
  const [shown, setShown] = useState<Credentials | null>(null)
  useEffect(() => {
    if (credentials) setShown(credentials)
  }, [credentials])
  const c = credentials ?? shown

  const copy = async (text: string, what: string) => toast((await copyText(text)) ? `${what} скопирован` : text)
  const all = c ? `Вход на ${location.origin} → «Вход для сотрудников»\nЛогин: ${c.login}\nПароль: ${c.password}` : ''

  return (
    <Sheet
      open={credentials !== null}
      onClose={onClose}
      title={c?.fresh ? 'Сотрудник добавлен' : 'Новый пароль'}
      right={
        <button className="nav-button" style={{ fontWeight: 600 }} onClick={onClose}>
          Готово
        </button>
      }
    >
      {c && (
        <>
          <div className="credentials-warning">
            Пароль показывается только сейчас — скопируйте и передайте его сотруднику. Потом его можно будет только
            сбросить.
          </div>
          <div className="section">
            <div className="group">
              <div className="cell">
                <div className="cell-main">
                  <div className="cell-sub">Логин</div>
                  <div className="credential mono">{c.login}</div>
                </div>
                <button className="nav-button" aria-label="Скопировать логин" onClick={() => copy(c.login, 'Логин')}>
                  <Copy size={19} />
                </button>
              </div>
              <div className="cell">
                <div className="cell-main">
                  <div className="cell-sub">Пароль</div>
                  <div className="credential mono">{c.password}</div>
                </div>
                <button
                  className="nav-button"
                  aria-label="Скопировать пароль"
                  onClick={() => copy(c.password, 'Пароль')}
                >
                  <Copy size={19} />
                </button>
              </div>
            </div>
          </div>
          <div style={{ padding: '0 16px 16px' }}>
            <button
              className="button"
              onClick={async () => toast((await copyText(all)) ? 'Данные для входа скопированы' : all)}
            >
              <Copy size={18} />
              Скопировать всё
            </button>
          </div>
        </>
      )}
    </Sheet>
  )
}
