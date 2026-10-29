import { AnimatePresence, motion } from 'framer-motion'
import { ChevronDown, FileUp, Loader2, X } from 'lucide-react'
import { type DragEvent, useEffect, useRef, useState } from 'react'
import { api, type ImportResult } from '../api'
import { Segmented } from '../components/Segmented'
import { Sheet } from '../components/Sheet'
import { useToast } from '../components/Toast'
import { useMouse } from '../hooks/useMedia'
import { plural } from '../lib/format'
import { spring } from '../lib/motion'

type Mode = 'phone' | 'session' | 'files'
type Step = 'phone' | 'code' | 'password'

interface Props {
  open: boolean
  onClose: () => void
  onAdded: () => void
}

export function AddAccountSheet({ open, onClose, onAdded }: Props) {
  const [mode, setMode] = useState<Mode>('phone')
  const [step, setStep] = useState<Step>('phone')
  const [phone, setPhone] = useState('')
  const [code, setCode] = useState('')
  const [password, setPassword] = useState('')
  const [session, setSession] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [result, setResult] = useState<ImportResult | null>(null)
  const [advanced, setAdvanced] = useState(false)
  const [apiId, setApiId] = useState('')
  const [apiHash, setApiHash] = useState('')
  const [loginId, setLoginId] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const toast = useToast()

  useEffect(() => {
    if (open) return
    if (loginId) api.loginCancel(loginId).catch(() => {})
    setStep('phone')
    setCode('')
    setPassword('')
    setSession('')
    setFiles([])
    setResult(null)
    setLoginId(null)
    setError(null)
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps

  const connection = () => ({
    api_id: apiId.trim() ? Number(apiId.trim()) : undefined,
    api_hash: apiHash.trim() || undefined,
  })

  const done = () => {
    toast('Аккаунт добавлен — он подключится в течение минуты')
    setLoginId(null)
    onAdded()
    onClose()
  }

  const submit = async () => {
    if (mode === 'files' && result) {
      onClose()
      return
    }
    setBusy(true)
    setError(null)
    try {
      if (mode === 'files') {
        const res = await api.importAccountFiles(files)
        onAdded()
        if (res.errors.length === 0 && res.duplicates.length === 0) {
          toast(`Добавлено ${res.added} ${plural(res.added, 'аккаунт', 'аккаунта', 'аккаунтов')} — они подключатся в течение пары минут`)
          onClose()
        } else {
          setResult(res)
        }
      } else if (mode === 'session') {
        await api.importSession({ session: session.trim(), ...connection() })
        done()
      } else if (step === 'phone') {
        const res = await api.loginStart({ phone, ...connection() })
        setLoginId(res.login_id)
        setStep('code')
      } else if (step === 'code' && loginId) {
        const res = await api.loginCode(loginId, code)
        if (res.status === 'password_needed') setStep('password')
        else done()
      } else if (step === 'password' && loginId) {
        await api.loginPassword(loginId, password)
        done()
      }
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const canSubmit =
    mode === 'files'
      ? files.length > 0
      : mode === 'session'
        ? session.trim().length > 10
        : step === 'phone'
          ? phone.replace(/\D/g, '').length >= 7
          : step === 'code'
            ? code.trim().length >= 3
            : password.length > 0

  const actionLabel =
    mode === 'files' ? (result ? 'Готово' : 'Загрузить') : mode === 'session' ? 'Добавить' : step === 'phone' ? 'Далее' : 'Войти'

  const footer =
    mode === 'session'
      ? 'Строка сессии Telethon или Pyrogram. Если сессия создана с другим api_id, укажите его в дополнительных настройках.'
      : step === 'phone'
        ? 'Telegram пришлёт код в приложение или по SMS. Вход идёт сразу через свободный прокси из пула.'
        : step === 'code'
          ? `Код отправлен на ${phone}. Он приходит в чат «Telegram» на другом устройстве.`
          : 'На аккаунте включена двухэтапная проверка — введите облачный пароль.'

  return (
    <Sheet
      open={open}
      onClose={onClose}
      title="Новый аккаунт"
      left={
        <button className="nav-button" onClick={onClose}>
          Отмена
        </button>
      }
      right={
        <button
          className="nav-button"
          style={{ fontWeight: 600 }}
          disabled={busy || (!canSubmit && !result)}
          onClick={submit}
        >
          {busy ? <Loader2 size={20} className="spinner" /> : actionLabel}
        </button>
      }
    >
      {step === 'phone' && (
        <Segmented
          id="account-mode"
          options={[
            { value: 'phone', label: 'Номер' },
            { value: 'session', label: 'Сессия' },
            { value: 'files', label: 'Файлы' },
          ]}
          value={mode}
          onChange={(m) => {
            setMode(m)
            setError(null)
          }}
        />
      )}

      <AnimatePresence mode="wait" initial={false}>
        <motion.div
          key={mode === 'phone' ? step : mode}
          initial={{ opacity: 0, x: 24 }}
          animate={{ opacity: 1, x: 0 }}
          exit={{ opacity: 0, x: -24 }}
          transition={spring}
        >
          {mode === 'files' ? (
            <FilesStep files={files} setFiles={setFiles} result={result} error={error} busy={busy} />
          ) : (
            <div className="section" style={{ marginTop: 12, marginBottom: 20 }}>
              <div className="group">
                {mode === 'session' ? (
                  <textarea
                    className="field mono"
                    placeholder="1BVtsOHwBu..."
                    spellCheck={false}
                    autoCapitalize="none"
                    autoCorrect="off"
                    value={session}
                    onChange={(e) => setSession(e.target.value)}
                    style={{ minHeight: 110 }}
                  />
                ) : step === 'phone' ? (
                  <input
                    className="field"
                    type="tel"
                    autoFocus
                    placeholder="+7 900 000-00-00"
                    value={phone}
                    onChange={(e) => setPhone(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && canSubmit && submit()}
                  />
                ) : step === 'code' ? (
                  <input
                    className="field"
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    autoFocus
                    placeholder="Код"
                    value={code}
                    onChange={(e) => setCode(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && canSubmit && submit()}
                    style={{ fontSize: 22, letterSpacing: 6 }}
                  />
                ) : (
                  <input
                    className="field"
                    type="password"
                    autoFocus
                    placeholder="Облачный пароль"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && canSubmit && submit()}
                  />
                )}
              </div>
              <div className="section-footer" style={error ? { color: 'var(--red)' } : undefined}>
                {error ?? footer}
              </div>
            </div>
          )}
        </motion.div>
      </AnimatePresence>

      {step === 'phone' && mode !== 'files' && (
        <>
          <button className="disclosure" onClick={() => setAdvanced((v) => !v)}>
            Дополнительно
            <motion.span animate={{ rotate: advanced ? 180 : 0 }} transition={spring} style={{ display: 'flex' }}>
              <ChevronDown size={16} />
            </motion.span>
          </button>
          <AnimatePresence initial={false}>
            {advanced && (
              <motion.div
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: 'auto', opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                transition={spring}
                style={{ overflow: 'hidden' }}
              >
                <div className="section">
                  <div className="group">
                    <input className="field" inputMode="numeric" placeholder="api_id" value={apiId} onChange={(e) => setApiId(e.target.value)} />
                    <input className="field" placeholder="api_hash" value={apiHash} onChange={(e) => setApiHash(e.target.value)} />
                  </div>
                  <div className="section-footer">
                    api_id и api_hash с my.telegram.org — если пусто, берутся общие из настроек сервера.
                  </div>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </>
      )}
    </Sheet>
  )
}

function formatSize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} КБ`
  return `${(bytes / 1024 / 1024).toFixed(1).replace('.', ',')} МБ`
}

function FilesStep({
  files,
  setFiles,
  result,
  error,
  busy,
}: {
  files: File[]
  setFiles: (f: File[]) => void
  result: ImportResult | null
  error: string | null
  busy: boolean
}) {
  const input = useRef<HTMLInputElement>(null)
  const mouse = useMouse()
  const [dragOver, setDragOver] = useState(false)

  const add = (list: FileList | null) => {
    if (!list) return
    const seen = new Set(files.map((f) => f.name + f.size))
    setFiles([...files, ...Array.from(list).filter((f) => !seen.has(f.name + f.size))])
  }

  const onDrop = (e: DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    add(e.dataTransfer.files)
  }

  if (result) {
    return (
      <div className="section" style={{ marginTop: 12 }}>
        <div className="group">
          <div className="cell">
            <div className="cell-main">Добавлено</div>
            <span className="cell-value" style={{ color: 'var(--green)', fontWeight: 600 }}>{result.added}</span>
          </div>
          {result.duplicates.length > 0 && (
            <div className="cell">
              <div className="cell-main">Уже были в списке</div>
              <span className="cell-value">{result.duplicates.length}</span>
            </div>
          )}
          {result.errors.length > 0 && (
            <div className="cell">
              <div className="cell-main">Не удалось прочитать</div>
              <span className="cell-value" style={{ color: 'var(--red)' }}>{result.errors.length}</span>
            </div>
          )}
        </div>
        {result.errors.length > 0 && (
          <div className="import-errors">
            {result.errors.map((e) => (
              <div key={e.file}>
                <b>{e.file}</b> — {e.error}
              </div>
            ))}
          </div>
        )}
        <div className="section-footer">Добавленные аккаунты подключатся через прокси из пула в течение пары минут.</div>
      </div>
    )
  }

  const total = files.reduce((n, f) => n + f.size, 0)
  return (
    <div className="section" style={{ marginTop: 12 }}>
      <input
        ref={input}
        type="file"
        multiple
        hidden
        // iOS greys out unknown types like .session when `accept` is set, so filter only on desktop
        accept={mouse ? '.session,.json,.zip,.txt' : undefined}
        onChange={(e) => {
          add(e.target.files)
          e.target.value = ''
        }}
      />
      <button
        className={`dropzone${dragOver ? ' over' : ''}`}
        onClick={() => input.current?.click()}
        onDragOver={(e) => {
          e.preventDefault()
          setDragOver(true)
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        disabled={busy}
      >
        <FileUp size={28} strokeWidth={1.7} />
        <span className="dropzone-title">{mouse ? 'Перетащите файлы сюда' : 'Выбрать файлы'}</span>
        <span className="dropzone-sub">{mouse ? 'или нажмите, чтобы выбрать' : '.session, .json, .zip или .txt'}</span>
      </button>

      {files.length > 0 && (
        <div className="group" style={{ marginTop: 12 }}>
          <div className="cell">
            <div className="cell-main">
              {files.length} {plural(files.length, 'файл', 'файла', 'файлов')} · {formatSize(total)}
            </div>
            <button className="nav-button" style={{ minWidth: 0, padding: 0, height: 'auto' }} onClick={() => setFiles([])} aria-label="Очистить">
              <X size={18} />
            </button>
          </div>
          <div className="cell">
            <div className="cell-sub" style={{ whiteSpace: 'normal' }}>
              {files.slice(0, 6).map((f) => f.name).join(', ')}
              {files.length > 6 && ` и ещё ${files.length - 6}`}
            </div>
          </div>
        </div>
      )}

      <div className="section-footer" style={error ? { color: 'var(--red)' } : undefined}>
        {error ??
          'Сессии Telethon и Pyrogram (.session) — вместе с .json от продавца, если он есть: из него возьмутся api_id и устройство. Можно загрузить .zip целиком или .txt со строками сессий. Формат tdata пока не поддерживается.'}
      </div>
    </div>
  )
}
