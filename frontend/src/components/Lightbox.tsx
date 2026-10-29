import { AnimatePresence, motion, useIsPresent } from 'framer-motion'
import { ChevronLeft, ChevronRight, Loader2, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { useLeavingStyle, useScrollLock } from '../lib/freezeGuard'
import { spring } from '../lib/motion'

export interface LightboxPhoto {
  /** the small picture shown at once */
  preview: string
  /** the full-size photo, fetched from Telegram on demand (never stored on the server) */
  full: string
}

/** A photo over the whole screen: the preview at once, the full size as soon as it arrives. */
export function Lightbox({ photos, start, onClose }: { photos: LightboxPhoto[]; start: number; onClose: () => void }) {
  const [index, setIndex] = useState(start)
  const [loaded, setLoaded] = useState<Record<string, boolean>>({})
  const [failed, setFailed] = useState<Record<string, boolean>>({})
  const photo = photos[index]
  const many = photos.length > 1
  // the page scrolls again as soon as the photo starts fading out, not when it is gone
  useScrollLock(useIsPresent())
  const leaving = useLeavingStyle()

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
      else if (e.key === 'ArrowLeft') setIndex((i) => (i - 1 + photos.length) % photos.length)
      else if (e.key === 'ArrowRight') setIndex((i) => (i + 1) % photos.length)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, photos.length])

  const step = (delta: number) => (e: React.MouseEvent) => {
    e.stopPropagation()
    setIndex((i) => (i + delta + photos.length) % photos.length)
  }

  return createPortal(
    <motion.div
      className="lightbox"
      role="dialog"
      aria-label="Фото"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      style={leaving}
      transition={{ duration: 0.18 }}
      onClick={onClose}
    >
      <AnimatePresence mode="popLayout" initial={false}>
        <motion.div
          key={photo.full}
          className="lightbox-frame"
          initial={{ opacity: 0, scale: 0.96 }}
          animate={{ opacity: 1, scale: 1 }}
          exit={{ opacity: 0 }}
          transition={spring}
          onClick={(e) => e.stopPropagation()}
        >
          <img className="lightbox-preview" src={photo.preview} alt="" />
          {!failed[photo.full] && (
            <img
              className={`lightbox-full${loaded[photo.full] ? ' loaded' : ''}`}
              src={photo.full}
              alt=""
              onLoad={() => setLoaded((l) => ({ ...l, [photo.full]: true }))}
              onError={() => setFailed((f) => ({ ...f, [photo.full]: true }))}
            />
          )}
        </motion.div>
      </AnimatePresence>

      <div className="lightbox-status">
        {failed[photo.full] ? (
          'Не удалось загрузить фото в полном качестве'
        ) : !loaded[photo.full] ? (
          <>
            <Loader2 size={16} className="spinner" /> Загружаем полное качество…
          </>
        ) : many ? (
          `${index + 1} из ${photos.length}`
        ) : null}
      </div>

      <button className="lightbox-button close" aria-label="Закрыть" onClick={onClose}>
        <X size={22} />
      </button>
      {many && (
        <>
          <button className="lightbox-button prev" aria-label="Предыдущее фото" onClick={step(-1)}>
            <ChevronLeft size={26} />
          </button>
          <button className="lightbox-button next" aria-label="Следующее фото" onClick={step(1)}>
            <ChevronRight size={26} />
          </button>
        </>
      )}
    </motion.div>,
    document.body,
  )
}
