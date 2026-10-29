import {
  ArrowUpRight,
  ChevronRight,
  Code2,
  CreditCard,
  LifeBuoy,
  Loader2,
  LogOut,
  Megaphone,
  MonitorSmartphone,
  Plug,
  Radio,
  Smartphone,
  Sparkles,
  Trash2,
  UsersRound,
  Wallet,
  FileText,
  ShieldCheck,
  Moon,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../api'
import { Avatar } from '../components/Avatar'
import { LegalSheet } from '../components/LegalSheet'
import { Page } from '../components/Page'
import { Switch } from '../components/Controls'
import { setTheme, useTheme } from '../lib/theme'
import { ChannelPicker } from '../components/ProjectSwitcher'
import { Sheet } from '../components/Sheet'
import { useToast } from '../components/Toast'
import { useGroups } from '../hooks/useGroups'
import { useMe, useReloadMe } from '../hooks/useMe'
import { reloadProjects, useCurrentProject, useProjects } from '../hooks/useProjects'
import { usePwaInstall } from '../hooks/usePwa'
import type { LegalDoc } from '../lib/legal'
import { cents } from '../lib/money'
import { InstallSheet } from './InstallSheet'

export const SUPPORT_URL = 'https://t.me/naomifontaine'
export const CHANNEL_URL = 'https://t.me/feedor_pro'

/**
 * Settings of a user (not the admin): their Telegram profile; the current project's channel,
 * promo code, API and deletion; and what is the same in every project (groups, the app, support).
 */
export function UserSettings({
  onLogout,
  onOpenApi,
  onOpenMcp,
  onOpenGroups,
  onOpenTeam,
  onOpenPlan,
  onOpenBalance,
  onOpenAi,
  onOpenDevices,
}: {
  onLogout: () => void
  onOpenApi: () => void
  onOpenMcp: () => void
  onOpenGroups: () => void
  onOpenTeam: () => void
  onOpenPlan: () => void
  onOpenBalance: () => void
  onOpenAi: () => void
  onOpenDevices: () => void
}) {
  const me = useMe()
  const theme = useTheme()
  const groups = useGroups()
  const projects = useProjects()
  const project = useCurrentProject()
  const pwa = usePwaInstall()
  const reloadMe = useReloadMe()
  const toast = useToast()
  const [installing, setInstalling] = useState(false)
  const [connecting, setConnecting] = useState(false)
  // "#/settings?flash=ai": the feed's «AI» sent the owner here to link OpenRouter — show where
  const [flash, setFlash] = useState(() => new URLSearchParams(location.hash.split('?')[1] ?? '').get('flash') === 'ai')
  useEffect(() => {
    if (!flash) return
    history.replaceState(null, '', '#/settings')
    const el = document.getElementById('ai-settings')
    const scroll = window.setTimeout(() => el?.scrollIntoView({ behavior: 'smooth', block: 'center' }), 250)
    const done = window.setTimeout(() => setFlash(false), 4000)
    return () => {
      window.clearTimeout(scroll)
      window.clearTimeout(done)
    }
  }, [flash])
  const [picking, setPicking] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [legal, setLegal] = useState<LegalDoc | null>(null)
  // the account's money, one for all projects
  const [balance, setBalance] = useState<number | null>(null)
  useEffect(() => {
    api.billing().then(
      (b) => setBalance(b.balance),
      () => setBalance(null),
    )
  }, [project?.id])
  const name = me.user || (me.username ? `@${me.username}` : 'Пользователь')
  const several = (projects?.items.length ?? 0) > 1

  const remove = async () => {
    if (!project) return
    try {
      await api.deleteProject(project.id)
      setDeleting(false)
      await Promise.all([reloadMe(), reloadProjects()])
      toast(`Проект «${project.title}» удалён`)
    } catch (e) {
      toast((e as Error).message)
    }
  }

  // to openrouter.ai to approve the link; it sends the owner back to the AI settings
  const connectAi = async () => {
    setConnecting(true)
    try {
      location.href = (await api.aiConnect()).url
    } catch (e) {
      toast((e as Error).message)
      setConnecting(false)
    }
  }

  // what the project's channel is up to, before the section's note (nothing when it is fine)
  const channel = project?.channel
  const channelNote = !channel
    ? 'Канал, который вы развиваете: все данные мониторинга служат ему.'
    : channel.status === 'error'
      ? channel.status_detail || 'Канал не удалось подключить — проверьте ссылку и выберите его заново.'
      : channel.status === 'pending'
        ? channel.status_detail || 'Канал подключается — его посты появятся, как только наш аккаунт в него вступит.'
        : null

  return (
    <Page title="Настройки" project>
      <div className="hero" style={{ paddingTop: 4 }}>
        <Avatar title={name} seed={name.length} size={72} />
        <div className="hero-title" style={{ marginTop: 10 }}>
          {name}
        </div>
        {me.username && <div className="hero-sub">@{me.username}</div>}
      </div>

      <div className="section">
        <div className="group">
          <button className="cell" style={{ ['--inset' as string]: '57px' }} onClick={onOpenBalance}>
            <div className="cell-icon" style={{ background: 'var(--green)' }}>
              <Wallet size={17} />
            </div>
            <div className="cell-main">Баланс</div>
            <span className="cell-value">{balance === null ? '' : cents(balance)}</span>
            <ChevronRight size={18} color="var(--text-3)" />
          </button>
        </div>
      </div>

      {project && (
        <>
          <div className="section">
            <div className="section-header">Проект «{project.title}»</div>
            <div className="group">
              <button className="cell" style={{ ['--inset' as string]: '57px' }} onClick={() => setPicking(true)}>
                <div className="cell-icon" style={{ background: 'var(--indigo)' }}>
                  <Radio size={17} />
                </div>
                <div className="cell-main">Канал проекта</div>
                <span className={`cell-value${project.channel ? '' : ' accent'}`}>
                  {project.channel ? project.channel.title || 'Канал' : 'Выбрать'}
                </span>
                <ChevronRight size={18} color="var(--text-3)" />
              </button>
              <button className="cell" style={{ ['--inset' as string]: '57px' }} onClick={onOpenPlan}>
                <div className="cell-icon" style={{ background: 'var(--green)' }}>
                  <CreditCard size={17} />
                </div>
                <div className="cell-main">Тариф</div>
                <span className="cell-value">{me.plan ? me.plan.title : 'нет'}</span>
                <ChevronRight size={18} color="var(--text-3)" />
              </button>
              <button className="cell" style={{ ['--inset' as string]: '57px' }} onClick={onOpenGroups}>
                <div className="cell-icon" style={{ background: 'var(--accent)' }}>
                  <UsersRound size={18} />
                </div>
                <div className="cell-main">Группы проекта</div>
                <span className="cell-value">{groups ? groups.items.length || '' : ''}</span>
                <ChevronRight size={18} color="var(--text-3)" />
              </button>
              <button className="cell" style={{ ['--inset' as string]: '57px' }} onClick={onOpenTeam}>
                <div className="cell-icon" style={{ background: 'var(--orange)' }}>
                  <UsersRound size={18} />
                </div>
                <div className="cell-main">Совместный мониторинг</div>
                <ChevronRight size={18} color="var(--text-3)" />
              </button>
              <button className="cell" style={{ ['--inset' as string]: '57px' }} onClick={onOpenApi}>
                <div className="cell-icon" style={{ background: 'var(--text-2)' }}>
                  <Code2 size={18} />
                </div>
                <div className="cell-main">API</div>
                <ChevronRight size={18} color="var(--text-3)" />
              </button>
              <button className="cell" style={{ ['--inset' as string]: '57px' }} onClick={onOpenMcp}>
                <div className="cell-icon" style={{ background: 'var(--indigo)' }}>
                  <Plug size={18} />
                </div>
                <div className="cell-main">MCP-коннектор</div>
                <ChevronRight size={18} color="var(--text-3)" />
              </button>
              {several && (
                <button
                  className="cell destructive"
                  style={{ ['--inset' as string]: '57px' }}
                  onClick={() => setDeleting(true)}
                >
                  <div className="cell-icon" style={{ background: 'var(--red)' }}>
                    <Trash2 size={17} />
                  </div>
                  Удалить проект
                </button>
              )}
            </div>
            {channelNote && (
              <div className="section-footer">
                <span style={channel?.status === 'error' ? { color: 'var(--orange)' } : undefined}>{channelNote}</span>
              </div>
            )}
          </div>
        </>
      )}

      {!me.ai_own && (
        <div className="section" id="ai-settings">
          <div className="section-header">Искусственный интеллект</div>
          <div className="group">
            <button className="cell" style={{ ['--inset' as string]: '57px' }} onClick={onOpenAi}>
              <div className="cell-icon ai-icon">
                <Sparkles size={17} />
              </div>
              <div className="cell-main">Расход на ИИ</div>
              <ChevronRight size={18} color="var(--text-3)" />
            </button>
          </div>
          <div className="section-footer">
            ИИ входит в тариф: умная лента, темы, посты, рубрики и чат. Сколько ИИ осталось в этом месяце — в «Тарифе».
          </div>
        </div>
      )}

      {me.ai_own && (
      <div className={`section${flash ? ' flash' : ''}`} id="ai-settings">
        <div className="section-header">Искусственный интеллект</div>
        <div className="group">
          <button
            className="cell"
            style={{ ['--inset' as string]: '57px' }}
            onClick={me.ai_linked ? onOpenAi : connectAi}
            disabled={connecting}
          >
            <div className="cell-icon ai-icon">
              <Sparkles size={17} />
            </div>
            <div className={`cell-main${me.ai_linked ? '' : ' accent-text'}`}>
              {me.ai_linked ? 'Настройки OpenRouter.ai' : 'Подключить OpenRouter.ai'}
            </div>
            {connecting ? <Loader2 size={18} className="spinner" /> : <ChevronRight size={18} color="var(--text-3)" />}
          </button>
        </div>
        <div className="section-footer">
          {me.ai_linked
            ? 'Настройте искусственный интеллект, чтобы получить максимальную пользу от мониторинга.'
            : 'Подключите LLM-агрегатор и больше не пропускайте новости. Вам станут доступны: Умная лента, Готовые темы, посты от ИИ, рубрики и многое другое. Ваша команда также сможет ими воспользоваться.'}
        </div>
      </div>
      )}

      {pwa.available && (
        <div className="section">
          <div className="group">
            <button className="cell" style={{ ['--inset' as string]: '57px' }} onClick={() => setInstalling(true)}>
              <div className="cell-icon" style={{ background: 'var(--accent)' }}>
                <Smartphone size={18} />
              </div>
              <div className="cell-main">Установить на экран «Домой»</div>
              <ChevronRight size={18} color="var(--text-3)" />
            </button>
          </div>
        </div>
      )}

      <div className="section">
        <div className="group">
          <div className="cell" style={{ ['--inset' as string]: '57px' }}>
            <div className="cell-icon" style={{ background: 'var(--indigo)' }}>
              <Moon size={17} />
            </div>
            <div className="cell-main">Тёмная тема</div>
            <Switch label="Тёмная тема" on={theme === 'dark'} onChange={(on) => setTheme(on ? 'dark' : 'light')} />
          </div>
        </div>
      </div>

      <div className="section">
        <div className="section-header">Конфиденциальность</div>
        <div className="group">
          <button className="cell" style={{ ['--inset' as string]: '57px' }} onClick={onOpenDevices}>
            <div className="cell-icon" style={{ background: 'var(--orange)' }}>
              <MonitorSmartphone size={18} />
            </div>
            <div className="cell-main">Устройства</div>
            <ChevronRight size={18} color="var(--text-3)" />
          </button>
        </div>
      </div>

      <div className="section">
        <div className="group">
          <a
            className="cell"
            style={{ ['--inset' as string]: '57px' }}
            href={SUPPORT_URL}
            target="_blank"
            rel="noreferrer"
          >
            <div className="cell-icon" style={{ background: 'var(--accent)' }}>
              <LifeBuoy size={18} />
            </div>
            <div className="cell-main">Поддержка</div>
            <ArrowUpRight size={18} color="var(--text-3)" />
          </a>
          <a
            className="cell"
            style={{ ['--inset' as string]: '57px' }}
            href={CHANNEL_URL}
            target="_blank"
            rel="noreferrer"
          >
            <div className="cell-icon" style={{ background: 'var(--indigo)' }}>
              <Megaphone size={17} />
            </div>
            <div className="cell-main">Наш канал</div>
            <ArrowUpRight size={18} color="var(--text-3)" />
          </a>
          <button className="cell" style={{ ['--inset' as string]: '57px' }} onClick={() => setLegal('terms')}>
            <div className="cell-icon" style={{ background: 'var(--text-2)' }}>
              <FileText size={17} />
            </div>
            <div className="cell-main">Пользовательское соглашение</div>
            <ChevronRight size={18} color="var(--text-3)" />
          </button>
          <button className="cell" style={{ ['--inset' as string]: '57px' }} onClick={() => setLegal('privacy')}>
            <div className="cell-icon" style={{ background: 'var(--text-2)' }}>
              <ShieldCheck size={17} />
            </div>
            <div className="cell-main">Политика конфиденциальности</div>
            <ChevronRight size={18} color="var(--text-3)" />
          </button>
        </div>
      </div>

      <div className="section">
        <div className="group">
          <button className="cell destructive" onClick={onLogout}>
            <LogOut size={18} />
            Выйти
          </button>
        </div>
      </div>

      {pwa.available && <InstallSheet open={installing} onClose={() => setInstalling(false)} />}
      <ChannelPicker projectId={picking && project ? project.id : null} onClose={() => setPicking(false)} />
      <DeleteProjectSheet
        open={deleting}
        title={project?.title ?? ''}
        onClose={() => setDeleting(false)}
        onDelete={remove}
      />
      <LegalSheet doc={legal} onClose={() => setLegal(null)} />
    </Page>
  )
}

/** Deleting a project cannot be undone: the person types its name to confirm. */
function DeleteProjectSheet({
  open,
  title,
  onClose,
  onDelete,
}: {
  open: boolean
  title: string
  onClose: () => void
  onDelete: () => Promise<void>
}) {
  const [typed, setTyped] = useState('')
  const [busy, setBusy] = useState(false)
  // the name it was opened with: the project list changes under the sheet once it is deleted
  const [name, setName] = useState(title)

  useEffect(() => {
    if (open) {
      setTyped('')
      setName(title)
    }
  }, [open, title])

  const same = typed.trim().toLowerCase() === name.trim().toLowerCase()

  const confirm = async () => {
    if (!same || busy) return
    setBusy(true)
    try {
      await onDelete()
    } finally {
      setBusy(false)
    }
  }

  return (
    <Sheet
      open={open}
      onClose={onClose}
      title="Удаление проекта"
      left={
        <button className="nav-button" onClick={onClose}>
          Отмена
        </button>
      }
    >
      <form
        onSubmit={(e) => {
          e.preventDefault()
          confirm()
        }}
      >
        <div
          className="credentials-warning"
          style={{ color: 'var(--red)', background: 'color-mix(in srgb, var(--red) 12%, transparent)' }}
        >
          Все отслеживаемые каналы проекта, его сотрудники и ключ API будут удалены, оставшиеся дни тарифа сгорят. Это
          нельзя отменить.
        </div>
        <div className="section">
          <div className="delete-prompt">
            Чтобы подтвердить, введите название проекта: <b className="delete-name">{name}</b>
          </div>
          <div className="group">
            <input
              className="field"
              placeholder={name}
              aria-label="Название проекта"
              autoComplete="off"
              autoCapitalize="none"
              spellCheck={false}
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
            />
          </div>
        </div>
        <div style={{ padding: '0 16px 16px' }}>
          <button className="button destructive" disabled={!same || busy}>
            {busy ? <Loader2 size={20} className="spinner" style={{ color: '#fff' }} /> : 'Удалить проект'}
          </button>
        </div>
      </form>
    </Sheet>
  )
}
