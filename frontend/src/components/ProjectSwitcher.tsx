import { AnimatePresence, motion } from 'framer-motion'
import { Bot, Check, ChevronDown, Loader2, Plus } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { api, type CandidateChannel, type Project } from '../api'
import { useWide } from '../hooks/useMedia'
import { useMe, useReloadMe } from '../hooks/useMe'
import { reloadProjects, setProjects, useProjects, useSwitchProject } from '../hooks/useProjects'
import { addBotLink } from '../lib/bot'
import { planTerm } from '../lib/format'
import { spring } from '../lib/motion'
import { Avatar } from './Avatar'
import { Sheet } from './Sheet'
import { useToast } from './Toast'

/** What a project row says under its title: its plan, or that it has none. */
function planLine(p: Project): string {
  if (p.plan) {
    const term = planTerm(p.plan.expires_at)
    return term ? `${p.plan.title} · ${term}` : p.plan.title
  }
  return p.expired ? 'Тариф закончился' : 'Без тарифа'
}

function ProjectAvatar({ project, size }: { project: Project; size: number }) {
  return <Avatar title={project.title} seed={project.id} src={project.avatar_url} size={size} />
}

/**
 * The current project's avatar (a "+" until it has a channel). Opens the list of projects; with a
 * single project and no channel yet, goes straight to choosing the channel.
 */
export function ProjectButton({ size = 40 }: { size?: number }) {
  const me = useMe()
  const projects = useProjects()
  const wide = useWide()
  const [menu, setMenu] = useState(false)
  const [picking, setPicking] = useState<number | null>(null)
  const current = projects?.items.find((p) => p.current)
  if (me.role !== 'user' || !projects || !current) return null

  const noChannel = !current.channel
  const open = () => (noChannel && projects.items.length === 1 ? setPicking(current.id) : setMenu(!menu))

  return (
    <div className="project-switch">
      <motion.button
        className={`project-button${noChannel ? ' no-channel' : ''}`}
        style={{ width: size, height: size }}
        onClick={open}
        whileTap={{ scale: 0.92 }}
        aria-label={noChannel ? 'Выбрать канал проекта' : `Проект: ${current.title}`}
        aria-expanded={menu}
        title={current.title}
      >
        <AnimatePresence mode="popLayout" initial={false}>
          <motion.span
            key={noChannel ? 'plus' : `p${current.id}`}
            className="project-button-face"
            initial={{ opacity: 0, scale: 0.6 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.6 }}
            transition={spring}
          >
            {noChannel ? (
              <Plus size={Math.round(size * 0.55)} strokeWidth={1.8} />
            ) : (
              <ProjectAvatar project={current} size={size} />
            )}
          </motion.span>
        </AnimatePresence>
        <span className="project-button-badge">
          <ChevronDown size={11} strokeWidth={3} />
        </span>
      </motion.button>

      {wide ? (
        <AnimatePresence>
          {menu && (
            <Popover onClose={() => setMenu(false)}>
              <ProjectList onDone={() => setMenu(false)} onPickChannel={(id) => setPicking(id)} />
            </Popover>
          )}
        </AnimatePresence>
      ) : (
        <Sheet open={menu} onClose={() => setMenu(false)} title="Проекты">
          <ProjectList onDone={() => setMenu(false)} onPickChannel={(id) => setPicking(id)} />
        </Sheet>
      )}
      <ChannelPicker projectId={picking} onClose={() => setPicking(null)} />
    </div>
  )
}

function Popover({ onClose, children }: { onClose: () => void; children: React.ReactNode }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const onDown = (e: PointerEvent) => {
      const box = ref.current?.parentElement
      if (box && !box.contains(e.target as Node)) onClose()
    }
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    document.addEventListener('pointerdown', onDown)
    window.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('pointerdown', onDown)
      window.removeEventListener('keydown', onKey)
    }
  }, [onClose])
  return (
    <motion.div
      ref={ref}
      className="project-popover"
      role="dialog"
      aria-label="Проекты"
      initial={{ opacity: 0, scale: 0.94, y: -6 }}
      animate={{ opacity: 1, scale: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.96, y: -4, transition: { duration: 0.12 } }}
      transition={spring}
    >
      {children}
    </motion.div>
  )
}

/** Every project of the user, the current one checked, and a new one at the end. */
function ProjectList({ onDone, onPickChannel }: { onDone: () => void; onPickChannel: (projectId: number) => void }) {
  const projects = useProjects()
  const switchTo = useSwitchProject()
  const toast = useToast()
  const [busy, setBusy] = useState<number | 'new' | null>(null)
  const reloadMe = useReloadMe()
  if (!projects) return null

  const choose = async (p: Project) => {
    if (p.current) return onDone()
    setBusy(p.id)
    try {
      await switchTo(p.id)
      onDone()
    } catch (e) {
      toast((e as Error).message)
    } finally {
      setBusy(null)
    }
  }

  const create = async () => {
    setBusy('new')
    try {
      const { id } = await api.createProject()
      await Promise.all([reloadMe(), reloadProjects()])
      onDone()
      onPickChannel(id) // a new project starts by choosing its channel
    } catch (e) {
      toast((e as Error).message)
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="project-list">
      {projects.items.map((p) => (
        <button key={p.id} className={`project-row${p.current ? ' current' : ''}`} onClick={() => choose(p)}>
          <ProjectAvatar project={p} size={38} />
          <div className="project-row-main">
            <div className="project-row-title">{p.title}</div>
            <div className={`project-row-sub${p.plan ? '' : ' warn'}`}>{planLine(p)}</div>
          </div>
          {busy === p.id ? (
            <Loader2 size={18} className="spinner" />
          ) : (
            p.current && <Check size={19} strokeWidth={2.6} color="var(--accent)" />
          )}
        </button>
      ))}
      <button className="project-row new" onClick={create} disabled={!projects.can_create || busy !== null}>
        <span className="project-row-plus">
          {busy === 'new' ? <Loader2 size={18} className="spinner" /> : <Plus size={20} />}
        </span>
        <div className="project-row-main">
          <div className="project-row-title">Новый проект</div>
          {projects.create_hint && <div className="project-row-sub">{projects.create_hint}</div>}
        </div>
      </button>
    </div>
  )
}

/**
 * Choosing the project's channel: any channel by its link, or one of the channels the bot was added
 * to, as a shortcut (that list refreshes by itself).
 */
export function ChannelPicker({ projectId, onClose }: { projectId: number | null; onClose: () => void }) {
  return (
    <Sheet
      open={projectId !== null}
      onClose={onClose}
      title="Канал проекта"
      right={
        <button className="nav-button" style={{ fontWeight: 600 }} onClick={onClose}>
          Готово
        </button>
      }
    >
      {projectId !== null && <ChannelChoice projectId={projectId} onChosen={onClose} />}
    </Sheet>
  )
}

/** The link field, «Добавить бота в канал» and the channels with the bot: in the sheet above, and on a new
 * owner's first screen (ChannelSetup). */
export function ChannelChoice({ projectId, onChosen }: { projectId: number; onChosen: () => void }) {
  const [found, setFound] = useState<{ items: CandidateChannel[]; bot_username: string | null } | null>(null)
  const [link, setLink] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState<number | 'link' | null>(null)
  const reloadMe = useReloadMe()
  const toast = useToast()

  useEffect(() => {
    let alive = true
    // the channels the bot was added to: a shortcut, refreshed while shown — one the bot was just added to
    // shows up by itself
    const load = () =>
      api.projectChannels().then(
        (res) => alive && setFound(res),
        () => alive && setFound(null),
      )
    load()
    const timer = window.setInterval(load, 4000)
    return () => {
      alive = false
      window.clearInterval(timer)
    }
  }, [])

  const save = async (target: { link: string } | { chat_id: number }, key: number | 'link') => {
    setSaving(key)
    setError(null)
    try {
      setProjects(await api.setProjectChannel(projectId, target))
      await reloadMe()
      toast('Канал проекта выбран')
      onChosen()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSaving(null)
    }
  }

  const choose = (c: CandidateChannel) =>
    c.project?.id === projectId ? onChosen() : save({ chat_id: c.chat_id }, c.chat_id)
  const withBot = found?.items ?? []
  const bot = found?.bot_username

  return (
    <>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          if (link.trim()) save({ link: link.trim() }, 'link')
        }}
      >
        <div className="section">
          <div className="group">
            <div className="cell" style={{ paddingRight: 8 }}>
              <input
                className="cell-input"
                style={{ flex: 1, minWidth: 0, textAlign: 'left' }}
                placeholder="t.me/channel, @channel или t.me/+…"
                aria-label="Ссылка на канал"
                autoCapitalize="none"
                autoComplete="off"
                spellCheck={false}
                value={link}
                onChange={(e) => setLink(e.target.value)}
              />
              <button className="nav-button" style={{ fontWeight: 600 }} disabled={!link.trim() || saving !== null}>
                {saving === 'link' ? <Loader2 size={18} className="spinner" /> : 'Выбрать'}
              </button>
            </div>
          </div>
          <div className="section-footer" style={error ? { color: 'var(--red)' } : undefined}>
            {error ??
              'Подойдёт любой канал — ссылка, @username или ссылка-приглашение закрытого.'}
          </div>
        </div>
      </form>

      {bot && (
        <div className="section">
          <div className="group">
            <a className="cell" href={addBotLink(bot)} target="_blank" rel="noopener">
              <Bot size={20} color="var(--accent)" />
              <div className="cell-main">
                <div className="cell-title" style={{ color: 'var(--accent)' }}>
                  Добавить бота в канал
                </div>
              </div>
            </a>
          </div>
          <div className="section-footer">
            Откроется Telegram: выберите свой канал, и @{bot} станет в нём администратором — сможет публиковать
            посты, которые вы одобрите, и править или удалять их. Канал сразу появится ниже. Без бота канал тоже
            можно выбрать, но публиковать в него придётся самим.
          </div>
        </div>
      )}

      {withBot.length > 0 && (
        <div className="section">
          <div className="section-header">Каналы с ботом</div>
          <div className="group">
            <AnimatePresence initial={false}>
              {withBot.map((c) => {
                const here = c.project?.id === projectId
                const elsewhere = c.project !== null && !here
                return (
                  <motion.button
                    key={c.chat_id}
                    className="cell"
                    style={{ ['--inset' as string]: '62px' }}
                    disabled={elsewhere || saving !== null}
                    onClick={() => choose(c)}
                    layout="position"
                    initial={{ opacity: 0, height: 0 }}
                    animate={{ opacity: 1, height: 'auto' }}
                    exit={{ opacity: 0, height: 0 }}
                    transition={spring}
                  >
                    <Avatar title={c.title || 'Канал'} seed={Math.abs(c.chat_id)} size={36} />
                    <div className="cell-main">
                      <div className="cell-title">{c.title || 'Канал'}</div>
                      <div className="cell-sub">
                        {elsewhere
                          ? `Уже в проекте «${c.project!.title}»`
                          : c.username
                            ? `@${c.username}`
                            : 'закрытый канал'}
                      </div>
                    </div>
                    {saving === c.chat_id ? (
                      <Loader2 size={18} className="spinner" />
                    ) : (
                      here && <Check size={19} strokeWidth={2.6} color="var(--accent)" />
                    )}
                  </motion.button>
                )
              })}
            </AnimatePresence>
          </div>
          <div className="section-footer">
            Каналы, куда вы добавили {bot ? `@${bot}` : 'нашего бота'} или где вы администратор, — выбор в одно
            касание.
          </div>
        </div>
      )}
    </>
  )
}
