import { motion } from 'framer-motion'
import { ChevronLeft, Loader2, MessageCircle } from 'lucide-react'
import { Fragment, useCallback, useEffect, useRef, useState } from 'react'
import { api, type Post, type PostComment } from '../api'
import { Comment } from '../components/Comment'
import { Page } from '../components/Page'
import { PostCard } from '../components/PostCard'
import { commentLink, openedPost } from '../lib/comments'
import { dayLabel, plural } from '../lib/format'

const BACK_LABEL: Record<string, string> = {
  feed: 'Лента',
  top: 'Топ',
  ai: 'AI',
  channels: 'Каналы',
  settings: 'Назад',
}

/** A post's comments, as Telegram shows them under a channel post: the post on top, the comments below from the
 * first one, more of them as the page scrolls down. Telegram is asked when the page opens — nothing is stored. */
export function CommentsScreen({ id, tab, onBack }: { id: number; tab: string; onBack: () => void }) {
  const [post, setPost] = useState<Post | null>(() => openedPost(id) ?? null)
  const [postError, setPostError] = useState<string | null>(null)
  const [items, setItems] = useState<PostComment[]>([])
  const [total, setTotal] = useState<number | null>(null)
  const [group, setGroup] = useState<string | null>(null)
  const [hasMore, setHasMore] = useState(true)
  const [loading, setLoading] = useState(false)
  // the whole page cannot be read (no comments, a closed group...), or a next part failed (tap to try again)
  const [unavailable, setUnavailable] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const last = useRef(0)
  const busy = useRef(false)
  const more = useRef(true)
  const turn = useRef(0)

  const loadPost = useCallback(async () => {
    try {
      setPost(await api.post(id))
      setPostError(null)
    } catch (e) {
      setPostError((e as Error).message)
    }
  }, [id])

  const load = useCallback(
    async (fromStart = false) => {
      if (busy.current && !fromStart) return
      if (!fromStart && !more.current) return
      const mine = ++turn.current
      busy.current = true
      setLoading(true)
      setError(null)
      try {
        const page = await api.comments(id, fromStart ? 0 : last.current)
        if (mine !== turn.current) return
        if (page.unavailable) {
          if (fromStart || last.current === 0) {
            setUnavailable(page.message)
            setItems([])
            more.current = false
            setHasMore(false)
          } else setError(page.message)
          return
        }
        setUnavailable(null)
        setItems((prev) => {
          const base = fromStart ? [] : prev
          const seen = new Set(base.map((c) => c.id))
          return [...base, ...page.items.filter((c) => !seen.has(c.id))]
        })
        if (page.items.length) last.current = page.items[page.items.length - 1].id
        else if (fromStart) last.current = 0
        more.current = page.has_more
        setHasMore(page.has_more)
        setTotal(page.total)
        setGroup(page.group)
      } catch (e) {
        if (mine === turn.current) setError((e as Error).message)
      } finally {
        if (mine === turn.current) {
          busy.current = false
          setLoading(false)
        }
      }
    },
    [id],
  )

  useEffect(() => {
    loadPost()
    load(true)
  }, [loadPost, load])

  // the next comments as the end comes near; not after a failure, which waits for a tap. Watched anew after each
  // part: a short one leaves the end in sight, and the observer only tells of changes
  const sentinel = useRef<HTMLDivElement>(null)
  const errorRef = useRef(error)
  errorRef.current = error
  useEffect(() => {
    const el = sentinel.current
    if (!el) return
    const io = new IntersectionObserver((e) => e[0].isIntersecting && !errorRef.current && load(), {
      rootMargin: '800px',
    })
    io.observe(el)
    return () => io.disconnect()
  }, [load, hasMore, items.length])

  const refresh = async () => {
    last.current = 0
    more.current = true
    await Promise.all([loadPost(), load(true)])
  }

  // a quote tapped: the comment it answers is above (they load from the first), it lights up for a moment
  const jump = useCallback((commentId: number) => {
    const el = document.getElementById(`comment-${commentId}`)
    if (!el) return
    el.scrollIntoView({ behavior: 'smooth', block: 'center' })
    el.classList.remove('flash')
    void el.offsetWidth
    el.classList.add('flash')
  }, [])

  return (
    <Page
      title="Комментарии"
      onRefresh={refresh}
      left={
        <motion.button className="nav-button" onClick={onBack} whileTap={{ opacity: 0.4 }} style={{ paddingLeft: 0 }}>
          <ChevronLeft size={30} strokeWidth={2.2} style={{ marginRight: -4 }} />
          {BACK_LABEL[tab] ?? 'Назад'}
        </motion.button>
      }
    >
      <div className="comments-page">
        {post ? (
          <PostCard post={post} commentsLink={false} />
        ) : postError ? (
          <div className="error-banner">{postError}</div>
        ) : null}

        {total !== null && total > 0 && (
          <div className="comments-count">
            {total} {plural(total, 'комментарий', 'комментария', 'комментариев')}
          </div>
        )}

        {unavailable ? (
          <div className="comments-empty">
            <MessageCircle size={34} strokeWidth={1.6} />
            <div>{unavailable}</div>
            {post && (post.source_url ?? post.url) && (
              <a href={post.source_url ?? post.url!} target="_blank" rel="noopener noreferrer">
                Открыть пост в Telegram
              </a>
            )}
          </div>
        ) : total === 0 && !loading ? (
          <div className="comments-empty">
            <MessageCircle size={34} strokeWidth={1.6} />
            <div>Комментариев пока нет</div>
          </div>
        ) : (
          <div className="comments-list">
            {items.map((c, i) => (
              <Fragment key={c.id}>
                {(i === 0 || dayLabel(items[i - 1].date) !== dayLabel(c.date)) && (
                  <div className="comments-day">
                    <span>{dayLabel(c.date)}</span>
                  </div>
                )}
                <Comment comment={c} postId={id} link={post ? commentLink(post, group, c.id) : null} onJump={jump} />
              </Fragment>
            ))}
          </div>
        )}

        {error && (
          <button className="comments-retry" onClick={() => load(items.length === 0)}>
            {error} <span>Повторить</span>
          </button>
        )}
        {loading && !error && (
          <div className="comments-loading">
            <Loader2 size={22} className="spinner" />
          </div>
        )}
        {hasMore && <div ref={sentinel} />}
      </div>
    </Page>
  )
}
