import { AnimatePresence, motion } from 'framer-motion'
import { Segmented, SortChips } from '../components/Segmented'
import { Page } from '../components/Page'
import { NoPlan, useNoPlan } from '../components/NoPlan'
import { PostList } from '../components/PostList'
import { usePosts } from '../hooks/usePosts'
import { useStored } from '../hooks/useStored'
import { spring } from '../lib/motion'
import { openIntro, useFirstVisit } from '../lib/intros'

const PERIODS = [
  { value: '20m', label: '20 минут' },
  { value: '1h', label: 'Час' },
  { value: '6h', label: '6 часов' },
  { value: '24h', label: 'Сутки' },
  { value: '7d', label: 'Неделя' },
] as const

const SORTS = [
  { value: 'er', label: 'ER' },
  { value: 'fr', label: 'FR' },
  { value: 'fr_plus', label: 'FR+' },
  { value: 'forwards', label: 'Репосты' },
  { value: 'reactions', label: 'Реакции' },
  { value: 'comments', label: 'Комментарии' },
  { value: 'views', label: 'Просмотры' },
] as const

/** What the ratio sorts mean, shown next to the title. */
const SORT_HINTS: Partial<Record<SortValue, string>> = {
  er: 'ER — вовлечённость: (реакции + комментарии + репосты) / просмотры',
  fr: 'FR — репосты / просмотры: какая доля читателей поделилась постом',
  fr_plus: 'FR+ — во сколько раз FR поста выше среднего за последние 100 постов канала',
}

type PeriodValue = (typeof PERIODS)[number]['value']
type SortValue = (typeof SORTS)[number]['value']

const PERIOD_TEXT: Record<PeriodValue, string> = {
  '20m': 'за последние 20 минут',
  '1h': 'за последний час',
  '6h': 'за последние 6 часов',
  '24h': 'за последние сутки',
  '7d': 'за последнюю неделю',
}

export function Top() {
  useFirstVisit('top')
  const [period, setPeriod] = useStored<PeriodValue>('top.period', '24h', PERIODS.map((p) => p.value))
  const [sort, setSort] = useStored<SortValue>('top.sort', 'views', SORTS.map((s) => s.value))
  const posts = usePosts({ period, sort })
  const noPlan = useNoPlan()

  return (
    <Page
      onHelp={() => openIntro('top')}
      title="Топ"
      titleAside={
        <AnimatePresence mode="wait" initial={false}>
          {SORT_HINTS[sort] && (
            <motion.span
              key={sort}
              initial={{ opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -4, transition: { duration: 0.12 } }}
              transition={spring}
            >
              {SORT_HINTS[sort]}
            </motion.span>
          )}
        </AnimatePresence>
      }
      onRefresh={posts.refresh}
      accessory={
        <>
          <Segmented id="period" options={[...PERIODS]} value={period} onChange={setPeriod} />
          <SortChips id="sort" options={[...SORTS]} value={sort} onChange={setSort} />
        </>
      }
    >
      {noPlan ? (
        <NoPlan />
      ) : (
        <PostList
          {...posts}
          onMore={posts.loadMore}
          onShowPending={posts.showPending}
          sort={sort}
          ranked
          empty={{ title: 'Нет постов', text: `Ничего не опубликовано ${PERIOD_TEXT[period]}` }}
        />
      )}
    </Page>
  )
}
