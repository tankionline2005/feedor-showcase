import { animate, motion, useMotionValue, useTransform } from 'framer-motion'
import { AlertCircle, X } from 'lucide-react'
import type { ReactNode } from 'react'
import { useMouse } from '../hooks/useMedia'
import { spring } from '../lib/motion'

const SWIPE_DISTANCE = 90

/**
 * An error note the user can hide: a close button with a mouse, a sideways swipe on touch.
 * Render it inside <AnimatePresence> so it collapses smoothly once `onDismiss` removes it.
 */
export function DismissibleNote({ children, onDismiss }: { children: ReactNode; onDismiss: () => void }) {
  const mouse = useMouse()
  const x = useMotionValue(0)
  const opacity = useTransform(x, [-180, 0, 180], [0, 1, 0])

  return (
    <motion.div
      className="note"
      role="alert"
      style={mouse ? undefined : { x, opacity, touchAction: 'pan-y' }}
      initial={{ opacity: 0, height: 0 }}
      animate={{ opacity: 1, height: 'auto' }}
      exit={{ opacity: 0, height: 0, marginTop: 0, transition: { duration: 0.2 } }}
      drag={mouse ? false : 'x'}
      dragDirectionLock
      dragMomentum={false}
      onDragEnd={(_, info) => {
        const flung = Math.abs(info.offset.x) > SWIPE_DISTANCE || Math.abs(info.velocity.x) > 500
        if (flung) {
          const direction = Math.sign(info.offset.x || info.velocity.x) || 1
          animate(x, direction * 420, { duration: 0.18 }).then(onDismiss)
        } else {
          animate(x, 0, spring)
        }
      }}
    >
      <div className="note-inner">
        <AlertCircle size={16} style={{ flex: 'none', marginTop: 1 }} />
        <div className="note-text">
          {children}
          {!mouse && <div className="note-hint">Смахните, чтобы скрыть</div>}
        </div>
        {mouse && (
          <button className="note-close" onClick={onDismiss} aria-label="Скрыть" title="Скрыть">
            <X size={16} />
          </button>
        )}
      </div>
    </motion.div>
  )
}
