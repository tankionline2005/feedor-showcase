import { animate, motion, useMotionValue, useTransform } from 'framer-motion'
import {
  Bell,
  BellRing,
  Bot,
  Check,
  Code2,
  Copy,
  KeyRound,
  Megaphone,
  Plug,
  Plus,
  Search,
  Send,
  Sparkles,
  Unplug,
  Users,
  Wrench,
} from 'lucide-react'
import { type ReactNode, useEffect } from 'react'

/**
 * The welcome cards' pictures (Intro): small live scenes made of the site's own pieces — posts coming into the feed,
 * a word lighting up, posts gathering into a topic, a post flying into the channel. Each loops quietly while shown.
 */

export type ArtName =
  | 'feed'
  | 'metrics'
  | 'search'
  | 'smart'
  | 'trigger'
  | 'forms'
  | 'bell'
  | 'ranking'
  | 'sorts'
  | 'idea'
  | 'chat'
  | 'postcard'
  | 'learn'
  | 'converge'
  | 'topiccard'
  | 'clock'
  | 'pubs'
  | 'publish'
  | 'autopilot'
  | 'channels'
  | 'status'
  | 'stats'
  | 'groups'
  | 'groupsteps'
  | 'groupsend'
  | 'team'
  | 'rights'
  | 'teamtg'
  | 'api'
  | 'apidocs'
  | 'apikey'
  | 'mcp'
  | 'mcpconnect'
  | 'mcpsafe'

const loop = (duration: number, delay = 0) => ({ duration, delay, repeat: Infinity, ease: 'easeInOut' as const })

const COLORS = ['#ff7a45', '#34c759', '#5e5ce6', '#ff2d55', '#30b0c7', '#ff9f0a']

function Avatar({ i, size = 18, children }: { i: number; size?: number; children?: ReactNode }) {
  return (
    <span className="ia-avatar" style={{ width: size, height: size, background: COLORS[i % COLORS.length] }}>
      {children}
    </span>
  )
}

function Lines({ widths, gap = 5, h = 6 }: { widths: number[]; gap?: number; h?: number }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap }}>
      {widths.map((w, i) => (
        <span key={i} className="ia-line" style={{ width: `${w}%`, height: h }} />
      ))}
    </div>
  )
}

/** A post in small: the channel's avatar and name, a few lines of text, sometimes its numbers. */
function MiniPost({
  i,
  lines = [100, 72],
  stats,
  width = 200,
  children,
}: {
  i: number
  lines?: number[]
  stats?: boolean
  width?: number
  children?: ReactNode
}) {
  return (
    <div className="ia-card" style={{ width }}>
      <div className="ia-head">
        <Avatar i={i} />
        <span className="ia-line" style={{ width: 54, height: 6, opacity: 0.9 }} />
      </div>
      {children ?? <Lines widths={lines} />}
      {stats && (
        <div className="ia-stats">
          {[0, 1, 2, 3].map((k) => (
            <span key={k} className="ia-line" style={{ width: 22, height: 5 }} />
          ))}
        </div>
      )}
    </div>
  )
}

/** A number that counts up, again and again. */
function Counter({ to, suffix = '', decimals = 0 }: { to: number; suffix?: string; decimals?: number }) {
  const value = useMotionValue(0)
  const text = useTransform(value, (v) =>
    (decimals ? v.toFixed(decimals).replace('.', ',') : Math.round(v).toLocaleString('ru-RU')) + suffix,
  )
  useEffect(() => {
    const controls = animate(value, [0, to, to], { duration: 3.2, times: [0, 0.55, 1], repeat: Infinity, ease: 'easeOut' })
    return () => controls.stop()
  }, [to, value])
  return <motion.span>{text}</motion.span>
}

function Feed() {
  return (
    <div className="ia-stack">
      {[0, 1, 2].map((i) => (
        <motion.div
          key={i}
          animate={{ y: [-26, 0, 0, 8], opacity: [0, 1, 1, 0] }}
          transition={{ ...loop(4.2, i * 0.4), times: [0, 0.14, 0.86, 1] }}
        >
          <MiniPost i={i} lines={i === 1 ? [100, 88, 46] : [100, 64]} />
        </motion.div>
      ))}
    </div>
  )
}

function Metrics() {
  return (
    <div className="ia-card" style={{ width: 230 }}>
      <div className="ia-head">
        <Avatar i={2} />
        <span className="ia-line" style={{ width: 70, height: 6 }} />
      </div>
      <Lines widths={[100, 80]} />
      <div className="ia-metrics">
        <div>
          <b>
            <Counter to={12480} />
          </b>
          <span>просмотры</span>
        </div>
        <div>
          <b>
            <Counter to={4.6} decimals={1} suffix="%" />
          </b>
          <span>ER</span>
        </div>
        <motion.div
          className="ia-hot"
          animate={{ scale: [1, 1.12, 1] }}
          transition={{ ...loop(1.6, 1.4), repeatDelay: 1.6 }}
        >
          <b>
            <Counter to={1.8} decimals={1} suffix="×" />
          </b>
          <span>FR</span>
        </motion.div>
      </div>
    </div>
  )
}

function Typed({ text, hold = 2 }: { text: string; hold?: number }) {
  const n = useMotionValue(0)
  const shown = useTransform(n, (v) => text.slice(0, Math.round(v)))
  useEffect(() => {
    const total = text.length * 0.12 + hold + 0.6
    const controls = animate(n, [0, text.length, text.length, 0], {
      duration: total,
      times: [0, (text.length * 0.12) / total, (text.length * 0.12 + hold) / total, 1],
      repeat: Infinity,
      ease: 'linear',
    })
    return () => controls.stop()
  }, [n, text, hold])
  return <motion.span>{shown}</motion.span>
}

function SearchArt() {
  return (
    <div className="ia-col">
      <div className="ia-search">
        <Search size={13} />
        <Typed text="дрон" />
        <motion.span className="ia-caret" animate={{ opacity: [1, 0, 1] }} transition={loop(1)} />
      </div>
      {[0, 1].map((i) => (
        <MiniPost key={i} i={i + 3} width={210}>
          <div className="ia-text">
            {i === 0 ? 'Над городом сбили ' : 'Новый '}
            <motion.mark
              className="ia-mark"
              animate={{ backgroundColor: ['rgba(255,204,0,0)', 'rgba(255,204,0,0.55)', 'rgba(255,204,0,0.55)', 'rgba(255,204,0,0)'] }}
              transition={{ ...loop(3.4, 0.6 + i * 0.2), times: [0, 0.2, 0.8, 1] }}
            >
              {i === 0 ? 'дрон' : 'дроны'}
            </motion.mark>
            {i === 0 ? ' над окраиной' : ' для доставки'}
          </div>
        </MiniPost>
      ))}
    </div>
  )
}

function Smart() {
  return (
    <div className="ia-stack">
      <MiniPost i={0} lines={[100]} />
      <motion.div
        animate={{ opacity: [1, 1, 0.25, 0.25, 1], scale: [1, 1, 0.94, 0.94, 1] }}
        transition={{ ...loop(4.4), times: [0, 0.3, 0.42, 0.85, 1] }}
        style={{ position: 'relative' }}
      >
        <MiniPost i={4}>
          <div className="ia-text">
            <span className="ia-ad">Реклама</span> Скидки до 90% только сегодня
          </div>
        </MiniPost>
        <motion.span
          className="ia-sparkle"
          animate={{ opacity: [0, 0, 1, 1, 0], scale: [0.6, 0.6, 1, 1, 0.6] }}
          transition={{ ...loop(4.4), times: [0, 0.3, 0.42, 0.85, 1] }}
        >
          <Sparkles size={14} /> скрыто
        </motion.span>
      </motion.div>
      <MiniPost i={1} lines={[100]} />
    </div>
  )
}

function Trigger() {
  return (
    <div className="ia-col" style={{ position: 'relative' }}>
      <motion.div
        className="ia-notify"
        animate={{ y: [-60, -60, 0, 0, -60], opacity: [0, 0, 1, 1, 0] }}
        transition={{ ...loop(4.6), times: [0, 0.35, 0.45, 0.88, 1] }}
      >
        <BellRing size={14} />
        <span>
          Триггер <b>«БПЛА»</b> · новый пост
        </span>
      </motion.div>
      <MiniPost i={2} width={230}>
        <div className="ia-text">
          Ночью над областью сбили 12{' '}
          <motion.mark
            className="ia-mark"
            animate={{ backgroundColor: ['rgba(255,204,0,0)', 'rgba(255,204,0,0)', 'rgba(255,204,0,0.6)', 'rgba(255,204,0,0.6)'] }}
            transition={{ ...loop(4.6), times: [0, 0.2, 0.3, 1] }}
          >
            БПЛА
          </motion.mark>
          , обломки упали на склад
        </div>
      </MiniPost>
    </div>
  )
}

function Forms() {
  const forms = ['взрывы', 'взрыва', 'взрывом']
  return (
    <div className="ia-forms">
      <span className="ia-chip strong">взрыв</span>
      <div className="ia-forms-list">
        {forms.map((f, i) => (
          <motion.span
            key={f}
            className="ia-chip"
            animate={{ opacity: [0, 1, 1, 0], x: [-14, 0, 0, 0] }}
            transition={{ ...loop(3.8, 0.35 * i), times: [0, 0.18, 0.85, 1] }}
          >
            <Check size={12} /> {f}
          </motion.span>
        ))}
        <motion.span
          className="ia-chip off"
          animate={{ opacity: [0, 1, 1, 0] }}
          transition={{ ...loop(3.8, 1.1), times: [0, 0.18, 0.85, 1] }}
        >
          взрывной — нет
        </motion.span>
      </div>
    </div>
  )
}

function BellArt({ label = 'Feedor', text = 'Новый пост по триггеру «дрон»' }: { label?: string; text?: string }) {
  return (
    <div className="ia-phone">
      <motion.div
        className="ia-push"
        animate={{ y: [-50, 0, 0, -50], opacity: [0, 1, 1, 0] }}
        transition={{ ...loop(4), times: [0, 0.15, 0.85, 1] }}
      >
        <span className="ia-avatar" style={{ width: 26, height: 26, background: 'var(--accent)' }}>
          <Bot size={15} color="#fff" />
        </span>
        <div style={{ minWidth: 0 }}>
          <b>{label}</b>
          <div className="ia-push-text">{text}</div>
        </div>
      </motion.div>
      <motion.div
        className="ia-bell"
        animate={{ rotate: [0, 0, -16, 14, -10, 6, 0, 0] }}
        transition={{ ...loop(4), times: [0, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 1] }}
      >
        <Bell size={30} />
      </motion.div>
    </div>
  )
}

function Ranking() {
  const rows = [
    { w: 92, n: '128K' },
    { w: 74, n: '96K' },
    { w: 58, n: '71K' },
    { w: 40, n: '48K' },
  ]
  return (
    <div className="ia-col" style={{ gap: 7 }}>
      {rows.map((r, i) => (
        <div key={i} className="ia-rank">
          <span className="ia-rank-n">{i + 1}</span>
          <Avatar i={i} size={16} />
          <div className="ia-rank-bar">
            <motion.span
              animate={{ width: ['0%', `${r.w}%`, `${r.w}%`] }}
              transition={{ ...loop(3.6, i * 0.15), times: [0, 0.4, 1] }}
            />
          </div>
          <span className="ia-rank-v">{r.n}</span>
        </div>
      ))}
    </div>
  )
}

function Sorts() {
  const chips = ['Просмотры', 'Реакции', 'ER', 'FR']
  return (
    <div className="ia-col" style={{ alignItems: 'center', gap: 14 }}>
      <div className="ia-chips">
        {chips.map((c, i) => (
          <span key={c} className="ia-sort">
            <motion.span
              className="ia-sort-pill"
              animate={{ opacity: [0, 1, 1, 0] }}
              transition={{ duration: 4.8, repeat: Infinity, delay: i * 1.2, times: [0, 0.04, 0.22, 0.26], ease: 'linear' }}
            />
            {/* white on the blue pill, in both themes */}
            <motion.span
              style={{ position: 'relative' }}
              animate={{ color: ['var(--text)', '#ffffff', '#ffffff', 'var(--text)'] }}
              transition={{ duration: 4.8, repeat: Infinity, delay: i * 1.2, times: [0, 0.04, 0.22, 0.26], ease: 'linear' }}
            >
              {c}
            </motion.span>
          </span>
        ))}
      </div>
      <div style={{ display: 'flex', gap: 8 }}>
        {[0, 1, 2].map((i) => (
          <motion.div
            key={i}
            animate={{ y: [0, -6, 0] }}
            transition={{ ...loop(1.2, i * 0.15), repeatDelay: 0.6 }}
          >
            <MiniPost i={i + 2} width={80} lines={[100, 60]} />
          </motion.div>
        ))}
      </div>
    </div>
  )
}

function Idea() {
  return (
    <div className="ia-row" style={{ gap: 18 }}>
      <MiniPost i={5} width={120} stats lines={[100, 70, 40]} />
      <motion.div animate={{ x: [0, 8, 0] }} transition={loop(1.6)} style={{ color: 'var(--accent)' }}>
        <Sparkles size={24} />
      </motion.div>
      <motion.div
        animate={{ scale: [0.9, 1, 1, 0.9], opacity: [0, 1, 1, 0] }}
        transition={{ ...loop(3.6), times: [0, 0.2, 0.85, 1] }}
      >
        <div className="ia-card ia-own" style={{ width: 120 }}>
          <div className="ia-head">
            <span className="ia-avatar" style={{ width: 18, height: 18, background: 'var(--accent)' }}>
              <Megaphone size={10} color="#fff" />
            </span>
            <span style={{ fontSize: 10, fontWeight: 600 }}>Ваш канал</span>
          </div>
          <Lines widths={[100, 84, 52]} />
        </div>
      </motion.div>
    </div>
  )
}

function Chat() {
  return (
    <div className="ia-chat">
      <motion.div
        className="ia-bubble me"
        animate={{ opacity: [0, 1, 1, 0], y: [10, 0, 0, 0] }}
        transition={{ ...loop(5), times: [0, 0.08, 0.9, 1] }}
      >
        Что важного за утро?
      </motion.div>
      <motion.div
        className="ia-bubble ai ia-dots"
        animate={{ opacity: [0, 0, 1, 1, 0, 0] }}
        transition={{ ...loop(5), times: [0, 0.12, 0.16, 0.34, 0.38, 1] }}
      >
        {[0, 1, 2].map((i) => (
          <motion.span key={i} animate={{ y: [0, -3, 0] }} transition={loop(0.6, i * 0.12)} />
        ))}
      </motion.div>
      <motion.div
        className="ia-bubble ai"
        animate={{ opacity: [0, 0, 1, 1, 0], y: [10, 10, 0, 0, 0] }}
        transition={{ ...loop(5), times: [0, 0.36, 0.44, 0.9, 1] }}
        style={{ marginTop: -30 }}
      >
        Три главные темы: атака на НПЗ, новые санкции и рекорд биткоина. Подробнее о каждой —
      </motion.div>
    </div>
  )
}

function PostCard() {
  return (
    <div className="ia-card" style={{ width: 240, position: 'relative', paddingBottom: 14 }}>
      <div className="ia-head">
        <span className="ia-avatar" style={{ width: 18, height: 18, background: 'var(--accent)' }}>
          <Megaphone size={10} color="#fff" />
        </span>
        <span style={{ fontSize: 10, fontWeight: 600 }}>Ваш канал</span>
        <span className="ia-src">3 источника</span>
      </div>
      <div className="ia-photo">
        <motion.span animate={{ opacity: [0.5, 1, 0.5] }} transition={loop(2.4)} />
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 5, marginTop: 7 }}>
        {[100, 92, 64].map((w, i) => (
          <motion.span
            key={i}
            className="ia-line"
            animate={{ width: ['0%', `${w}%`, `${w}%`] }}
            transition={{ ...loop(3.8, i * 0.35), times: [0, 0.35, 1] }}
          />
        ))}
      </div>
      <motion.span
        className="ia-send"
        animate={{ scale: [1, 1.15, 1] }}
        transition={{ ...loop(1.4, 1.6), repeatDelay: 1.2 }}
      >
        <Send size={13} color="#fff" />
      </motion.span>
    </div>
  )
}

function Learn() {
  return (
    <div className="ia-card" style={{ width: 240 }}>
      <div className="ia-text" style={{ fontSize: 12, lineHeight: 1.5 }}>
        В регионе{' '}
        <motion.span
          animate={{ opacity: [1, 1, 0.35, 0.35, 1] }}
          transition={{ ...loop(4.4), times: [0, 0.2, 0.3, 0.9, 1] }}
          style={{ textDecoration: 'line-through', textDecorationColor: 'var(--red)' }}
        >
          осуществлён запуск
        </motion.span>{' '}
        <motion.span
          className="ia-new"
          animate={{ opacity: [0, 0, 1, 1, 0] }}
          transition={{ ...loop(4.4), times: [0, 0.3, 0.4, 0.9, 1] }}
        >
          запустили
        </motion.span>{' '}
        новый завод
      </div>
      <motion.div
        className="ia-learned"
        animate={{ opacity: [0, 0, 1, 1, 0], y: [6, 6, 0, 0, 0] }}
        transition={{ ...loop(4.4), times: [0, 0.45, 0.55, 0.9, 1] }}
      >
        <Sparkles size={12} /> ИИ запомнил: проще и короче
      </motion.div>
    </div>
  )
}

function Converge() {
  const from = [
    { x: -110, y: -50 },
    { x: 110, y: -46 },
    { x: -118, y: 40 },
    { x: 116, y: 48 },
    { x: 0, y: -74 },
  ]
  return (
    <div className="ia-converge">
      {from.map((p, i) => (
        <motion.div
          key={i}
          className="ia-mini"
          animate={{ x: [p.x, p.x, 0, 0], y: [p.y, p.y, 0, 0], opacity: [0, 1, 0, 0], scale: [1, 1, 0.5, 0.5] }}
          transition={{ ...loop(4.4), times: [0, 0.12 + i * 0.03, 0.45, 1] }}
        >
          <Avatar i={i} size={14} />
          <span className="ia-line" style={{ width: 34, height: 5 }} />
        </motion.div>
      ))}
      <motion.div
        className="ia-card ia-topic"
        animate={{ scale: [0.6, 0.6, 1, 1, 0.6], opacity: [0, 0, 1, 1, 0] }}
        transition={{ ...loop(4.4), times: [0, 0.42, 0.52, 0.9, 1] }}
      >
        <div className="ia-topic-title">Атака на НПЗ: что известно</div>
        <Lines widths={[100, 76]} h={5} />
        <div className="ia-topic-foot">
          {[0, 1, 2].map((i) => (
            <Avatar key={i} i={i} size={12} />
          ))}
          <span>5 источников</span>
        </div>
      </motion.div>
    </div>
  )
}

function TopicCard() {
  return (
    <div className="ia-col" style={{ gap: 8 }}>
      {[
        { t: 'Главное событие дня', hot: true },
        { t: 'Важная новость', own: true },
        { t: 'Заметная новость' },
      ].map((x, i) => (
        <motion.div
          key={i}
          className="ia-card ia-topic-row"
          animate={{ opacity: [0, 1, 1, 0], x: [20, 0, 0, 0] }}
          transition={{ ...loop(4.4, i * 0.25), times: [0, 0.14, 0.88, 1] }}
        >
          <span className={`ia-flame${x.hot ? ' hot' : ''}`}>{3 - i}</span>
          <span className="ia-topic-name">{x.t}</span>
          {x.own && (
            <motion.span
              className="ia-own-badge"
              animate={{ opacity: [0, 0, 1, 1] }}
              transition={{ ...loop(4.4), times: [0, 0.4, 0.5, 1] }}
            >
              Есть в канале
            </motion.span>
          )}
        </motion.div>
      ))}
    </div>
  )
}

function Clock() {
  const marks = [0, 60, 120, 180, 240, 300]
  return (
    <div className="ia-clock">
      <svg viewBox="-60 -60 120 120" width={128} height={128}>
        <circle r="52" className="ia-clock-face" />
        {marks.map((deg, i) => (
          <motion.circle
            key={deg}
            cx={Math.sin((deg * Math.PI) / 180) * 42}
            cy={-Math.cos((deg * Math.PI) / 180) * 42}
            r="5"
            className="ia-clock-dot"
            animate={{ opacity: [0.25, 0.25, 1, 0.25] }}
            transition={{ duration: 6, repeat: Infinity, ease: 'linear', delay: i, times: [0, 0.01, 0.06, 0.2] }}
          />
        ))}
        <circle r="4" className="ia-clock-center" />
      </svg>
      <motion.span
        className="ia-clock-hand"
        animate={{ rotate: 360 }}
        transition={{ duration: 6, repeat: Infinity, ease: 'linear' }}
      />
    </div>
  )
}

function Pubs() {
  const tags = [
    { t: 'Запланирован · 18:00', c: 'var(--accent)' },
    { t: 'Опубликован', c: 'var(--green)' },
    { t: 'Предложение ИИ', c: 'var(--indigo)' },
  ]
  return (
    <div className="ia-col" style={{ gap: 8 }}>
      {tags.map((x, i) => (
        <motion.div
          key={i}
          className="ia-card ia-pub-row"
          animate={{ opacity: [0, 1, 1, 0], y: [12, 0, 0, 0] }}
          transition={{ ...loop(4.4, i * 0.3), times: [0, 0.14, 0.88, 1] }}
        >
          <Avatar i={i + 1} size={16} />
          <Lines widths={[100, 60]} h={5} gap={4} />
          <span className="ia-pub-tag" style={{ color: x.c, background: `color-mix(in srgb, ${x.c} 15%, transparent)` }}>
            {x.t}
          </span>
        </motion.div>
      ))}
    </div>
  )
}

// «Публикация в один тап»: a tap on the post's round button, the button turns into the green check, the post flies
// into the channel
const PUBLISH_LOOP = 5

function Publish() {
  const t = { ...loop(PUBLISH_LOOP) }
  return (
    <div className="ia-publish">
      <div className="ia-publish-post">
        <MiniPost i={1} width={160} lines={[72, 60, 36]} />
        <motion.span
          className="ia-publish-button"
          animate={{ scale: [1, 1, 0.82, 1, 1, 1] }}
          transition={{ ...t, times: [0, 0.2, 0.25, 0.32, 0.95, 1] }}
        >
          <Send size={13} color="#fff" style={{ marginLeft: -1 }} />
          <motion.span
            className="ia-publish-done"
            animate={{ opacity: [0, 0, 1, 1, 0] }}
            transition={{ ...t, times: [0, 0.3, 0.36, 0.92, 1] }}
          >
            <Check size={14} strokeWidth={3} color="#fff" />
          </motion.span>
        </motion.span>
        {/* the finger's tap */}
        <motion.span
          className="ia-publish-tap"
          animate={{ opacity: [0, 0, 0.9, 0.6, 0, 0], scale: [1.6, 1.6, 1, 0.9, 1.8, 1.8] }}
          transition={{ ...t, times: [0, 0.1, 0.2, 0.26, 0.36, 1] }}
        />
        <motion.div
          className="ia-publish-flying"
          animate={{ x: [0, 0, 104, 104], y: [0, 0, -52, -52], scale: [1, 1, 0.2, 0.2], opacity: [0, 0.9, 0, 0] }}
          transition={{ ...t, times: [0, 0.34, 0.58, 1] }}
        >
          <MiniPost i={1} width={160} lines={[72, 60, 36]} />
        </motion.div>
      </div>
      <div className="ia-channel">
        <span className="ia-avatar" style={{ width: 44, height: 44, background: 'var(--accent)' }}>
          <Megaphone size={20} color="#fff" />
        </span>
        <motion.span
          className="ia-check"
          animate={{ scale: [0, 0, 1.2, 1, 1, 0] }}
          transition={{ ...loop(PUBLISH_LOOP), times: [0, 0.58, 0.64, 0.7, 0.92, 1] }}
        >
          <Check size={13} strokeWidth={3} color="#fff" />
        </motion.span>
        <span className="ia-channel-name">Ваш канал</span>
      </div>
    </div>
  )
}

function Autopilot() {
  return (
    <div className="ia-col" style={{ alignItems: 'center', gap: 12 }}>
      <div className="ia-toggle-row">
        <span>Автопостинг</span>
        <span className="ia-toggle">
          <motion.span animate={{ x: [0, 16, 16] }} transition={{ ...loop(5), times: [0, 0.08, 1] }} />
        </span>
      </div>
      <div className="ia-timeline">
        {['10:00', '10:15', '10:30'].map((t, i) => (
          <div key={t} className="ia-slot">
            <motion.div
              animate={{ opacity: [0.3, 0.3, 1, 1], scale: [0.9, 0.9, 1, 1] }}
              transition={{ ...loop(5), times: [0, 0.2 + i * 0.2, 0.28 + i * 0.2, 1] }}
            >
              <MiniPost i={i} width={72} lines={[100, 60]} />
            </motion.div>
            <span>{t}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function Channels() {
  return (
    <div className="ia-grid">
      {[0, 1, 2, 3, 4].map((i) => (
        <motion.span
          key={i}
          animate={{ scale: [0, 1.1, 1, 1, 0] }}
          transition={{ ...loop(4.2, i * 0.18), times: [0, 0.1, 0.16, 0.9, 1] }}
        >
          <Avatar i={i} size={42} />
        </motion.span>
      ))}
      <motion.span
        className="ia-plus"
        animate={{ scale: [1, 1.12, 1] }}
        transition={{ ...loop(1.4), repeatDelay: 0.6 }}
      >
        <Plus size={20} />
      </motion.span>
    </div>
  )
}

function Status() {
  return (
    <div className="ia-col" style={{ gap: 7 }}>
      {[0, 1, 2].map((i) => (
        <div key={i} className="ia-card ia-chan-row">
          <Avatar i={i + 2} size={20} />
          <Lines widths={[70, 44]} h={5} gap={4} />
          <span className="ia-chan-state">
            <motion.span
              className="ia-spin"
              animate={{ opacity: [1, 1, 0, 0], rotate: [0, 360, 360, 360] }}
              transition={{ ...loop(4, i * 0.3), times: [0, 0.35, 0.4, 1] }}
            />
            <motion.span
              className="ia-ok"
              animate={{ opacity: [0, 0, 1, 1], scale: [0.4, 0.4, 1, 1] }}
              transition={{ ...loop(4, i * 0.3), times: [0, 0.38, 0.46, 1] }}
            >
              <Check size={11} strokeWidth={3} color="#fff" />
            </motion.span>
          </span>
        </div>
      ))}
    </div>
  )
}

function Stats() {
  return (
    <div className="ia-card" style={{ width: 240, padding: '10px 12px' }}>
      <div style={{ fontSize: 10, fontWeight: 600, color: 'var(--text-2)', marginBottom: 6 }}>Просмотры за неделю</div>
      <svg viewBox="0 0 200 70" width="100%" height={70}>
        <motion.path
          d="M0 58 C 20 50, 30 54, 45 44 S 75 30, 90 36 S 120 18, 140 22 S 175 6, 200 10"
          fill="none"
          stroke="var(--accent)"
          strokeWidth="3"
          strokeLinecap="round"
          animate={{ pathLength: [0, 1, 1] }}
          transition={{ ...loop(3.6), times: [0, 0.55, 1] }}
        />
        <motion.path
          d="M0 58 C 20 50, 30 54, 45 44 S 75 30, 90 36 S 120 18, 140 22 S 175 6, 200 10 L200 70 L0 70 Z"
          fill="var(--accent)"
          animate={{ opacity: [0, 0.12, 0.12] }}
          transition={{ ...loop(3.6), times: [0, 0.55, 1] }}
        />
      </svg>
    </div>
  )
}

function GroupBubble({ title, i }: { title: string; i: number }) {
  return (
    <div className="ia-group">
      <span className="ia-avatar" style={{ width: 30, height: 30, background: COLORS[i] }}>
        <Users size={15} color="#fff" />
      </span>
      <span className="ia-group-name">{title}</span>
    </div>
  )
}

function Groups() {
  return (
    <div className="ia-row" style={{ gap: 26 }}>
      <motion.div animate={{ y: [0, -4, 0] }} transition={loop(2.4)}>
        <MiniPost i={1} width={110} lines={[100, 70, 40]} />
      </motion.div>
      <div className="ia-col" style={{ gap: 14, position: 'relative' }}>
        {['Редакция', 'Новости'].map((t, i) => (
          <div key={t} style={{ position: 'relative' }}>
            <motion.span
              className="ia-flyer"
              animate={{ x: [-90, 0, 0], opacity: [0, 1, 0], scale: [0.8, 0.4, 0.4] }}
              transition={{ ...loop(3.2, i * 0.5), times: [0, 0.4, 0.5] }}
            />
            <GroupBubble title={t} i={i + 2} />
          </div>
        ))}
      </div>
    </div>
  )
}

function GroupSteps() {
  return (
    <div className="ia-col" style={{ gap: 10, width: 250 }}>
      {['Добавьте бота в свою группу', 'Выберите группу на сайте', 'Готово — посты идут в группу'].map((t, i) => (
        <motion.div
          key={t}
          className="ia-step"
          animate={{ opacity: [0.3, 1, 1, 0.3] }}
          transition={{ ...loop(4.5, i * 1.1), times: [0, 0.1, 0.7, 1] }}
        >
          <span className="ia-step-n">{i + 1}</span>
          <span>{t}</span>
        </motion.div>
      ))}
    </div>
  )
}

function GroupSend() {
  return (
    <div className="ia-card" style={{ width: 230, padding: 6 }}>
      <div className="ia-menu-title">Отправить пост</div>
      {['Себе в Telegram', 'Редакция', 'Новости'].map((t, i) => (
        <motion.div
          key={t}
          className="ia-menu-item"
          animate={{ backgroundColor: ['rgba(0,0,0,0)', 'rgba(0,0,0,0)', 'var(--accent-soft)', 'rgba(0,0,0,0)'] }}
          transition={{ duration: 4.5, repeat: Infinity, delay: i * 1.5, times: [0, 0.01, 0.12, 0.33], ease: 'linear' }}
        >
          <span className="ia-avatar" style={{ width: 20, height: 20, background: i ? COLORS[i + 1] : 'var(--accent)' }}>
            {i ? <Users size={11} color="#fff" /> : <Send size={10} color="#fff" />}
          </span>
          {t}
        </motion.div>
      ))}
    </div>
  )
}

function Team() {
  const people = [
    { n: 'anna', r: 'Каналы' },
    { n: 'max', r: 'ИИ-чат' },
    { n: 'olga', r: 'Публикация' },
  ]
  return (
    <div className="ia-row" style={{ gap: 14 }}>
      {people.map((p, i) => (
        <motion.div
          key={p.n}
          className="ia-person"
          animate={{ y: [16, 0, 0, 16], opacity: [0, 1, 1, 0] }}
          transition={{ ...loop(4.2, i * 0.3), times: [0, 0.14, 0.86, 1] }}
        >
          <Avatar i={i + 1} size={42}>
            <span className="ia-initial">{p.n[0].toUpperCase()}</span>
          </Avatar>
          <span className="ia-login">{p.n}</span>
          <span className="ia-role">{p.r}</span>
        </motion.div>
      ))}
    </div>
  )
}

function Rights() {
  const rights = ['Добавлять каналы', 'Общаться с ИИ', 'Публиковать посты ИИ']
  return (
    <div className="ia-card" style={{ width: 250, padding: '4px 0' }}>
      {rights.map((r, i) => (
        <div key={r} className="ia-right">
          <span>{r}</span>
          <span className="ia-toggle">
            <motion.span
              animate={{ x: [0, 0, 16, 16, 0] }}
              transition={{ ...loop(4.5), times: [0, 0.15 + i * 0.15, 0.22 + i * 0.15, 0.9, 1] }}
            />
          </span>
        </div>
      ))}
    </div>
  )
}

function TeamTg() {
  return <BellArt label="Feedor" text="Оригинал поста — вам в Telegram" />
}

function Api() {
  const lines = [
    { t: 'GET  /api/v1/posts?sort=fr_plus', c: '#64d2ff' },
    { t: '{ "views": 12480, "er": 4.6 }', c: '#f2f2f7' },
    { t: 'POST /api/chat-posts/7/publish', c: '#0a84ff' },
    { t: '{ "published": true }', c: '#f2f2f7' },
    { t: 'PUT  /api/rubrics/3', c: '#ff9f0a' },
    { t: '{ "enabled": true }', c: '#f2f2f7' },
  ]
  return (
    <div className="ia-code">
      {lines.map((l, i) => (
        <motion.div
          key={i}
          style={{ color: l.c }}
          animate={{ opacity: [0, 1, 1, 0] }}
          transition={{ ...loop(5, i * 0.25), times: [0, 0.08, 0.86, 1] }}
        >
          {l.t}
        </motion.div>
      ))}
    </div>
  )
}

function ApiDocs() {
  return (
    <div className="ia-row" style={{ gap: 16 }}>
      <div className="ia-card" style={{ width: 120 }}>
        <div className="ia-head">
          <Code2 size={14} color="var(--accent)" />
          <span style={{ fontSize: 10, fontWeight: 600 }}>Документация</span>
        </div>
        <Lines widths={[100, 80, 90, 60]} h={5} gap={4} />
      </div>
      <motion.div
        className="ia-copy"
        animate={{ scale: [1, 0.9, 1], backgroundColor: ['var(--accent)', 'var(--green)', 'var(--accent)'] }}
        transition={{ ...loop(2.4), repeatDelay: 1 }}
      >
        <Copy size={16} color="#fff" />
      </motion.div>
      <motion.div animate={{ opacity: [0.4, 1, 0.4] }} transition={loop(2.4, 0.4)}>
        <span className="ia-avatar" style={{ width: 40, height: 40, background: 'var(--indigo)' }}>
          <Sparkles size={18} color="#fff" />
        </span>
      </motion.div>
    </div>
  )
}

function ApiKey() {
  return (
    <div className="ia-key">
      <motion.span animate={{ rotate: [0, -12, 8, 0] }} transition={{ ...loop(2), repeatDelay: 1.4 }}>
        <KeyRound size={30} />
      </motion.span>
      <span className="ia-key-text">
        <Typed text="km_3f9a…c2e1" hold={2.4} />
      </span>
    </div>
  )
}

function Mcp() {
  // the AI app is asked, calls the site's tool, says it is done
  return (
    <div className="ia-chat">
      <motion.div
        className="ia-bubble me"
        animate={{ opacity: [0, 1, 1, 0], y: [10, 0, 0, 0] }}
        transition={{ ...loop(5.5), times: [0, 0.08, 0.9, 1] }}
      >
        Опубликуй пост о главной теме утра
      </motion.div>
      <motion.div
        className="ia-tool"
        animate={{ opacity: [0, 0, 1, 1, 0], x: [-8, -8, 0, 0, 0] }}
        transition={{ ...loop(5.5), times: [0, 0.2, 0.28, 0.9, 1] }}
      >
        <Wrench size={11} />
        post_chat_posts_post_id_publish
        <motion.span
          animate={{ opacity: [0, 0, 1, 1, 0], scale: [0.4, 0.4, 1, 1, 1] }}
          transition={{ ...loop(5.5), times: [0, 0.4, 0.46, 0.9, 1] }}
          style={{ display: 'flex' }}
        >
          <Check size={12} color="var(--green)" strokeWidth={3} />
        </motion.span>
      </motion.div>
      <motion.div
        className="ia-bubble ai"
        animate={{ opacity: [0, 0, 1, 1, 0], y: [10, 10, 0, 0, 0] }}
        transition={{ ...loop(5.5), times: [0, 0.52, 0.6, 0.9, 1] }}
      >
        Готово: пост вышел в вашем канале.
      </motion.div>
    </div>
  )
}

function McpConnect() {
  // the connector's address goes into the app; the site asks; «Разрешить» is pressed
  return (
    <div className="ia-row" style={{ gap: 10 }}>
      <div className="ia-card ia-mcp-url">
        <Plug size={13} color="var(--indigo)" />
        <span>…/mcp</span>
      </div>
      <motion.span animate={{ x: [0, 4, 0] }} transition={loop(1.6)} style={{ color: 'var(--text-3)', fontSize: 16 }}>
        →
      </motion.span>
      <div className="ia-card" style={{ width: 130, textAlign: 'center' }}>
        <div style={{ fontSize: 10.5, fontWeight: 600, marginBottom: 6 }}>«Claude» просит доступ</div>
        <motion.div
          className="ia-allow"
          animate={{
            scale: [1, 1, 0.92, 1, 1],
            backgroundColor: ['#0a84ff', '#0a84ff', '#0a84ff', '#34c759', '#0a84ff'],
          }}
          transition={{ ...loop(3.2), times: [0, 0.4, 0.5, 0.6, 1] }}
        >
          Разрешить
        </motion.div>
      </div>
    </div>
  )
}

function McpSafe() {
  // «Подключённые приложения»: one of them is turned off
  return (
    <div className="ia-card" style={{ width: 210, padding: '6px 10px' }}>
      {['Claude', 'ChatGPT'].map((name, i) => (
        <motion.div
          key={name}
          className="ia-mcp-app"
          animate={i === 1 ? { opacity: [1, 1, 0.25, 0.25, 1], x: [0, 0, 12, 12, 0] } : undefined}
          transition={i === 1 ? { ...loop(4), times: [0, 0.35, 0.5, 0.85, 1] } : undefined}
        >
          <span className="ia-avatar" style={{ width: 22, height: 22, background: i ? 'var(--green)' : 'var(--orange)' }}>
            <Plug size={11} color="#fff" />
          </span>
          <span style={{ flex: 1, fontWeight: 600 }}>{name}</span>
          <Unplug size={13} color="var(--red)" />
        </motion.div>
      ))}
    </div>
  )
}

const ARTS: Record<ArtName, () => ReactNode> = {
  feed: Feed,
  metrics: Metrics,
  search: SearchArt,
  smart: Smart,
  trigger: Trigger,
  forms: Forms,
  bell: () => <BellArt />,
  ranking: Ranking,
  sorts: Sorts,
  idea: Idea,
  chat: Chat,
  postcard: PostCard,
  learn: Learn,
  converge: Converge,
  topiccard: TopicCard,
  clock: Clock,
  pubs: Pubs,
  publish: Publish,
  autopilot: Autopilot,
  channels: Channels,
  status: Status,
  stats: Stats,
  groups: Groups,
  groupsteps: GroupSteps,
  groupsend: GroupSend,
  team: Team,
  rights: Rights,
  teamtg: TeamTg,
  api: Api,
  apidocs: ApiDocs,
  apikey: ApiKey,
  mcp: Mcp,
  mcpconnect: McpConnect,
  mcpsafe: McpSafe,
}

export function IntroArt({ name }: { name: ArtName }) {
  const Art = ARTS[name]
  return (
    <div className="intro-art" aria-hidden="true">
      <Art />
    </div>
  )
}
