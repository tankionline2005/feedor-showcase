import { motion } from 'framer-motion'
import { ChevronDown, ChevronLeft, ChevronUp, EyeOff, Loader2, PenLine, Play, SlidersHorizontal } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { api, type TopicDetail } from '../api'
import { Alert } from '../components/Alert'
import { Lightbox } from '../components/Lightbox'
import { VideoViewer } from '../components/VideoViewer'
import { videoFallback } from '../lib/videoFallback'
import { Page } from '../components/Page'
import { PostCard } from '../components/PostCard'
import { PostPromptSheet } from '../components/PostPromptSheet'
import { ReasonSheet } from '../components/ReasonSheet'
import { useToast } from '../components/Toast'
import { useMe } from '../hooks/useMe'
import { compact, plural, relative, shortDate, time } from '../lib/format'
import { spring } from '../lib/motion'
import { forgetTopic } from '../lib/topicsCache'
import { may, myName, whoDid } from '../lib/who'

/** A topic: what happened, its pictures, how it went on, and the posts it is made of. */
export function TopicScreen({ id, onBack }: { id: number; onBack: () => void }) {
  const [topic, setTopic] = useState<TopicDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [viewing, setViewing] = useState<number | null>(null)
  const [watching, setWatching] = useState<TopicDetail['media'][number] | null>(null)
  // the posts themselves are long: folded until asked for
  const [sources, setSources] = useState(false)
  // «Есть в вашем канале» tapped: why the mark is there, and «Это не так»
  const [coveredInfo, setCoveredInfo] = useState(false)
  const [hiding, setHiding] = useState(false)
  const [prompting, setPrompting] = useState(false)
  const toast = useToast()
  const me = useMe()
  // a post on it is written in a chat with the AI: for those who may talk to it
  const canWrite = me.ai_linked && may(me, 'ai_chat')
  // hiding it, «Это не так» and putting the mark by hand, «Вернуть»: decisions the project's AI learns from
  const canTeach = may(me, 'ai_teach')

  const reload = useCallback(async () => {
    try {
      setTopic(await api.topic(id))
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [id])

  useEffect(() => {
    reload()
  }, [reload])

  const notCovered = async () => {
    setCoveredInfo(false)
    setTopic((t) => (t ? { ...t, covered: false, covered_by: null } : t))
    try {
      await api.topicNotCovered(id)
      toast('Пометка снята')
    } catch (e) {
      toast((e as Error).message)
      reload()
    }
  }

  const markCovered = async () => {
    try {
      await api.topicCovered(id)
      setTopic((t) => (t ? { ...t, covered: true, covered_by: myName(me) } : t))
      toast('Отмечено: есть в вашем канале')
    } catch (e) {
      toast((e as Error).message)
    }
  }

  const hide = async (reason: string) => {
    await api.hideTopic(id, reason)
    forgetTopic(id)
    setHiding(false)
    toast('Тема скрыта')
    onBack()
  }

  const unhide = async () => {
    try {
      await api.unhideTopic(id)
      setTopic((t) => (t ? { ...t, hidden: false, hide_reason: null, hidden_at: null } : t))
      toast('Тема вернулась в список')
    } catch (e) {
      toast((e as Error).message)
    }
  }

  const photos = (topic?.media ?? []).filter((m) => m.full_url).map((m) => ({ preview: m.url, full: m.full_url! }))

  return (
    <Page
      title="Тема"
      onRefresh={reload}
      left={
        <motion.button className="nav-button" onClick={onBack} whileTap={{ opacity: 0.4 }} style={{ paddingLeft: 0 }}>
          <ChevronLeft size={30} strokeWidth={2.2} style={{ marginRight: -4 }} />
          AI
        </motion.button>
      }
    >
      {error && <div className="error-banner">{error}</div>}
      {!topic && !error && (
        <div className="loader">
          <Loader2 size={22} className="spinner" />
        </div>
      )}
      {topic && (
        <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={spring}>
          {topic.hidden && (
            <div className="topic-hidden-note">
              <EyeOff size={17} />
              <span>
                Тема скрыта{whoDid(topic.hidden_by, me) === 'вы' ? ' вами' : ` · ${whoDid(topic.hidden_by, me)}`}
                {topic.hide_reason ? `: «${topic.hide_reason}»` : ''}. ИИ не собирает похожие темы.
              </span>
              {canTeach && (
                <button className="button small plain" onClick={unhide}>
                  Вернуть
                </button>
              )}
            </div>
          )}
          <div className="topic-head">
            <h2 className="topic-heading">{topic.title}</h2>
            <div className="topic-top">
              {topic.updated && <span className="topic-badge">Обновлена</span>}
              {topic.covered && (
                <button className="topic-covered" onClick={() => setCoveredInfo(true)}>
                  Есть в вашем канале
                </button>
              )}
              {topic.followup && (
                <button className="topic-followup" onClick={() => setCoveredInfo(true)}>
                  Есть продолжение
                </button>
              )}
              <span>
                {topic.sources} {plural(topic.sources, 'источник', 'источника', 'источников')} · {compact(topic.views)}{' '}
                {plural(topic.views, 'просмотр', 'просмотра', 'просмотров')} · {relative(topic.updated_at)}
              </span>
            </div>
          </div>

          {topic.media.length > 0 && (
            <div className="topic-gallery">
              {topic.media.map((m) => (
                <button
                  key={m.url}
                  className="topic-gallery-tile"
                  onClick={() =>
                    m.full_url
                      ? setViewing(photos.findIndex((p) => p.preview === m.url))
                      : m.video_url && setWatching(m)
                  }
                  aria-label={m.full_url ? 'Открыть фото' : 'Смотреть видео'}
                >
                  <img src={m.url} alt="" loading="lazy" />
                  {m.kind === 'video' && (
                    <span className="post-media-badge">
                      <Play size={12} fill="currentColor" />
                      Видео
                    </span>
                  )}
                </button>
              ))}
            </div>
          )}

          <div className="section">
            <div className="group topic-text">{topic.summary}</div>
            {topic.first && (
              <div className="section-footer">
                Первым написал «{topic.first.channel}» {relative(topic.first.posted_at)}.
              </div>
            )}
          </div>

          {canWrite && (!topic.covered || topic.followup) && !topic.hidden && (
            <div className="topic-write-post">
              <a href={`#/ai/chat/new?topic=${topic.id}`}>
                <PenLine size={17} />
                Создать об этом пост для моего канала
              </a>
              <button onClick={() => setPrompting(true)} aria-label="Шаблон запроса" title="Шаблон запроса">
                <SlidersHorizontal size={19} />
              </button>
            </div>
          )}

          {topic.timeline.length > 0 && (
            <div className="section">
              <div className="section-header">Хронология</div>
              <div className="group timeline">
                {topic.timeline.map((t, i) => (
                  <div key={i} className="timeline-item">
                    <span className="timeline-time">
                      {new Date(t.at).toDateString() === new Date().toDateString()
                        ? time(t.at)
                        : `${shortDate(t.at)}, ${time(t.at)}`}
                    </span>
                    <span className="timeline-dot" />
                    <span className="timeline-text">{t.text}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          <button className="sources-toggle" onClick={() => setSources(!sources)}>
            {sources
              ? 'Скрыть посты'
              : `Показать ${topic.posts.length} ${plural(topic.posts.length, 'пост', 'поста', 'постов')}`}
            {sources ? <ChevronUp size={17} /> : <ChevronDown size={17} />}
          </button>
          {sources && (
            <div className="feed">
              {topic.posts.map((p) => (
                <PostCard key={p.id} post={p} />
              ))}
            </div>
          )}

          {canTeach && !topic.hidden && !topic.covered && (
            <button className="topic-mark-covered" onClick={markCovered}>
              Отметить: есть в вашем канале
            </button>
          )}
          {canTeach && !topic.hidden && (
            <button className="topic-hide" onClick={() => setHiding(true)}>
              Скрыть тему
            </button>
          )}
        </motion.div>
      )}
      <PostPromptSheet open={prompting} onClose={() => setPrompting(false)} />
      <ReasonSheet
        open={hiding}
        title="Скрыть тему"
        question="Почему тема вам не подходит?"
        placeholder="Например: новости о криптовалютах нам интересны, но мы пишем о том, что популярно, а этот токен пока не вызывает такого ажиотажа. Пока он не станет популярным, новости о нём нам не интересны"
        quick={['Не наша тематика', 'Реклама или самопиар', 'Слишком мелкая новость', 'Уже неактуально']}
        footer="Тема скроется у всех в проекте. ИИ учтёт причину и не будет собирать похожие темы. Вернуть тему можно в «Скрытых темах» внизу списка."
        action="Скрыть"
        destructive
        onClose={() => setHiding(false)}
        onSave={hide}
      />
      {viewing !== null && viewing >= 0 && (
        <Lightbox photos={photos} start={viewing} onClose={() => setViewing(null)} />
      )}
      {watching?.video_url && topic && (
        <VideoViewer
          videoUrl={watching.video_url}
          poster={watching.url}
          fallback={(() => {
            const post = topic.posts.find((p) => p.id === watching.post_id)
            return post ? videoFallback(post, me) : {}
          })()}
          onClose={() => setWatching(null)}
        />
      )}
      <Alert
        open={coveredInfo}
        title={topic?.followup ? 'Есть продолжение' : 'Есть в вашем канале'}
        text={
          topic?.followup
            ? 'Вы уже писали об этой теме, но после вашего поста появились важные новости — повод для ещё одного поста. Пометка уйдёт, когда в вашем канале выйдет пост о продолжении.'
            : topic?.covered_by
              ? whoDid(topic.covered_by, me) === 'вы'
                ? 'Вы отметили вручную, что в вашем канале уже есть пост об этой новости.'
                : `Отмечено вручную · ${whoDid(topic.covered_by, me)}: в вашем канале уже есть пост об этой новости.`
              : 'ИИ решил, что в вашем канале уже есть пост об этой новости.'
        }
        buttons={[
          ...(canTeach
            ? [{ label: topic?.covered_by ? 'Снять пометку' : 'Это не так', onClick: notCovered, destructive: true }]
            : []),
          { label: 'Закрыть', onClick: () => setCoveredInfo(false), strong: true },
        ]}
        onClose={() => setCoveredInfo(false)}
      />
    </Page>
  )
}
