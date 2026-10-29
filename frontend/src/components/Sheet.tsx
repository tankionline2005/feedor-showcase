import { AnimatePresence, motion, useDragControls } from 'framer-motion'
import { type ReactNode, useEffect } from 'react'
import { createPortal } from 'react-dom'
import { useWide } from '../hooks/useMedia'
import { sheetSpring } from '../lib/motion'
import { Leaving, useScrollLock } from '../lib/freezeGuard'
import { backdropZ, isTop, useLayer } from '../lib/layers'

interface Props {
  open: boolean
  onClose: () => void
  title?: ReactNode
  left?: ReactNode
  right?: ReactNode
  children: ReactNode
}

// centred by the stylesheet, not by a transform of -50%: framer-motion measures the layout animations inside (a
// segmented control's thumb, a check moving to the chosen item) through the window's own transform, and a
// percentage there sent them flying from half a window away on every render
const dialogMotion = {
  initial: { opacity: 0, scale: 0.96, y: 12 },
  animate: { opacity: 1, scale: 1, y: 0 },
  exit: { opacity: 0, scale: 0.97, y: 6, transition: { duration: 0.15 } },
}

/**
 * Phone: iOS bottom sheet that slides up and is dismissed by dragging it down.
 * Wide screen: a centered dialog (no drag), closed by Esc, the backdrop or the buttons.
 */
export function Sheet({ open, onClose, title, left, right, children }: Props) {
  const controls = useDragControls()
  const wide = useWide()

  useScrollLock(open)
  const level = useLayer(open)
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && isTop(level) && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose, level])

  // rendered at the page root: a transformed ancestor (an animating card, the page transition)
  // would otherwise become the frame of "position: fixed" and the sheet would appear inside it
  return createPortal(
    <AnimatePresence>
      {open && (
        <Leaving key="layers">
          {(leaving) => (
            <>
              <motion.div
                key="backdrop"
                className="sheet-backdrop"
                style={{ ...leaving, zIndex: backdropZ(level) }}
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.25 }}
                onClick={onClose}
              />
              <motion.div
                key="sheet"
                style={{ ...leaving, zIndex: backdropZ(level) + 1 }}
                className={`sheet${wide ? ' dialog' : ''}`}
                role="dialog"
                aria-modal
                {...(wide
                  ? { ...dialogMotion, transition: sheetSpring }
                  : {
                      initial: { y: '100%' },
                      animate: { y: 0 },
                      exit: { y: '100%' },
                      transition: sheetSpring,
                      drag: 'y' as const,
                      dragListener: false,
                      dragControls: controls,
                      dragConstraints: { top: 0, bottom: 0 },
                      dragElastic: { top: 0.05, bottom: 0.7 },
                      onDragEnd: (_: unknown, info: { offset: { y: number }; velocity: { y: number } }) => {
                        if (info.offset.y > 110 || info.velocity.y > 600) onClose()
                      },
                    })}
              >
                <div onPointerDown={wide ? undefined : (e) => controls.start(e)} style={{ touchAction: 'none' }}>
                  {!wide && <div className="sheet-grabber" />}
                  <div className="sheet-header">
                    <div className="navbar-side">{left}</div>
                    <div className="sheet-title">{title}</div>
                    <div className="navbar-side right">{right}</div>
                  </div>
                </div>
                {/* layoutScroll: the layout animations inside measure with this scroll, not jump by it on a re-render */}
                <motion.div className="sheet-body" layoutScroll>
                  {children}
                </motion.div>
              </motion.div>
            </>
          )}
        </Leaving>
      )}
    </AnimatePresence>,
    document.body,
  )
}

export interface SheetAction {
  label: string
  destructive?: boolean
  onClick: () => void
}

interface ActionSheetProps {
  open: boolean
  onClose: () => void
  title?: string
  message?: string
  actions: SheetAction[]
}

/** Phone: iOS action sheet from the bottom. Wide screen: a compact centered alert. */
export function ActionSheet({ open, onClose, title, message, actions }: ActionSheetProps) {
  const wide = useWide()

  const level = useLayer(open)
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && isTop(level) && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose, level])

  // rendered at the page root: a transformed ancestor (an animating card, the page transition)
  // would otherwise become the frame of "position: fixed" and the sheet would appear inside it
  return createPortal(
    <AnimatePresence>
      {open && (
        <Leaving key="layers">
          {(leaving) => (
            <>
              <motion.div
                key="backdrop"
                className="sheet-backdrop"
                style={{ ...leaving, zIndex: backdropZ(level) }}
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.2 }}
                onClick={onClose}
              />
              <motion.div
                key="actions"
                style={{ ...leaving, zIndex: backdropZ(level) + 1 }}
                className={`action-sheet${wide ? ' dialog' : ''}`}
                role="dialog"
                aria-modal
                {...(wide
                  ? dialogMotion
                  : {
                      initial: { y: '110%' },
                      animate: { y: 0 },
                      exit: { y: '110%' },
                    })}
                transition={sheetSpring}
              >
                <div className="action-group">
                  {(title || message) && (
                    <div className="action-head">
                      {title && <div className="action-title">{title}</div>}
                      {message && <div className="action-message">{message}</div>}
                    </div>
                  )}
                  {actions.map((a) => (
                    <button
                      key={a.label}
                      className={`action-button${a.destructive ? ' destructive' : ''}`}
                      onClick={() => {
                        onClose()
                        a.onClick()
                      }}
                    >
                      {a.label}
                    </button>
                  ))}
                </div>
                <button className="action-button cancel" onClick={onClose}>
                  Отмена
                </button>
              </motion.div>
            </>
          )}
        </Leaving>
      )}
    </AnimatePresence>,
    document.body,
  )
}
