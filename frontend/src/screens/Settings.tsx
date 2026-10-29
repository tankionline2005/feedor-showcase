import { AnimatePresence } from 'framer-motion'
import {
  Activity,
  Bell,
  ChevronRight,
  Clock,
  Code2,
  Eraser,
  HardDrive,
  LogOut,
  Network,
  Plug,
  Send,
  Smartphone,
  Sparkles,
  Ticket,
  UserRound,
  Users,
  Moon,
} from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { api, type NotifyState, type Settings as SettingsT, type Status, type Storage } from '../api'
import { Stepper, Switch } from '../components/Controls'
import { DismissibleNote } from '../components/DismissibleNote'
import { Page } from '../components/Page'
import { setTheme, useTheme } from '../lib/theme'
import { useToast } from '../components/Toast'
import { usePwaInstall } from '../hooks/usePwa'
import { bytes, plural } from '../lib/format'
import { InstallSheet } from './InstallSheet'
import { NotifySheet } from './NotifySheet'

const RETENTION_STEPS = [3, 7, 14, 30, 60, 90, 180, 365]
const CLEANUP_STEPS = [256, 512, 1024, 2048, 5120, 10240]

/** 512 → "512 МБ", 5120 → "5 ГБ" */
const megabytes = (mb: number) => (mb < 1024 ? `${mb} МБ` : `${mb / 1024} ГБ`)

/** Disk usage bar in the style of iPhone Storage: database, previews, everything else, free. */
function StorageCell({ storage }: { storage: Storage }) {
  const other = Math.max(0, storage.used - storage.database - storage.media)
  const parts = [
    { key: 'db', label: 'База данных', value: storage.database, color: 'var(--accent)' },
    { key: 'media', label: 'Превью', value: storage.media, color: 'var(--orange)' },
    { key: 'other', label: 'Система и прочее', value: other, color: 'var(--text-3)' },
  ]
  const share = (v: number) => `${storage.total ? Math.max(v > 0 ? 0.6 : 0, (v / storage.total) * 100) : 0}%`
  const freeColor =
    storage.level === 'critical' ? 'var(--red)' : storage.level === 'low' ? 'var(--orange)' : 'var(--text-2)'
  return (
    <div className="cell storage-cell" style={{ alignItems: 'flex-start', paddingTop: 12, paddingBottom: 12 }}>
      <div className="cell-icon" style={{ background: 'var(--text-2)' }}>
        <HardDrive size={17} />
      </div>
      <div className="cell-main">
        <div className="storage-head">
          <span>Использовано памяти</span>
          <span className="cell-value">
            {bytes(storage.used)} из {bytes(storage.total)}
          </span>
        </div>
        <div className="storage-bar" role="img" aria-label={`Свободно ${bytes(storage.free)}`}>
          {parts.map((p) => (
            <span key={p.key} style={{ width: share(p.value), background: p.color }} />
          ))}
        </div>
        <div className="storage-legend">
          {parts.map((p) => (
            <span key={p.key}>
              <i style={{ background: p.color }} />
              {p.label} {bytes(p.value)}
            </span>
          ))}
          <span style={{ color: freeColor, fontWeight: storage.level === 'ok' ? 400 : 600 }}>
            <i style={{ background: 'var(--fill)' }} />
            Свободно {bytes(storage.free)}
          </span>
        </div>
      </div>
    </div>
  )
}

interface Props {
  onLogout: () => void
  onOpenAccounts: () => void
  onOpenProxies: () => void
  onOpenApi: () => void
  onOpenMcp: () => void
  onOpenErrors: () => void
  onOpenUsers: () => void
  onOpenPromo: () => void
  onOpenPerformance: () => void
  onOpenServiceAi: () => void
}

export function Settings({
  onLogout,
  onOpenAccounts,
  onOpenProxies,
  onOpenApi,
  onOpenMcp,
  onOpenErrors,
  onOpenUsers,
  onOpenPromo,
  onOpenPerformance,
  onOpenServiceAi,
}: Props) {
  const theme = useTheme()
  const [settings, setSettings] = useState<SettingsT | null>(null)
  const [status, setStatus] = useState<Status | null>(null)
  const [aggregator, setAggregator] = useState('')
  const [notify, setNotify] = useState<NotifyState | null>(null)
  const [storage, setStorage] = useState<Storage | null>(null)
  const [usersCount, setUsersCount] = useState<number | null>(null)
  const [notifyOpen, setNotifyOpen] = useState(false)
  const [installing, setInstalling] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const toast = useToast()
  const pwa = usePwaInstall()

  const reload = useCallback(async () => {
    try {
      const [s, st, n, disk, people] = await Promise.all([
        api.settings(),
        api.status(),
        api.notify(),
        api.storage(),
        api.users(),
      ])
      setUsersCount(people.items.length)
      setSettings(s)
      setAggregator(s.aggregator_channel)
      setStatus(st)
      setNotify(n)
      setStorage(disk)
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

  const save = async (values: Partial<SettingsT>) => {
    if (!settings) return
    setSettings({ ...settings, ...values })
    try {
      setSettings(await api.saveSettings(values))
    } catch (e) {
      toast((e as Error).message)
      reload()
    }
  }

  const saveAggregator = () => {
    if (settings && aggregator.trim() !== settings.aggregator_channel) {
      save({ aggregator_channel: aggregator.trim() })
      toast('Группа-агрегатор сохранена')
    }
  }

  const dismissError = async () => {
    // hide right away, the server remembers it so it stays hidden everywhere
    setStatus((s) => (s ? { ...s, last_repost_error: null } : s))
    try {
      await api.dismissRepostError()
    } catch (e) {
      toast((e as Error).message)
      reload()
    }
  }

  const accountCounts = status?.accounts ?? {}
  const accountsTotal = Object.values(accountCounts).reduce((a, b) => a + b, 0)
  const accountProblems = (accountCounts.error ?? 0) + (accountCounts.dead ?? 0) + (accountCounts.waiting ?? 0)
  const proxyCounts = status?.proxies ?? {}
  const proxiesTotal = Object.values(proxyCounts).reduce((a, b) => a + b, 0)
  const proxyProblems = proxyCounts.dead ?? 0
  const reposts = status?.reposts ?? {}

  return (
    <Page title="Настройки" onRefresh={reload}>
      {error && <div className="error-banner">{error}</div>}

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
          <div className="section-footer">Откроется как отдельное приложение, без панелей браузера.</div>
        </div>
      )}

      <div className="section">
        <div className="group">
          <button className="cell" style={{ ['--inset' as string]: '57px' }} onClick={onOpenUsers}>
            <div className="cell-icon" style={{ background: 'var(--green)' }}>
              <UserRound size={18} />
            </div>
            <div className="cell-main">Пользователи</div>
            <span className="cell-value">{usersCount ?? ''}</span>
            <ChevronRight size={18} color="var(--text-3)" />
          </button>
          <button className="cell" style={{ ['--inset' as string]: '57px' }} onClick={onOpenPromo}>
            <div className="cell-icon" style={{ background: 'var(--orange)' }}>
              <Ticket size={17} />
            </div>
            <div className="cell-main">Промокоды</div>
            <ChevronRight size={18} color="var(--text-3)" />
          </button>
        </div>
        <div className="section-footer">Люди, которые вошли на сайт через Telegram-бота, и промокоды на тарифы для них.</div>
      </div>

      <div className="section-header" style={{ marginLeft: 16 }}>Боты</div>
      <div className="section">
        <div className="group">
          <button className="cell" style={{ ['--inset' as string]: '57px' }} onClick={onOpenAccounts}>
            <div className="cell-icon" style={{ background: 'var(--accent)' }}>
              <Users size={18} />
            </div>
            <div className="cell-main">Аккаунты</div>
            {accountProblems > 0 && (
              <span className="count-badge" title={`С ошибкой: ${accountProblems}`}>
                {accountProblems}
              </span>
            )}
            <span className="cell-value">{status ? accountsTotal : ''}</span>
            <ChevronRight size={18} color="var(--text-3)" />
          </button>
          <button className="cell" style={{ ['--inset' as string]: '57px' }} onClick={onOpenProxies}>
            <div className="cell-icon" style={{ background: 'var(--indigo)' }}>
              <Network size={18} />
            </div>
            <div className="cell-main">Прокси</div>
            {proxyProblems > 0 && (
              <span className="count-badge" title={`Не работают: ${proxyProblems}`}>
                {proxyProblems}
              </span>
            )}
            <span className="cell-value">{status ? proxiesTotal || 'нет' : ''}</span>
            <ChevronRight size={18} color="var(--text-3)" />
          </button>
        </div>
        <div className="section-footer">
          Боты читают каналы и пересылают посты в группу-агрегатор, подключаясь через прокси из общего пула.
        </div>
      </div>

      {settings && (
        <>
          <div className="section-header" style={{ marginLeft: 16 }}>Репост</div>
          <div className="section">
            <div className="group">
              <label className="cell" style={{ ['--inset' as string]: '57px' }}>
                <div className="cell-icon" style={{ background: 'var(--accent)' }}>
                  <Send size={17} />
                </div>
                <span style={{ flex: 'none' }}>Группа-агрегатор</span>
                <input
                  className="cell-input"
                  placeholder="@group или t.me/+…"
                  value={aggregator}
                  onChange={(e) => setAggregator(e.target.value)}
                  onBlur={saveAggregator}
                  onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
                />
              </label>
            </div>
            <div className="section-footer">
              Группа, куда пересылаются посты. Аккаунты вступают в неё сами, админов назначать не нужно. В группе должны
              быть разрешены сообщения и медиа от участников; включена история чата для новых участников; медленный
              режим выключен. Посты из публичных каналов пересылает наименее загруженный аккаунт — так ни один не
              попадает под спам-ограничения.
              <div style={{ marginTop: 6 }}>
                Переслано {reposts.done ?? 0}
                {reposts.pending ? ` · в очереди ${reposts.pending}` : ''}
                {reposts.failed ? (
                  <>
                    {' · '}
                    <button className="link-inline" onClick={onOpenErrors}>
                      ошибок {reposts.failed}
                    </button>
                  </>
                ) : null}
              </div>
              <AnimatePresence initial={false}>
                {status?.last_repost_error && (
                  <DismissibleNote key={status.last_repost_error} onDismiss={dismissError}>
                    Последняя ошибка: {status.last_repost_error}
                  </DismissibleNote>
                )}
              </AnimatePresence>
            </div>
          </div>

          <div className="section-header" style={{ marginLeft: 16 }}>Уведомления</div>
          <div className="section">
            <div className="group">
              <button className="cell" style={{ ['--inset' as string]: '57px' }} onClick={() => setNotifyOpen(true)}>
                <div className="cell-icon" style={{ background: 'var(--red)' }}>
                  <Bell size={18} />
                </div>
                <div className="cell-main">Telegram-бот</div>
                <span className="cell-value">
                  {notify?.configured
                    ? `${notify.chats.length} ${plural(notify.chats.length, 'получатель', 'получателя', 'получателей')}`
                    : 'не подключён'}
                </span>
                <ChevronRight size={18} color="var(--text-3)" />
              </button>
            </div>
            <div className="section-footer">
              {notify?.configured
                ? 'Бот пишет о каждой проблеме: отключённые аккаунты, нерабочие прокси, сломанный репост, недоступные каналы, нехватка прокси и места на сервере.'
                : 'Подключите своего бота, чтобы узнавать о проблемах сразу, а не заходя в настройки. Через этого же бота пользователи входят на сайт.'}
            </div>
          </div>

          <div className="section-header" style={{ marginLeft: 16 }}>Хранение</div>
          <div className="section">
            <div className="group">
              <div className="cell">
                <div className="cell-icon" style={{ background: 'var(--orange)' }}>
                  <Clock size={18} />
                </div>
                <div className="cell-main">
                  {settings.retention_days} {plural(settings.retention_days, 'день', 'дня', 'дней')}
                </div>
                <Stepper value={settings.retention_days} steps={RETENTION_STEPS} onChange={(v) => save({ retention_days: v })} />
              </div>
              <div className="cell" style={{ ['--inset' as string]: '57px' }}>
                <div className="cell-icon" style={{ background: 'var(--red)' }}>
                  <Eraser size={17} />
                </div>
                <div className="cell-main">Удалять старые посты, если осталось меньше {megabytes(settings.cleanup_free_mb)}</div>
                <Stepper value={settings.cleanup_free_mb} steps={CLEANUP_STEPS} onChange={(v) => save({ cleanup_free_mb: v })} />
              </div>
              {storage && <StorageCell storage={storage} />}
            </div>
            <div className="section-footer">
              Посты старше этого срока удаляются автоматически из базы данных мониторинга. Если свободного места
              на сервере меньше порога, самые старые посты удаляются раньше срока (посты за последние сутки — никогда).
              Сейчас в базе{' '}
              {status?.posts ?? 0} {plural(status?.posts ?? 0, 'пост', 'поста', 'постов')}.
            </div>
          </div>
        </>
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
        <div className="group">
          <button className="cell" style={{ ['--inset' as string]: '57px' }} onClick={onOpenPerformance}>
            <div className="cell-icon" style={{ background: 'var(--green)' }}>
              <Activity size={18} />
            </div>
            <div className="cell-main">System Performance</div>
            <ChevronRight size={18} color="var(--text-3)" />
          </button>
        </div>
        <div className="section-footer">Нагрузка на сервер: процессор, память, диск и сеть за час, сутки или неделю.</div>
      </div>

      <div className="section">
        <div className="group">
          <button className="cell" style={{ ['--inset' as string]: '57px' }} onClick={onOpenServiceAi}>
            <div className="cell-icon ai-icon">
              <Sparkles size={17} />
            </div>
            <div className="cell-main">ИИ и оплата</div>
            <ChevronRight size={18} color="var(--text-3)" />
          </button>
        </div>
        <div className="section-footer">
          OpenRouter сервиса — ИИ всех тарифов, кроме dev:service:plan: модели, наценка, расход и выручка. Запуск оплаты.
        </div>
      </div>

      <div className="section">
        <div className="group">
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
        </div>
        <div className="section-footer">Доступ к собранным данным для внешних проектов: ключ и документация.</div>
      </div>

      <div className="section">
        <div className="group">
          <button className="cell destructive" onClick={onLogout}>
            <LogOut size={18} />
            Выйти
          </button>
        </div>
      </div>

      <NotifySheet open={notifyOpen} onClose={() => setNotifyOpen(false)} onChanged={setNotify} />
      {pwa.available && <InstallSheet open={installing} onClose={() => setInstalling(false)} />}
    </Page>
  )
}
