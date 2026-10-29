import { motion } from 'framer-motion'
import { ChevronLeft } from 'lucide-react'
import { Page } from '../components/Page'
import { PostList } from '../components/PostList'
import { usePosts } from '../hooks/usePosts'

/** «Избранное», from the feed: the posts this person starred — theirs alone, kept past the retention period. */
export function FavoritesScreen({ onBack }: { onBack: () => void }) {
  const posts = usePosts({ sort: 'new', favorites: true })
  return (
    <Page
      title="Избранное"
      onRefresh={posts.refresh}
      left={
        <motion.button className="nav-button" onClick={onBack} whileTap={{ opacity: 0.4 }} style={{ paddingLeft: 0 }}>
          <ChevronLeft size={30} strokeWidth={2.2} style={{ marginRight: -4 }} />
          Лента
        </motion.button>
      }
    >
      <PostList
        {...posts}
        onMore={posts.loadMore}
        sort="new"
        empty={{
          title: 'Здесь пока пусто',
          text: 'Нажмите на звёздочку у поста в ленте, топе или теме — он сохранится здесь и не удалится со временем. Избранное видно только вам.',
        }}
      />
    </Page>
  )
}
