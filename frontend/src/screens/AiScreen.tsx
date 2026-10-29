import { AnimatePresence, motion } from 'framer-motion'
import { Loader2, Newspaper, Settings2 } from 'lucide-react'
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { api, type Topic, type TopicSettings } from '../api'
import { Avatar } from '../components/Avatar'
import { Switch } from '../components/Controls'
import { ErrorAlert, type ErrorInfo } from '../components/ErrorAlert'
import { Page, TitleActions } from '../components/Page'
import { RunButton, useAiRun } from '../components/RunButton'
import { ScheduleEditor, nextRunLabel } from '../components/ScheduleEditor'
import { Segmented } from '../components/Segmented'
import { Sheet } from '../components/Sheet'
import { useToast } from '../components/Toast'
import { badgeText, badgesChanged, useBadges, useSeen } from '../hooks/useBadges'
import { useMe } from '../hooks/useMe'
import { useStored } from '../hooks/useStored'
import { compact, plural, relative } from '../lib/format'
import { spring } from '../lib/motion'
import { topicsCache } from '../lib/topicsCache'
import { whoDid } from '../lib/who'
import { ChatList } from './ChatList'
import { Publications } from './Publications'
import { openIntro, useFirstVisit } from '../lib/intros'

const SECTIONS = ['chat', 'topics', 'posts'] as const
type Section = (typeof SECTIONS)[number]

/** The «AI» tab: chat, topics and publications of the current project. */
export function AiScreen({
  onOpenTopic,
  onOpenChat,
}: {
  onOpenTopic: (id: number) => void
  onOpenChat: (id: number) => void
}) {
  const me = useMe()
  const chat = me.role === 'user' || !!me.member?.ai_chat
  const [stored, setSection] = useStored<Section>('ai-section', 'topics', SECTIONS)
  const section = stored === 'chat' && !chat ? 'topics' : stored
  const [reload, setReload] = useState<() => Promise<unknown>>(() => async () => {})

  // the section in front of the person: its new things are seen; the others keep their blue badges
  const badges = useBadges()
  useSeen('topics', section === 'topics')
  useSeen('posts', section === 'posts')
  // each section's welcome cards on its first visit
  const intro = section === 'posts' ? 'publications' : section
  useFirstVisit(intro)
  const labelled = (label: string, n: number) => (
    <>
      {label}
      {n > 0 && <span className="seg-badge">{badgeText(n)}</span>}
    </>
  )
  const options = [
    ...(chat ? [{ value: 'chat' as const, label: labelled('Чат', badges.chats) }] : []),
    { value: 'topics' as const, label: labelled('Темы', badges.topics) },
    { value: 'posts' as const, label: labelled('Публикации', badges.posts) },
  ]

  return (
    <Page
      title="AI"
      titleActions
      onHelp={() => openIntro(intro)}
      onRefresh={() => reload()}
      accessory={<Segmented id="ai-section" options={options} value={section} onChange={setSection} />}
    >
      {section === 'topics' ? (
        <Topics onOpen={onOpenTopic} onReloadReady={(fn) => setReload(() => fn)} />
      ) : section === 'posts' ? (
        <Publications onReloadReady={(fn) => setReload(() => fn)} />
      ) : (
        <ChatList onOpen={onOpenChat} onReloadReady={(fn) => setReload(() => fn)} />
      )}
    </Page>
  )
}

const PAGE = 20
const MOST = 200 // topics the list asks for at once: those «Показать ещё» loaded, again

function Topics({
  onOpen,
  onReloadReady,
}: {
  onOpen: (id: number) => void
  onReloadReady: (fn: () => Promise<void>) => void
}) {
  const toast = useToast()
  const me = useMe()
  const project = me.project?.id ?? null
  // back from a topic: the list as it was, at once, then asked again
  const cached = topicsCache.project === project ? topicsCache : null
  const [items, setItems] = useState<Topic[] | null>(cached?.items ?? null)
  const [hasMore, setHasMore] = useState(cached?.hasMore ?? false)
  const [settings, setSettings] = useState<TopicSettings | null>(cached?.settings ?? null)
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)
  const [hiddenOpen, setHiddenOpen] = useState(false)
  const [failure, setFailure] = useState<ErrorInfo | null>(null)
  const shown = useRef(items?.length ?? 0)
  shown.current = items?.length ?? 0

  useEffect(() => {
    Object.assign(topicsCache, { project, items, hasMore, settings })
  }, [project, items, hasMore, settings])

  // where the topic was opened from: there again, the list being already in place
  useLayoutEffect(() => {
    const y = topicsCache.scrollY
    topicsCache.scrollY = null
    if (cached?.items && y !== null) {
      window.scrollTo(0, y)
      requestAnimationFrame(() => window.scrollTo(0, y))
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const reload = useCallback(async () => {
    try {
      // as many as are shown: the pages «Показать ещё» loaded stay, and nothing jumps
      const [list, s] = await Promise.all([
        api.topics(0, Math.min(MOST, Math.max(PAGE, shown.current))),
        api.topicSettings(),
      ])
      setItems(list.items)
      setHasMore(list.has_more)
      setSettings(s)
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])

  useEffect(() => {
    onReloadReady(reload)
    reload()
    const timer = window.setInterval(reload, 60_000)
    return () => window.clearInterval(timer)
  }, [reload]) // eslint-disable-line react-hooks/exhaustive-deps

  const more = async () => {
    if (!items) return
    try {
      const res = await api.topics(items.length, PAGE)
      setItems([...items, ...res.items.filter((s) => !items.some((x) => x.id === s.id))])
      setHasMore(res.has_more)
    } catch (e) {
      toast((e as Error).message)
    }
  }

  const run = useAiRun({
    run: settings?.run,
    fetchRun: api.topicsRun,
    startRun: api.runTopics,
    onFailed: setFailure,
    onEnded: (r) => {
      reload()
      badgesChanged()
      if (r.error) setFailure(r.error)
      else {
        const fresh = r.result?.new ?? 0
        const updated = r.result?.updated ?? 0
        toast(
          fresh
            ? `Новых тем: ${fresh}` + (updated ? `, обновлено: ${updated}` : '')
            : updated
              ? `Обновлено тем: ${updated}`
              : 'Новых тем нет',
        )
      }
    },
  })

  const notify = async (on: boolean) => {
    try {
      setSettings(await api.topicNotify(on))
      toast(on ? 'Бот будет присылать новые и обновлённые темы' : 'Бот больше не присылает темы')
    } catch (e) {
      toast((e as Error).message)
    }
  }

  if (error) return <div className="error-banner">{error}</div>
  if (!items || !settings) {
    return (
      <div className="loader">
        <Loader2 size={22} className="spinner" />
      </div>
    )
  }

  const status = run.going
    ? 'ИИ собирает темы…'
    : !settings.linked
      ? me.ai_own
        ? 'OpenRouter.ai не подключён — новые темы не собираются'
        : 'ИИ временно недоступен — новые темы не собираются'
      : settings.paused
        ? settings.paused
        : !settings.enabled
          ? 'Темы выключены'
          : // a schedule of a few rules is too long for this line: its words are in the settings
            `ИИ собирает темы${
              !settings.schedule.rules.length
                ? `, ${settings.schedule_summary}`
                : settings.schedule.rules.length === 1 && !settings.schedule.posts
                  ? ` ${settings.schedule_summary}`
                  : ' по расписанию'
            }` +
            (settings.next_run_at
              ? ` · следующий раз ${nextRunLabel(settings.next_run_at)}`
              : settings.waiting_posts !== null && settings.schedule.posts
                ? ` · сейчас ${settings.waiting_posts} из ${settings.schedule.posts.count}`
                : settings.last_run_at
                  ? ` · последний раз ${relative(settings.last_run_at)}`
                  : '')

  return (
    <>
      <div className="topics-bar">
        <span className={settings.linked && !settings.paused && settings.enabled ? '' : 'warn'}>{status}</span>
        {(settings.editable || settings.can_run) && (
          <TitleActions>
            <span className="topics-bar-actions">
              {settings.can_run && settings.linked && settings.enabled && (
                <RunButton label="Собрать сейчас" going={run.going} percent={run.percent} onClick={run.start} />
              )}
              {settings.editable && (
                <button
                  className="nav-button"
                  onClick={() => setEditing(true)}
                  title="Настройки тем"
                  aria-label="Настройки тем"
                >
                  <Settings2 size={19} />
                </button>
              )}
            </span>
          </TitleActions>
        )}
      </div>

      {items.length === 0 ? (
        <motion.div className="empty" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={spring}>
          <div className="empty-icon">
            <Newspaper size={30} strokeWidth={1.6} />
          </div>
          {settings.enabled ? (
            <>
              <div className="empty-title">Тем пока нет</div>
              <div>
                ИИ соберёт их из новых постов проекта в ближайший запуск: события, о которых пишут каналы, — в одном
                месте
              </div>
            </>
          ) : (
            <>
              <div className="empty-title">Темы выключены</div>
              <div>
                ИИ соберёт из постов каналов события, о которых пишут, — в одном месте.{' '}
                {settings.editable
                  ? 'Включите сбор тем в настройках тем — пока они выключены, ИИ ничего не тратит.'
                  : 'Сбор тем включает владелец проекта.'}
              </div>
              {settings.editable && settings.linked && (
                <button className="button small" style={{ marginTop: 16 }} onClick={() => setEditing(true)}>
                  Настройки тем
                </button>
              )}
            </>
          )}
        </motion.div>
      ) : (
        <div className="topic-list">
          <AnimatePresence initial={false}>
            {items.map((s) => (
              <TopicCard
                key={s.id}
                topic={s}
                onOpen={() => {
                  topicsCache.scrollY = window.scrollY
                  onOpen(s.id)
                }}
              />
            ))}
          </AnimatePresence>
          {hasMore && (
            <button className="more-topics" onClick={more}>
              Показать ещё
            </button>
          )}
        </div>
      )}

      <div className="section" style={{ marginTop: 24 }}>
        <div className="group">
          <div className="cell">
            <div className="cell-main">Присылать темы в Telegram</div>
            <Switch label="Присылать темы в Telegram" on={settings.notify} onChange={notify} />
          </div>
        </div>
        <div className="section-footer">
          {settings.telegram_linked
            ? 'После каждого запуска бот пришлёт вам одно сообщение со всеми новыми и обновлёнными темами и ссылками на них.'
            : 'Чтобы бот присылал темы, сначала привяжите свой Telegram в настройках.'}
        </div>
      </div>

      <button className="hidden-topics-link" onClick={() => setHiddenOpen(true)}>
        Скрытые темы
      </button>
      <HiddenTopicsSheet
        open={hiddenOpen}
        canTeach={settings.can_teach}
        onClose={() => setHiddenOpen(false)}
        onReturned={reload}
      />

      <ErrorAlert error={failure} title="Не удалось собрать темы" onClose={() => setFailure(null)} />
      {settings.editable && (
        <TopicSettingsSheet
          open={editing}
          settings={settings}
          onClose={() => setEditing(false)}
          onChange={setSettings}
        />
      )}
    </>
  )
}

/** A topic in the list: a link, so the wheel or Ctrl+click opens it in a new tab of the browser. */
function TopicCard({ topic, onOpen }: { topic: Topic; onOpen: () => void }) {
  const shown = topic.media.slice(0, 4)
  const extra = topic.media.length - shown.length
  return (
    <motion.a
      className="topic-card"
      href={`#/ai/topic/${topic.id}`}
      onClick={(e) => {
        if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return // a new tab or window
        e.preventDefault()
        onOpen()
      }}
      layout="position"
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0 }}
      transition={spring}
      whileTap={{ scale: 0.985 }}
    >
      {shown.length > 0 && (
        <div className={`topic-media count-${shown.length}`}>
          {shown.map((m, i) => (
            <span key={m.url} className="topic-media-tile">
              <img src={m.url} alt="" loading="lazy" />
              {i === shown.length - 1 && extra > 0 && <span className="topic-media-more">+{extra}</span>}
            </span>
          ))}
        </div>
      )}
      <div className="topic-body">
        <div className="topic-top">
          {topic.updated && <span className="topic-badge">Обновлена</span>}
          {topic.covered && <span className="topic-covered">Есть в вашем канале</span>}
          {topic.followup && <span className="topic-followup">Есть продолжение</span>}
          <span>{relative(topic.updated_at)}</span>
        </div>
        <div className="topic-title">{topic.title}</div>
        <div className="topic-summary">{topic.summary}</div>
        <div className="topic-foot">
          <span className="topic-avatars">
            {topic.channels.slice(0, 3).map((c) => (
              <Avatar key={c.id} title={c.title} seed={c.id} src={c.avatar_url} size={20} />
            ))}
          </span>
          {topic.sources} {plural(topic.sources, 'источник', 'источника', 'источников')} · {compact(topic.views)}{' '}
          {plural(topic.views, 'просмотр', 'просмотра', 'просмотров')}
        </div>
      </div>
    </motion.a>
  )
}

/** «Скрытые темы»: what was hidden and why; «Вернуть» puts a topic back, and the AI forgets it as not wanted. */
function HiddenTopicsSheet({
  open,
  canTeach,
  onClose,
  onReturned,
}: {
  open: boolean
  /** «Вернуть»: for those who may teach the AI */
  canTeach: boolean
  onClose: () => void
  onReturned: () => void
}) {
  const toast = useToast()
  const me = useMe()
  const [items, setItems] = useState<Topic[] | null>(null)
  const [busy, setBusy] = useState<number | null>(null)

  useEffect(() => {
    if (!open) return
    setItems(null)
    api.topics(0, 100, true).then(
      (res) => setItems(res.items),
      (e) => {
        toast((e as Error).message)
        setItems([])
      },
    )
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps

  const unhide = async (id: number) => {
    setBusy(id)
    try {
      await api.unhideTopic(id)
      setItems((list) => list?.filter((t) => t.id !== id) ?? null)
      toast('Тема вернулась в список')
      onReturned()
    } catch (e) {
      toast((e as Error).message)
    } finally {
      setBusy(null)
    }
  }

  return (
    <Sheet
      open={open}
      onClose={onClose}
      title="Скрытые темы"
      right={
        <button className="nav-button" style={{ fontWeight: 600 }} onClick={onClose}>
          Готово
        </button>
      }
    >
      {!items ? (
        <div className="loader">
          <Loader2 size={22} className="spinner" />
        </div>
      ) : items.length === 0 ? (
        <div className="empty" style={{ paddingTop: 24 }}>
          <div className="empty-title">Скрытых тем нет</div>
          <div>Скрыть тему можно на её странице — ИИ учтёт, почему она вам не подошла.</div>
        </div>
      ) : (
        <div className="section">
          <div className="group">
            <AnimatePresence initial={false}>
              {items.map((t) => (
                <motion.div
                  key={t.id}
                  className="cell hidden-topic"
                  layout="position"
                  exit={{ opacity: 0, transition: { duration: 0.15 } }}
                >
                  <div className="cell-main">
                    <a className="hidden-topic-title" href={`#/ai/topic/${t.id}`} onClick={onClose}>
                      {t.title}
                    </a>
                    <div className="hidden-topic-reason">«{t.hide_reason}»</div>
                    {t.hidden_at && (
                      <div className="cell-sub">
                        скрыта {relative(t.hidden_at)} · {whoDid(t.hidden_by, me)}
                      </div>
                    )}
                  </div>
                  {canTeach && (
                    <button className="button small plain" disabled={busy === t.id} onClick={() => unhide(t.id)}>
                      Вернуть
                    </button>
                  )}
                </motion.div>
              ))}
            </AnimatePresence>
          </div>
          <div className="section-footer">
            ИИ не собирает темы, похожие на скрытые, — по причинам, которые вы указали. Вернёте тему — ИИ перестанет
            считать её неподходящей.
          </div>
        </div>
      )}
    </Sheet>
  )
}

function TopicSettingsSheet({
  open,
  settings,
  onClose,
  onChange,
}: {
  open: boolean
  settings: TopicSettings
  onClose: () => void
  onChange: (s: TopicSettings) => void
}) {
  const toast = useToast()
  const [prompt, setPrompt] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    if (open) setPrompt(settings.prompt ?? '')
  }, [open, settings.prompt])

  const save = async (values: Parameters<typeof api.saveTopicSettings>[0], done?: string) => {
    setBusy(true)
    try {
      onChange(await api.saveTopicSettings(values))
      if (done) toast(done)
    } catch (e) {
      toast((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const dirty = prompt.trim() !== (settings.prompt ?? '').trim()
  const isDefault = prompt.trim() === (settings.default_prompt ?? '').trim()

  return (
    <Sheet
      open={open}
      onClose={onClose}
      title="Темы"
      right={
        <button className="nav-button" style={{ fontWeight: 600 }} onClick={onClose}>
          Готово
        </button>
      }
    >
      <div className="section">
        <div className="group">
          <div className="cell">
            <div className="cell-main">Собирать темы</div>
            <Switch label="Собирать темы" on={settings.enabled} onChange={(v) => save({ enabled: v })} />
          </div>
        </div>
        <div className="section-footer">
          ИИ изучает новые посты и собирает из них темы. Посты, которые скрыла умная лента проекта, в темы не попадают —
          даже если сейчас она выключена. Каждый новый пост вашего канала ИИ сверяет с темами и ставит на подходящих
          пометку «Есть в вашем канале».
        </div>
      </div>
      <ScheduleEditor
        info={settings}
        waiting={settings.waiting_posts}
        postsWord="постов в каналах"
        onSave={async (schedule) => onChange(await api.saveTopicSettings({ schedule }))}
      />
      <div className="section">
        <div className="section-header">Что для вас тема</div>
        <div className="group">
          <textarea
            className="field prompt-field"
            rows={7}
            spellCheck={false}
            aria-label="Промпт тем"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
          />
        </div>
        <div className="section-footer">Расскажите о том, какие новости подойдут для вашего проекта.</div>
        <div className="prompt-actions">
          {dirty && (
            <button
              className="button small"
              disabled={busy || !prompt.trim()}
              onClick={() => save({ prompt }, 'Промпт сохранён')}
            >
              Сохранить
            </button>
          )}
          {!isDefault && (
            <button
              className="button small plain"
              disabled={busy}
              onClick={() => save({ reset_prompt: true }, 'Промпт по умолчанию')}
            >
              Вернуть по умолчанию
            </button>
          )}
        </div>
      </div>
    </Sheet>
  )
}
