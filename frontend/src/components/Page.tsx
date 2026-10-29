import { motion, useMotionValue, useScroll, useTransform, animate } from 'framer-motion'
import { CircleAlert, RefreshCw } from 'lucide-react'
import { createContext, type ReactNode, useContext, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useMouse, useWide } from '../hooks/useMedia'
import { spring } from '../lib/motion'
import { useMe } from '../hooks/useMe'
import { ProjectButton } from './ProjectSwitcher'

interface Props {
  title: string
  left?: ReactNode
  right?: ReactNode
  /** Rendered under the large title (search field, segmented control…) */
  accessory?: ReactNode
  /** Replaces the large title block (e.g. channel header) */
  hero?: ReactNode
  /** Small text to the right of the large title (e.g. what the current sort means) */
  titleAside?: ReactNode
  /** A small grey number right after the large title (e.g. how many channels there are) */
  titleCount?: number
  /** On a phone, the project switcher right of the large title (wide screens have it in the sidebar) */
  project?: boolean
  /** A button right of the large title, on every screen size (the feed's «AI») */
  titleAction?: ReactNode
  /** The title can be tapped (e.g. to rename a chat) */
  onTitleClick?: () => void
  /** On a phone, room right of the large title for the section's own buttons (TitleActions puts them there) */
  titleActions?: boolean
  /** «?» after the title: the section's welcome cards again */
  onHelp?: () => void
  onRefresh?: () => Promise<unknown>
  children: ReactNode
}

const PULL_TRIGGER = 72

// the room right of the large title on a phone (`titleActions`), for the page's sections
const TitleSlot = createContext<HTMLElement | null>(null)

/** A section's buttons: right of the page's large title on a phone, where they stand (inline) otherwise. */
export function TitleActions({ children }: { children: ReactNode }) {
  const slot = useContext(TitleSlot)
  return slot ? createPortal(children, slot) : <>{children}</>
}

/** The owner's banner under a section's title (backend billing.notice): the plan ends, the project is frozen, the AI
 * stands — a tap leads to «Тариф» or «Баланс». */
function NoticeBar() {
  const { notice } = useMe()
  if (!notice) return null
  return (
    <motion.button
      className={`notice-bar ${notice.tone}`}
      initial={{ opacity: 0, y: -4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={spring}
      whileTap={{ scale: 0.98 }}
      onClick={() => (location.hash = notice.action === 'balance' ? '/balance' : '/plan')}
    >
      <CircleAlert size={18} />
      <span>{notice.text}</span>
    </motion.button>
  )
}

/** iOS page: large title that collapses into a blurred navigation bar, plus pull-to-refresh. */
export function Page({
  title,
  left,
  right,
  accessory,
  hero,
  titleAside,
  titleCount,
  project,
  titleAction,
  titleActions,
  onTitleClick,
  onHelp,
  onRefresh,
  children,
}: Props) {
  const { scrollY } = useScroll()
  const collapseAt = hero ? 120 : 34
  // the bar and its title show as the page scrolls under them — and go when the page's top edge is in sight:
  // an iPhone flung up past the top bounces without telling its scroll position, the bar stayed hanging in the
  // empty space above the page; the top edge in sight is seen on every frame, bounce or not
  const edge = useRef<HTMLDivElement>(null)
  const atTop = useMotionValue(1)
  useEffect(() => {
    const el = edge.current
    if (!el) return
    const watch = new IntersectionObserver(([e]) => {
      animate(atTop, e.isIntersecting ? 1 : 0, { duration: 0.15 })
    })
    watch.observe(el)
    return () => watch.disconnect()
  }, [atTop])
  const titleByScroll = useTransform(scrollY, [collapseAt, collapseAt + 18], [0, 1])
  const barByScroll = useTransform(scrollY, [0, 10], [0, 1])
  const titleOpacity = useTransform(() => titleByScroll.get() * (1 - atTop.get()))
  const barOpacity = useTransform(() => barByScroll.get() * (1 - atTop.get()))
  const largeScale = useTransform(scrollY, [-150, 0], [1.1, 1], { clamp: true })

  const pull = useMotionValue(0)
  const iconRotate = useTransform(pull, [0, PULL_TRIGGER], [0, 270])
  const iconOpacity = useTransform(pull, [12, PULL_TRIGGER], [0, 1])
  const [refreshing, setRefreshing] = useState(false)
  const refreshRef = useRef(onRefresh)
  refreshRef.current = onRefresh
  const mouse = useMouse()
  const wide = useWide()
  const [slot, setSlot] = useState<HTMLElement | null>(null)

  const refreshByClick = async () => {
    if (!onRefresh || refreshing) return
    setRefreshing(true)
    try {
      await onRefresh()
    } finally {
      setRefreshing(false)
    }
  }

  useEffect(() => {
    if (!onRefresh || mouse) return
    let startY: number | null = null
    let busy = false
    const onStart = (e: TouchEvent) => {
      startY = window.scrollY <= 0 && !busy ? e.touches[0].clientY : null
    }
    const onMove = (e: TouchEvent) => {
      if (startY === null) return
      const dist = Math.max(0, e.touches[0].clientY - startY)
      pull.set(Math.min(110, dist * 0.45))
    }
    const onEnd = async () => {
      if (startY === null) return
      startY = null
      if (pull.get() >= PULL_TRIGGER && refreshRef.current) {
        busy = true
        setRefreshing(true)
        animate(pull, 52, spring)
        try {
          await refreshRef.current()
        } finally {
          busy = false
          setRefreshing(false)
          animate(pull, 0, spring)
        }
      } else {
        animate(pull, 0, spring)
      }
    }
    window.addEventListener('touchstart', onStart, { passive: true })
    window.addEventListener('touchmove', onMove, { passive: true })
    window.addEventListener('touchend', onEnd)
    return () => {
      window.removeEventListener('touchstart', onStart)
      window.removeEventListener('touchmove', onMove)
      window.removeEventListener('touchend', onEnd)
    }
  }, [onRefresh, pull, mouse])

  const help = onHelp && (
    <button className="title-help" onClick={onHelp} aria-label="Как это работает" title="Как это работает">
      ?
    </button>
  )
  const largeTitle =
    titleAside !== undefined ? (
      <div className="large-title-row">
        <motion.h1 className="large-title" style={{ scale: largeScale }}>
          {title}
          {titleCount !== undefined && <span className="title-count">{titleCount}</span>}
          {help}
        </motion.h1>
        <div className="title-aside">{titleAside}</div>
      </div>
    ) : (
      <motion.h1 className="large-title" style={{ scale: largeScale }}>
        {onTitleClick ? (
          <button className="large-title-button" onClick={onTitleClick}>
            {title}
          </button>
        ) : (
          title
        )}
        {titleCount !== undefined && <span className="title-count">{titleCount}</span>}
        {help}
      </motion.h1>
    )

  return (
    <div className="page">
      <div ref={edge} className="page-top-edge" aria-hidden="true" />
      <header className="navbar">
        <motion.div className="navbar-bg" style={{ opacity: barOpacity }} />
        <div className="navbar-side">{left}</div>
        <motion.div className="navbar-title" style={{ opacity: titleOpacity }}>
          {title}
        </motion.div>
        <div className="navbar-side right">
          {mouse && onRefresh && (
            <button className="nav-button" onClick={refreshByClick} aria-label="Обновить" title="Обновить">
              <RefreshCw size={19} className={refreshing ? 'spinner' : undefined} />
            </button>
          )}
          {right}
        </div>
      </header>

      {onRefresh && !mouse && (
        <motion.div className="ptr" style={{ height: pull }}>
          <motion.div style={{ rotate: iconRotate, opacity: iconOpacity, paddingBottom: 14 }}>
            <RefreshCw size={22} className={refreshing ? 'spinner' : undefined} />
          </motion.div>
        </motion.div>
      )}

      {hero ??
        (titleAction || (project && !wide) ? (
          <div className="large-title-bar">
            {largeTitle}
            {titleAction ?? <ProjectButton size={36} />}
          </div>
        ) : titleActions && !wide ? (
          <div className="large-title-bar">
            {largeTitle}
            <div className="title-actions" ref={setSlot} />
          </div>
        ) : (
          largeTitle
        ))}
      <TitleSlot.Provider value={titleActions && !wide ? slot : null}>
        {!left && !hero && <NoticeBar />}
        {accessory}
        {children}
      </TitleSlot.Provider>
    </div>
  )
}
