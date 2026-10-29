import DOMPurify from 'dompurify'
import { AnimatePresence, motion } from 'framer-motion'
import {
  AlertCircle,
  ArrowUpRight,
  BarChart3,
  Copy,
  Eye,
  EyeOff,
  FileText,
  Forward,
  LayoutTemplate,
  Loader2,
  Mic,
  Music,
  Play,
  ScanText,
  Sparkles,
  Star,
  type LucideIcon,
} from 'lucide-react'
import { memo, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { api, type Post, type PostMedia, type Sort } from '../api'
import { useIsAdmin, useMe } from '../hooks/useMe'
import { copyText } from '../lib/clipboard'
import { openComments } from '../lib/comments'
import { whoDid } from '../lib/who'
import { compact, exact, multiplier, percent, relative } from '../lib/format'
import { spring } from '../lib/motion'
import { Avatar } from './Avatar'
import { Lightbox } from './Lightbox'
import { videoFallback } from '../lib/videoFallback'
import { VideoViewer } from './VideoViewer'
import { useGroups } from '../hooks/useGroups'
import { ActionSheet } from './Sheet'
import { useToast } from './Toast'

export const ALLOWED_TAGS = ['b', 'strong', 'i', 'em', 'u', 's', 'del', 'a', 'code', 'pre', 'blockquote', 'br']

DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node.tagName === 'A') {
    node.setAttribute('target', '_blank')
    node.setAttribute('rel', 'noopener noreferrer')
  }
})

/** Marks the words in the text of sanitized HTML (never inside its tags). */
function mark(html: string, words: string[]): string {
  const escaped = words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\s+/g, '\\s+'))
  // whole words only: «взрыв» found is not marked inside «взрывной»
  const re = new RegExp(`(?<![\\p{L}\\p{N}])(${escaped.join('|')})(?![\\p{L}\\p{N}])`, 'giu')
  return html
    .split(/(<[^>]+>)/)
    .map((part) => (part.startsWith('<') ? part : part.replace(re, '<mark class="trigger-mark">$1</mark>')))
    .join('')
}

const MEDIA_LABELS: Record<string, [LucideIcon, string]> = {
  voice: [Mic, 'Голосовое сообщение'],
  audio: [Music, 'Аудио'],
  document: [FileText, 'Файл'],
  poll: [BarChart3, 'Опрос'],
  video: [Play, 'Видео'],
  gif: [Play, 'GIF'],
}

/** Ratio sorts rank a post only once its views reach 1% of the channel's subscribers (post.rated). */
const RATIO_SORTS: Sort[] = ['er', 'fr', 'fr_plus']

/** The smart feed corrected by hand: «Скрыть» in it, «Показать» among the posts it turned down. */
export interface SmartActions {
  action: 'hide' | 'show'
  onCorrect: (post: Post) => void
  onUndo: (post: Post) => void
}

interface Props {
  post: Post
  rank?: number
  sort?: Sort
  showChannel?: boolean
  smartActions?: SmartActions
  /** «комменты» opens the post's comments page (not on that page itself) */
  commentsLink?: boolean
}

function PostCardInner({ post, rank, sort, showChannel = true, smartActions, commentsLink = true }: Props) {
  const [expanded, setExpanded] = useState(false)
  const [overflowing, setOverflowing] = useState(false)
  const textRef = useRef<HTMLDivElement>(null)
  const admin = useIsAdmin()
  const me = useMe()
  // a staff member gets posts from the bot once their Telegram is linked; until then, a plain link
  const member = me.role === 'member'
  // the project's groups: everyone of it sends there; a staff member also to their own Telegram once it is linked
  const groupCount = useGroups(!admin)?.items.length ?? 0
  const sendable = post.sendable && (!member || Boolean(me.member?.telegram) || groupCount > 0)
  const link = member ? (post.source_url ?? post.url) : post.url

  // «Триггер-слова»: the words the post has, marked in its text
  const html = useMemo(() => {
    const clean = DOMPurify.sanitize(post.html || '', { ALLOWED_TAGS, ALLOWED_ATTR: ['href'] })
    return post.trigger_found?.length ? mark(clean, post.trigger_found) : clean
  }, [post.html, post.trigger_found])

  useLayoutEffect(() => {
    const el = textRef.current
    if (el && !expanded) setOverflowing(el.scrollHeight > el.clientHeight + 2)
  }, [html, expanded])

  const placeholder = post.media.length === 0 && post.media_type ? MEDIA_LABELS[post.media_type] : undefined
  const [viewing, setViewing] = useState<number | null>(null)
  // a video plays here, straight from Telegram's servers (VideoViewer)
  const [watching, setWatching] = useState<PostMedia | null>(null)
  // the channel's page, from the feed and the top
  const openChannel = () => {
    location.hash = `/channel/${post.channel.id}`
  }

  const photos = post.media.filter((m) => m.full_url)
  const openMedia = (m: PostMedia) =>
    m.full_url ? setViewing(photos.indexOf(m)) : m.video_url ? setWatching(m) : undefined

  const stats: { key: Sort; label: string; value: string; title?: string }[] = [
    { key: 'er', label: 'ER', value: percent(post.er) },
    { key: 'fr', label: 'FR', value: percent(post.fr) },
    {
      key: 'fr_plus',
      label: 'FR+',
      value: post.fr_plus ? multiplier(post.fr_plus) : '—',
      title: 'FR поста относительно среднего FR последних 100 постов канала',
    },
    { key: 'forwards', label: 'репосты', value: exact(post.forwards) },
    { key: 'reactions', label: 'реакции', value: exact(post.reactions) },
    { key: 'comments', label: 'комменты', value: exact(post.comments) },
    { key: 'views', label: 'просмотры', value: exact(post.views) },
  ]

  return (
    <article className="post">
      <header className="post-header">
        {showChannel && (
          <button
            className="post-avatar-link"
            onClick={openChannel}
            aria-label={`Открыть канал «${post.channel.title}»`}
          >
            <Avatar title={post.channel.title} seed={post.channel.id} src={post.channel.avatar_url} size={38} />
          </button>
        )}
        <div className="post-channel">
          {showChannel && (
            <button className="post-channel-title" onClick={openChannel}>
              {post.channel.title}
            </button>
          )}
          <div className="post-channel-sub">
            {relative(post.posted_at)}
            {post.deleted && (
              <span title="Пост удалён в канале — цифры больше не обновляются" style={{ color: 'var(--red)' }}>
                {' · удалён'}
              </span>
            )}
            {admin && post.repost_status === 'failed' && (
              <span title={post.repost_error ?? undefined} style={{ color: 'var(--red)' }}>
                {' '}
                · <AlertCircle size={12} style={{ verticalAlign: -1 }} /> не переслан
              </span>
            )}
          </div>
        </div>
        {rank !== undefined &&
          (sort && RATIO_SORTS.includes(sort) && !post.rated ? (
            <span
              className="post-rank unrated"
              title="Просмотров меньше 1% подписчиков канала — займёт место в рейтинге, когда наберёт"
            >
              мало просмотров
            </span>
          ) : (
            <span className="post-rank">#{rank}</span>
          ))}
        {smartActions && !post.smart?.mine && (
          <button
            className="nav-button"
            onClick={() => smartActions.onCorrect(post)}
            aria-label={smartActions.action === 'hide' ? 'Скрыть из умной ленты' : 'Показать в умной ленте'}
            title={smartActions.action === 'hide' ? 'Скрыть из умной ленты' : 'Показать в умной ленте'}
          >
            {smartActions.action === 'hide' ? <EyeOff size={18} /> : <Eye size={18} />}
          </button>
        )}
        {post.favorite !== undefined && <FavoriteStar postId={post.id} initial={post.favorite} />}
        {post.text.trim() && <CopyText text={post.text} />}
        <span className="post-action">
          {sendable ? (
            <SendOriginal postId={post.id} />
          ) : (
            link && (
              <a
                className="nav-button"
                href={link}
                target="_blank"
                rel="noopener noreferrer"
                aria-label={post.reposted && !member ? 'Открыть пост в группе' : 'Открыть в Telegram'}
                title={post.reposted && !member ? 'Открыть пост в группе' : 'Открыть в Telegram'}
              >
                <ArrowUpRight size={20} />
              </a>
            )
          )}
        </span>
      </header>

      {post.media.length > 0 && (
        <div className={`post-media count-${Math.min(post.media.length, 5)}`}>
          {post.media.map((m) => (
            <button
              key={m.url}
              className={`post-media-tile${m.kind === 'other' ? ' static' : ''}`}
              onClick={() => openMedia(m)}
              aria-label={m.full_url ? 'Открыть фото' : m.video_url ? 'Смотреть видео' : undefined}
            >
              <img src={m.url} alt="" loading="lazy" />
              {m.kind === 'video' && (
                <span className="post-media-badge">
                  <Play size={12} fill="currentColor" />
                  {post.media_type === 'gif' ? 'GIF' : 'Видео'}
                </span>
              )}
            </button>
          ))}
        </div>
      )}
      <AnimatePresence>
        {viewing !== null && (
          <Lightbox
            photos={photos.map((m) => ({ preview: m.url, full: m.full_url! }))}
            start={viewing}
            onClose={() => setViewing(null)}
          />
        )}
        {watching?.video_url && (
          <VideoViewer
            videoUrl={watching.video_url}
            poster={watching.url}
            fallback={videoFallback(post, me)}
            onClose={() => setWatching(null)}
          />
        )}
      </AnimatePresence>

      {placeholder && <MediaPlaceholder icon={placeholder[0]} label={placeholder[1]} count={post.media_count} />}

      {/* a newer kind of Telegram post (rich messages) reaches us empty: its text is seen in Telegram only */}
      {post.media_type === 'unsupported' && (
        <div className="post-unsupported">
          <LayoutTemplate size={18} />
          <div>
            Пост в новом формате Telegram — его текст пока не виден.
            {link && (
              <>
                {' '}
                <a href={link} target="_blank" rel="noopener noreferrer">
                  Открыть в Telegram
                </a>
              </>
            )}
          </div>
        </div>
      )}

      {html && (
        <div className="post-body">
          <motion.div
            ref={textRef}
            className={`post-text${expanded ? '' : ' clamped'}`}
            dangerouslySetInnerHTML={{ __html: html }}
            layout="size"
            transition={spring}
          />
          {overflowing && !expanded && (
            <button className="more-button" onClick={() => setExpanded(true)}>
              Показать полностью
            </button>
          )}
        </div>
      )}

      {post.found_on_picture && (
        <div className="post-found-picture">
          <ScanText size={13} />
          <span>Найдено в тексте на картинке</span>
        </div>
      )}

      {post.reactions_top.length > 0 && (
        <div className="reactions">
          {post.reactions_top.map((r) => (
            <span className="reaction" key={r.e}>
              {r.e === 'custom' ? (
                <Sparkles size={15} />
              ) : r.e === 'paid' ? (
                <Star size={15} fill="currentColor" />
              ) : (
                <span className="reaction-emoji">{r.e}</span>
              )}
              {compact(r.c)}
            </span>
          ))}
        </div>
      )}

      {post.smart?.mine ? (
        <div className={`post-smart mine${post.smart.mine === 'show' ? '' : ' rejected'}`}>
          {post.smart.mine === 'show' ? <Eye size={13} /> : <EyeOff size={13} />}
          <span>
            {whoDid(post.smart.mine_by, me) === 'вы'
              ? post.smart.mine === 'show'
                ? 'Вы показали'
                : 'Вы скрыли'
              : `${post.smart.mine === 'show' ? 'Показано' : 'Скрыто'} · ${whoDid(post.smart.mine_by, me)}`}
            : «{post.smart.mine_reason}»
          </span>
          {smartActions && (
            <button className="post-smart-undo" onClick={() => smartActions.onUndo(post)}>
              Отменить
            </button>
          )}
        </div>
      ) : (
        post.smart?.reason && (
          <div
            className={`post-smart${post.smart.match ? '' : ' rejected'}`}
            title={`Совпадение с промптом умной ленты: ${post.smart.score}%`}
          >
            <Sparkles size={13} />
            <span>{post.smart.reason}</span>
          </div>
        )
      )}

      <div className="post-stats" style={{ marginTop: 10 }}>
        {stats.map((s) =>
          s.key === 'comments' && commentsLink && post.comments > 0 ? (
            <button
              key={s.key}
              className={`stat stat-link${s.key === sort ? ' highlight' : ''}`}
              onClick={() => openComments(post)}
              title="Читать комментарии"
            >
              <RollingValue value={s.value} />
              <span className="stat-label">{s.label}</span>
            </button>
          ) : (
            <div key={s.key} className={`stat${s.key === sort ? ' highlight' : ''}`} title={s.title}>
              <RollingValue value={s.value} />
              <span className="stat-label">{s.label}</span>
            </div>
          ),
        )}
      </div>
    </article>
  )
}

/** A number of the post that rolls when it changes: the old one goes up and out, the new one comes
 * in from below (live updates bring new views, reactions and forwards). */
function RollingValue({ value }: { value: string }) {
  return (
    <span className="stat-value rolling">
      <AnimatePresence initial={false} mode="popLayout">
        <motion.span
          key={value}
          className="rolling-digit"
          initial={{ y: '75%', opacity: 0 }}
          animate={{ y: 0, opacity: 1 }}
          exit={{ y: '-75%', opacity: 0 }}
          transition={{ duration: 0.4, ease: [0.22, 0.9, 0.3, 1] }}
        >
          {value}
        </motion.span>
      </AnimatePresence>
    </span>
  )
}

/** «В избранное»: the star; the post goes to the person's «Избранное» and is kept there. */
function FavoriteStar({ postId, initial }: { postId: number; initial: boolean }) {
  const [on, setOn] = useState(initial)
  const toast = useToast()
  useEffect(() => setOn(initial), [initial])
  const toggle = async () => {
    const next = !on
    setOn(next)
    try {
      await api.star(postId, next)
      toast(next ? 'Добавлено в избранное' : 'Убрано из избранного')
    } catch (e) {
      setOn(!next)
      toast((e as Error).message)
    }
  }
  return (
    <motion.button
      className={`nav-button post-star${on ? ' on' : ''}`}
      onClick={toggle}
      whileTap={{ scale: 0.8 }}
      aria-pressed={on}
      aria-label={on ? 'Убрать из избранного' : 'В избранное'}
      title={on ? 'Убрать из избранного' : 'В избранное'}
    >
      <motion.span
        key={on ? 'on' : 'off'}
        initial={{ scale: on ? 0.4 : 1 }}
        animate={{ scale: 1 }}
        transition={{ type: 'spring', stiffness: 500, damping: 14 }}
        style={{ display: 'inline-flex' }}
      >
        <Star size={19} fill={on ? 'currentColor' : 'none'} />
      </motion.span>
    </motion.button>
  )
}

/** «Скопировать текст поста»: the text as it reads, without formatting. */
function CopyText({ text }: { text: string }) {
  const toast = useToast()
  return (
    <button
      className="nav-button"
      onClick={async () => toast((await copyText(text)) ? 'Текст поста скопирован' : 'Не удалось скопировать текст')}
      aria-label="Скопировать текст поста"
      title="Скопировать текст поста"
    >
      <Copy size={18} />
    </button>
  )
}

/** "Посмотреть оригинал поста": the bot sends the post to the person's Telegram — or, when the project has
 * groups, to where they choose. */
function SendOriginal({ postId }: { postId: number }) {
  const [busy, setBusy] = useState(false)
  const [choosing, setChoosing] = useState(false)
  const toast = useToast()
  const me = useMe()
  // the project's groups (staff too); a staff member to themselves once their Telegram is linked
  const groups = useGroups()?.items ?? []
  const toMe = me.role !== 'member' || Boolean(me.member?.telegram)

  const send = async (chatId?: number) => {
    setBusy(true)
    try {
      const res = await api.sendOriginal(postId, chatId)
      toast(res.to ? `Пост отправлен в группу «${res.to}»` : 'Пост отправлен вам в Telegram')
    } catch (e) {
      toast((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <button
        className="nav-button"
        // one place to send it: at once; several: the person chooses
        onClick={() =>
          (toMe ? 1 : 0) + groups.length > 1 ? setChoosing(true) : send(toMe ? undefined : groups[0]?.chat_id)
        }
        disabled={busy}
        aria-label="Переслать пост"
        title={
          groups.length
            ? 'Переслать пост — в личные сообщения или в группу проекта'
            : 'Посмотреть оригинал поста — бот пришлёт его вам в Telegram'
        }
      >
        {busy ? <Loader2 size={19} className="spinner" /> : <Forward size={20} />}
      </button>
      <ActionSheet
        open={choosing}
        onClose={() => setChoosing(false)}
        title="Куда отправить пост?"
        actions={[
          ...(toMe ? [{ label: 'Личные сообщения', onClick: () => send() }] : []),
          ...groups.map((g) => ({
            label: `Группа ${g.username ? `@${g.username}` : `«${g.title ?? g.chat_id}»`}`,
            onClick: () => send(g.chat_id),
          })),
        ]}
      />
    </>
  )
}

function MediaPlaceholder({ icon: Icon, label, count }: { icon: LucideIcon; label: string; count: number }) {
  return (
    <div className="post-media-placeholder" style={{ marginTop: 4 }}>
      <Icon size={18} />
      {label}
      {count > 1 && ` · ${count}`}
    </div>
  )
}

export const PostCard = memo(PostCardInner)
