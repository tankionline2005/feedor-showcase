import { animate, motion, useMotionValue, useTransform } from 'framer-motion'
import type { LucideIcon } from 'lucide-react'
import { type ReactNode, useEffect, useRef } from 'react'
import { useMouse } from '../hooks/useMedia'
import { spring } from '../lib/motion'

export interface SwipeAction {
  label: string
  icon: LucideIcon
  color: string
  onClick: () => void
}

const ACTION_WIDTH = 80

/** List row with actions: revealed by a left swipe on touch screens, shown on hover with a mouse. */
export function SwipeRow({ actions, children }: { actions: SwipeAction[]; children: ReactNode }) {
  const mouse = useMouse()
  if (actions.length === 0) return <>{children}</>
  return mouse ? <HoverRow actions={actions}>{children}</HoverRow> : <TouchRow actions={actions}>{children}</TouchRow>
}

function HoverRow({ actions, children }: { actions: SwipeAction[]; children: ReactNode }) {
  return (
    <div className="swipe-row hover-row">
      {children}
      <div className="hover-actions">
        {actions.map(({ label, icon: Icon, color, onClick }) => (
          <button
            key={label}
            className="hover-action"
            style={{ ['--action' as string]: color }}
            onClick={onClick}
            title={label}
            aria-label={label}
          >
            <Icon size={17} />
          </button>
        ))}
      </div>
    </div>
  )
}

// Like iOS lists: only one row is open at a time.
let closeOpenRow: (() => void) | null = null

function TouchRow({ actions, children }: { actions: SwipeAction[]; children: ReactNode }) {
  const x = useMotionValue(0)
  const width = actions.length * ACTION_WIDTH
  const dragged = useRef(false)
  const rowRef = useRef<HTMLDivElement>(null)
  // the actions appear only once the row actually moves, so nothing shows through it on a tap
  const actionsOpacity = useTransform(x, [-12, 0], [1, 0])

  const closeRef = useRef(() => {})
  closeRef.current = () => {
    animate(x, 0, spring)
    if (closeOpenRow === closeRef.current) closeOpenRow = null
  }
  const close = () => closeRef.current()

  const closeOthers = () => {
    if (closeOpenRow && closeOpenRow !== closeRef.current) closeOpenRow()
  }

  // an open row closes on scroll or on a touch anywhere else
  useEffect(() => {
    const isOpen = () => x.get() < -1
    const onPointerDown = (e: PointerEvent) => {
      if (isOpen() && !rowRef.current?.contains(e.target as Node)) closeRef.current()
    }
    const onScroll = () => isOpen() && closeRef.current()
    document.addEventListener('pointerdown', onPointerDown, true)
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => {
      document.removeEventListener('pointerdown', onPointerDown, true)
      window.removeEventListener('scroll', onScroll)
      if (closeOpenRow === closeRef.current) closeOpenRow = null
    }
  }, [x])

  return (
    <div className="swipe-row" ref={rowRef}>
      <motion.div className="swipe-actions" style={{ width, opacity: actionsOpacity }}>
        {actions.map(({ label, icon: Icon, color, onClick }) => (
          <button
            key={label}
            className="swipe-action"
            style={{ background: color }}
            onClick={() => {
              close()
              onClick()
            }}
          >
            <Icon size={22} />
            {label}
          </button>
        ))}
      </motion.div>
      <motion.div
        style={{ x, position: 'relative', zIndex: 1, touchAction: 'pan-y' }}
        drag="x"
        dragDirectionLock
        dragConstraints={{ left: -width, right: 0 }}
        dragElastic={{ left: 0.15, right: 0 }}
        dragMomentum={false}
        onDragStart={() => {
          dragged.current = true
          closeOthers()
        }}
        onDragEnd={(_, info) => {
          const open = x.get() < -width / 2 || info.velocity.x < -400
          animate(x, open ? -width : 0, spring)
          if (open) closeOpenRow = closeRef.current
          else if (closeOpenRow === closeRef.current) closeOpenRow = null
          setTimeout(() => (dragged.current = false), 0)
        }}
        onClickCapture={(e) => {
          // a drag must not open the row; a tap on an open row closes it
          if (dragged.current || x.get() < -4) {
            e.stopPropagation()
            e.preventDefault()
            close()
          }
        }}
      >
        {children}
      </motion.div>
    </div>
  )
}
