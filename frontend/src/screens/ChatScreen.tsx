import { AnimatePresence, motion } from 'framer-motion'
import { AlertCircle, ArrowUp, ChevronLeft, Copy, Ellipsis, Loader2, RotateCcw, Sparkles } from 'lucide-react'
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { api, type ChatMessageOut, type ChatPostOut, type ChatThreadOut } from '../api'
import { ChatPostCard } from '../components/ChatPostCard'
import { ChatRenameSheet } from '../components/ChatRenameSheet'
import { ErrorAlert, type ErrorInfo } from '../components/ErrorAlert'
import { Page } from '../components/Page'
import { ActionSheet } from '../components/Sheet'
import { useToast } from '../components/Toast'
import { useMouse } from '../hooks/useMedia'
import { TypedMarkdown } from '../components/TypedMarkdown'
import { badgesChanged } from '../hooks/useBadges'
import { watchChat } from '../lib/chatWatch'
import { copyText } from '../lib/clipboard'
import { plainText } from '../lib/markdown'
import { spring } from '../lib/motion'

const shortModel = (id: string) => id.split('/').pop() ?? id

const HINTS = [
  'Что важного случилось за сутки?',
  'О чём писали конкуренты на этой неделе, а я нет?',
  'Какие посты моего канала зашли лучше всего?',
  'Предложи три идеи для постов на завтра',
]

const dropTrailingErrors = (messages: ChatMessageOut[]) => {
  let end = messages.length
  while (end > 0 && messages[end - 1].role === 'error') end--
  return messages.slice(0, end)
}

/** Closer to the bottom than this, the page follows the answer as it grows. */
const STICK = 120
/** While an answer is written elsewhere (the page was left and opened again), how often to look. */
const POLL_MS = 1200

/** A chat's first message, already written in its field when it opens (a post on a topic, NewChatScreen):
 * the person may change it, and sends it themselves. This tab only. */
export function firstMessage(id: number, text: string) {
  try {
    sessionStorage.setItem(`chat-send:${id}`, text)
  } catch {
    /* the person asks themselves */
  }
}

function takeFirstMessage(id: number): string | null {
  try {
    const text = sessionStorage.getItem(`chat-send:${id}`)
    sessionStorage.removeItem(`chat-send:${id}`)
    return text
  } catch {
    return null
  }
}

const timeFmt = new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit' })
const dayTimeFmt = new Intl.DateTimeFormat('ru-RU', {
  day: 'numeric',
  month: 'short',
  hour: '2-digit',
  minute: '2-digit',
})
/** When an answer was ready: the time today, the day and time before. */
const readyAt = (iso: string) => {
  const d = new Date(iso)
  return (d.toDateString() === new Date().toDateString() ? timeFmt : dayTimeFmt).format(d)
}
/** 35с, 2м 05с */
const seconds = (s: number) => (s < 60 ? `${s}с` : `${Math.floor(s / 60)}м ${String(s % 60).padStart(2, '0')}с`)

const atBottom = () => document.documentElement.scrollHeight - window.scrollY - window.innerHeight < STICK
const toBottom = (smooth = false) =>
  window.scrollTo({ top: document.documentElement.scrollHeight, behavior: smooth ? 'smooth' : 'auto' })

/**
 * A chat with the AI: the answer appears as it is written, with what it is looking at meanwhile.
 * The answer is written on the server: leaving the page does not stop it, and coming back picks it up.
 */
export function ChatScreen({ id, onBack }: { id: number; onBack: () => void }) {
  const toast = useToast()
  const [thread, setThread] = useState<ChatThreadOut | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [text, setText] = useState('')
  // the answer being written: streamed to this page, or (written elsewhere) asked for every second
  const [pending, setPending] = useState<{ text: string; status: string | null } | null>(null)
  const [streaming, setStreaming] = useState(false)
  const [menu, setMenu] = useState(false)
  const [renaming, setRenaming] = useState(false)
  // a failed answer, told in an alert with its exact text
  const [failure, setFailure] = useState<ErrorInfo | null>(null)
  const input = useRef<HTMLTextAreaElement>(null)
  const abort = useRef<AbortController | null>(null)
  // follow the bottom only while the reader is there; scrolling up leaves the page still
  const stick = useRef(true)
  const box = useRef<HTMLDivElement>(null)
  // where the answer being written stands among the messages: its bubble keeps the key "live" from
  // the first letters to the saved message, so the typing goes on in it instead of starting anew
  const [liveIndex, setLiveIndex] = useState<number | null>(null)
  // when the AI took the question: the seconds of the answer run from here (on this page or on the server)
  const [startedAt, setStartedAt] = useState<number | null>(null)
  const [now, setNow] = useState(() => Date.now())

  const reload = useCallback(async () => {
    try {
      const t = await api.chat(id)
      setThread(t)
      setPending(t.pending)
      // an answer being written while the page was away goes after the last message
      if (t.busy) setLiveIndex((i) => i ?? t.messages.length)
      // the answer may come while the person is elsewhere on the site: they are told (ChatNotice)
      if (t.busy) watchChat(t.id)
      // an answer begun before the page was opened: its time so far comes from the server
      if (t.busy && t.pending) setStartedAt((at) => at ?? Date.now() - t.pending!.elapsed * 1000)
      else if (!t.busy) setStartedAt(null)
      setError(null)
      return t
    } catch (e) {
      setError((e as Error).message)
      return null
    }
  }, [id])

  useEffect(() => {
    reload().then(() => {
      stick.current = true
      requestAnimationFrame(() => toBottom())
      badgesChanged() // opened: its answer is read
    })
    // leaving only stops reading: the server writes the answer to the end
    return () => abort.current?.abort()
  }, [reload])

  // an answer written while the page was away: follow it until it is done
  const busyElsewhere = !!thread?.busy && !streaming
  useEffect(() => {
    if (!busyElsewhere) return
    const timer = window.setInterval(reload, POLL_MS)
    return () => window.clearInterval(timer)
  }, [busyElsewhere, reload])

  useEffect(() => {
    const onScroll = () => {
      stick.current = atBottom()
    }
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  // the chat grew (a message, a typed letter): down with it, if the reader is at the bottom —
  // vertically only, so nothing jumps sideways
  const ready = !!thread
  useEffect(() => {
    const el = box.current
    if (!el) return
    const observer = new ResizeObserver(() => {
      if (stick.current) toBottom()
    })
    observer.observe(el)
    return () => observer.disconnect()
  }, [ready])

  // the field grows with the text, up to a few lines
  useLayoutEffect(() => {
    const el = input.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`
  }, [text])

  const send = (raw?: string) => {
    const message = (raw ?? text).trim()
    if (!message) return
    setText('')
    answer(message)
  }

  /** The AI answers a new message, or (null) the last question again after a failed answer. */
  const answer = async (message: string | null) => {
    if (streaming || thread?.busy || !thread) return
    setStreaming(true)
    watchChat(thread.id)
    setStartedAt(Date.now())
    setNow(Date.now())
    setPending({ text: '', status: 'Думаю' })
    // a retry: the failure under the question goes, the answer takes its place
    const kept = message === null ? dropTrailingErrors(thread.messages) : thread.messages
    const mine: ChatMessageOut[] =
      message === null ? [] : [{ id: -Date.now(), role: 'user', text: message, at: new Date().toISOString() }]
    setLiveIndex(kept.length + mine.length)
    setThread((t) => (t ? { ...t, busy: true, messages: [...kept, ...mine] } : t))
    stick.current = true
    let afterTool = false
    abort.current = new AbortController()
    try {
      await api.sendChat(
        thread.id,
        message,
        (e) => {
          if (e.type === 'status') {
            setPending((p) => ({ text: p?.text ?? '', status: e.text }))
            afterTool = true
          } else if (e.type === 'delta') {
            const glue = afterTool
            afterTool = false
            setPending((p) => {
              const before = p?.text ?? ''
              return { text: before + (glue && before && !before.endsWith('\n') ? '\n\n' : '') + e.text, status: null }
            })
          } else if (e.type === 'error') {
            setFailure({ text: e.text, detail: e.detail })
          }
        },
        abort.current.signal,
      )
    } catch (e) {
      if ((e as Error).name !== 'AbortError') setFailure({ text: (e as Error).message })
    } finally {
      setStreaming(false)
      reload()
    }
  }

  const remove = async () => {
    try {
      await api.deleteChat(id)
      onBack()
    } catch (e) {
      toast((e as Error).message)
    }
  }

  const answering = streaming || !!thread?.busy

  // a chat made to ask something (a post on a topic): the question is in the field, ready to send
  const loadedId = thread?.id
  const mouse = useMouse()
  useEffect(() => {
    if (!thread || thread.busy || thread.messages.length) return
    const first = takeFirstMessage(thread.id)
    if (!first) return
    setText(first)
    // a phone would open its keyboard over the text: there the person taps the field if they want to edit
    if (mouse) requestAnimationFrame(() => input.current?.focus())
  }, [loadedId]) // eslint-disable-line react-hooks/exhaustive-deps
  // a phone's keyboard: the tab bar does not go up with the field (styles.css, .keyboard-open)
  useEffect(() => () => document.documentElement.classList.remove('keyboard-open'), [])
  useEffect(() => {
    if (!answering) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [answering])
  const elapsed = startedAt === null ? 0 : Math.max(0, Math.floor((now - startedAt) / 1000))
  const messages = thread?.messages ?? []
  const rows: (Pick<ChatMessageOut, 'role' | 'text' | 'detail' | 'took_ms' | 'post'> & {
    key: string
    id?: number
    at?: string
    live: boolean
  })[] = messages.map((m, i) => {
    const live = i === liveIndex && m.role === 'assistant'
    return { ...m, key: live ? 'live' : m.role === 'post' ? `post-${m.post!.id}` : String(m.id), live }
  })

  // «Оформить как пост»: the answer becomes a post, shown as one
  const makePost = async (messageId: number) => {
    try {
      await api.chatPostFromMessage(messageId)
      await reload()
    } catch (e) {
      toast((e as Error).message)
    }
  }
  const postChanged = (post: ChatPostOut) =>
    setThread((t) => (t ? { ...t, messages: t.messages.map((m) => (m.post?.id === post.id ? { ...m, post } : m)) } : t))
  const lastIsError = messages[messages.length - 1]?.role === 'error'
  if (answering && pending?.text && liveIndex !== null && !messages[liveIndex]) {
    rows.push({ key: 'live', role: 'assistant', text: pending.text, live: true })
  }

  return (
    <Page
      title={thread?.title ?? 'Чат'}
      onTitleClick={thread ? () => setRenaming(true) : undefined}
      left={
        <motion.button className="nav-button" onClick={onBack} whileTap={{ opacity: 0.4 }} style={{ paddingLeft: 0 }}>
          <ChevronLeft size={30} strokeWidth={2.2} style={{ marginRight: -4 }} />
          AI
        </motion.button>
      }
      right={
        <button className="nav-button" onClick={() => setMenu(true)} aria-label="Ещё">
          <Ellipsis size={22} />
        </button>
      }
    >
      {error && <div className="error-banner">{error}</div>}
      {!thread && !error && (
        <div className="loader">
          <Loader2 size={22} className="spinner" />
        </div>
      )}
      {thread && (
        <div className="chat" ref={box}>
          {thread.model && (
            <button className="chat-model" onClick={() => (location.hash = '/openrouter')}>
              <Sparkles size={13} />
              {shortModel(thread.model)} · модель этого чата
            </button>
          )}
          {messages.length === 0 && !answering && (
            <div className="chat-hints">
              {HINTS.map((h) => (
                <button key={h} className="chat-hint" onClick={() => send(h)}>
                  {h}
                </button>
              ))}
            </div>
          )}
          <AnimatePresence initial={false}>
            {rows.map((m, i) =>
              m.role === 'error' ? (
                <motion.div
                  key={m.key}
                  className="chat-failed"
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={spring}
                >
                  <button className="chat-failed-text" onClick={() => setFailure({ text: m.text, detail: m.detail })}>
                    <AlertCircle size={15} />
                    <span>ИИ не ответил: {m.text}</span>
                  </button>
                  {i === rows.length - 1 && lastIsError && !answering && (
                    <button className="button small" onClick={() => answer(null)}>
                      <RotateCcw size={15} />
                      Отправить заново
                    </button>
                  )}
                </motion.div>
              ) : m.role === 'post' && m.post ? (
                <motion.div
                  key={m.key}
                  className="chat-post-row"
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={spring}
                >
                  <ChatPostCard post={m.post} onChange={postChanged} />
                </motion.div>
              ) : m.role === 'assistant' ? (
                <motion.div
                  key={m.key}
                  className="chat-answer"
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={spring}
                >
                  <Answer
                    text={m.text}
                    typing={m.live && answering}
                    at={m.at}
                    took={m.took_ms}
                    onMakePost={m.id && m.id > 0 && !(m.live && answering) ? () => makePost(m.id!) : undefined}
                  />
                </motion.div>
              ) : (
                <motion.div
                  key={m.key}
                  className="bubble user"
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={spring}
                >
                  {m.text}
                </motion.div>
              ),
            )}
          </AnimatePresence>
          {answering && (
            <div className="chat-status">
              <Loader2 size={14} className="spinner" />
              <span className="chat-timer">{seconds(elapsed)}</span>·{' '}
              {pending?.status ?? (pending?.text ? 'Пишу ответ' : 'Думаю')}…
            </div>
          )}
        </div>
      )}

      {thread && (
        <form
          className="chat-composer"
          onSubmit={(e) => {
            e.preventDefault()
            send()
          }}
        >
          <textarea
            ref={input}
            rows={1}
            placeholder="Сообщение"
            // a touch screen: its keyboard comes up (a field on a computer brings none)
            onFocus={() =>
              matchMedia('(pointer: coarse)').matches && document.documentElement.classList.add('keyboard-open')
            }
            onBlur={() => document.documentElement.classList.remove('keyboard-open')}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault()
                send()
              }
            }}
          />
          <button className="chat-send" type="submit" disabled={!text.trim() || answering} aria-label="Отправить">
            {answering ? <Loader2 size={18} className="spinner" /> : <ArrowUp size={20} strokeWidth={2.4} />}
          </button>
        </form>
      )}

      <ErrorAlert error={failure} onClose={() => setFailure(null)} />
      <ActionSheet
        open={menu}
        onClose={() => setMenu(false)}
        title={thread?.title}
        actions={[
          { label: 'Переименовать', onClick: () => setRenaming(true) },
          { label: 'Удалить чат', destructive: true, onClick: remove },
        ]}
      />
      <ChatRenameSheet
        open={renaming}
        title={thread?.title ?? ''}
        onClose={() => setRenaming(false)}
        onSave={async (title) => {
          try {
            const res = await api.renameChat(id, title)
            setThread((t) => (t ? { ...t, title: res.title } : t))
            setRenaming(false)
          } catch (e) {
            toast((e as Error).message)
          }
        }}
      />
    </Page>
  )
}

/** The AI's answer, and once it is written, under it: «…» (with «Оформить как пост», for a post the model wrote as
 * text) and «Скопировать» (the text as it reads, without the markup). */
function Answer({
  text,
  typing,
  at,
  took,
  onMakePost,
}: {
  text: string
  typing: boolean
  at?: string
  took?: number | null
  onMakePost?: () => void
}) {
  const toast = useToast()
  const [menu, setMenu] = useState(false)
  const copy = async () =>
    toast((await copyText(plainText(text))) ? 'Ответ скопирован' : 'Не удалось скопировать ответ')
  return (
    <>
      <div className="bubble assistant">
        <TypedMarkdown text={text} typing={typing} />
      </div>
      <AnimatePresence initial={false}>
        {!typing && (
          <motion.div
            className="chat-answer-meta"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
          >
            {onMakePost && (
              <motion.button
                className="chat-copy chat-more"
                onClick={() => setMenu(true)}
                aria-label="Ещё"
                title="Ещё"
                whileTap={{ opacity: 0.4 }}
              >
                <Ellipsis size={16} />
              </motion.button>
            )}
            <motion.button
              className="chat-copy"
              onClick={copy}
              aria-label="Скопировать ответ"
              title="Скопировать ответ"
              whileTap={{ opacity: 0.4 }}
            >
              <Copy size={15} />
              <span className="chat-copy-label">Скопировать</span>
            </motion.button>
            {took != null && (
              <span className="chat-took">Ответ занял {seconds(Math.max(1, Math.round(took / 1000)))}</span>
            )}
            {at && <span className="chat-ready">{readyAt(at)}</span>}
          </motion.div>
        )}
      </AnimatePresence>
      {onMakePost && (
        <ActionSheet
          open={menu}
          onClose={() => setMenu(false)}
          actions={[{ label: 'Оформить как пост', onClick: onMakePost }]}
        />
      )}
    </>
  )
}
