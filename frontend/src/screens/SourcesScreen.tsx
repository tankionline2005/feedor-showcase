import { motion } from 'framer-motion'
import { ChevronLeft, Loader2 } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { api, type Post } from '../api'
import { Page } from '../components/Page'
import { PostCard } from '../components/PostCard'

/** «N источников» of a post the AI wrote: the posts it was written from, as the feed shows them. */
export function SourcesScreen({ id, onBack }: { id: number; onBack: () => void }) {
  const [items, setItems] = useState<Post[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setItems((await api.chatPostSources(id)).items)
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [id])

  useEffect(() => {
    load()
  }, [load])

  return (
    <Page
      title="Источники"
      onRefresh={load}
      left={
        <motion.button className="nav-button" onClick={onBack} whileTap={{ opacity: 0.4 }} style={{ paddingLeft: 0 }}>
          <ChevronLeft size={30} strokeWidth={2.2} style={{ marginRight: -4 }} />
          AI
        </motion.button>
      }
    >
      {error && <div className="error-banner">{error}</div>}
      {items === null && !error ? (
        <div className="loader">
          <Loader2 size={22} className="spinner" />
        </div>
      ) : (
        <div className="feed">
          {items?.length === 0 && <div className="comments-empty">Посты-источники удалены</div>}
          {items?.map((p) => (
            <PostCard key={p.id} post={p} />
          ))}
        </div>
      )}
    </Page>
  )
}
