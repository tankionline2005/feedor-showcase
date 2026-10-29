import { AnimatePresence, motion } from 'framer-motion'
import { CalendarDays, ChevronRight, List, Loader2, Megaphone, Newspaper, Settings2 } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type ChatPostOut, type PublicationSettings, type PublicationsFilter } from '../api'
import { ChatPostCard } from '../components/ChatPostCard'
import { Switch } from '../components/Controls'
import { ImportanceSheet, importanceLabel } from '../components/ImportanceSheet'
import { TitleActions } from '../components/Page'
import { Choice } from '../components/ScheduleEditor'
import { Chips } from '../components/Segmented'
import { Sheet } from '../components/Sheet'
import { useToast } from '../components/Toast'
import { PublicationsCalendar } from './PublicationsCalendar'
import { CoverRows, CoversTip, preloadCoverStyle } from './CoversSheet'
import { RubricsSheet } from './RubricsSheet'
import { useCurrentProject } from '../hooks/useProjects'
import { useStored } from '../hooks/useStored'
import { plural } from '../lib/format'
import { useTip } from '../lib/intros'
import { spring } from '../lib/motion'

const FILTERS = ['all', 'scheduled', 'published', 'suggested'] as const
const FILTER_LABEL: Record<PublicationsFilter, string> = {
  all: 'Все',
  scheduled: 'Запланированные',
  published: 'Опубликованные',
  suggested: 'Предложения ИИ',
}
const EMPTY: Record<PublicationsFilter, string> = {
  all: 'Здесь появятся посты, которые опубликовали или запланировали из чатов, и посты, которые ИИ написал по новым темам.',
  scheduled: 'Запланированных постов нет.',
  published: 'Опубликованных постов пока нет.',
  suggested: 'Предложений ИИ нет. Включите «Создавать тематические посты» — ИИ будет писать пост по новым темам.',
}

/** Is the post one of the filter's (as the server picks them). */
function fits(filter: PublicationsFilter, p: ChatPostOut): boolean {
  if (filter === 'scheduled') return Boolean(p.scheduled_at)
  if (filter === 'published') return Boolean(p.published_at)
  if (filter === 'suggested') return Boolean(p.topic) && !p.published_at && !p.scheduled_at
  return true
}

/** AI → «Публикации»: what went out or is to (from anyone's chat), and the posts the AI wrote for the new topics.
 * Everyone of the project sees them; the owner and staff allowed to publish edit and publish; the settings are the
 * owner's alone. */
export function Publications({ onReloadReady }: { onReloadReady: (fn: () => Promise<unknown>) => void }) {
  const [filter, setFilter] = useStored<PublicationsFilter>('publications-filter', 'all', FILTERS)
  const [rubrics, setRubrics] = useState(false)
  // «Список» or «Календарь»
  const [view, setView] = useStored<'list' | 'calendar'>('publications-view', 'list', ['list', 'calendar'])
  const [items, setItems] = useState<ChatPostOut[] | null>(null)
  const [hasMore, setHasMore] = useState(false)
  const [settings, setSettings] = useState<PublicationSettings | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)
  const [more, setMore] = useState(false)
  const turn = useRef(0)
  const [coversTip, closeCoversTip] = useTip('covers')

  const load = useCallback(async () => {
    const mine = ++turn.current
    try {
      const page = await api.publications(filter)
      if (mine !== turn.current) return
      setItems(page.items)
      setHasMore(page.has_more)
      setSettings(page.settings)
      setError(null)
    } catch (e) {
      if (mine === turn.current) setError((e as Error).message)
    }
  }, [filter])

  useEffect(() => {
    setItems(null)
    load()
  }, [load])
  useEffect(() => onReloadReady(load), [load, onReloadReady])
  // the owner's: the settings' «Обложки» rows ready before they are opened
  const project = useCurrentProject()?.id
  const owner = settings !== null
  useEffect(() => {
    if (owner && project) preloadCoverStyle(project)
  }, [owner, project])

  const loadMore = async () => {
    if (!items) return
    setMore(true)
    try {
      const page = await api.publications(filter, items.length)
      const seen = new Set(items.map((p) => p.id))
      setItems([...items, ...page.items.filter((p) => !seen.has(p.id))])
      setHasMore(page.has_more)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setMore(false)
    }
  }

  const viewToggle = (
    <button
      className="nav-button"
      onClick={() => setView(view === 'list' ? 'calendar' : 'list')}
      title={view === 'list' ? 'Календарь' : 'Список'}
      aria-label={view === 'list' ? 'Календарь' : 'Список'}
    >
      {view === 'list' ? <CalendarDays size={19} /> : <List size={20} />}
    </button>
  )

  // «Календарь», «Рубрики», the settings: by the large title on a phone, at the end of the filters on a computer
  const actions = (
    <span className="publications-bar-actions">
      {viewToggle}
      {settings && (
        <button className="nav-button" onClick={() => setRubrics(true)} title="Рубрики" aria-label="Рубрики">
          <Newspaper size={19} />
        </button>
      )}
      {settings && (
        <button
          className="nav-button"
          onClick={() => setEditing(true)}
          title="Настройки публикаций"
          aria-label="Настройки публикаций"
        >
          <Settings2 size={19} />
        </button>
      )}
    </span>
  )

  if (view === 'calendar') {
    return (
      <>
        <div className="publications-bar calendar-bar">
          <span className="calendar-bar-title">Календарь публикаций</span>
          <TitleActions>{actions}</TitleActions>
        </div>
        <PublicationsCalendar onReloadReady={onReloadReady} />
        {settings && <RubricsSheet open={rubrics} onClose={() => setRubrics(false)} />}
        {settings && (
          <PublicationSettingsSheet
            open={editing}
            settings={settings}
            onClose={() => setEditing(false)}
            onChange={setSettings}
          />
        )}
      </>
    )
  }

  return (
    <>
      <div className="publications-bar">
        <Chips
          id="publications-filter"
          options={FILTERS.map((f) => ({ value: f, label: FILTER_LABEL[f] }))}
          value={filter}
          onChange={setFilter}
        />
        <TitleActions>{actions}</TitleActions>
      </div>

      <AnimatePresence initial={false}>
        {settings && coversTip && <CoversTip key="covers-tip" onClose={closeCoversTip} />}
      </AnimatePresence>
      {error && <div className="error-banner">{error}</div>}
      {items === null ? (
        !error && (
          <div className="loader">
            <Loader2 size={22} className="spinner" />
          </div>
        )
      ) : items.length === 0 ? (
        <motion.div className="empty" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={spring}>
          <div className="empty-icon">
            <Megaphone size={30} strokeWidth={1.6} />
          </div>
          <div>{EMPTY[filter]}</div>
        </motion.div>
      ) : (
        <div className="feed publications-list">
          {items.map((p) => (
            <ChatPostCard
              key={p.id}
              post={p}
              inList
              onChange={(next) =>
                setItems(
                  (list) =>
                    list &&
                    // published or scheduled now: gone from a filter it no longer fits
                    list
                      .filter((x) => x.id !== next.id || fits(filter, next))
                      .map((x) => (x.id === next.id ? next : x)),
                )
              }
              onDelete={() => setItems((list) => list && list.filter((x) => x.id !== p.id))}
            />
          ))}
          {hasMore && (
            <button className="more-topics" onClick={loadMore} disabled={more}>
              {more ? <Loader2 size={18} className="spinner" /> : 'Показать ещё'}
            </button>
          )}
        </div>
      )}

      {settings && (
        <PublicationSettingsSheet
          open={editing}
          settings={settings}
          onClose={() => setEditing(false)}
          onChange={setSettings}
        />
      )}
      {settings && <RubricsSheet open={rubrics} onClose={() => setRubrics(false)} />}
    </>
  )
}

/** «Публикации» → settings, the owner's alone: «Создавать тематические посты» and «Автопостинг». */
function PublicationSettingsSheet({
  open,
  settings,
  onClose,
  onChange,
}: {
  open: boolean
  settings: PublicationSettings
  onClose: () => void
  onChange: (s: PublicationSettings) => void
}) {
  const toast = useToast()
  const [prompt, setPrompt] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    if (open) setPrompt(settings.prompt)
  }, [open, settings.prompt])

  const save = async (values: Parameters<typeof api.savePublicationSettings>[0], done?: string) => {
    setBusy(true)
    try {
      onChange(await api.savePublicationSettings(values))
      if (done) toast(done)
    } catch (e) {
      toast((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const dirty = prompt.trim() !== settings.prompt.trim()
  const autoposting = settings.autopost !== 'off'
  // «Минимальная важность» open in its sheet: of the posts, or of autoposting
  const [importance, setImportance] = useState(false)

  return (
    <Sheet
      open={open}
      onClose={onClose}
      title="Публикации"
      right={
        <button className="nav-button" style={{ fontWeight: 600 }} onClick={onClose}>
          Готово
        </button>
      }
    >
      <div className="section">
        <div className="group">
          <div className="cell">
            <div className="cell-main">Создавать тематические посты</div>
            <Switch
              label="Создавать тематические посты"
              on={settings.topic_posts}
              onChange={(v) => save({ topic_posts: v })}
            />
          </div>
          {settings.topic_posts && (
            <Choice
              label="Постов за один сбор тем"
              value={settings.posts_limit}
              options={settings.limits}
              labels={Object.fromEntries(settings.limits.map((n) => [n, `не больше ${n}`]))}
              onChange={(posts_limit) => save({ posts_limit })}
            />
          )}
          {settings.topic_posts && (
            <button className="cell" onClick={() => setImportance(true)}>
              <div className="cell-main">Минимальная важность</div>
              <span className="cell-value">{importanceLabel(settings.min_importance_posts)}</span>
              <ChevronRight size={18} color="var(--text-3)" />
            </button>
          )}
        </div>
        <div className="section-footer">
          После каждого сбора тем ИИ напишет пост по каждой из самых крупных новых тем — так же, как в чате: в стиле
          вашего канала и с фото из источников. Посты попадают сюда, в «Предложения ИИ». Если в теме появится крупная
          новость, ИИ перепишет ещё не вышедший пост по ней, а к уже опубликованному напишет продолжение.
        </div>
      </div>

      {settings.topic_posts && (
        <div className="section">
          <div className="section-header">Пожелания к постам</div>
          <div className="group">
            <textarea
              className="field prompt-field"
              rows={5}
              spellCheck={false}
              aria-label="Пожелания к тематическим постам"
              placeholder="Например: коротко, без эмодзи, в конце — @название_канала"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
            />
          </div>
          <div className="section-footer">
            Необязательно: ИИ и так учится вашему стилю по тому, как в проекте правят его посты.
          </div>
          {dirty && (
            <div className="prompt-actions">
              <button className="button small" disabled={busy} onClick={() => save({ prompt }, 'Сохранено')}>
                Сохранить
              </button>
            </div>
          )}
        </div>
      )}

      <div className="section">
        <div className="section-header">Обложки</div>
        <div className="group">
          <CoverRows active={open} modeLabel="К автопостам и рубрикам" />
        </div>
      </div>

      {settings.topic_posts && (
        <div className="section">
          <div className="group">
            <div className="cell">
              <div className="cell-main">Автопостинг</div>
              <Switch label="Автопостинг" on={autoposting} onChange={(v) => save({ autopost: v ? 'now' : 'off' })} />
            </div>
            {autoposting && (
              <Choice
                label="Когда публиковать"
                value={settings.autopost === 'now' ? 0 : settings.autopost_hours}
                options={[0, ...settings.hours]}
                labels={Object.fromEntries([
                  [0, 'Моментально'],
                  ...settings.hours.map((h) => [h, `Отложить на ${h} ${plural(h, 'час', 'часа', 'часов')}`]),
                ])}
                onChange={(hours) => save(hours ? { autopost: 'delay', autopost_hours: hours } : { autopost: 'now' })}
              />
            )}
            {autoposting && (
              <Choice
                label="Время между публикациями"
                value={settings.autopost_gap_min}
                options={settings.gaps}
                labels={Object.fromEntries(
                  settings.gaps.map((m) => [m, `${m} ${plural(m, 'минута', 'минуты', 'минут')}`]),
                )}
                onChange={(autopost_gap_min) => save({ autopost_gap_min })}
              />
            )}
            {autoposting && (
              <Choice
                label="Максимум в сутки"
                value={settings.daily_limit}
                options={settings.daily_limits}
                labels={Object.fromEntries(
                  settings.daily_limits.map((n) => [
                    n,
                    n ? `${n} ${plural(n, 'пост', 'поста', 'постов')}` : 'Без ограничений',
                  ]),
                )}
                onChange={(daily_limit) => save({ daily_limit })}
              />
            )}
            {autoposting && (
              <Choice
                label="Минимум источников"
                value={settings.autopost_min_sources}
                options={settings.sources_options}
                labels={Object.fromEntries(
                  settings.sources_options.map((n) => [
                    n,
                    n > 1 ? `${n} ${plural(n, 'канал', 'канала', 'каналов')}` : 'Не важно',
                  ]),
                )}
                onChange={(autopost_min_sources) => save({ autopost_min_sources })}
              />
            )}
          </div>
          <div className="section-footer">
            Готовый тематический пост бот сам опубликует в канале проекта — сразу или через выбранное время: пока он
            ждёт, его можно поправить или отменить. Если постов несколько, они выходят по очереди, с выбранным
            промежутком.
          </div>
        </div>
      )}


      <ImportanceSheet
        open={importance}
        title="Минимальная важность"
        what="тематические посты"
        value={settings.min_importance_posts}
        onClose={() => setImportance(false)}
        onChange={(n) => save({ min_importance_posts: n })}
      />
    </Sheet>
  )
}
