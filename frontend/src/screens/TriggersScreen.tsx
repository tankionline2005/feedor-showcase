import { motion } from 'framer-motion'
import { Bell, BellOff, Check, ChevronLeft, Loader2, Plus, Search, Settings2, Trash2, X, Zap } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, type Trigger, type TriggerIn, type TriggersState } from '../api'
import { Avatar } from '../components/Avatar'
import { Switch } from '../components/Controls'
import { Page } from '../components/Page'
import { PostList } from '../components/PostList'
import { Segmented } from '../components/Segmented'
import { ActionSheet, Sheet } from '../components/Sheet'
import { useToast } from '../components/Toast'
import { badgeText, badgesChanged } from '../hooks/useBadges'
import { useGroups } from '../hooks/useGroups'
import { useMe } from '../hooks/useMe'
import { usePosts } from '../hooks/usePosts'
import { spring } from '../lib/motion'
import { openIntro, useFirstVisit } from '../lib/intros'

const SELECTED = 'trigger-selected'
const remembered = () => {
  try {
    return Number(localStorage.getItem(SELECTED)) || null
  } catch {
    return null
  }
}

const channelsLabel = (t: Trigger) =>
  t.channels_mode === 'only' ? 'выбранные' : t.channel_ids.length ? `кроме ${t.channel_ids.length}` : 'все'

/**
 * «Триггер-слова», from the feed: the person's words to watch for, a feed for each — without the AI,
 * at once — and the bot telling of new posts with them, if they want.
 */
export function TriggersScreen({ onBack }: { onBack: () => void }) {
  useFirstVisit('triggers')
  const toast = useToast()
  const [state, setState] = useState<TriggersState | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<number | null>(remembered)
  const [editing, setEditing] = useState<Trigger | 'new' | null>(null)
  const [input, setInput] = useState('')
  const [q, setQ] = useState('')

  const reload = useCallback(async () => {
    try {
      setState(await api.triggers())
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])

  useEffect(() => {
    reload()
  }, [reload])

  useEffect(() => {
    const t = window.setTimeout(() => setQ(input.trim()), 300)
    return () => window.clearTimeout(t)
  }, [input])

  const items = state?.items ?? []
  const current = items.find((t) => t.id === selected) ?? items[0] ?? null
  const choose = (id: number) => {
    setSelected(id)
    try {
      localStorage.setItem(SELECTED, String(id))
    } catch {
      /* the first trigger next time */
    }
  }

  const posts = usePosts({ sort: 'new', q: q || undefined, trigger: current?.id ?? -1 })

  // the feed in front of the person: its new posts are seen (its badge and the feed button's go)
  const currentId = current?.id
  const currentNew = current?.new ?? 0
  useEffect(() => {
    if (!currentId || document.visibilityState !== 'visible') return
    api.triggerSeen(currentId).then(badgesChanged, () => {})
    if (currentNew)
      setState((s) => s && { ...s, items: s.items.map((t) => (t.id === currentId ? { ...t, new: 0 } : t)) })
  }, [currentId, currentNew, posts.items.length])

  // the other triggers' new posts, as they come
  useEffect(() => {
    const timer = window.setInterval(() => document.visibilityState === 'visible' && reload(), 30_000)
    return () => window.clearInterval(timer)
  }, [reload])

  const saved = (next: TriggersState, id?: number) => {
    setState(next)
    if (id) choose(id)
    setEditing(null)
    badgesChanged()
    posts.refresh()
  }

  return (
    <Page
      onHelp={() => openIntro('triggers')}
      title="Триггер-слова"
      onRefresh={async () => {
        await reload()
        await posts.refresh()
      }}
      left={
        <motion.button className="nav-button" onClick={onBack} whileTap={{ opacity: 0.4 }} style={{ paddingLeft: 0 }}>
          <ChevronLeft size={30} strokeWidth={2.2} style={{ marginRight: -4 }} />
          Лента
        </motion.button>
      }
      right={
        <button className="nav-button" onClick={() => setEditing('new')} aria-label="Добавить триггер-слово">
          <Plus size={24} />
        </button>
      }
      titleAside={
        current && (
          <button className="triggers-setup" onClick={() => setEditing(current)} aria-label="Настроить">
            <Settings2 size={16} />
            <span>Настроить</span>
          </button>
        )
      }
      accessory={
        items.length > 0 && (
          <>
            <label className="search">
              <Search size={17} />
              <input
                placeholder="Поиск"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                enterKeyHint="search"
              />
              {input && (
                <button aria-label="Очистить" onClick={() => setInput('')} style={{ display: 'flex' }}>
                  <X size={17} />
                </button>
              )}
            </label>
            <div className="chips trigger-chips" role="tablist">
              {items.map((t) => (
                <motion.button
                  key={t.id}
                  role="tab"
                  aria-selected={t.id === current?.id}
                  className={`chip trigger-chip${t.id === current?.id ? ' active' : ''}`}
                  onClick={() => choose(t.id)}
                  whileTap={{ scale: 0.94 }}
                  style={{ isolation: 'isolate' }}
                >
                  {t.id === current?.id && (
                    <motion.div layoutId="chip-triggers" className="chip-thumb" transition={spring} />
                  )}
                  {t.notify ? <Bell size={14} /> : <BellOff size={14} />}
                  <span>{t.name}</span>
                  <small>{channelsLabel(t)}</small>
                  {t.new > 0 && t.id !== current?.id && <b className="seg-badge">{badgeText(t.new)}</b>}
                </motion.button>
              ))}
            </div>
          </>
        )
      }
    >
      {error && <div className="error-banner">{error}</div>}
      {!state && !error && (
        <div className="loader">
          <Loader2 size={22} className="spinner" />
        </div>
      )}
      {state && items.length === 0 && (
        <motion.div className="empty" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={spring}>
          <div className="empty-icon">
            <Zap size={30} strokeWidth={1.6} />
          </div>
          <div className="empty-title">Триггер-слов пока нет</div>
          <div>
            Добавьте слова — например, «взрыв», «БПЛА», «стрельба». Посты с ними из ваших каналов сразу попадут в
            отдельную ленту, а бот может присылать их вам в Telegram. Без ИИ, только по словам.
          </div>
          <button className="button small" style={{ marginTop: 16 }} onClick={() => setEditing('new')}>
            Добавить триггер-слово
          </button>
        </motion.div>
      )}
      {current && (
        <PostList
          {...posts}
          onMore={posts.loadMore}
          onShowPending={posts.showPending}
          sort="new"
          empty={
            q
              ? { title: 'Ничего не найдено', text: `Нет постов с «${q}»` }
              : {
                  title: 'Пока ни одного поста',
                  text: `Посты со словами «${current.words.join('», «')}» появятся здесь, как только выйдут`,
                }
          }
        />
      )}
      <TriggerSheet
        trigger={editing}
        state={state}
        onClose={() => setEditing(null)}
        onSaved={saved}
        onError={(m) => toast(m)}
      />
    </Page>
  )
}

const EMPTY: TriggerIn = {
  name: '',
  words: [],
  mode: 'forms',
  channels_mode: 'all',
  channel_ids: [],
  notify: false,
  notify_dm: true,
  notify_chat_id: null,
}

const MODES = [
  { value: 'forms' as const, label: 'Разные формы' },
  { value: 'exact' as const, label: 'Точное совпадение' },
]

/** A trigger made or changed: its words, how they are looked for, the bot, the channels; and deleting it. */
function TriggerSheet({
  trigger,
  state,
  onClose,
  onSaved,
  onError,
}: {
  trigger: Trigger | 'new' | null
  state: TriggersState | null
  onClose: () => void
  onSaved: (next: TriggersState, id?: number) => void
  onError: (message: string) => void
}) {
  const me = useMe()
  const open = trigger !== null
  const existing = trigger && trigger !== 'new' ? trigger : null
  const [values, setValues] = useState<TriggerIn>(EMPTY)
  const [words, setWords] = useState('')
  const [busy, setBusy] = useState(false)
  const [confirm, setConfirm] = useState(false)
  const [channels, setChannels] = useState<
    { id: number; title: string; username: string | null; avatar_url: string | null }[] | null
  >(null)
  const [findChannel, setFindChannel] = useState('')
  const groups = useGroups(open && !!state?.can_groups)?.items ?? []

  useEffect(() => {
    if (!open) return
    const v = existing ? { ...existing } : EMPTY
    setValues(v)
    setWords((existing?.words ?? []).join('\n'))
    setFindChannel('')
    api.triggerChannels().then(
      (r) => setChannels(r.items),
      () => setChannels([]),
    )
  }, [open, existing?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  const set = (patch: Partial<TriggerIn>) => setValues((v) => ({ ...v, ...patch }))
  const list = useMemo(
    () =>
      words
        .split(/[\n,;]+/)
        .map((w) => w.trim())
        .filter(Boolean),
    [words],
  )
  const watched = (id: number) =>
    values.channels_mode === 'all' ? !values.channel_ids.includes(id) : values.channel_ids.includes(id)
  const toggle = (id: number) =>
    set({
      channel_ids: values.channel_ids.includes(id)
        ? values.channel_ids.filter((x) => x !== id)
        : [...values.channel_ids, id],
    })
  const count = channels ? channels.filter((c) => watched(c.id)).length : 0
  const needle = findChannel.trim().toLowerCase().replace(/^@/, '')
  const shown = (channels ?? []).filter(
    (c) => !needle || c.title.toLowerCase().includes(needle) || (c.username ?? '').toLowerCase().includes(needle),
  )
  const valid = list.length > 0 && (values.channels_mode === 'all' || values.channel_ids.length > 0)

  const save = async () => {
    if (!valid || busy) return
    setBusy(true)
    try {
      const body = { ...values, words: list }
      if (existing) onSaved(await api.saveTrigger(existing.id, body))
      else {
        const res = await api.addTrigger(body)
        onSaved(res, res.id)
      }
    } catch (e) {
      onError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const remove = async () => {
    if (!existing) return
    try {
      onSaved(await api.deleteTrigger(existing.id))
    } catch (e) {
      onError((e as Error).message)
    }
  }

  const noTelegram = me.role === 'member' && !state?.telegram_linked

  return (
    <>
      <Sheet
        open={open}
        onClose={onClose}
        title={existing ? 'Триггер-слово' : 'Новое триггер-слово'}
        left={
          <button className="nav-button" onClick={onClose}>
            Отмена
          </button>
        }
        right={
          <button className="nav-button" style={{ fontWeight: 600 }} disabled={!valid || busy} onClick={save}>
            {busy ? <Loader2 size={18} className="spinner" /> : existing ? 'Готово' : 'Добавить'}
          </button>
        }
      >
        {existing && (
          <div className="section">
            <div className="group">
              <button className="cell destructive" onClick={() => setConfirm(true)}>
                <Trash2 size={18} />
                Удалить триггер-слово
              </button>
            </div>
          </div>
        )}
        <div className="section">
          <div className="section-header">Слова</div>
          <div className="group">
            <textarea
              className="field prompt-field trigger-words"
              rows={4}
              autoFocus={!existing}
              aria-label="Слова"
              placeholder={'БПЛА\nбеспилотник\nдрон'}
              value={words}
              onChange={(e) => setWords(e.target.value)}
            />
          </div>
          <div className="section-footer">
            Каждое слово или фраза — с новой строки или через запятую. Пост попадёт в ленту, если в нём есть любое из
            них — в тексте или на картинке.
          </div>
        </div>

        <div className="section">
          <div className="section-header">Название</div>
          <div className="group">
            <input
              className="field"
              aria-label="Название"
              maxLength={64}
              placeholder={list[0] ?? 'Как первое слово'}
              value={values.name}
              onChange={(e) => set({ name: e.target.value })}
            />
          </div>
        </div>

        <div className="section">
          <div className="section-header">Поиск</div>
          <div className="trigger-mode">
            <Segmented id="trigger-mode" options={MODES} value={values.mode} onChange={(mode) => set({ mode })} />
          </div>
          <div className="section-footer">
            {values.mode === 'forms'
              ? '«взрыв» найдёт и «взрывы», «взрыва», «взрывом» — но не «взрывной». Сокращения вроде «БПЛА» ищутся как написаны.'
              : 'Только слово как написано, без учёта заглавных букв: «взрыв» не найдёт «взрывы».'}
          </div>
        </div>

        <div className="section">
          <div className="group">
            <div className="cell">
              <div className="cell-main">Уведомления в Telegram</div>
              <Switch label="Уведомления в Telegram" on={values.notify} onChange={(notify) => set({ notify })} />
            </div>
            {values.notify && (
              <div className="cell">
                <div className="cell-main">В личные сообщения</div>
                <Switch label="В личные сообщения" on={values.notify_dm} onChange={(notify_dm) => set({ notify_dm })} />
              </div>
            )}
            {values.notify &&
              groups.map((g) => (
                <button
                  key={g.chat_id}
                  className="cell"
                  onClick={() => set({ notify_chat_id: values.notify_chat_id === g.chat_id ? null : g.chat_id })}
                >
                  <div className="cell-main">
                    В группу {g.username ? `@${g.username}` : `«${g.title ?? g.chat_id}»`}
                  </div>
                  {values.notify_chat_id === g.chat_id && <Check size={18} color="var(--accent)" />}
                </button>
              ))}
          </div>
          <div className="section-footer">
            {values.notify
              ? noTelegram && values.notify_dm
                ? 'Чтобы бот писал вам, привяжите свой Telegram в настройках.'
                : 'Бот сразу пришлёт «Триггер-слово «…»» и сам пост — о каждом новом посте с этими словами.'
              : 'Посты с этими словами будут только в ленте триггера.'}
          </div>
        </div>

        <div className="section">
          <div className="section-header trigger-channels-head">
            <span>Каналы · {channels ? `${count} из ${channels.length}` : '…'}</span>
            <span>
              <button onClick={() => set({ channels_mode: 'all', channel_ids: [] })}>Все</button>
              <button onClick={() => set({ channels_mode: 'only', channel_ids: [] })}>Убрать все</button>
            </span>
          </div>
          <div className="group">
            {channels && channels.length > 5 && (
              <label className="cell trigger-channel-search">
                <Search size={17} />
                <input
                  className="cell-input"
                  placeholder="Найти канал"
                  aria-label="Найти канал"
                  value={findChannel}
                  onChange={(e) => setFindChannel(e.target.value)}
                />
                {findChannel && (
                  <button aria-label="Очистить" onClick={() => setFindChannel('')} style={{ display: 'flex' }}>
                    <X size={16} />
                  </button>
                )}
              </label>
            )}
            {!channels ? (
              <div className="cell">
                <Loader2 size={18} className="spinner" />
              </div>
            ) : shown.length === 0 ? (
              <div className="cell">
                <div className="cell-main cell-sub">Ничего не найдено</div>
              </div>
            ) : (
              shown.map((c) => (
                <button
                  key={c.id}
                  className="cell"
                  style={{ ['--inset' as string]: '58px' }}
                  onClick={() => toggle(c.id)}
                >
                  <Avatar title={c.title} seed={c.id} src={c.avatar_url} size={30} />
                  <div className="cell-main cell-title">{c.title}</div>
                  {watched(c.id) && <Check size={18} color="var(--accent)" />}
                </button>
              ))
            )}
          </div>
          <div className="section-footer">
            {values.channels_mode === 'all'
              ? 'Все каналы проекта, кроме снятых, — и те, что вы добавите позже.'
              : 'Только отмеченные каналы. Новые каналы проекта сюда не попадают.'}
          </div>
        </div>
      </Sheet>
      <ActionSheet
        open={confirm}
        onClose={() => setConfirm(false)}
        title={existing ? `Удалить «${existing.name}»?` : undefined}
        message="Лента триггера исчезнет, бот перестанет присылать его посты."
        actions={[{ label: 'Удалить', destructive: true, onClick: remove }]}
      />
    </>
  )
}
