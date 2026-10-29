import { AnimatePresence, motion } from 'framer-motion'
import { ArrowUp, Inbox, Loader2 } from 'lucide-react'
import { type ReactNode, useEffect, useRef } from 'react'
import type { Post, Sort } from '../api'
import type { SmartActions } from './PostCard'
import { dayLabel, plural } from '../lib/format'
import { spring } from '../lib/motion'
import { PostCard } from './PostCard'

interface Props {
  items: Post[]
  loading: boolean
  hasMore: boolean
  error: string | null
  onMore: () => void
  sort?: Sort
  ranked?: boolean
  showChannel?: boolean
  empty: { title: string; text: ReactNode }
  /** New posts held back while the reader is scrolled down (see usePosts). */
  pending?: number
  onShowPending?: () => void
  /** in the smart feed, for whoever sets it up: hide or show a post, with why */
  smartActions?: SmartActions
}

export function PostList({
  items,
  loading,
  hasMore,
  error,
  onMore,
  sort,
  ranked,
  showChannel,
  empty,
  pending = 0,
  onShowPending,
  smartActions,
}: Props) {
  const sentinel = useRef<HTMLDivElement>(null)
  const onMoreRef = useRef(onMore)
  onMoreRef.current = onMore

  useEffect(() => {
    const el = sentinel.current
    if (!el) return
    const io = new IntersectionObserver((entries) => entries[0].isIntersecting && onMoreRef.current(), {
      rootMargin: '800px',
    })
    io.observe(el)
    return () => io.disconnect()
  }, [])

  const firstLoad = loading && items.length === 0

  return (
    <>
      {error && <div className="error-banner">{error}</div>}
      <div className="new-posts-anchor">
        <AnimatePresence>
          {pending > 0 && (
            <motion.button
              className="new-posts-pill"
              onClick={onShowPending}
              initial={{ opacity: 0, y: -16, scale: 0.9 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: -16, scale: 0.9 }}
              whileTap={{ scale: 0.94 }}
              transition={spring}
            >
              <ArrowUp size={16} strokeWidth={2.4} />
              {pending} {plural(pending, 'новый пост', 'новых поста', 'новых постов')}
            </motion.button>
          )}
        </AnimatePresence>
      </div>
      <div className="feed">
        {firstLoad && <Skeletons />}
        {!loading && !error && items.length === 0 && (
          <motion.div
            className="empty"
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={spring}
          >
            <div className="empty-icon">
              <Inbox size={32} strokeWidth={1.6} />
            </div>
            <div className="empty-title">{empty.title}</div>
            <div>{empty.text}</div>
          </motion.div>
        )}
        <AnimatePresence initial={false} mode="popLayout">
          {items.flatMap((post, i) => {
            const day = sort === 'new' ? dayLabel(post.posted_at) : null
            const showDay = day !== null && (i === 0 || dayLabel(items[i - 1].posted_at) !== day)
            const card = (
              <motion.div
                key={post.id}
                layout="position"
                initial={{ opacity: 0, y: 14, scale: 0.98 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, scale: 0.97 }}
                transition={spring}
              >
                <PostCard
                  post={post}
                  sort={sort}
                  rank={ranked ? i + 1 : undefined}
                  showChannel={showChannel}
                  smartActions={smartActions}
                />
              </motion.div>
            )
            if (!showDay) return [card]
            return [
              <motion.div
                key={`day-${day}`}
                layout="position"
                className="day-separator"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={spring}
              >
                {day}
              </motion.div>,
              card,
            ]
          })}
        </AnimatePresence>
      </div>
      <div ref={sentinel} />
      {loading && items.length > 0 && hasMore && (
        <div className="loader">
          <Loader2 size={22} className="spinner" />
        </div>
      )}
    </>
  )
}

function Skeletons() {
  return (
    <>
      {[0, 1, 2].map((i) => (
        <div className="skeleton" key={i}>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            <div className="skeleton-line" style={{ width: 38, height: 38, borderRadius: 19 }} />
            <div className="skeleton-line" style={{ width: '40%' }} />
          </div>
          <div className="skeleton-line" style={{ width: '95%' }} />
          <div className="skeleton-line" style={{ width: '88%' }} />
          <div className="skeleton-line" style={{ width: '60%' }} />
        </div>
      ))}
    </>
  )
}
