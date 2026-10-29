import {
  ArrowUpRight,
  ChevronRight,
  Code2,
  LifeBuoy,
  Loader2,
  LogOut,
  Megaphone,
  MonitorSmartphone,
  Plug,
  Send,
  FileText,
  ShieldCheck,
  Moon,
} from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { Avatar } from '../components/Avatar'
import { LegalSheet } from '../components/LegalSheet'
import { Page } from '../components/Page'
import { Switch } from '../components/Controls'
import { setTheme, useTheme } from '../lib/theme'
import { ActionSheet } from '../components/Sheet'
import { useToast } from '../components/Toast'
import { useMe, useReloadMe } from '../hooks/useMe'
import type { LegalDoc } from '../lib/legal'
import { CHANNEL_URL, SUPPORT_URL } from './UserSettings'

/** Settings of a project's staff member: their own Telegram (for original posts), the API, support. */
export function MemberSettings({
  onLogout,
  onOpenApi,
  onOpenMcp,
  onOpenDevices,
}: {
  onLogout: () => void
  onOpenApi: () => void
  onOpenMcp: () => void
  onOpenDevices: () => void
}) {
  const me = useMe()
  const theme = useTheme()
  const reloadMe = useReloadMe()
  const toast = useToast()
  const info = me.member
  const linked = info?.telegram ?? null
  const [link, setLink] = useState<string | null>(null)
  const [legal, setLegal] = useState<LegalDoc | null>(null)
  const [waiting, setWaiting] = useState(false)
  const [unlinking, setUnlinking] = useState(false)
  // "#/settings?flash=telegram": a post's «прислать в Telegram» sent them here to link it — show where
  const [flash, setFlash] = useState(
    () => new URLSearchParams(location.hash.split('?')[1] ?? '').get('flash') === 'telegram',
  )
  useEffect(() => {
    if (!flash) return
    history.replaceState(null, '', '#/settings')
    const el = document.getElementById('telegram-settings')
    const scroll = window.setTimeout(() => el?.scrollIntoView({ behavior: 'smooth', block: 'center' }), 250)
    const done = window.setTimeout(() => setFlash(false), 6000)
    return () => {
      window.clearTimeout(scroll)
      window.clearTimeout(done)
    }
  }, [flash])

  // the bot link is fetched ahead, so tapping it opens Telegram at once (a link opened after a
  // request would be blocked as a popup on phones)
  const fresh = useCallback(async () => {
    try {
      setLink((await api.linkTelegram()).link)
    } catch (e) {
      setLink(null)
      toast((e as Error).message)
    }
  }, [toast])

  useEffect(() => {
    if (!linked) fresh()
  }, [linked, fresh])

  // waiting for the bot: the settings notice as soon as Telegram is linked
  useEffect(() => {
    if (!waiting) return
    const timer = window.setInterval(reloadMe, 2000)
    const stop = window.setTimeout(() => setWaiting(false), 10 * 60_000)
    return () => {
      window.clearInterval(timer)
      window.clearTimeout(stop)
    }
  }, [waiting, reloadMe])

  useEffect(() => {
    if (linked && waiting) {
      setWaiting(false)
      toast('Telegram привязан')
    }
  }, [linked, waiting, toast])

  const unlink = async () => {
    try {
      await api.unlinkTelegram()
      await reloadMe()
      toast('Telegram отвязан')
    } catch (e) {
      toast((e as Error).message)
    }
  }

  return (
    <Page title="Настройки">
      <div className="hero" style={{ paddingTop: 4 }}>
        <Avatar title={me.user ?? 'Сотрудник'} seed={(me.user ?? '').length} size={72} />
        <div className="hero-title mono" style={{ marginTop: 10 }}>
          {me.user}
        </div>
        {info && <div className="hero-sub">Сотрудник проекта «{info.project_title}»</div>}
      </div>

      <div className={`section${flash ? ' flash' : ''}`} id="telegram-settings">
        <div className="group">
          {linked ? (
            <button className="cell" style={{ ['--inset' as string]: '57px' }} onClick={() => setUnlinking(true)}>
              <div className="cell-icon" style={{ background: '#008bff' }}>
                <Send size={16} />
              </div>
              <div className="cell-main">Telegram</div>
              <span className="cell-value">{linked.username ? `@${linked.username}` : linked.name}</span>
              <ChevronRight size={18} color="var(--text-3)" />
            </button>
          ) : (
            <a
              className={`cell accent${link ? '' : ' disabled'}`}
              style={{ ['--inset' as string]: '57px' }}
              href={link ?? undefined}
              target="_blank"
              rel="noreferrer"
              onClick={(e) => (link ? setWaiting(true) : e.preventDefault())}
            >
              <div className="cell-icon" style={{ background: '#008bff' }}>
                {waiting || !link ? <Loader2 size={16} className="spinner" /> : <Send size={16} />}
              </div>
              <div className="cell-main">{waiting ? 'Ждём подтверждения в боте…' : 'Привязать Telegram'}</div>
              <ArrowUpRight size={18} color="var(--text-3)" />
            </a>
          )}
        </div>
        <div className="section-footer">
          {linked
            ? 'Кнопка «Посмотреть оригинал поста» присылает посты вам в Telegram — и посты, которые написал ИИ.'
            : flash
              ? 'Чтобы бот присылал вам посты, которые написал ИИ, привяжите Telegram: откроется наш бот — нажмите «Старт».'
              : 'Откроется наш бот — нажмите «Старт». Тогда кнопка у постов будет присылать оригинал вам в Telegram; пока Telegram не привязан, она открывает пост по ссылке.'}
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
        <div className="section-footer">Ваш личный ключ к данным проекта и документация.</div>
      </div>

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

      <ActionSheet
        open={unlinking}
        onClose={() => setUnlinking(false)}
        message="Посты перестанут приходить в этот Telegram. Привязать его можно снова в любой момент."
        actions={[{ label: 'Отвязать Telegram', destructive: true, onClick: unlink }]}
      />
      <LegalSheet doc={legal} onClose={() => setLegal(null)} />
    </Page>
  )
}
