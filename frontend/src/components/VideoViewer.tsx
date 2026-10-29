import { motion, useIsPresent } from 'framer-motion'
import { AlertCircle, ArrowUpRight, Forward, Loader2, RotateCcw, Volume2, X } from 'lucide-react'
import { type CSSProperties, useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { api, type VideoSource } from '../api'
import { useLeavingStyle, useScrollLock } from '../lib/freezeGuard'
import type { VideoFallback } from '../lib/videoFallback'
import { useToast } from './Toast'

const SLOW_MS = 15000 // no picture this long: the person is told, and offered Telegram
// a format the browser cannot play: HEVC from iPhones in Firefox and part of Chrome, mostly
const UNPLAYABLE = 'Этот браузер не умеет воспроизводить формат этого видео.'
const NOT_LOADED = 'Не удалось загрузить видео.'
// a broken link and a format the browser does not know read the same to it
const NOT_PLAYED =
  'Не получилось воспроизвести это видео: браузер не поддерживает его формат или Telegram не отдал файл.'

/** A post's video over the whole screen, played straight from Telegram's servers (the server finds the link on
 * Telegram's public page of the post). A GIF plays muted in a loop, a «кружок» in a circle. Whatever goes wrong —
 * Telegram does not give it, the browser cannot play it, the link expired twice — is said, with Telegram offered. */
export function VideoViewer({
  videoUrl,
  poster,
  fallback,
  onClose,
}: {
  videoUrl: string
  poster: string
  fallback: VideoFallback
  onClose: () => void
}) {
  const [source, setSource] = useState<Extract<VideoSource, { url: string }> | null>(null)
  const [error, setError] = useState<{ message: string; retry: boolean } | null>(null)
  const [ready, setReady] = useState(false)
  const [buffering, setBuffering] = useState(false)
  const [slow, setSlow] = useState(false)
  const [muted, setMuted] = useState(false)
  // width / height: Telegram's page tells it roughly (it boxes tall videos at 3:4), the video itself exactly
  const [ratio, setRatio] = useState<number | null>(null)
  const [sending, setSending] = useState(false)
  const video = useRef<HTMLVideoElement>(null)
  // the page scrolls again as soon as the video starts fading out, not when it is gone; and it stops playing
  const present = useIsPresent()
  useScrollLock(present)
  const leaving = useLeavingStyle()
  useEffect(() => {
    if (!present) video.current?.pause()
  }, [present])
  // the link lives about three hours: one that stops playing is asked for anew, once, from where it stopped
  const refreshed = useRef(false)
  const resumeAt = useRef(0)
  const toast = useToast()

  const load = useCallback(
    async (fresh = false) => {
      setError(null)
      try {
        const got = await api.video(videoUrl, fresh)
        if (got.error) setError({ message: got.message, retry: got.error === 'telegram' })
        else setSource(got)
      } catch (e) {
        setError({ message: (e as Error).message || NOT_LOADED, retry: true })
      }
    },
    [videoUrl],
  )

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  // started by the person's tap, but after a request: a browser (Safari above all) may not let it start with
  // sound by itself. A GIF is muted anyway; a «кружок» then plays muted, a tap gives the sound; a video waits
  // for its play button
  useEffect(() => {
    const v = video.current
    if (!source || !v) return
    setReady(false)
    setSlow(false)
    if (resumeAt.current) v.currentTime = resumeAt.current
    v.play().catch(() => {
      if (source.kind === 'round') {
        v.muted = true
        setMuted(true)
        v.play().catch(() => undefined)
      }
    })
    const timer = window.setTimeout(() => setSlow(true), SLOW_MS)
    return () => window.clearTimeout(timer)
  }, [source])

  const failed = (message: string) => {
    const v = video.current
    if (!refreshed.current) {
      // an expired link reads as a broken file: a new link first
      refreshed.current = true
      resumeAt.current = v?.currentTime ?? 0
      setSource(null)
      load(true)
      return
    }
    setError({ message, retry: false })
  }

  const onVideoError = () => {
    const code = video.current?.error?.code
    failed(code === 3 ? UNPLAYABLE : code === 4 ? NOT_PLAYED : NOT_LOADED)
  }

  const onLoadedData = () => {
    const v = video.current
    setReady(true)
    setSlow(false)
    if (v?.videoWidth && v.videoHeight) setRatio(v.videoWidth / v.videoHeight)
    // a video track the browser cannot decode plays as sound only, with no picture
    if (v && source?.kind !== 'round' && v.videoWidth === 0 && v.videoHeight === 0) {
      v.pause()
      setError({ message: UNPLAYABLE, retry: false })
    }
  }

  const tapVideo = () => {
    const v = video.current
    if (!v || source?.kind === 'video') return // its own controls
    if (v.muted && source?.kind === 'round') {
      v.muted = false
      setMuted(false)
      if (v.paused) v.play().catch(() => undefined)
    } else if (v.paused) v.play().catch(() => undefined)
    else v.pause()
  }

  const send = async () => {
    if (!fallback.send) return
    setSending(true)
    try {
      await fallback.send()
      toast('Пост отправлен вам в Telegram')
      onClose()
    } catch (e) {
      toast((e as Error).message)
    } finally {
      setSending(false)
    }
  }

  // sized by its proportions: a tall video is not put in a wide box
  const proportions = ratio ?? source?.ratio
  const sized = Boolean(proportions && source?.kind !== 'round')
  const style = sized
    ? ({ '--ratio': String(proportions), aspectRatio: String(proportions) } as CSSProperties)
    : undefined
  const waiting = !error && (!source || !ready || buffering)

  return createPortal(
    <motion.div
      className="lightbox video-viewer"
      role="dialog"
      aria-label="Видео"
      style={leaving}
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.18 }}
      onClick={onClose}
    >
      <div className={`video-frame${source?.kind === 'round' ? ' round' : ''}`} onClick={(e) => e.stopPropagation()}>
        {(!source || !ready || error) && (
          <img className={`video-poster${source?.kind === 'round' ? ' round' : ''}`} src={poster} alt="" />
        )}
        {source && !error && (
          <video
            ref={video}
            key={source.url}
            className={[ready && 'ready', sized && 'sized'].filter(Boolean).join(' ') || undefined}
            style={style}
            src={source.url}
            poster={poster}
            playsInline
            preload="auto"
            controls={source.kind === 'video'}
            muted={source.kind === 'gif'}
            loop={source.kind !== 'video'}
            onClick={tapVideo}
            onLoadedData={onLoadedData}
            onCanPlay={() => setSlow(false)}
            onWaiting={() => setBuffering(true)}
            onPlaying={() => setBuffering(false)}
            onError={onVideoError}
          />
        )}
        {muted && !error && (
          <button className="video-unmute" onClick={tapVideo}>
            <Volume2 size={16} /> Нажмите, чтобы включить звук
          </button>
        )}
      </div>

      {error ? (
        <div className="video-error" onClick={(e) => e.stopPropagation()}>
          <AlertCircle size={28} />
          <div className="video-error-text">{error.message}</div>
          <div className="video-error-actions">
            {fallback.send ? (
              <button className="button" onClick={send} disabled={sending}>
                {sending ? <Loader2 size={17} className="spinner" /> : <Forward size={18} />}
                Прислать в Telegram
              </button>
            ) : (
              fallback.link && (
                <a className="button" href={fallback.link} target="_blank" rel="noopener noreferrer">
                  <ArrowUpRight size={17} />
                  Открыть в Telegram
                </a>
              )
            )}
            {error.retry && (
              <button className="button secondary" onClick={() => load()}>
                <RotateCcw size={16} />
                Попробовать ещё раз
              </button>
            )}
          </div>
        </div>
      ) : (
        (waiting || slow) && (
          <div className="lightbox-status" onClick={(e) => e.stopPropagation()}>
            <Loader2 size={16} className="spinner" />
            {slow && !ready ? 'Видео загружается медленно…' : 'Загружаем видео…'}
            {slow && !ready && fallback.send && (
              <button className="video-status-action" onClick={send} disabled={sending}>
                Прислать в Telegram
              </button>
            )}
          </div>
        )
      )}

      <button className="lightbox-button close" aria-label="Закрыть" onClick={onClose}>
        <X size={22} />
      </button>
    </motion.div>,
    document.body,
  )
}
