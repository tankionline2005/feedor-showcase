import { motion } from 'framer-motion'
import { ChevronLeft, ChevronRight, Ellipsis, Loader2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, type Backup, type Channel, type ChannelFollower } from '../api'
import { Avatar } from '../components/Avatar'
import { Page } from '../components/Page'
import { NoPlan, useNoPlan } from '../components/NoPlan'
import { PostList } from '../components/PostList'
import { SortChips } from '../components/Segmented'
import { ActionSheet, Sheet, type SheetAction } from '../components/Sheet'
import { useToast } from '../components/Toast'
import { useChannels } from '../hooks/useChannels'
import { useIsAdmin, useMe } from '../hooks/useMe'
import { usePosts } from '../hooks/usePosts'
import { useStored } from '../hooks/useStored'
import { copyText } from '../lib/clipboard'
import { compact, dayLabel, percent, plural, time } from '../lib/format'
import { spring } from '../lib/motion'
import { useChannelActions } from './Channels'

// no FR+ here: within one channel it orders posts exactly like FR (the baseline is the same)
const SORTS = [
  { value: 'new', label: 'Новые' },
  { value: 'er', label: 'ER' },
  { value: 'fr', label: 'FR' },
  { value: 'forwards', label: 'Репосты' },
  { value: 'reactions', label: 'Реакции' },
  { value: 'comments', label: 'Комментарии' },
  { value: 'views', label: 'Просмотры' },
] as const
type SortValue = (typeof SORTS)[number]['value']

export function ChannelDetail({
  id,
  onBack,
  onOpenStats,
}: {
  id: number
  onBack: () => void
  onOpenStats: () => void
}) {
  const { items, reload } = useChannels()
  const channel = items?.find((c) => c.id === id)
  const [sort, setSort] = useStored<SortValue>(
    'channel.sort',
    'new',
    SORTS.map((s) => s.value),
  )
  const posts = usePosts({ channel_id: id, sort })
  const [menu, setMenu] = useState(false)
  const [followers, setFollowers] = useState(false)
  const actions = useChannelActions(reload, onBack)
  const admin = useIsAdmin()
  const me = useMe()
  const noPlan = useNoPlan()

  const title = channel?.title || channel?.link || 'Канал'

  const menuActions = (ch: Channel): SheetAction[] => {
    const list: SheetAction[] = []
    if (ch.username)
      list.push({ label: 'Открыть в Telegram', onClick: () => window.open(`https://t.me/${ch.username}`, '_blank') })
    if (!admin) {
      // the project's own channel is changed in the settings, not removed
      if (!ch.own && me.can_edit_channels)
        list.push({ label: 'Удалить канал', destructive: true, onClick: () => actions.askDelete(ch) })
      return list
    }
    if (ch.status === 'paused') list.push({ label: 'Возобновить', onClick: () => actions.run(ch, 'resume') })
    else if (ch.status === 'error')
      list.push({ label: 'Повторить вступление', onClick: () => actions.run(ch, 'retry') })
    else list.push({ label: 'Поставить на паузу', onClick: () => actions.run(ch, 'pause') })
    list.push({ label: 'Удалить канал', destructive: true, onClick: () => actions.askDelete(ch) })
    return list
  }

  return (
    <Page
      title={title}
      onRefresh={async () => {
        await Promise.all([posts.refresh(), reload()])
      }}
      left={
        <motion.button className="nav-button" onClick={onBack} whileTap={{ opacity: 0.4 }} style={{ paddingLeft: 0 }}>
          <ChevronLeft size={30} strokeWidth={2.2} style={{ marginRight: -4 }} />
          Каналы
        </motion.button>
      }
      right={
        channel && (
          <button className="nav-button" aria-label="Действия" onClick={() => setMenu(true)}>
            <Ellipsis size={24} />
          </button>
        )
      }
      hero={
        <motion.div className="hero" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={spring}>
          <Avatar title={title} seed={id} src={channel?.avatar_url} size={88} />
          <div className="hero-title">{title}</div>
          <div className="hero-sub">
            {[
              channel?.subscribers != null &&
                `${compact(channel.subscribers)} ${plural(channel.subscribers, 'подписчик', 'подписчика', 'подписчиков')}`,
              // the admin sees who the channel is kept for, and opens the list of them
              channel?.users !== undefined &&
                (channel.users ? (
                  <button key="users" className="link-inline" onClick={() => setFollowers(true)}>
                    отслеживают {channel.users} {plural(channel.users, 'пользователь', 'пользователя', 'пользователей')}
                  </button>
                ) : channel.held ? (
                  'добавлен админом'
                ) : (
                  'никто не отслеживает'
                )),
            ]
              .filter(Boolean)
              .flatMap((part, i) => (i ? [' · ', part] : [part]))}
          </div>
          {channel?.status_detail && channel.status !== 'active' && (
            <div
              className="hero-sub"
              style={{ color: channel.status === 'pending' ? 'var(--orange)' : 'var(--red)', maxWidth: 420 }}
            >
              {channel.status_detail}
            </div>
          )}
          {channel?.account_name && (
            <div className="hero-sub" style={{ maxWidth: 420 }}>
              {accountsLine(channel)}
            </div>
          )}
          {channel && (
            <ChannelLinks
              names={channel.usernames?.length ? channel.usernames : channel.username ? [channel.username] : []}
            />
          )}
          {channel && (channel.status !== 'error' || (channel.own && !admin)) && (
            <div className={`hero-stats${channel.own && !admin ? ' with-link' : ''}`}>
              {channel.status !== 'error' && (
                <>
                  <Stat value={String(channel.posts_per_day).replace('.', ',')} label="постов в день" />
                  <Stat value={compact(channel.avg_views)} label="ср. просмотры" />
                  <Stat value={percent(channel.avg_er)} label="средний ER" />
                  <Stat value={percent(channel.avg_fr)} label="средний FR" />
                </>
              )}
              {/* the project's own channel: its statistics, as the fifth tile */}
              {channel.own && !admin && (
                <button className="hero-stat hero-stat-link" onClick={onOpenStats}>
                  Подробная статистика
                </button>
              )}
            </div>
          )}
        </motion.div>
      }
      accessory={<SortChips id="channel-sort" options={[...SORTS]} value={sort} onChange={setSort} />}
    >
      {noPlan ? (
        <NoPlan />
      ) : (
        <PostList
          {...posts}
          onMore={posts.loadMore}
          onShowPending={posts.showPending}
          sort={sort}
          ranked={sort !== 'new'}
          showChannel={false}
          empty={{
            title: 'Постов пока нет',
            text: channel?.status === 'pending' ? 'Канал ещё подключается' : 'Новые посты появятся здесь',
          }}
        />
      )}
      <ActionSheet
        open={menu}
        onClose={() => setMenu(false)}
        title={title}
        actions={channel ? menuActions(channel) : []}
      />
      {actions.sheet}
      {admin && <FollowersSheet channelId={followers ? id : null} title={title} onClose={() => setFollowers(false)} />}
    </Page>
  )
}

const BACKUP_STATUS: Record<Backup['status'], (b: Backup) => string> = {
  planned: (b) => (b.due_at ? `вступит в ${time(b.due_at)}` : 'вступит позже'),
  requested: () => 'заявка ждёт одобрения',
  member: () => 'в канале',
  failed: () => 'не может вступить: ссылка не работает',
}

/** Admin: "Читает Андрей · запасной Гена — в канале". */
function accountsLine(ch: Channel): string {
  const backups = (ch.backups ?? []).map((b) => `запасной ${b.name} — ${BACKUP_STATUS[b.status](b)}`)
  return [`Читает ${ch.account_name}`, ...backups].join(' · ')
}

/** The channel's public addresses, as Telegram shows them: the main one as a t.me link, and under it
 * «также @…» for the others. A tap copies the address as a t.me link, whichever way it is written.
 * None for a private channel. */
function ChannelLinks({ names }: { names: string[] }) {
  const toast = useToast()
  if (!names.length) return null
  const [main, ...others] = names
  const copy = async (name: string) => {
    const link = `https://t.me/${name}`
    toast((await copyText(link)) ? 'Ссылка скопирована' : link)
  }
  return (
    <div className="channel-links">
      <button
        className="channel-link-main"
        onClick={() => copy(main)}
        title="Скопировать ссылку"
        aria-label={`Скопировать ссылку t.me/${main}`}
      >
        t.me/{main}
      </button>
      {others.length > 0 && (
        <div className="channel-link-others">
          также{' '}
          {others.map((n, i) => (
            <span key={n}>
              {i > 0 && ', '}
              <button onClick={() => copy(n)} title="Скопировать ссылку" aria-label={`Скопировать ссылку t.me/${n}`}>
                @{n}
              </button>
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

function Stat({ value, label }: { value: string; label: string }) {
  return (
    <div className="hero-stat">
      <div className="hero-stat-value">{value}</div>
      <div className="hero-stat-label">{label}</div>
    </div>
  )
}

/** The admin's list of who tracks the channel: each person and the project of theirs it is in. */
function FollowersSheet({
  channelId,
  title,
  onClose,
}: {
  channelId: number | null
  title: string
  onClose: () => void
}) {
  const [items, setItems] = useState<ChannelFollower[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (channelId === null) return
    setItems(null)
    api.channelFollowers(channelId).then(
      (res) => setItems(res.items),
      (e) => setError((e as Error).message),
    )
  }, [channelId])

  return (
    <Sheet
      open={channelId !== null}
      onClose={onClose}
      title="Кто отслеживает"
      right={
        <button className="nav-button" style={{ fontWeight: 600 }} onClick={onClose}>
          Готово
        </button>
      }
    >
      {error && <div className="error-banner">{error}</div>}
      <div className="section">
        <div className="section-header">{title}</div>
        <div className="group">
          {!items ? (
            <div className="cell" style={{ justifyContent: 'center' }}>
              <Loader2 size={20} className="spinner" />
            </div>
          ) : (
            items.map((f) => {
              const name = f.user.name || (f.user.username ? `@${f.user.username}` : `Пользователь ${f.user.id}`)
              return (
                <button
                  key={f.project.id}
                  className="cell"
                  style={{ ['--inset' as string]: '62px' }}
                  onClick={() => {
                    onClose()
                    location.hash = `/users/${f.user.id}`
                  }}
                >
                  <Avatar title={name} seed={f.user.id} size={36} />
                  <div className="cell-main">
                    <div className="cell-title">{name}</div>
                    <div className="cell-sub">
                      проект «{f.project.title}»{f.own ? ' · канал проекта' : ''} · добавлен{' '}
                      {dayLabel(f.added_at).toLowerCase()}
                    </div>
                  </div>
                  <ChevronRight size={18} color="var(--text-3)" />
                </button>
              )
            })
          )}
        </div>
      </div>
    </Sheet>
  )
}
