import { AnimatePresence, motion } from 'framer-motion'
import { CreditCard, Loader2, Send } from 'lucide-react'
import { type FormEvent, useCallback, useEffect, useRef, useState } from 'react'
import { api, type QrLoginStatus } from '../api'
import { LegalLinks, LegalSheet } from '../components/LegalSheet'
import { Wordmark } from '../components/Logo'
import type { LegalDoc } from '../lib/legal'
import { QrCode, QrIcon, qrLink } from '../components/Qr'
import { Sheet } from '../components/Sheet'
import { useMouse } from '../hooks/useMedia'
import { spring } from '../lib/motion'
import { siteName } from '../lib/site'
import { CHANNEL_URL, SUPPORT_URL } from './UserSettings'

/** The admin's password form lives only at this hidden address; owners sign in through the bot, a
 * project's staff with the login and password the owner gave them. */
const isAdminAddress = () => /^#\/?admin\b/.test(location.hash)

export function Login({ onDone }: { onDone: () => void }) {
  const [admin, setAdmin] = useState(isAdminAddress)
  const [staff, setStaff] = useState(false)
  const [legal, setLegal] = useState<LegalDoc | null>(null)
  // a computer's QR code opened by this phone's camera before it signed in: it confirms that after
  const scannedHere = /^#\/qr\//.test(location.hash)

  useEffect(() => {
    const onHash = () => setAdmin(isAdminAddress())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  return (
    <motion.div className="login" initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={spring}>
      <motion.h1 className="login-wordmark" initial={{ scale: 0.9 }} animate={{ scale: 1 }} transition={spring}>
        <Wordmark width={240} />
      </motion.h1>
      <p>
        {staff && !admin
          ? 'Вход для сотрудников проекта'
          : scannedHere
            ? 'Войдите, чтобы подтвердить вход на компьютере по QR-коду'
            : 'Посты из ваших Telegram-каналов в одном месте'}
      </p>
      <AnimatePresence mode="wait" initial={false}>
        <motion.div
          key={admin ? 'admin' : staff ? 'staff' : 'tg'}
          style={{ width: '100%' }}
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -6, transition: { duration: 0.12 } }}
          transition={spring}
        >
          {admin ? (
            <PasswordForm
              onSubmit={async (login, password) => {
                await api.login(login, password)
                location.hash = '/'
              }}
              onDone={onDone}
            />
          ) : staff ? (
            <>
              <PasswordForm onSubmit={api.memberLogin} onDone={onDone} hint="Логин и пароль выдаёт владелец проекта." />
              <div className="login-switch">
                <button className="link-inline" onClick={() => setStaff(false)}>
                  Войти через Telegram
                </button>
              </div>
            </>
          ) : (
            <>
              <TelegramLogin onDone={onDone} />
              <div className="login-plans">
                <motion.button whileTap={{ scale: 0.96 }} onClick={() => (location.hash = '/plans')}>
                  <CreditCard size={16} />
                  Посмотреть тарифы
                </motion.button>
              </div>
              <div className="login-switch quiet">
                <button onClick={() => setStaff(true)}>Вход для сотрудников</button>
              </div>
            </>
          )}
        </motion.div>
      </AnimatePresence>
      <footer className="login-footer">
        <a href={SUPPORT_URL} target="_blank" rel="noreferrer">
          Поддержка
        </a>
        <a href={CHANNEL_URL} target="_blank" rel="noreferrer">
          Наш канал
        </a>
        <LegalLinks onOpen={setLegal} />
      </footer>
      <LegalSheet doc={legal} onClose={() => setLegal(null)} />
    </motion.div>
  )
}

const POLL_EVERY = 1500

function TelegramLogin({ onDone }: { onDone: () => void }) {
  // a phone is what scans the code, not what shows it: the button is a computer's
  const computer = useMouse()
  const [qr, setQr] = useState(false)
  const [link, setLink] = useState<string | null>(null)
  const [waiting, setWaiting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const expiresAt = useRef(0)

  // the link is fetched ahead, so the button is a plain link: a tap opens Telegram right away
  const fresh = useCallback(async () => {
    try {
      const res = await api.tgLoginStart()
      setLink(res.link)
      expiresAt.current = Date.now() + (res.expires_in - 20) * 1000
      return true
    } catch (e) {
      setLink(null)
      setError((e as Error).message)
      return false
    }
  }, [])

  useEffect(() => {
    fresh()
  }, [fresh])

  // an unused link expires: renew it quietly while the page is open
  useEffect(() => {
    if (waiting || !link) return
    const timer = window.setInterval(() => {
      if (Date.now() > expiresAt.current && document.visibilityState === 'visible') fresh()
    }, 10_000)
    return () => window.clearInterval(timer)
  }, [waiting, link, fresh])

  useEffect(() => {
    if (!waiting) return
    let stopped = false
    const check = async () => {
      try {
        const { status } = await api.tgLoginStatus()
        if (stopped) return
        if (status === 'ok') {
          onDone()
          return
        }
        if (status === 'declined' || status === 'expired') {
          setWaiting(false)
          setError(
            status === 'declined' ? 'Вход отменён в Telegram' : 'Время на подтверждение вышло — нажмите кнопку ещё раз',
          )
          fresh()
        }
      } catch {
        /* a network blip: the next check will tell */
      }
    }
    const timer = window.setInterval(check, POLL_EVERY)
    // coming back from the Telegram app: check at once instead of waiting for the timer
    const onVisible = () => document.visibilityState === 'visible' && check()
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      stopped = true
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [waiting, onDone, fresh])

  return (
    <div className="section" style={{ padding: '0 16px', marginTop: 8 }}>
      <div className="tg-split">
        <a
          className={`button tg-button${link ? '' : ' disabled'}`}
          href={link ?? undefined}
          target="_blank"
          rel="noreferrer"
          aria-disabled={!link}
          onClick={(e) => {
            if (!link) {
              e.preventDefault()
              return
            }
            setError(null)
            setWaiting(true)
          }}
        >
          {link ? (
            <Send size={20} style={{ marginLeft: -4 }} />
          ) : (
            <Loader2 size={20} className="spinner" style={{ color: '#fff' }} />
          )}
          Войти через Telegram
        </a>
        {computer && (
          <button
            className="tg-qr"
            onClick={() => setQr(true)}
            aria-label="Войти по QR-коду"
            title="Войти по QR-коду — отсканируйте его телефоном, на котором вы уже вошли"
          >
            <QrIcon size={26} />
          </button>
        )}
      </div>
      {computer && <QrLoginSheet open={qr} onClose={() => setQr(false)} onDone={onDone} />}
      <AnimatePresence initial={false} mode="wait">
        {waiting ? (
          <motion.div
            key="waiting"
            className="login-note"
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            transition={spring}
          >
            <Loader2 size={16} className="spinner" />
            <span>
              Нажмите «Подтвердить вход» в сообщении от бота.{' '}
              <button className="link-inline" onClick={() => setWaiting(false)}>
                Отмена
              </button>
            </span>
          </motion.div>
        ) : error ? (
          <motion.div
            key={error}
            className="login-note"
            style={{ color: 'var(--red)' }}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
          >
            {error}
          </motion.div>
        ) : (
          <motion.div
            key="hint"
            className="login-note"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
          >
            Откроется наш бот в Telegram — подтвердите в нём вход. Пароль не нужен.
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

function PasswordForm({
  onSubmit,
  onDone,
  hint,
}: {
  onSubmit: (login: string, password: string) => Promise<unknown>
  onDone: () => void
  hint?: string
}) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await onSubmit(username.trim(), password)
      onDone()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit} style={{ width: '100%' }}>
      <div className="section">
        <div className="group">
          <input
            aria-label="Логин"
            className="field"
            placeholder="Логин"
            autoComplete="username"
            autoCapitalize="none"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
          <input
            aria-label="Пароль"
            className="field"
            placeholder="Пароль"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>
        {error ? (
          <motion.div
            className="section-footer"
            style={{ color: 'var(--red)' }}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
          >
            {error}
          </motion.div>
        ) : (
          hint && <div className="section-footer">{hint}</div>
        )}
      </div>
      <div style={{ padding: '0 16px' }}>
        <button className="button" disabled={busy || !username || !password}>
          {busy ? <Loader2 size={20} className="spinner" style={{ color: '#fff' }} /> : 'Войти'}
        </button>
      </div>
    </form>
  )
}

/**
 * «Войти по QR-коду»: the code a phone signed in scans (Settings → «Устройства» → «Подключить устройство»). A new
 * one every minute while the window is open; the page signs in as soon as the phone confirms.
 */
function QrLoginSheet({ open, onClose, onDone }: { open: boolean; onClose: () => void; onDone: () => void }) {
  const [token, setToken] = useState<string | null>(null)
  const [status, setStatus] = useState<QrLoginStatus>('pending')
  const [error, setError] = useState<string | null>(null)
  const [tired, setTired] = useState(false)
  const renewals = useRef(0)
  const renewAt = useRef(0)

  const fresh = useCallback(async () => {
    try {
      const res = await api.qrLoginStart()
      setToken(res.token)
      setError(null)
      renewAt.current = Date.now() + res.expires_in * 1000
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])

  useEffect(() => {
    if (!open) return
    setStatus('pending')
    setTired(false)
    renewals.current = 0
    setToken(null)
    fresh()
  }, [open, fresh])

  useEffect(() => {
    if (!open || tired) return
    let stopped = false
    const check = async () => {
      try {
        const res = await api.qrLoginStatus()
        if (stopped) return
        if (res.status === 'ok') {
          stopped = true
          onDone()
          return
        }
        setStatus(res.status)
        if (res.status === 'declined') {
          setError('Вход отменён на телефоне')
          renewals.current = 0
          fresh()
          return
        }
        if (res.status === 'scanned') return // the phone is asking: the code stays
        if (Date.now() > renewAt.current && document.visibilityState === 'visible') {
          // a window left open stops asking after a while (the codes a browser may ask for are counted)
          if (++renewals.current > 10) setTired(true)
          else fresh()
        }
      } catch {
        /* a network blip: the next check will tell */
      }
    }
    const timer = window.setInterval(check, POLL_EVERY)
    return () => {
      stopped = true
      window.clearInterval(timer)
    }
  }, [open, tired, onDone, fresh])

  const again = () => {
    renewals.current = 0
    setTired(false)
    setError(null)
    fresh()
  }

  return (
    <Sheet
      open={open}
      onClose={onClose}
      title="Вход по QR-коду"
      left={
        <button className="nav-button" onClick={onClose}>
          Закрыть
        </button>
      }
    >
      <div className="qr-login">
        <div className={`qr-login-code${status === 'scanned' || tired || !token ? ' veiled' : ''}`}>
          <AnimatePresence initial={false} mode="popLayout">
            {token && (
              <motion.div
                key={token}
                initial={{ opacity: 0, scale: 0.96 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0 }}
                transition={spring}
              >
                <QrCode value={qrLink(token)} size={232} />
              </motion.div>
            )}
          </AnimatePresence>
          {(status === 'scanned' || tired || !token) && (
            <div className="qr-login-veil">
              {tired ? (
                <button className="button" onClick={again}>
                  Показать новый код
                </button>
              ) : (
                <>
                  <Loader2 size={26} className="spinner" />
                  {status === 'scanned' && <span>Подтвердите вход на телефоне</span>}
                </>
              )}
            </div>
          )}
        </div>
        {error && <div className="qr-login-error">{error}</div>}
        <ol className="qr-login-steps">
          <li>
            <span>Откройте {siteName()} на телефоне, где вы уже вошли</span>
          </li>
          <li>
            <span>
              Перейдите в <b>Настройки</b> → <b>Устройства</b>
            </span>
          </li>
          <li>
            <span>
              Нажмите <b>Подключить устройство</b> и наведите камеру на этот код
            </span>
          </li>
        </ol>
      </div>
    </Sheet>
  )
}
