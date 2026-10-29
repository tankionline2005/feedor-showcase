import { AnimatePresence, motion } from 'framer-motion'
import { ArrowUpRight, ChevronLeft, CircleCheck, Loader2 } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { api, type FailedRepost } from '../api'
import { Avatar } from '../components/Avatar'
import { Page } from '../components/Page'
import { plural, relative } from '../lib/format'
import { spring } from '../lib/motion'

const PAGE = 50

/** Every failed repost with its reason, newest first. */
export function ErrorsScreen({ onBack }: { onBack: () => void }) {
  const [items, setItems] = useState<FailedRepost[] | null>(null)
  const [total, setTotal] = useState(0)
  const [hasMore, setHasMore] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const reload = useCallback(async () => {
    try {
      const res = await api.failedReposts(0, PAGE)
      setItems(res.items)
      setTotal(res.total)
      setHasMore(res.has_more)
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])

  useEffect(() => {
    reload()
  }, [reload])

  const more = async () => {
    if (!items) return
    setLoadingMore(true)
    try {
      const res = await api.failedReposts(items.length, PAGE)
      setItems([...items, ...res.items.filter((i) => !items.some((x) => x.post_id === i.post_id))])
      setHasMore(res.has_more)
      setTotal(res.total)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoadingMore(false)
    }
  }

  return (
    <Page
      title="Ошибки"
      onRefresh={reload}
      left={
        <motion.button className="nav-button" onClick={onBack} whileTap={{ opacity: 0.4 }} style={{ paddingLeft: 0 }}>
          <ChevronLeft size={30} strokeWidth={2.2} style={{ marginRight: -4 }} />
          Настройки
        </motion.button>
      }
    >
      {error && <div className="error-banner">{error}</div>}

      {items === null && !error && (
        <div className="loader">
          <Loader2 size={22} className="spinner" />
        </div>
      )}

      {items && items.length === 0 && (
        <motion.div className="empty" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={spring}>
          <div className="empty-icon" style={{ color: 'var(--green)' }}>
            <CircleCheck size={32} strokeWidth={1.6} />
          </div>
          <div className="empty-title">Ошибок нет</div>
          <div>Все посты пересланы без проблем</div>
        </motion.div>
      )}

      {items && items.length > 0 && (
        <>
          <div className="section-header" style={{ marginLeft: 16 }}>
            {total} {plural(total, 'неудачный репост', 'неудачных репоста', 'неудачных репостов')}
          </div>
          <div className="section">
            <div className="group">
              <AnimatePresence initial={false}>
                {items.map((item) => (
                  <motion.div
                    key={item.post_id}
                    className="list-item error-row"
                    style={{ ['--inset' as string]: '60px' }}
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    transition={spring}
                  >
                    <Avatar title={item.channel.title} seed={item.channel.id} size={34} />
                    <div className="error-row-main">
                      <div className="error-row-top">
                        <span className="cell-title">{item.channel.title}</span>
                        <span className="error-row-time">{relative(item.failed_at ?? item.posted_at)}</span>
                      </div>
                      <div className="error-row-message">{item.error}</div>
                      {item.text && <div className="error-row-text">{item.text}</div>}
                    </div>
                    {item.url && (
                      <a
                        className="error-row-link"
                        href={item.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        aria-label="Открыть пост в Telegram"
                        title="Открыть пост в Telegram"
                      >
                        <ArrowUpRight size={18} />
                      </a>
                    )}
                  </motion.div>
                ))}
              </AnimatePresence>
            </div>
            {hasMore && (
              <div className="group" style={{ marginTop: 12 }}>
                <button className="cell accent" style={{ justifyContent: 'center' }} disabled={loadingMore} onClick={more}>
                  {loadingMore ? <Loader2 size={18} className="spinner" /> : 'Показать ещё'}
                </button>
              </div>
            )}
          </div>
        </>
      )}
    </Page>
  )
}
