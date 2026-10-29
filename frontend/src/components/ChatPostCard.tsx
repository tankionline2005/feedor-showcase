import { AnimatePresence, motion } from 'framer-motion'
import {
  AlertCircle,
  Check,
  ChevronRight,
  Clock,
  Forward,
  Image as ImageIcon,
  Info,
  Loader2,
  Lock,
  Pencil,
  Play,
  Plus,
  RotateCcw,
  Send,
  Trash2,
  X,
} from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Switch } from './Controls'
import { api, ApiError, type ChatPostMedia, type ChatPostOut } from '../api'
import { useGroups } from '../hooks/useGroups'
import { useWide } from '../hooks/useMedia'
import { useMe } from '../hooks/useMe'
import { addBotLink } from '../lib/bot'
import { plural, whenLabel } from '../lib/format'
import { may, whoDid } from '../lib/who'
import { Leaving, useScrollLock } from '../lib/freezeGuard'
import { backdropZ, useLayer } from '../lib/layers'
import { spring } from '../lib/motion'
import { Alert, type AlertButton } from './Alert'
import { Avatar } from './Avatar'
import { Lightbox } from './Lightbox'
import { PostEditor, type PostEditorApi } from './PostEditor'
import { SchedulePicker } from './SchedulePicker'
import { ActionSheet } from './Sheet'
import { TgText } from './TgText'
import { useToast } from './Toast'
import { VideoViewer } from './VideoViewer'
import { CoversSheet } from '../screens/CoversSheet'
import { CoverPicture } from './CoverPicture'

const CAPTION = 1024
const MESSAGE = 4096

/** What the bot could not attach, said once per reason: «канал запретил копирование (2)». */
const skippedText = (skipped: string[]) => {
  const counts = new Map<string, number>()
  for (const s of skipped) counts.set(s, (counts.get(s) ?? 0) + 1)
  return [...counts].map(([why, n]) => (n > 1 ? `${why} (${n})` : why)).join('; ')
}

const key = (m: { post_id: number; slot: number }) => `${m.post_id}-${m.slot}`

/**
 * A post the AI wrote for the person's channel, in the chat, the way the feed shows a post: the channel, the
 * pictures and videos (they open as in the feed), the text in Telegram's formatting. Under the channel's name:
 * «N источников», the posts it was written from. Top right: the bot sends it to the person's own Telegram (a staff
 * member without a linked Telegram is taken to where it is linked), and the pencil. Bottom right: the bot publishes
 * it in the project's channel, once asked to (the owner; a staff member if allowed). Editing: every picture and video of its sources to choose (up to 10, the way the Top's gear chooses
 * sorts), the text in an editor with Telegram's formatting that keeps to Telegram's limit — 1024 characters
 * under pictures, 4096 without.
 */
export function ChatPostCard({
  post,
  onChange,
  inList = false,
  onDelete,
}: {
  post: ChatPostOut
  onChange: (post: ChatPostOut) => void
  /** in «Публикации»: who published or scheduled it, or the topic the AI wrote it for, under the channel's name */
  inList?: boolean
  /** an AI's post by a topic, gone from «Публикации» */
  onDelete?: () => void
}) {
  const me = useMe()
  const toast = useToast()
  const [sending, setSending] = useState(false)
  const [viewing, setViewing] = useState<{ list: ChatPostMedia[]; index: number } | null>(null)
  const [watching, setWatching] = useState<ChatPostMedia | null>(null)
  const [editing, setEditing] = useState(false)
  const [picked, setPicked] = useState<string[]>([])
  const [length, setLength] = useState(post.length)
  const [saving, setSaving] = useState(false)
  const editor = useRef<PostEditorApi | null>(null)
  // where the bot may send it, as a feed's post: the person's own Telegram (a staff member once theirs is
  // linked), and the project's groups (staff too)
  const toMe = me.role !== 'member' || Boolean(me.member?.telegram)
  const groups = useGroups()?.items ?? []
  const needsTelegram = !toMe && !groups.length
  const [choosing, setChoosing] = useState(false)
  // «Изменить в канале»: the editor changes the published post's text there — its pictures stay as they went out
  const [inChannel, setInChannel] = useState(false)
  const limit = editing
    ? inChannel
      ? (post.channel_actions?.limit ?? MESSAGE)
      : picked.length || post.cover
        ? CAPTION
        : MESSAGE
    : post.limit
  // publishing in the channel: asked first; then what the bot cannot do, or what of the pictures cannot go
  // «Публикации» without the right to publish: looking only — no pencil; a published or scheduled one shows so,
  // not to be tapped
  const canPublish = may(me, 'ai_publish') && post.editable
  const showsState = canPublish || Boolean(post.published_at || post.scheduled_at)
  const deletable =
    inList && canPublish && Boolean(post.topic || post.rubric || post.written_by) && !post.published_at && !post.scheduled_at
  const [publishing, setPublishing] = useState(false)
  const [ask, setAsk] = useState<{ title: string; text?: string; buttons: AlertButton[] } | null>(null)
  const [picking, setPicking] = useState(false)

  const send = async (chatId?: number) => {
    setSending(true)
    try {
      const res = await api.sendChatPost(post.id, chatId)
      const where = res.to ? `Пост отправлен в группу «${res.to}»` : 'Пост отправлен вам в Telegram'
      toast(res.skipped.length ? `${where} без части медиа: ${skippedText(res.skipped)}` : where)
      onChange({ ...post, sent_at: new Date().toISOString() })
    } catch (e) {
      toast((e as Error).message)
    } finally {
      setSending(false)
    }
  }

  // one place to send it to: at once; several: the person chooses; none: where Telegram is linked
  const sendTapped = () => {
    if (needsTelegram) location.hash = '/settings?flash=telegram'
    else if ((toMe ? 1 : 0) + groups.length > 1) setChoosing(true)
    else send(toMe ? undefined : groups[0].chat_id)
  }

  // what went wrong, as an alert; the bot not in the channel: the owner adds it there through Telegram's own
  // «choose a channel» (as an admin that may post)
  const failed = (title: string, e: unknown) => {
    const info = e instanceof ApiError ? e.info : {}
    const addBot = me.role === 'user' && info.reason === 'missing' && typeof info.bot === 'string'
    setAsk({
      title,
      text: (e as Error).message,
      buttons: [
        ...(addBot
          ? [
              {
                label: 'Добавить бота',
                strong: true,
                onClick: () => {
                  setAsk(null)
                  window.open(addBotLink(info.bot as string), '_blank', 'noopener')
                },
              },
            ]
          : []),
        { label: 'Понятно', strong: !addBot, onClick: () => setAsk(null) },
      ],
    })
  }

  const publish = async (partial = false) => {
    setAsk(null)
    setPublishing(true)
    try {
      const res = await api.publishChatPost(post.id, partial)
      if (!res.published) {
        setAsk({
          title: 'Не всё получится приложить',
          text: `${skippedText(res.skipped)}. Опубликовать пост без этого?`,
          buttons: [
            { label: 'Опубликовать без этого', strong: true, onClick: () => publish(true) },
            { label: 'Отмена', onClick: () => setAsk(null) },
          ],
        })
        return
      }
      onChange({
        ...post,
        published_at: new Date().toISOString(),
        published_url: res.url ?? null,
        scheduled_at: null,
        publish_error: null,
        publish_error_at: null,
      })
      toast(
        res.skipped.length
          ? `Пост опубликован в канале без части медиа: ${skippedText(res.skipped)}`
          : 'Пост опубликован в канале',
      )
    } catch (e) {
      failed('Не удалось опубликовать', e)
    } finally {
      setPublishing(false)
    }
  }

  // «Отложенная публикация»: the time chosen; what of the pictures cannot go is asked about as for publishing
  const schedule = async (at: Date, partial = false) => {
    setAsk(null)
    setPicking(false)
    setPublishing(true)
    try {
      const res = await api.scheduleChatPost(post.id, at.toISOString(), partial)
      if (!res.scheduled) {
        setAsk({
          title: 'Не всё получится приложить',
          text: `${skippedText(res.skipped ?? [])}. Запланировать пост без этого?`,
          buttons: [
            { label: 'Запланировать без этого', strong: true, onClick: () => schedule(at, true) },
            { label: 'Отмена', onClick: () => setAsk(null) },
          ],
        })
        return
      }
      onChange({ ...post, scheduled_at: res.scheduled_at ?? null, publish_error: null, publish_error_at: null })
      toast(`Пост выйдет в канале ${whenLabel(res.scheduled_at!)}`)
    } catch (e) {
      failed('Не удалось запланировать', e)
    } finally {
      setPublishing(false)
    }
  }

  const unschedule = async () => {
    setAsk(null)
    try {
      await api.unscheduleChatPost(post.id)
      onChange({ ...post, scheduled_at: null, publish_error: null, publish_error_at: null })
      toast('Публикация отменена')
    } catch (e) {
      toast((e as Error).message)
    }
  }

  const pickTime = () => {
    setAsk(null)
    setPicking(true)
  }

  // not yet published: asked first; scheduled: now, another time, or not at all; published: open it in the
  // channel, or publish once more
  const publishTapped = () => {
    const channel = `«${post.channel.title}»`
    if (post.scheduled_at) {
      setAsk({
        title: `Пост выйдет ${whenLabel(post.scheduled_at)}`,
        text: `Бот опубликует его в канале ${channel}.`,
        buttons: [
          { label: 'Опубликовать сейчас', strong: true, onClick: () => publish() },
          { label: 'Изменить время', onClick: pickTime },
          { label: 'Отменить публикацию', destructive: true, onClick: unschedule },
          { label: 'Отмена', onClick: () => setAsk(null) },
        ],
      })
      return
    }
    if (post.published_at) {
      const acts = post.channel_actions
      // why it cannot be deleted from the channel is said here, before anything is tapped (the pencil changes it)
      const limit = acts && !acts.delete ? acts.why_delete : null
      setAsk({
        title: `Пост уже опубликован ${whenLabel(post.published_at)}`,
        text: limit ?? undefined,
        buttons: [
          ...(post.published_url
            ? [
                {
                  label: 'Открыть в канале',
                  strong: true,
                  onClick: () => {
                    setAsk(null)
                    window.open(post.published_url!, '_blank', 'noopener,noreferrer')
                  },
                },
              ]
            : []),
          ...(acts?.delete ? [{ label: 'Удалить из канала', destructive: true, onClick: askWithdraw }] : []),
          { label: 'Отмена', onClick: () => setAsk(null) },
        ],
      })
      return
    }
    setAsk({
      title: `Опубликовать в ${channel}?`,
      text: 'Бот сразу опубликует пост в канале.',
      buttons: [
        { label: 'Опубликовать', strong: true, onClick: () => publish() },
        { label: 'Отложенная публикация', onClick: pickTime },
        { label: 'Отмена', onClick: () => setAsk(null) },
      ],
    })
  }

  const askWithdraw = () =>
    setAsk({
      title: 'Удалить пост из канала?',
      text: `Бот удалит его из канала «${post.channel.title}». Здесь пост останется — его можно будет опубликовать снова.`,
      buttons: [
        {
          label: 'Удалить из канала',
          destructive: true,
          onClick: async () => {
            setAsk(null)
            try {
              onChange(await api.withdrawChatPost(post.id))
              toast('Пост удалён из канала')
            } catch (e) {
              toast((e as Error).message)
            }
          },
        },
        { label: 'Отмена', onClick: () => setAsk(null) },
      ],
    })

  const askDelete = () =>
    setAsk({
      title: 'Удалить предложение ИИ?',
      text: 'Пост исчезнет из «Публикаций».',
      buttons: [
        {
          label: 'Удалить',
          destructive: true,
          onClick: async () => {
            setAsk(null)
            try {
              await api.deleteChatPost(post.id)
              onDelete?.()
            } catch (e) {
              toast((e as Error).message)
            }
          },
        },
        { label: 'Отмена', onClick: () => setAsk(null) },
      ],
    })

  // «Публикации»: what was done with it, by whom
  const by = (name: string | null, did: string, didYou: string) =>
    name === 'автопостинг'
      ? `Автопостинг`
      : name?.startsWith('рубрика «')
        ? `Р${name.slice(1)}`
        : whoDid(name, me) === 'вы'
          ? didYou
          : `${did} ${name ?? 'владелец'}`
  const byline = post.scheduled_at
    ? `${by(post.scheduled_by, 'Запланировал', 'Вы запланировали')} · ${whenLabel(post.scheduled_at)}`
    : post.published_at
      ? `${by(post.published_by, 'Опубликовал', 'Вы опубликовали')} · ${whenLabel(post.published_at)}` +
        (post.published_edited_at ? ' · изменён' : '')
      : post.withdrawn_at
        ? `${by(post.withdrawn_by, 'Удалил из канала', 'Вы удалили из канала')} · ${whenLabel(post.withdrawn_at)}`
        : post.topic
          ? `Предложение ИИ по теме «${post.topic.title}»`
          : post.rubric
            ? `Выпуск рубрики «${post.rubric.name}»`
            : post.written_by
              ? `${by(post.written_by, 'Написал', 'Вы написали')} через API`
              : null

  const openSources = () => {
    location.hash = `/ai/sources/${post.id}`
  }

  const open = (m: ChatPostMedia, list: ChatPostMedia[]) => {
    if (m.full_url) {
      const photos = list.filter((x) => x.full_url)
      setViewing({ list: photos, index: photos.indexOf(m) })
    } else if (m.video_url) setWatching(m)
  }

  const startEditing = (channel = false) => {
    setPicked(post.media.map(key))
    setLength(post.length)
    setInChannel(channel)
    setEditing(true)
  }

  // the pencil: a post not out yet is edited here; one already out is changed where it went out — in the channel
  const editTapped = () => {
    if (!post.published_at) return startEditing()
    const acts = post.channel_actions
    if (acts?.edit) startEditing(true)
    else toast(acts?.why_edit ?? 'Этот пост уже нельзя изменить в канале')
  }

  const byKey = new Map(post.candidates.map((c) => [key(c), c]))
  const toggle = (c: ChatPostMedia) => {
    const k = key(c)
    if (c.why) return toast(c.why)
    if (picked.includes(k)) return setPicked(picked.filter((p) => p !== k))
    if (!picked.length && length > CAPTION)
      return toast(`Сократите текст до ${CAPTION} символов — столько Telegram разрешает под фото и видео`)
    if (picked.length >= post.max_media) return toast(`В посте Telegram — не больше ${post.max_media} фото и видео`)
    const gif = c.kind === 'gif' || picked.some((p) => byKey.get(p)?.kind === 'gif')
    if (gif && picked.length) return toast('GIF в Telegram отправляется только один, без других фото и видео')
    setPicked([...picked, k])
  }

  const save = async () => {
    const text = editor.current?.html()
    if (text === undefined) return
    setSaving(true)
    try {
      if (inChannel) {
        onChange(await api.editInChannel(post.id, text))
        toast('Пост изменён в канале')
      } else {
        const media = picked.map((k) => byKey.get(k)!).map((c) => ({ post: c.post_id, slot: c.slot }))
        onChange(await api.editChatPost(post.id, { text, media }))
      }
      setEditing(false)
    } catch (e) {
      toast((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const backToAi = () => {
    if (!editor.current?.reset(post.ai_text)) return
    toast('Вернули текст, который написал ИИ')
  }

  const near = length > limit * 0.9
  // a computer edits in the card; a phone in a window of its own (sheet), where everything fits
  const wide = useWide()
  const inline = editing && wide
  const sheet = editing && !wide

  const tiles = post.candidates.map((c) => {
    const index = picked.indexOf(key(c))
    const on = index >= 0
    return (
      <motion.div
        key={key(c)}
        layout="position"
        className={`chat-post-pick${on ? ' on' : ''}${c.why ? ' locked' : ''}`}
        transition={spring}
      >
        <button
          className="chat-post-pick-image"
          onClick={() => open(c, post.candidates)}
          aria-label={c.full_url ? 'Открыть фото' : 'Смотреть видео'}
        >
          <img src={c.url} alt="" loading="lazy" />
          {c.kind !== 'photo' && (
            <span className="post-media-badge">
              <Play size={11} fill="currentColor" />
              {c.kind === 'gif' ? 'GIF' : 'Видео'}
            </span>
          )}
        </button>
        <motion.button
          className="chat-post-pick-toggle"
          onClick={() => toggle(c)}
          aria-label={c.why ? c.why : on ? 'Убрать из поста' : 'Добавить в пост'}
          title={c.why ?? undefined}
          whileTap={{ scale: 0.85 }}
        >
          {c.why ? <Lock size={13} /> : on ? <span>{index + 1}</span> : <Plus size={15} strokeWidth={2.6} />}
        </motion.button>
      </motion.div>
    )
  })

  const refused = () =>
    toast(`Не помещается: Telegram разрешает ${limit} символов${limit === CAPTION ? ' под фото и видео' : ''}`)
  const counter = (
    <span className={`chat-post-count${near ? ' near' : ''}`}>
      {length} / {limit}
    </span>
  )
  const back = (
    <button className="chat-post-back" onClick={backToAi}>
      <RotateCcw size={14} />
      Вернуть текст ИИ
    </button>
  )

  return (
    <article
      className={`post chat-post${editing ? ' editing' : ''}${showsState && !editing ? ' publishable' : ''}${showsState && post.scheduled_at ? ' later' : ''}`}
    >
      <header className="post-header">
        <Avatar title={post.channel.title} seed={post.channel.seed} src={post.channel.avatar_url} size={38} />
        <div className="post-channel">
          <span className="post-channel-title">{post.channel.title}</span>
          <div className="post-channel-sub">
            {editing ? (
              'Редактирование поста'
            ) : post.sources ? (
              <button className="chat-post-sources" onClick={openSources}>
                {post.sources} {plural(post.sources, 'источник', 'источника', 'источников')}
              </button>
            ) : (
              'Предложение поста'
            )}
          </div>
          {inList && !editing && byline && <div className="post-channel-sub chat-post-byline">{byline}</div>}
        </div>
        <AnimatePresence mode="popLayout" initial={false}>
          {inline ? (
            <motion.div
              key="editing"
              className="chat-post-actions"
              initial={{ opacity: 0, scale: 0.8 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.8 }}
              transition={spring}
            >
              <button className="nav-button" onClick={() => setEditing(false)} aria-label="Отменить правки">
                <X size={20} />
              </button>
              <button className="chat-post-save" onClick={save} disabled={saving} aria-label="Сохранить пост">
                {saving ? <Loader2 size={18} className="spinner" /> : <Check size={18} strokeWidth={2.6} />}
              </button>
            </motion.div>
          ) : (
            <motion.div
              key="viewing"
              className="chat-post-actions"
              initial={{ opacity: 0, scale: 0.8 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.8 }}
              transition={spring}
            >
              <button
                className="nav-button"
                onClick={sendTapped}
                disabled={sending}
                aria-label="Переслать пост"
                title={
                  needsTelegram
                    ? 'Привяжите Telegram, чтобы бот присылал вам посты'
                    : groups.length
                      ? 'Переслать пост — в личные сообщения или в группу проекта'
                      : 'Бот пришлёт пост вам в Telegram'
                }
              >
                {sending ? <Loader2 size={19} className="spinner" /> : <Forward size={20} />}
              </button>
              {deletable && (
                <button className="nav-button" onClick={askDelete} aria-label="Удалить предложение" title="Удалить">
                  <Trash2 size={18} />
                </button>
              )}
              {/* a published post is changed in the channel: by those who may publish there */}
              {post.editable && (!post.published_at || canPublish) && (
                <button
                  className="nav-button"
                  onClick={editTapped}
                  aria-label={post.published_at ? 'Изменить в канале' : 'Редактировать пост'}
                  title={post.published_at ? 'Изменить в канале' : 'Редактировать'}
                >
                  <Pencil size={18} />
                </button>
              )}
            </motion.div>
          )}
        </AnimatePresence>
      </header>

      {inline && !inChannel && <CoverControl post={post} onChange={onChange} />}
      {inline && inChannel ? (
        <div className="chat-post-note channel-edit-note">
          <Info size={15} />
          <span>Изменяется пост в канале. Telegram меняет в вышедшем посте только текст — фото и видео останутся как есть.</span>
        </div>
      ) : inline
        ? post.candidates.length > 0 && (
            <div className="chat-post-picks">
              <div className="chat-post-picks-title">
                Фото и видео из источников · выбрано {picked.length} из {post.max_media}
              </div>
              <div className="chat-post-picks-grid">{tiles}</div>
            </div>
          )
        : post.cover_url && !inline && (
            <div className="chat-post-cover">
              <CoverPicture src={post.cover_url} alt="Обложка поста" lazy />
            </div>
          )}
      {!inline &&
        post.media.length > 0 && (
            <div className={`post-media count-${Math.min(post.media.length, 5)}`}>
              {post.media.map((m) => (
                <button
                  key={key(m)}
                  className="post-media-tile"
                  onClick={() => open(m, post.media)}
                  aria-label={m.full_url ? 'Открыть фото' : 'Смотреть видео'}
                >
                  <img src={m.url} alt="" loading="lazy" />
                  {m.kind !== 'photo' && (
                    <span className="post-media-badge">
                      <Play size={12} fill="currentColor" />
                      {m.kind === 'gif' ? 'GIF' : 'Видео'}
                    </span>
                  )}
                </button>
              ))}
            </div>
          )}

      <div className="post-body">
        {inList && !editing && post.autopost_note && (
          <div className="chat-post-note">
            <Info size={15} />
            <span>{post.autopost_note}</span>
          </div>
        )}
        {canPublish && !editing && post.publish_error && (
          <div className="chat-post-error">
            <AlertCircle size={16} />
            <span>
              Не удалось опубликовать{post.publish_error_at ? ` ${whenLabel(post.publish_error_at)}` : ''}.{' '}
              {post.publish_error}
            </span>
            <button onClick={publishTapped}>Повторить</button>
          </div>
        )}
        {inline ? (
          <>
            <PostEditor initial={post.text} limit={limit} onLength={setLength} onRefused={refused} apiRef={editor} />
            <div className="chat-post-editor-footer">
              {back}
              {counter}
            </div>
            <div className="chat-post-learn">
              ИИ запоминает, как в проекте правят его посты, и пишет следующие так же.
            </div>
          </>
        ) : (
          <TgText html={post.text} className="post-text" />
        )}
        {showsState && !editing && (
          <motion.button
            className={`chat-post-publish${post.scheduled_at ? ' scheduled' : post.published_at ? ' done' : ''}${canPublish ? '' : ' static'}`}
            onClick={canPublish ? publishTapped : undefined}
            disabled={publishing}
            tabIndex={canPublish ? undefined : -1}
            whileTap={canPublish ? { scale: 0.9 } : undefined}
            aria-label={
              post.scheduled_at
                ? `Пост выйдет ${whenLabel(post.scheduled_at)}`
                : post.published_at
                  ? 'Пост опубликован в канале'
                  : 'Опубликовать в канале'
            }
          >
            {publishing ? (
              <Loader2 size={22} className="spinner" />
            ) : post.scheduled_at ? (
              <>
                <Clock size={17} strokeWidth={2.4} />
                <span>{whenLabel(post.scheduled_at)}</span>
              </>
            ) : post.published_at ? (
              <Check size={24} strokeWidth={2.6} />
            ) : (
              <Send size={22} style={{ marginLeft: -2, marginTop: 1 }} />
            )}
          </motion.button>
        )}
      </div>

      <SchedulePicker
        open={picking}
        channel={post.channel.title}
        initial={post.scheduled_at}
        onClose={() => setPicking(false)}
        onPick={(at) => schedule(at)}
      />

      <Alert
        open={ask !== null}
        title={ask?.title ?? ''}
        text={ask?.text}
        buttons={ask?.buttons ?? []}
        onClose={() => setAsk(null)}
      />

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

      <PostSheet open={sheet}>
        <PostEditor
          initial={post.text}
          limit={limit}
          onLength={setLength}
          onRefused={refused}
          apiRef={editor}
          toolbarEnd={
            <>
              <button className="post-editor-button" onClick={() => setEditing(false)} aria-label="Отменить правки">
                <X size={20} />
              </button>
              <button className="chat-post-save" onClick={save} disabled={saving} aria-label="Сохранить пост">
                {saving ? <Loader2 size={18} className="spinner" /> : <Check size={18} strokeWidth={2.6} />}
              </button>
            </>
          }
          between={
            inChannel ? (
              <div className="chat-post-note channel-edit-note">
                <Info size={15} />
                <span>Изменяется пост в канале: Telegram меняет только текст, фото и видео останутся как есть.</span>
              </div>
            ) : (
              <>
                <CoverControl post={post} onChange={onChange} />
                {post.candidates.length > 0 && (
                  <div className="post-sheet-strip">
                    <div className="post-sheet-strip-tiles">{tiles}</div>
                  </div>
                )}
              </>
            )
          }
        />
        <div className="post-sheet-footer">
          {back}
          <span className="post-sheet-media">
            {post.candidates.length > 0 && !inChannel && `Медиа ${picked.length}/${post.max_media} · `}
            {counter}
          </span>
        </div>
      </PostSheet>

      <AnimatePresence>
        {viewing !== null && (
          <Lightbox
            photos={viewing.list.map((m) => ({ preview: m.url, full: m.full_url! }))}
            start={viewing.index}
            onClose={() => setViewing(null)}
          />
        )}
        {watching?.video_url && (
          <VideoViewer
            videoUrl={watching.video_url}
            poster={watching.url}
            fallback={{}}
            onClose={() => setWatching(null)}
          />
        )}
      </AnimatePresence>
    </article>
  )
}

/** The visible part of the screen: a phone's keyboard takes the rest. */
function useVisibleScreen() {
  const read = () => ({
    top: window.visualViewport?.offsetTop ?? 0,
    height: window.visualViewport?.height ?? window.innerHeight,
  })
  const [screen, setScreen] = useState(read)
  useEffect(() => {
    const v = window.visualViewport
    if (!v) return
    const update = () => setScreen(read())
    v.addEventListener('resize', update)
    v.addEventListener('scroll', update)
    return () => {
      v.removeEventListener('resize', update)
      v.removeEventListener('scroll', update)
    }
  }, [])
  return screen
}

/** A phone's window for editing a post, over the whole screen and above the keyboard: the buttons on top, the
 * pictures to swipe through, the text taking the rest. */
function PostSheet({ open, children }: { open: boolean; children: React.ReactNode }) {
  const screen = useVisibleScreen()
  useScrollLock(open)
  // a layer over the windows already open: the post may be edited from one (a day of «Календарь»)
  const level = useLayer(open)
  return createPortal(
    <AnimatePresence>
      {open && (
        <Leaving key="sheet">
          {(leaving) => (
            <motion.div
              className="post-sheet"
              role="dialog"
              aria-label="Редактирование поста"
              style={{ top: screen.top, height: screen.height, zIndex: backdropZ(level) + 1, ...leaving }}
              // the keyboard is open: the line under the text goes, the text takes the room
              data-keyboard={screen.height < window.innerHeight - 120 || undefined}
              initial={{ opacity: 0, y: 40 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 40 }}
              transition={spring}
            >
              {children}
            </motion.div>
          )}
        </Leaving>
      )}
    </AnimatePresence>,
    document.body,
  )
}

/** «Обложка» in the editor: on or off at once, its title saved when the field is left (empty: the first line). The
 * owner finds the project's cover style right here — the picture and «Стиль обложек» open it. */
function CoverControl({ post, onChange }: { post: ChatPostOut; onChange: (p: ChatPostOut) => void }) {
  const toast = useToast()
  const owner = useMe().role === 'user'
  const [title, setTitle] = useState(post.cover_title)
  const [busy, setBusy] = useState(false)
  const [style, setStyle] = useState(false)
  useEffect(() => setTitle(post.cover_title), [post.cover_title])
  const set = async (on: boolean, next?: string) => {
    setBusy(true)
    try {
      onChange(await api.setCover(post.id, on, next))
    } catch (e) {
      toast((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="cover-control">
      <div className="cover-control-row">
        <ImageIcon size={17} />
        <span className="cover-control-label">
          Обложка
          {!post.cover && <span className="cover-control-hint">Картинка с заголовком в стиле вашего канала</span>}
        </span>
        {busy && <Loader2 size={15} className="spinner" />}
        <Switch label="Обложка" on={post.cover} onChange={(on) => set(on)} />
      </div>
      {post.cover && (
        <div className="cover-control-body">
          {post.cover_url &&
            (owner ? (
              <button className="cover-control-picture" onClick={() => setStyle(true)} aria-label="Стиль обложек">
                <CoverPicture src={post.cover_url} ratio="1:1" alt="Обложка" />
              </button>
            ) : (
              <CoverPicture className="cover-control-picture" src={post.cover_url} ratio="1:1" alt="Обложка" />
            ))}
          <input
            className="cover-control-title"
            value={title}
            maxLength={200}
            placeholder="Заголовок на обложке"
            aria-label="Заголовок на обложке"
            onChange={(e) => setTitle(e.target.value)}
            onBlur={() => title !== post.cover_title && set(true, title)}
          />
        </div>
      )}
      {post.cover && owner && (
        <button className="cover-control-style" onClick={() => setStyle(true)}>
          Стиль обложек
          <ChevronRight size={15} />
        </button>
      )}
      {owner && (
        <CoversSheet
          open={style}
          onClose={() => {
            setStyle(false)
            // the picture in the new style
            api.setCover(post.id, post.cover).then(onChange, () => {})
          }}
        />
      )}
    </div>
  )
}
