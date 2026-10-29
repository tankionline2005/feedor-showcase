import { AnimatePresence, motion } from 'framer-motion'
import { Loader2, RefreshCw, Send, Share2, Trash2, UserPlus, UserRound } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { api, type NotifyState } from '../api'
import { ActionSheet, Sheet } from '../components/Sheet'
import { SwipeRow } from '../components/SwipeRow'
import { useToast } from '../components/Toast'
import { copyText } from '../lib/clipboard'
import { plural } from '../lib/format'
import { spring } from '../lib/motion'

interface Props {
  open: boolean
  onClose: () => void
  onChanged: (state: NotifyState) => void
}

const slide = {
  initial: { opacity: 0, x: 24 },
  animate: { opacity: 1, x: 0 },
  exit: { opacity: 0, x: -24 },
  transition: spring,
}

/** Connecting the alert bot: token from @BotFather, then recipients open the bot by a link. */
export function NotifySheet({ open, onClose, onChanged }: Props) {
  const [state, setState] = useState<NotifyState | null>(null)
  const [token, setToken] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [confirmDisconnect, setConfirmDisconnect] = useState(false)
  // replacing the bot of a connected setup: a token field instead of the recipients view
  const [changing, setChanging] = useState(false)
  const [confirmChange, setConfirmChange] = useState(false)
  const toast = useToast()

  const apply = useCallback(
    (s: NotifyState) => {
      setState(s)
      onChanged(s)
    },
    [onChanged],
  )

  const reload = useCallback(async () => {
    try {
      apply(await api.notify())
    } catch {
      /* shown by the parent */
    }
  }, [apply])

  // new recipients appear as soon as they press Start
  useEffect(() => {
    if (!open) return
    setError(null)
    setChanging(false)
    setToken('')
    reload()
    const timer = window.setInterval(reload, 3000)
    return () => window.clearInterval(timer)
  }, [open, reload])

  const saveToken = async () => {
    setBusy(true)
    setError(null)
    try {
      const wasConnected = Boolean(state?.configured)
      apply(await api.setNotifyToken(token.trim()))
      setToken('')
      if (wasConnected) {
        setChanging(false)
        toast('Бот заменён — подключитесь к нему заново')
      }
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const disconnect = async () => {
    try {
      apply(await api.setNotifyToken(''))
      toast('Бот отключён')
    } catch (e) {
      toast((e as Error).message)
    }
  }

  const test = async () => {
    setBusy(true)
    try {
      const res = await api.testNotify()
      toast(`Отправлено в ${res.sent} ${res.sent === 1 ? 'чат' : 'чата'}`)
    } catch (e) {
      toast((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const copyLink = async () => {
    if (!state?.link) return
    toast((await copyText(state.link)) ? 'Ссылка скопирована — отправьте её' : state.link)
  }

  const configured = state?.configured
  const tokenValid = token.trim().length >= 20
  const recipients = state?.chats.length ?? 0
  // replacing a bot with recipients asks first: they all have to press Start again
  const submitChange = () => (recipients > 0 ? setConfirmChange(true) : saveToken())
  const editing = !configured || changing

  const tokenField = (
    <div className="section">
      <div className="group">
        <input
          className="field mono"
          placeholder="123456789:AA…"
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          value={token}
          onChange={(e) => setToken(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && tokenValid && (changing ? submitChange() : saveToken())}
        />
      </div>
      <div className="section-footer" style={error ? { color: 'var(--red)' } : undefined}>
        {error ??
          (changing
            ? 'Список получателей очистится: каждому нужно будет заново нажать Start в новом боте. Если токен не подойдёт, останется прежний бот.'
            : 'Бот будет писать о каждой проблеме: отключённые аккаунты, нерабочие прокси, сломанный репост, недоступные каналы, нехватка прокси и места на сервере.')}
      </div>
    </div>
  )

  return (
    <>
      <Sheet
        open={open}
        onClose={onClose}
        title={changing ? 'Смена бота' : 'Уведомления'}
        left={
          editing ? (
            <button
              className="nav-button"
              onClick={() => {
                if (changing) {
                  setChanging(false)
                  setToken('')
                  setError(null)
                } else onClose()
              }}
            >
              Отмена
            </button>
          ) : undefined
        }
        right={
          editing ? (
            <button
              className="nav-button"
              style={{ fontWeight: 600 }}
              disabled={busy || !tokenValid}
              onClick={changing ? submitChange : saveToken}
            >
              {busy ? <Loader2 size={20} className="spinner" /> : changing ? 'Сменить' : 'Подключить'}
            </button>
          ) : (
            <button className="nav-button" style={{ fontWeight: 600 }} onClick={onClose}>
              Готово
            </button>
          )
        }
      >
        <AnimatePresence mode="wait" initial={false}>
          {!state ? (
            <div className="loader" key="loading">
              <Loader2 size={22} className="spinner" />
            </div>
          ) : !configured ? (
            <motion.div key="setup" {...slide}>
              <ol className="steps">
                <li>
                  Откройте{' '}
                  <a href="https://t.me/BotFather" target="_blank" rel="noopener noreferrer">
                    @BotFather
                  </a>{' '}
                  и создайте бота командой /newbot.
                </li>
                <li>Скопируйте токен, который он пришлёт, и вставьте сюда.</li>
                <li>Нажмите «Подключить себя» и в открывшемся боте — Start.</li>
              </ol>
              {tokenField}
            </motion.div>
          ) : changing ? (
            <motion.div key="change" {...slide}>
              <ol className="steps">
                <li>
                  В{' '}
                  <a href="https://t.me/BotFather" target="_blank" rel="noopener noreferrer">
                    @BotFather
                  </a>{' '}
                  создайте нового бота (/newbot) или возьмите токен существующего (/token).
                </li>
                <li>Вставьте токен сюда и нажмите «Сменить».</li>
              </ol>
              {tokenField}
            </motion.div>
          ) : (
            <motion.div key="ready" {...slide}>
              <div className="section-header" style={{ marginLeft: 16 }}>
                Получатели
              </div>
              <div className="section">
                <div className="group">
                  {state.chats.map((chat) => (
                    <SwipeRow
                      key={chat.chat_id}
                      actions={[
                        {
                          label: 'Убрать',
                          icon: Trash2,
                          color: 'var(--red)',
                          onClick: async () => {
                            try {
                              apply(await api.removeNotifyChat(chat.chat_id))
                            } catch (e) {
                              toast((e as Error).message)
                            }
                          },
                        },
                      ]}
                    >
                      <div className="cell">
                        <div className="cell-icon" style={{ background: 'var(--accent)' }}>
                          <UserRound size={17} />
                        </div>
                        <div className="cell-main">{chat.title || `Чат ${chat.chat_id}`}</div>
                      </div>
                    </SwipeRow>
                  ))}
                  {state.chats.length === 0 && (
                    <div className="cell">
                      <div className="cell-sub" style={{ whiteSpace: 'normal' }}>
                        Пока никого — нажмите «Подключить себя» ниже.
                      </div>
                    </div>
                  )}
                </div>
              </div>

              <div className="section-header" style={{ marginLeft: 16 }}>
                Добавить получателя
              </div>
              <div className="section">
                <div className="group">
                  {state.link && (
                    <a
                      className="cell"
                      style={{ ['--inset' as string]: '57px' }}
                      href={state.link}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      <div className="cell-icon" style={{ background: 'var(--accent)' }}>
                        <UserPlus size={17} />
                      </div>
                      <div className="cell-main">
                        <div className="cell-title" style={{ color: 'var(--accent)' }}>
                          Подключить себя
                        </div>
                        <div className="cell-sub" style={{ whiteSpace: 'normal' }}>
                          Откроется @{state.bot_username} — нажмите в нём Start
                        </div>
                      </div>
                    </a>
                  )}
                  <button className="cell" style={{ ['--inset' as string]: '57px' }} onClick={copyLink}>
                    <div className="cell-icon" style={{ background: 'var(--green)' }}>
                      <Share2 size={16} />
                    </div>
                    <div className="cell-main">
                      <div className="cell-title" style={{ color: 'var(--accent)' }}>
                        Пригласить другого человека
                      </div>
                      <div className="cell-sub">Скопировать ссылку на бота</div>
                    </div>
                  </button>
                </div>
                <div className="section-footer">
                  После Start человек сразу появится в списке получателей. В чате с ботом /status присылает сводку,
                  /stop отключает уведомления.
                </div>
              </div>

              <div className="section">
                <div className="group">
                  <button className="cell accent" disabled={busy || state.chats.length === 0} onClick={test}>
                    {busy ? <Loader2 size={18} className="spinner" /> : <Send size={18} />}
                    Отправить тестовое сообщение
                  </button>
                </div>
              </div>

              <div className="section-header" style={{ marginLeft: 16 }}>
                Бот @{state.bot_username}
              </div>
              <div className="section">
                <div className="group">
                  <button
                    className="cell accent"
                    onClick={() => {
                      setError(null)
                      setChanging(true)
                    }}
                  >
                    <RefreshCw size={18} />
                    Сменить бота
                  </button>
                  <button className="cell destructive" onClick={() => setConfirmDisconnect(true)}>
                    Отключить бота
                  </button>
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </Sheet>
      <ActionSheet
        open={confirmDisconnect}
        onClose={() => setConfirmDisconnect(false)}
        message="Уведомления перестанут приходить, список получателей очистится. Пользователи не смогут войти на сайт через Telegram, пока вы не подключите бота снова."
        actions={[{ label: 'Отключить бота', destructive: true, onClick: disconnect }]}
      />
      <ActionSheet
        open={confirmChange}
        onClose={() => setConfirmChange(false)}
        title="Сменить бота?"
        message={`${recipients} ${plural(recipients, 'получатель будет удалён', 'получателя будут удалены', 'получателей будут удалены')} — каждому нужно будет заново нажать Start в новом боте. Пользователи будут входить на сайт через новый бот; добавьте его в группу-агрегатор, чтобы работала кнопка «Посмотреть оригинал поста».`}
        actions={[{ label: 'Сменить бота', destructive: true, onClick: saveToken }]}
      />
    </>
  )
}
