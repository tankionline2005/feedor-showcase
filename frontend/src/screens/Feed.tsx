import { motion } from 'framer-motion'
import { ChevronRight, Funnel, Search, Sparkles, Star, Undo2, X } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, type Post } from '../api'
import { BillingNews } from '../components/BillingNews'
import { NoPlan, useNoPlan } from '../components/NoPlan'
import { Page } from '../components/Page'
import type { SmartActions } from '../components/PostCard'
import { PostList } from '../components/PostList'
import { ReasonSheet } from '../components/ReasonSheet'
import { useToast } from '../components/Toast'
import { useMe } from '../hooks/useMe'
import { usePosts } from '../hooks/usePosts'
import { badgeText, useBadges, useSeen } from '../hooks/useBadges'
import { setSmart, useSmart } from '../hooks/useSmart'
import { exact, plural } from '../lib/format'
import { AiFeedSheet } from './AiFeedSheet'
import { openIntro, useFirstVisit } from '../lib/intros'

export function Feed() {
  useFirstVisit('feed')
  const me = useMe()
  const [input, setInput] = useState('')
  const [q, setQ] = useState('')
  const [sheet, setSheet] = useState(false)
  // the smart feed's turned-down posts, as a feed of their own
  const [rejected, setRejected] = useState(false)
  const smart = useSmart()

  useEffect(() => {
    const t = window.setTimeout(() => setQ(input.trim()), 300)
    return () => window.clearTimeout(t)
  }, [input])

  const s = smart.state
  // the project's feed, the same for its staff: on in the project, on for everyone
  const active = !!s && s.linked && s.enabled
  const showRejected = active && rejected
  const posts = usePosts({
    sort: 'new',
    q: q || undefined,
    smart: showRejected ? 'rejected' : active ? 'true' : undefined,
  })
  const noPlan = useNoPlan()
  const toast = useToast()
  // the feed in front of the person: its new posts are seen (the badge on «Лента» goes)
  useSeen('feed')
  const badges = useBadges()

  // whoever sets the feed up corrects it: hides what it kept, shows what it turned down — with why
  const [correcting, setCorrecting] = useState<{ post: Post; show: boolean } | null>(null)
  const { remove } = posts
  const reloadSmart = smart.reload
  const undo = useCallback(
    async (post: Post) => {
      try {
        await api.smartUncorrect(post.id)
        remove(post.id)
        toast('Отменено — снова решает ИИ')
        reloadSmart()
      } catch (e) {
        toast((e as Error).message)
      }
    },
    [remove, reloadSmart, toast],
  )
  const smartActions = useMemo<SmartActions | undefined>(
    () =>
      active && s?.can_correct
        ? {
            action: showRejected ? 'show' : 'hide',
            onCorrect: (post) => setCorrecting({ post, show: showRejected }),
            onUndo: undo,
          }
        : undefined,
    [active, s?.can_correct, showRejected, undo],
  )
  const correct = async (reason: string) => {
    if (!correcting) return
    const { post, show } = correcting
    await api.smartCorrect(post.id, show, reason)
    remove(post.id)
    setCorrecting(null)
    toast(show ? 'Пост показан в умной ленте' : 'Пост скрыт из умной ленты')
    reloadSmart()
  }

  // the owner sees «AI» always: with their own OpenRouter not linked yet (dev:service:plan) it leads to linking it;
  // staff only once it is there
  const showAi = me.role === 'user' || (me.role === 'member' && me.ai_linked)
  const openAi = () => {
    if (me.role === 'user' && me.ai_own && !me.ai_linked) location.hash = '/settings?flash=ai'
    else {
      setSheet(true)
      smart.reload()
    }
  }

  return (
    <Page
      onHelp={() => openIntro('feed')}
      title="Лента"
      onRefresh={posts.refresh}
      titleAside={
        me.role !== 'admin' && (
          <span className="title-buttons">
            <motion.button
              className={`triggers-button${badges.triggers ? ' has-new' : ''}`}
              onClick={() => (location.hash = '/triggers')}
              whileTap={{ scale: 0.9 }}
              aria-label="Триггер-слова"
              title="Триггер-слова"
            >
              <Funnel size={16} strokeWidth={2.2} />
              {badges.triggers > 0 && <span>{badgeText(badges.triggers)}</span>}
            </motion.button>
            <motion.button
              className="triggers-button"
              onClick={() => (location.hash = '/favorites')}
              whileTap={{ scale: 0.9 }}
              aria-label="Избранное"
              title="Избранное"
            >
              <Star size={16} strokeWidth={2.2} />
            </motion.button>
          </span>
        )
      }
      titleAction={
        showAi && (
          <motion.button
            className={`ai-button${active ? ' active' : ''}`}
            onClick={openAi}
            whileTap={{ scale: 0.92 }}
            aria-label="Умная лента"
            title="Умная лента"
          >
            <Sparkles size={16} strokeWidth={2.2} />
            AI
          </motion.button>
        )
      }
      accessory={
        <>
          <label className="search">
            <Search size={17} />
            <input
              placeholder="Поиск по постам"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              enterKeyHint="search"
            />
            {input && (
              <button aria-label="Очистить" onClick={() => setInput('')} style={{ display: 'flex' }}>
                <X size={17} />
              </button>
            )}
          </label>
          {active && s && (
            <button
              className={`smart-line${showRejected ? ' rejected' : ''}`}
              onClick={() => {
                setRejected(!showRejected)
                window.scrollTo({ top: 0 })
              }}
              title={showRejected ? 'Вернуться к умной ленте' : 'Показать посты, которые не подошли'}
            >
              {showRejected ? <Undo2 size={14} /> : <Sparkles size={14} />}
              {showRejected
                ? `Не подошло за сутки ${exact(s.day.judged - s.day.matched)} · вернуться к умной ленте`
                : `Умная лента · за сутки подошло ${exact(s.day.matched)} из ${exact(s.day.judged)} ${plural(s.day.judged, 'поста', 'постов', 'постов')}`}
              {!showRejected && <ChevronRight size={14} />}
            </button>
          )}
        </>
      }
    >
      <BillingNews />
      {noPlan ? (
        <NoPlan />
      ) : (
        <PostList
          {...posts}
          onMore={posts.loadMore}
          onShowPending={posts.showPending}
          smartActions={smartActions}
          sort="new"
          empty={
            q
              ? { title: 'Ничего не найдено', text: `Нет постов с «${q}»` }
              : showRejected
                ? { title: 'Забракованных нет', text: 'Все проверенные посты подошли под промпт умной ленты' }
                : active
                  ? {
                      title: 'Умная лента пока пуста',
                      text: 'ИИ проверяет каждый новый пост — подходящие появятся здесь',
                    }
                  : { title: 'Пока пусто', text: 'Добавьте каналы — новые посты появятся здесь' }
          }
        />
      )}
      {showAi && (
        <AiFeedSheet
          open={sheet}
          state={s}
          onClose={() => setSheet(false)}
          onChange={(next) => setSmart(smart.key, next)}
        />
      )}
      <ReasonSheet
        open={!!correcting}
        title={correcting?.show ? 'Показать пост' : 'Скрыть пост'}
        question={correcting?.show ? 'Почему этот пост вам нужен?' : 'Почему этот пост вам не нужен?'}
        placeholder={
          correcting?.show
            ? 'Например: нам важны все новости о регулировании, даже короткие и без цифр'
            : 'Например: курсы и вебинары нам не интересны, даже если они на нашу тему'
        }
        quick={
          correcting?.show
            ? ['Это по нашей теме', 'Полезная информация', 'Важная новость', 'Нужны все посты этого канала']
            : ['Реклама', 'Не по нашей теме', 'Ничего нового', 'Слишком мелкая новость']
        }
        footer={
          (correcting?.show ? 'Пост появится в умной ленте. ' : 'Пост уйдёт из умной ленты в «Не подошло». ') +
          'ИИ учтёт причину и будет отбирать посты точнее. Передумаете — нажмите «Отменить» на самом посте.'
        }
        action={correcting?.show ? 'Показать' : 'Скрыть'}
        destructive={!correcting?.show}
        onClose={() => setCorrecting(null)}
        onSave={correct}
      />
    </Page>
  )
}
