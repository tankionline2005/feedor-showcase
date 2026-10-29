import { AnimatePresence, motion } from 'framer-motion'
import { useEffect, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { backdropZ, isTop, useLayer } from '../lib/layers'

export interface AlertButton {
  label: string
  onClick: () => void
  /** the default answer: bold */
  strong?: boolean
  /** it takes something away: red */
  destructive?: boolean
  disabled?: boolean
}

/** An iOS alert: a title, a few words, and its buttons one under another. Escape closes it. */
export function Alert({
  open,
  title,
  text,
  children,
  buttons,
  onClose,
  wide = false,
}: {
  open: boolean
  title: string
  text?: ReactNode
  /** more under the text */
  children?: ReactNode
  buttons: AlertButton[]
  onClose: () => void
  /** a longer text: wider, read from the left */
  wide?: boolean
}) {
  const level = useLayer(open)
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && isTop(level) && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose, level])

  return createPortal(
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            key="backdrop"
            className="sheet-backdrop"
            style={{ zIndex: backdropZ(level) }}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            onClick={onClose}
          />
          <motion.div
            key="alert"
            className={`alert${wide ? ' wide' : ''}`}
            style={{ zIndex: backdropZ(level) + 1 }}
            role="alertdialog"
            aria-modal
            aria-label={title}
            initial={{ opacity: 0, scale: 1.12 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.96, transition: { duration: 0.15 } }}
            transition={{ type: 'spring', stiffness: 520, damping: 34 }}
          >
            <div className="alert-body">
              <div className="alert-title">{title}</div>
              {text && <div className="alert-text">{text}</div>}
              {children}
            </div>
            {buttons.map((b, i) => (
              <button
                key={i}
                className={`alert-button${b.strong ? ' strong' : ''}${b.destructive ? ' destructive' : ''}`}
                onClick={b.onClick}
                disabled={b.disabled}
              >
                {b.label}
              </button>
            ))}
          </motion.div>
        </>
      )}
    </AnimatePresence>,
    document.body,
  )
}
