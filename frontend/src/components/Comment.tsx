import DOMPurify from 'dompurify'
import { AnimatePresence } from 'framer-motion'
import { FileText, Loader2, Pause, Play, Sparkles, Star } from 'lucide-react'
import { memo, useEffect, useMemo, useRef, useState } from 'react'
import { api, type CommentMedia, type PostComment } from '../api'
import { compact, time } from '../lib/format'
import { Avatar } from './Avatar'
import { Lightbox } from './Lightbox'
import { ALLOWED_TAGS } from './PostCard'
import { VideoViewer } from './VideoViewer'

interface Props {
  comment: PostComment
  postId: number
  /** the comment in Telegram, for what does not play here */
  link: string | null
  /** a quote tapped: the page goes to the comment it quotes */
  onJump: (id: number) => void
}

/** A comment as Telegram shows one: a letter avatar, a bubble with the author's name in their colour (and
 * @username), the comment it answers, its picture, sticker or voice message, the text, the time. */
function CommentInner({ comment: c, postId, link, onJump }: Props) {
  const html = useMemo(() => DOMPurify.sanitize(c.html || '', { ALLOWED_TAGS, ALLOWED_ATTR: ['href'] }), [c.html])
  const bare = c.media?.kind === 'sticker' && !html && !c.reply
  return (
    <div className="comment" id={`comment-${c.id}`}>
      <Avatar title={c.author.name} seed={c.author.seed} size={34} />
      <div className={`comment-bubble${bare ? ' bare' : ''}`}>
        <div className={`comment-author peer-${c.author.seed}`}>
          {c.author.name}
          {c.author.username && <span className="comment-username"> (@{c.author.username})</span>}
        </div>
        {c.reply && (
          <button className="comment-reply" onClick={() => onJump(c.reply!.id)}>
            <b>{c.reply.name ?? 'Комментарий'}</b>
            <span>{c.reply.text || 'Сообщение'}</span>
          </button>
        )}
        {c.media && <Media media={c.media} postId={postId} commentId={c.id} link={link} />}
        {html && <div className="comment-text" dangerouslySetInnerHTML={{ __html: html }} />}
        <div className="comment-meta">
          {c.reactions.length > 0 && (
            <span className="comment-reactions">
              {c.reactions.map((r) => (
                <span key={r.e} className="comment-reaction">
                  {r.e === 'custom' ? (
                    <Sparkles size={13} />
                  ) : r.e === 'paid' ? (
                    <Star size={13} fill="currentColor" />
                  ) : (
                    <span className="reaction-emoji">{r.e}</span>
                  )}
                  {compact(r.c)}
                </span>
              ))}
            </span>
          )}
          <span className="comment-time">
            {c.edited && 'изменено '}
            {time(c.date)}
          </span>
        </div>
      </div>
    </div>
  )
}

export const Comment = memo(CommentInner)

function Media({
  media,
  postId,
  commentId,
  link,
}: {
  media: CommentMedia
  postId: number
  commentId: number
  link: string | null
}) {
  const [open, setOpen] = useState(false)
  if (media.kind === 'voice') return <Voice postId={postId} commentId={commentId} {...media} link={link} />
  if (media.kind === 'sticker') return <Sticker postId={postId} commentId={commentId} format={media.format} />
  if (media.kind === 'other')
    return (
      <div className="comment-other">
        <FileText size={18} />
        <span>{media.label}</span>
      </div>
    )
  const thumb = media.kind === 'photo' || media.thumb ? api.commentMedia(postId, commentId, 'thumb') : null
  const ratio = media.w && media.h ? media.w / media.h : media.kind === 'round' ? 1 : 16 / 9
  return (
    <>
      <button
        className={`comment-picture${media.kind === 'round' ? ' round' : ''}`}
        style={{
          aspectRatio: String(Math.min(Math.max(ratio, 0.6), 2)),
          backgroundImage: media.blur ? `url(${media.blur})` : undefined,
        }}
        onClick={() => setOpen(true)}
        aria-label={media.kind === 'photo' ? 'Открыть фото' : 'Смотреть видео'}
      >
        {thumb && <img src={thumb} alt="" loading="lazy" />}
        {media.kind !== 'photo' && (
          <>
            <span className="comment-play">
              <Play size={22} fill="currentColor" />
            </span>
            <span className="post-media-badge">{media.kind === 'gif' ? 'GIF' : clock(media.duration ?? 0)}</span>
          </>
        )}
      </button>
      <AnimatePresence>
        {open && media.kind === 'photo' && (
          <Lightbox
            photos={[{ preview: thumb!, full: api.commentMedia(postId, commentId, 'full') }]}
            start={0}
            onClose={() => setOpen(false)}
          />
        )}
        {open && media.kind !== 'photo' && (
          <VideoViewer
            videoUrl={api.commentVideo(postId, commentId)}
            poster={thumb ?? media.blur ?? ''}
            fallback={{ link }}
            onClose={() => setOpen(false)}
          />
        )}
      </AnimatePresence>
    </>
  )
}

function clock(seconds: number): string {
  const s = Math.max(0, Math.round(seconds))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

// one voice message plays at a time, as in Telegram
let playing: HTMLAudioElement | null = null
const BARS = 36

/** A voice message: its waveform and length at once; the sound itself is fetched on the first tap. */
function Voice({
  postId,
  commentId,
  duration,
  waveform,
  link,
}: {
  postId: number
  commentId: number
  duration: number
  waveform: number[]
  link: string | null
}) {
  const audio = useRef<HTMLAudioElement | null>(null)
  const [state, setState] = useState<'idle' | 'loading' | 'playing' | 'paused' | 'error'>('idle')
  const [at, setAt] = useState(0)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => () => audio.current?.pause(), [])

  const bars = useMemo(() => {
    if (waveform.length === 0) return Array.from({ length: BARS }, () => 4)
    return Array.from({ length: BARS }, (_, i) => {
      const from = Math.floor((i * waveform.length) / BARS)
      const to = Math.max(from + 1, Math.floor(((i + 1) * waveform.length) / BARS))
      return Math.max(...waveform.slice(from, to))
    })
  }, [waveform])

  const toggle = () => {
    if (state === 'playing') {
      audio.current?.pause()
      return
    }
    let a = audio.current
    if (!a) {
      if (!new Audio().canPlayType('audio/ogg; codecs=opus')) {
        setState('error')
        setError('Этот браузер не умеет воспроизводить голосовые сообщения Telegram.')
        return
      }
      a = new Audio(api.commentMedia(postId, commentId, 'file'))
      audio.current = a
      setState('loading')
      a.addEventListener('playing', () => setState('playing'))
      a.addEventListener('pause', () => setState('paused'))
      a.addEventListener('timeupdate', () => setAt(a!.currentTime))
      a.addEventListener('ended', () => {
        setState('paused')
        setAt(0)
      })
      a.addEventListener('error', () => {
        setState('error')
        setError('Не удалось загрузить голосовое сообщение.')
      })
    }
    if (playing && playing !== a) playing.pause()
    playing = a
    a.play().catch(() => undefined)
  }

  const progress = duration ? Math.min(1, at / duration) : 0
  return (
    <div className="comment-voice">
      <button className="comment-voice-button" onClick={toggle} aria-label={state === 'playing' ? 'Пауза' : 'Слушать'}>
        {state === 'loading' ? (
          <Loader2 size={20} className="spinner" />
        ) : state === 'playing' ? (
          <Pause size={20} fill="currentColor" />
        ) : (
          <Play size={20} fill="currentColor" style={{ marginLeft: 2 }} />
        )}
      </button>
      <div className="comment-voice-body">
        <div className="comment-voice-wave" aria-hidden>
          {bars.map((v, i) => (
            <span
              key={i}
              className={i / BARS < progress ? 'played' : undefined}
              style={{ height: 3 + Math.round((v / 31) * 17) }}
            />
          ))}
        </div>
        <div className="comment-voice-time">
          {error ? (
            <>
              {error}
              {link && (
                <>
                  {' '}
                  <a href={link} target="_blank" rel="noopener noreferrer">
                    Открыть в Telegram
                  </a>
                </>
              )}
            </>
          ) : (
            clock(state === 'idle' ? duration : at || duration)
          )}
        </div>
      </div>
    </div>
  )
}

/** A sticker: «Стикер» until tapped, then the sticker itself — a picture, a little looped video, or a Lottie
 * animation (played by lottie-web, loaded only then). */
function Sticker({
  postId,
  commentId,
  format,
}: {
  postId: number
  commentId: number
  format: 'webp' | 'tgs' | 'webm'
}) {
  const [shown, setShown] = useState(false)
  const [failed, setFailed] = useState(false)
  const [loading, setLoading] = useState(false)
  const box = useRef<HTMLDivElement>(null)
  const src = api.commentMedia(postId, commentId, 'file')

  useEffect(() => {
    if (!shown || format !== 'tgs') return
    let gone = false
    let anim: { destroy: () => void } | null = null
    setLoading(true)
    Promise.all([fetch(src, { credentials: 'same-origin' }), import('lottie-web/build/player/lottie_light')])
      .then(async ([res, lottie]) => {
        if (!res.ok) throw new Error(String(res.status))
        const data = await res.json()
        if (gone || !box.current) return
        anim = lottie.default.loadAnimation({
          container: box.current,
          renderer: 'svg',
          loop: true,
          autoplay: true,
          animationData: data,
        })
      })
      .catch(() => !gone && setFailed(true))
      .finally(() => !gone && setLoading(false))
    return () => {
      gone = true
      anim?.destroy()
    }
  }, [shown, format, src])

  if (!shown)
    return (
      <button className="comment-sticker-label" onClick={() => setShown(true)}>
        Стикер
      </button>
    )
  if (failed) return <div className="comment-other">Не удалось загрузить стикер</div>
  return (
    <div className="comment-sticker">
      {format === 'webp' && <img src={src} alt="Стикер" onError={() => setFailed(true)} />}
      {format === 'webm' && (
        <video src={src} autoPlay loop muted playsInline onError={() => setFailed(true)} aria-label="Стикер" />
      )}
      {format === 'tgs' && <div ref={box} className="comment-sticker-lottie" />}
      {loading && <Loader2 size={22} className="spinner comment-sticker-spinner" />}
    </div>
  )
}
