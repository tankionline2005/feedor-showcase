import { AnimatePresence, motion, useIsPresent } from 'framer-motion'
import { ChevronLeft, Flashlight, FlashlightOff } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useLeavingStyle, useScrollLock } from '../lib/freezeGuard'
import { siteName } from '../lib/site'
import { spring } from '../lib/motion'
import { qrToken } from './Qr'

/** How often a frame is read: often enough to feel instant, rare enough to spare the battery. */
const READ_EVERY = 120

type Reader = (video: HTMLVideoElement) => Promise<string | null>

interface Detector {
  detect: (source: HTMLVideoElement) => Promise<{ rawValue: string }[]>
}

/** The browser's own QR reader where there is one (Chrome on Android), else jsQR on the middle of the frame. */
async function makeReader(): Promise<Reader> {
  const Native = (
    window as unknown as { BarcodeDetector?: { new (o: object): Detector; getSupportedFormats(): Promise<string[]> } }
  ).BarcodeDetector
  if (Native && (await Native.getSupportedFormats().catch((): string[] => [])).includes('qr_code')) {
    const detector = new Native({ formats: ['qr_code'] })
    return async (video) => (await detector.detect(video))[0]?.rawValue ?? null
  }
  const { default: jsQR } = await import('jsqr')
  const canvas = document.createElement('canvas')
  const ctx = canvas.getContext('2d', { willReadFrequently: true })!
  return async (video) => {
    const w = video.videoWidth
    const h = video.videoHeight
    if (!w || !h) return null
    // the square in the middle, where the frame is, at most 640 pixels: enough for a code that fills the frame
    const side = Math.min(w, h)
    const scale = Math.min(1, 640 / side)
    canvas.width = canvas.height = Math.round(side * scale)
    ctx.drawImage(video, (w - side) / 2, (h - side) / 2, side, side, 0, 0, canvas.width, canvas.height)
    const image = ctx.getImageData(0, 0, canvas.width, canvas.height)
    return jsQR(image.data, image.width, image.height, { inversionAttempts: 'dontInvert' })?.data ?? null
  }
}

type Trouble = { title: string; text: string } | null

function trouble(e: unknown): Trouble {
  const name = (e as { name?: string })?.name
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia)
    return { title: 'Камера недоступна', text: 'Этот браузер не даёт сайту камеру. Откройте сайт в Safari или Chrome.' }
  if (name === 'NotAllowedError' || name === 'SecurityError')
    return {
      title: 'Нет доступа к камере',
      text: 'Разрешите сайту доступ к камере в настройках браузера и попробуйте снова.',
    }
  if (name === 'NotFoundError' || name === 'OverconstrainedError')
    return { title: 'Камера не найдена', text: 'На этом устройстве нет камеры, которую можно открыть.' }
  if (name === 'NotReadableError')
    return { title: 'Камера занята', text: 'Её использует другое приложение. Закройте его и попробуйте снова.' }
  return { title: 'Не удалось открыть камеру', text: 'Попробуйте ещё раз.' }
}

/**
 * «Подключить устройство»: the camera over the whole screen, as Telegram's — the back button, what to scan, the
 * frame and the flashlight. A login page's QR code found, it closes and hands its code over.
 */
export function QrScanner({
  open,
  onClose,
  onScan,
}: {
  open: boolean
  onClose: () => void
  onScan: (token: string) => void
}) {
  return createPortal(
    <AnimatePresence>{open && <Camera onClose={onClose} onScan={onScan} />}</AnimatePresence>,
    document.body,
  )
}

function Camera({ onClose, onScan }: { onClose: () => void; onScan: (token: string) => void }) {
  const video = useRef<HTMLVideoElement>(null)
  const track = useRef<MediaStreamTrack | null>(null)
  const [attempt, setAttempt] = useState(0)
  const [problem, setProblem] = useState<Trouble>(null)
  const [live, setLive] = useState(false)
  const [torch, setTorch] = useState<boolean | null>(null) // null: this camera has none
  const [foreign, setForeign] = useState(false)
  const done = useRef(onScan)
  done.current = onScan
  useScrollLock(useIsPresent())
  const leaving = useLeavingStyle()

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  useEffect(() => {
    let stopped = false
    let stream: MediaStream | null = null
    let timer = 0
    let lastForeign = ''
    let foreignTimer = 0
    setProblem(null)
    setLive(false)
    ;(async () => {
      try {
        if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) throw new Error('insecure')
        stream = await navigator.mediaDevices.getUserMedia({
          audio: false,
          video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 }, height: { ideal: 1280 } },
        })
        if (stopped) return
        const el = video.current!
        el.srcObject = stream
        await el.play().catch(() => {})
        const t = stream.getVideoTracks()[0]
        track.current = t
        const caps = (t.getCapabilities?.() ?? {}) as { torch?: boolean }
        setTorch(caps.torch ? false : null)
        setLive(true)
        const read = await makeReader()
        const tick = async () => {
          if (stopped) return
          try {
            const text = el.readyState >= 2 ? await read(el) : null
            const token = text && qrToken(text)
            if (token) {
              navigator.vibrate?.(30)
              stopped = true
              done.current(token)
              return
            }
            if (text && text !== lastForeign) {
              // some other code: say so for a moment, and look on
              lastForeign = text
              setForeign(true)
              window.clearTimeout(foreignTimer)
              foreignTimer = window.setTimeout(() => {
                setForeign(false)
                lastForeign = ''
              }, 2500)
            }
          } catch {
            /* a frame that could not be read: the next one */
          }
          timer = window.setTimeout(tick, READ_EVERY)
        }
        tick()
      } catch (e) {
        if (!stopped) setProblem(trouble(e))
      }
    })()
    return () => {
      stopped = true
      window.clearTimeout(timer)
      window.clearTimeout(foreignTimer)
      stream?.getTracks().forEach((t) => t.stop())
      track.current = null
    }
  }, [attempt])

  const toggleTorch = async () => {
    const t = track.current
    if (!t || torch === null) return
    try {
      await t.applyConstraints({ advanced: [{ torch: !torch } as MediaTrackConstraintSet] })
      setTorch(!torch)
    } catch {
      setTorch(null)
    }
  }

  const host = location.host
  return (
    <motion.div
      className="scanner"
      role="dialog"
      aria-label="Сканировать QR-код"
      style={leaving}
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.2 }}
    >
      <video ref={video} className={`scanner-video${live ? ' live' : ''}`} playsInline muted autoPlay />
      <div className="scanner-shade" />
      <button className="scanner-back" onClick={onClose} aria-label="Назад">
        <ChevronLeft size={28} strokeWidth={2.4} />
      </button>
      <div className="scanner-head">
        <h2>Сканировать QR-код</h2>
        <p>
          Чтобы войти по QR-коду, откройте <span className="scanner-chip">{host}</span> на компьютере и нажмите кнопку с
          QR-кодом
        </p>
      </div>
      <div className="scanner-frame" aria-hidden="true">
        <i />
        <i />
        <i />
        <i />
      </div>
      <AnimatePresence>
        {foreign && (
          <motion.div
            className="scanner-hint"
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            transition={spring}
          >
            Это не QR-код для входа в {siteName()}
          </motion.div>
        )}
      </AnimatePresence>
      {problem && (
        <div className="scanner-problem">
          <b>{problem.title}</b>
          <span>{problem.text}</span>
          <button className="button" onClick={() => setAttempt((n) => n + 1)}>
            Попробовать снова
          </button>
        </div>
      )}
      {torch !== null && (
        <button
          className={`scanner-torch${torch ? ' on' : ''}`}
          onClick={toggleTorch}
          aria-label={torch ? 'Выключить фонарик' : 'Включить фонарик'}
        >
          {torch ? <Flashlight size={28} /> : <FlashlightOff size={28} />}
        </button>
      )}
    </motion.div>
  )
}
