import { AnimatePresence, motion } from 'framer-motion'
import { Megaphone, Pause, Play, Plus, RotateCw, Search, Trash2, X } from 'lucide-react'
import { useMemo, useState } from 'react'
import { api, type Channel } from '../api'
import { Avatar } from '../components/Avatar'
import { Page } from '../components/Page'
import { Chips, Segmented } from '../components/Segmented'
import { ActionSheet } from '../components/Sheet'
import { SwipeRow, type SwipeAction } from '../components/SwipeRow'
import { useToast } from '../components/Toast'
import { useChannels } from '../hooks/useChannels'
import { useMouse } from '../hooks/useMedia'
import { useStored } from '../hooks/useStored'
import { useIsAdmin, useMe } from '../hooks/useMe'
import { compact, percent, plural, shortDate } from '../lib/format'
import { spring } from '../lib/motion'
import { AddChannelsSheet } from './AddChannelsSheet'
import { openIntro, useFirstVisit } from '../lib/intros'

type Filter = 'all' | 'active' | 'pending' | 'error' | 'paused' | 'restricted'

const SORTS = [
  { value: 'new', label: 'Новые' },
  { value: 'er', label: 'ER' },
  { value: 'fr', label: 'FR' },
  { value: 'subscribers', label: 'Подписчики' },
] as const
type SortValue = (typeof SORTS)[number]['value']

/** Same idea as in Top: what the ratio sorts mean, next to the title. */
const SORT_HINTS: Partial<Record<SortValue, string>> = {
  er: 'ER — (реакции + комментарии + репосты) / просмотры',
  fr: 'FR — репосты / просмотры',
}

const SORT_KEY: Record<Exclude<SortValue, 'new'>, (ch: Channel) => number> = {
  er: (ch) => ch.avg_er,
  fr: (ch) => ch.avg_fr,
  subscribers: (ch) => ch.subscribers ?? -1,
}

export function channelSubtitle(ch: Channel, ratio: 'er' | 'fr' = 'er'): string {
  switch (ch.status) {
    case 'pending':
      // e.g. "blocked for account X — connecting through another one"
      return ch.status_detail || 'Подключается'
    case 'paused':
      return 'На паузе'
    case 'error':
      return ch.status_detail || 'Ошибка'
    case 'restricted':
      return ch.status_detail || 'Запрещён в странах всех аккаунтов'
    default: {
      const parts = []
      if (ch.subscribers != null)
        parts.push(`${compact(ch.subscribers)} ${plural(ch.subscribers, 'подписчик', 'подписчика', 'подписчиков')}`)
      if (ch.posts_per_day) parts.push(`${String(ch.posts_per_day).replace('.', ',')} в день`)
      if (ratio === 'fr' ? ch.avg_fr : ch.avg_er)
        parts.push(ratio === 'fr' ? `FR ${percent(ch.avg_fr)}` : `ER ${percent(ch.avg_er)}`)
      return parts.join(' · ') || (ch.username ? `@${ch.username}` : '')
    }
  }
}

const STATUS_LABEL: Partial<Record<Channel['status'], string>> = {
  pending: 'ожидает',
  paused: 'пауза',
  error: 'ошибка',
  restricted: 'запрещён',
}

/** Statuses whose subtitle is a problem description shown in red. */
const PROBLEM: Channel['status'][] = ['error', 'restricted']

export function useChannelActions(reload: () => Promise<void>, onDeleted?: () => void) {
  const toast = useToast()
  const admin = useIsAdmin()
  const [confirm, setConfirm] = useState<Channel | null>(null)

  const run = async (ch: Channel, action: 'pause' | 'resume' | 'retry') => {
    try {
      await api.channelAction(ch.id, action)
      await reload()
    } catch (e) {
      toast((e as Error).message)
    }
  }

  const remove = async (ch: Channel) => {
    try {
      await api.deleteChannel(ch.id)
      toast(admin ? 'Канал удалён' : 'Канал удалён из вашего списка')
      onDeleted?.()
      await reload()
    } catch (e) {
      toast((e as Error).message)
    }
  }

  const sheet = (
    <ActionSheet
      open={confirm !== null}
      onClose={() => setConfirm(null)}
      title={confirm?.title || confirm?.link}
      message={
        !admin
          ? 'Канал пропадёт из вашего списка, ленты и топа'
          : confirm?.users
            ? `Канал удалится у всех, кто его отслеживает (${confirm.users} ${plural(confirm.users, 'пользователь', 'пользователя', 'пользователей')}): аккаунт выйдет из канала, собранные посты будут удалены`
            : 'Аккаунт выйдет из канала, собранные посты будут удалены'
      }
      actions={confirm ? [{ label: 'Удалить канал', destructive: true, onClick: () => remove(confirm) }] : []}
    />
  )

  return { run, askDelete: setConfirm, sheet }
}

export function Channels({ onOpen }: { onOpen: (id: number) => void }) {
  useFirstVisit('channels')
  const { items, error, reload } = useChannels()
  const [adding, setAdding] = useState(false)
  const [filter, setFilter] = useState<Filter>('all')
  const [query, setQuery] = useState('')
  const [sort, setSort] = useStored<SortValue>(
    'channels.sort',
    'new',
    SORTS.map((s) => s.value),
  )
  const actions = useChannelActions(reload)
  const mouse = useMouse()

  const counts = useMemo(() => {
    const c: Record<string, number> = {}
    items?.forEach((ch) => (c[ch.status] = (c[ch.status] ?? 0) + 1))
    return c
  }, [items])

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase()
    const list = (items ?? []).filter(
      (ch) =>
        (filter === 'all' || ch.status === filter) &&
        (!q || [ch.title, ch.username, ch.link].some((s) => s?.toLowerCase().includes(q))),
    )
    // "new" keeps the server order (most recently added first); the rest sort descending
    if (sort !== 'new') {
      const key = SORT_KEY[sort]
      list.sort((a, b) => key(b) - key(a))
    }
    return list
  }, [items, filter, query, sort])

  const filters = [
    { value: 'all' as const, label: `Все ${items?.length ?? ''}`.trim() },
    { value: 'active' as const, label: `Активные ${counts.active ?? 0}` },
    ...(counts.pending ? [{ value: 'pending' as const, label: `Ожидают ${counts.pending}` }] : []),
    ...(counts.error ? [{ value: 'error' as const, label: `Ошибки ${counts.error}` }] : []),
    ...(counts.restricted ? [{ value: 'restricted' as const, label: `Запрещены ${counts.restricted}` }] : []),
    ...(counts.paused ? [{ value: 'paused' as const, label: `Пауза ${counts.paused}` }] : []),
  ]

  // pausing is the admin's: a channel is shared, a user only adds it to or removes it from their list
  const admin = useIsAdmin()
  // a staff member without the right only looks
  const me = useMe()
  const canEdit = me.can_edit_channels
  const toast = useToast()
  // over the plan's limit: the newest channels are paused; one is swapped for another by a swipe
  const paused = !admin && items ? items.filter((ch) => ch.paused).length : 0
  const setPaused = async (ch: Channel, on: boolean) => {
    try {
      await api.setChannelPaused(ch.id, on)
      await reload()
    } catch (e) {
      toast((e as Error).message)
    }
  }

  const rowActions = (ch: Channel): SwipeAction[] => [
    ...(!admin && canEdit && !ch.own && (ch.paused || paused > 0)
      ? [
          ch.paused
            ? { label: 'Читать', icon: Play, color: 'var(--green)', onClick: () => setPaused(ch, false) }
            : { label: 'Пауза', icon: Pause, color: 'var(--orange)', onClick: () => setPaused(ch, true) },
        ]
      : []),
    ...(admin
      ? [
          ch.status === 'error'
            ? { label: 'Повторить', icon: RotateCw, color: 'var(--accent)', onClick: () => actions.run(ch, 'retry') }
            : ch.status === 'paused'
              ? { label: 'Включить', icon: Play, color: 'var(--green)', onClick: () => actions.run(ch, 'resume') }
              : { label: 'Пауза', icon: Pause, color: 'var(--orange)', onClick: () => actions.run(ch, 'pause') },
        ]
      : []),
    // the project's own channel is changed in the settings, not removed from here
    ...(ch.own || !canEdit
      ? []
      : [{ label: 'Удалить', icon: Trash2, color: 'var(--red)', onClick: () => actions.askDelete(ch) }]),
  ]

  return (
    <Page
      onHelp={() => openIntro('channels')}
      title="Каналы"
      // the admin sees every channel in the system: how many there are
      titleCount={admin && items ? items.length : undefined}
      titleAside={
        <AnimatePresence mode="wait" initial={false}>
          {SORT_HINTS[sort] && (
            <motion.span
              key={sort}
              initial={{ opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -4, transition: { duration: 0.12 } }}
              transition={spring}
            >
              {SORT_HINTS[sort]}
            </motion.span>
          )}
        </AnimatePresence>
      }
      onRefresh={reload}
      right={
        canEdit && (
          <motion.button
            className="nav-button"
            onClick={() => setAdding(true)}
            aria-label="Добавить каналы"
            whileTap={{ scale: 0.85 }}
          >
            <Plus size={26} />
          </motion.button>
        )
      }
      accessory={
        items && items.length > 0 ? (
          <>
            <label className="search">
              <Search size={17} />
              <input placeholder="Поиск" value={query} onChange={(e) => setQuery(e.target.value)} />
              {query && (
                <button aria-label="Очистить" onClick={() => setQuery('')} style={{ display: 'flex' }}>
                  <X size={17} />
                </button>
              )}
            </label>
            <Segmented id="channel-sort" options={[...SORTS]} value={sort} onChange={setSort} />
            {filters.length > 2 && <Chips id="channel-filter" options={filters} value={filter} onChange={setFilter} />}
          </>
        ) : null
      }
    >
      {error && <div className="error-banner">{error}</div>}

      {!admin && items && items.some((ch) => !ch.own) && <ListFileLine count={items.filter((ch) => !ch.own).length} />}

      {paused > 0 && (
        <div className="section-footer" style={{ margin: '0 16px 10px' }}>
          {paused} {plural(paused, 'канал', 'канала', 'каналов')} на паузе: тариф {me.plan?.title ?? ''} читает до{' '}
          {me.plan?.channels ?? 0} {plural(me.plan?.channels ?? 0, 'канала', 'каналов', 'каналов')}.{' '}
          {mouse ? 'Наведите на канал' : 'Смахните канал влево'}, чтобы поставить его на паузу или вернуть вместо
          другого, — или выберите тариф больше в настройках.
        </div>
      )}

      {items && items.length === 0 && (
        <motion.div className="empty" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={spring}>
          <div className="empty-icon">
            <Megaphone size={32} strokeWidth={1.6} />
          </div>
          <div className="empty-title">Нет каналов</div>
          <div>{canEdit ? 'Добавьте каналы по ссылкам или @username' : 'Каналы проекта добавляет его владелец'}</div>
          {canEdit && (
            <button
              className="button"
              style={{ width: 'auto', padding: '0 24px', marginTop: 16 }}
              onClick={() => setAdding(true)}
            >
              Добавить каналы
            </button>
          )}
        </motion.div>
      )}

      {visible.length > 0 && (
        <div className="list-plain">
          <AnimatePresence initial={false}>
            {visible.map((ch) => (
              <motion.div
                key={ch.id}
                className="list-item"
                style={{ overflow: 'hidden', ['--inset' as string]: '78px' }}
                layout="position"
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: 'auto' }}
                exit={{ opacity: 0, height: 0 }}
                transition={spring}
              >
                <SwipeRow actions={rowActions(ch)}>
                  <button className="chat-row" onClick={() => onOpen(ch.id)}>
                    <Avatar title={ch.title || ch.link} seed={ch.id} src={ch.avatar_url} size={54} />
                    <div className="chat-row-main">
                      <div className="chat-row-top">
                        <span className="chat-row-title">{ch.title || ch.link}</span>
                        {ch.own && <span className="status-pill own">мой канал</span>}
                        {ch.paused && <span className="status-pill paused">на паузе</span>}
                        {STATUS_LABEL[ch.status] && (
                          <span className={`status-pill ${ch.status}`}>{STATUS_LABEL[ch.status]}</span>
                        )}
                        {ch.last_post_at && <span className="chat-row-time">{shortDate(ch.last_post_at)}</span>}
                      </div>
                      <div className={`chat-row-sub${PROBLEM.includes(ch.status) ? ' error' : ''}`}>
                        {channelSubtitle(ch, sort === 'fr' ? 'fr' : 'er')}
                      </div>
                    </div>
                  </button>
                </SwipeRow>
              </motion.div>
            ))}
          </AnimatePresence>
        </div>
      )}

      {items && items.length > 0 && visible.length === 0 && (
        <div className="empty">
          <div className="empty-title">Ничего не найдено</div>
        </div>
      )}

      {items && items.length > 0 && (
        <div className="section-footer" style={{ textAlign: 'center', padding: '12px 32px' }}>
          {`${mouse ? 'Наведите курсор на канал' : 'Смахните канал влево'}, чтобы ${admin ? 'поставить на паузу или удалить' : 'удалить его'}`}
        </div>
      )}

      <AddChannelsSheet open={adding} onClose={() => setAdding(false)} onChanged={reload} count={items?.length ?? 0} />
      {actions.sheet}
    </Page>
  )
}

/** «128 каналов · Получить списком»: the bot sends the project's channels to the person's Telegram as a file
 * (a staff member without Telegram is shown where to link it). */
function ListFileLine({ count }: { count: number }) {
  const me = useMe()
  const toast = useToast()
  const [sending, setSending] = useState(false)
  const send = async () => {
    if (me.role === 'member' && !me.member?.telegram) {
      location.hash = '/settings?flash=telegram'
      return
    }
    setSending(true)
    try {
      const res = await api.channelListFile()
      toast(`Бот прислал файл: ${res.count} ${plural(res.count, 'канал', 'канала', 'каналов')}`)
    } catch (e) {
      toast((e as Error).message)
    } finally {
      setSending(false)
    }
  }
  return (
    <div className="channels-count">
      {count} {plural(count, 'канал', 'канала', 'каналов')} ·{' '}
      <button className="link-inline" onClick={send} disabled={sending}>
        {sending ? 'Отправляем…' : 'Получить списком'}
      </button>
    </div>
  )
}
