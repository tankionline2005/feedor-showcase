import { AnimatePresence, motion } from 'framer-motion'
import {
  BookOpen,
  Check,
  ChevronDown,
  ChevronLeft,
  Copy,
  FileText,
  KeyRound,
  Link2,
  Loader2,
  RotateCcw,
} from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { api, type ApiRefMethod, type ApiReference } from '../api'
import { Page } from '../components/Page'
import { ActionSheet } from '../components/Sheet'
import { useToast } from '../components/Toast'
import { useIsAdmin, useMe } from '../hooks/useMe'
import { MANAGE_INTRO, MY_CHANNEL, curlFor, docsFor, docsPlainText, fieldText, type ApiMethod } from '../lib/apiDocs'
import { copyText } from '../lib/clipboard'
import { spring } from '../lib/motion'
import { openIntro, useFirstVisit } from '../lib/intros'

export function ApiScreen({ onBack }: { onBack: () => void }) {
  useFirstVisit('api')
  const [key, setKey] = useState<string | null>(null)
  // the key hides under a spoiler, as in Telegram: a tap shows it (it stays shown while the page is open)
  const [shown, setShown] = useState(false)
  const [rateLimit, setRateLimit] = useState(120)
  const [error, setError] = useState<string | null>(null)
  const [docsOpen, setDocsOpen] = useState(false)
  // the site's methods this key may call, from the server: only those the person's rights allow
  const [reference, setReference] = useState<ApiReference | null>(null)
  const [confirm, setConfirm] = useState(false)
  const [resetting, setResetting] = useState(false)
  const toast = useToast()
  const origin = window.location.origin
  const base = `${origin}/api/v1`
  const admin = useIsAdmin()
  const me = useMe()
  const { plan } = me

  const load = useCallback(async () => {
    try {
      const res = await api.apiKey()
      setKey(res.key)
      setRateLimit(res.rate_limit)
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
    api.apiReference().then(setReference, () => {})
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const copy = async (text: string, what: string) => {
    toast((await copyText(text)) ? `${what} скопирован` : 'Не удалось скопировать — выделите текст вручную')
  }

  const copyRaw = async () => {
    const ok = await copyText(docsPlainText(origin, rateLimit, admin, reference))
    toast(ok ? 'Документация скопирована — ключ в ней заменён на <API_KEY>' : 'Не удалось скопировать')
  }

  const reset = async () => {
    setResetting(true)
    try {
      const res = await api.resetApiKey()
      setKey(res.key)
      toast('Новый ключ создан, старый больше не работает')
    } catch (e) {
      toast((e as Error).message)
    } finally {
      setResetting(false)
    }
  }

  return (
    <Page
      onHelp={() => openIntro('api')}
      title="API"
      onRefresh={load}
      left={
        <motion.button className="nav-button" onClick={onBack} whileTap={{ opacity: 0.4 }} style={{ paddingLeft: 0 }}>
          <ChevronLeft size={30} strokeWidth={2.2} style={{ marginRight: -4 }} />
          Настройки
        </motion.button>
      }
    >
      {error && <div className="error-banner">{error}</div>}
      {!admin && !plan?.api && (
        <div className="error-banner">
          {plan
            ? `Тариф ${plan.title} не включает API: запросы с этим ключом получат ошибку no_api. API есть во всех тарифах, начиная со Starter, — ключ заработает сразу.`
            : 'Без тарифа API не работает: запросы с этим ключом получат ошибку no_api. Выберите тариф в настройках — ключ заработает сразу.'}
        </div>
      )}

      <div className="section-header" style={{ marginLeft: 16 }}>
        Ключ
      </div>
      <div className="section">
        <div className="group">
          <div
            className="cell"
            style={{ ['--inset' as string]: '57px', alignItems: 'flex-start', paddingTop: 12, paddingBottom: 12 }}
          >
            <div className="cell-icon" style={{ background: 'var(--orange)' }}>
              <KeyRound size={17} />
            </div>
            <div className="cell-main api-key">
              {key ? (
                <span
                  className={`key-spoiler${shown ? ' shown' : ''}`}
                  role={shown ? undefined : 'button'}
                  tabIndex={shown ? undefined : 0}
                  aria-label={shown ? undefined : 'Показать ключ'}
                  onClick={() => setShown(true)}
                  onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && setShown(true)}
                >
                  {key}
                </span>
              ) : (
                <Loader2 size={18} className="spinner" />
              )}
            </div>
          </div>
          <button
            className="cell accent"
            style={{ ['--inset' as string]: '57px' }}
            disabled={!key}
            onClick={() => key && copy(key, 'Ключ')}
          >
            <div className="cell-icon" style={{ background: 'transparent', color: 'var(--accent)' }}>
              <Copy size={18} />
            </div>
            Скопировать ключ
          </button>
          <button
            className="cell"
            style={{ ['--inset' as string]: '57px', color: 'var(--red)' }}
            disabled={!key || resetting}
            onClick={() => setConfirm(true)}
          >
            <div className="cell-icon" style={{ background: 'transparent', color: 'var(--red)' }}>
              {resetting ? <Loader2 size={18} className="spinner" /> : <RotateCcw size={18} />}
            </div>
            Сбросить ключ
          </button>
        </div>
        <div className="section-footer">
          {admin
            ? 'Ключ администратора: через него можно делать всё, что вы делаете на сайте, — в том числе с аккаунтами, прокси и настройками сервиса.'
            : me.role === 'member'
              ? 'Ваш личный ключ: через него можно делать всё, что владелец проекта разрешил вам на сайте, — не больше.'
              : 'Ключ проекта: через него можно делать всё, что вы делаете на сайте, — читать данные и управлять проектом.'}{' '}
          Передавайте его в заголовке <code>X-API-Key</code> и храните в секрете.
        </div>
      </div>

      <div className="section-header" style={{ marginLeft: 16 }}>
        Адрес
      </div>
      <div className="section">
        <div className="group">
          <button className="cell" style={{ ['--inset' as string]: '57px' }} onClick={() => copy(origin, 'Адрес')}>
            <div className="cell-icon" style={{ background: 'var(--indigo)' }}>
              <Link2 size={17} />
            </div>
            <div className="cell-main api-key">{origin}</div>
            <Copy size={17} color="var(--text-2)" />
          </button>
        </div>
        <div className="section-footer">
          Управление — методы <code>/api/…</code>, данные для аналитики — <code>/api/v1/…</code>. Не больше {rateLimit}{' '}
          запросов в минуту на все вместе. Можно вызывать и из браузера (CORS разрешён).
        </div>
      </div>

      <div className="section">
        <div className="group">
          <button
            className="cell"
            style={{ ['--inset' as string]: '57px' }}
            onClick={() => setDocsOpen((v) => !v)}
            aria-expanded={docsOpen}
          >
            <div className="cell-icon" style={{ background: 'var(--green)' }}>
              <BookOpen size={17} />
            </div>
            <div className="cell-main">Документация</div>
            <motion.span animate={{ rotate: docsOpen ? 180 : 0 }} transition={spring} style={{ display: 'flex' }}>
              <ChevronDown size={18} color="var(--text-2)" />
            </motion.span>
          </button>
        </div>
      </div>

      <AnimatePresence initial={false}>
        {docsOpen && (
          <motion.div
            key="docs"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={spring}
            style={{ overflow: 'hidden' }}
          >
            <Docs
              origin={origin}
              base={base}
              apiKey={(shown && key) || 'ВАШ_КЛЮЧ'}
              rateLimit={rateLimit}
              admin={admin}
              reference={reference}
              onCopy={copy}
            />
          </motion.div>
        )}
      </AnimatePresence>

      <motion.div className="section" layout="position" transition={spring}>
        <div className="group">
          <button className="cell accent" style={{ ['--inset' as string]: '57px' }} onClick={copyRaw}>
            <div className="cell-icon" style={{ background: 'transparent', color: 'var(--accent)' }}>
              <FileText size={18} />
            </div>
            Скопировать сырой текст
          </button>
        </div>
        <div className="section-footer">
          Копирует всю документацию в виде чистого текста без форматирования, удобного для LLM.
        </div>
      </motion.div>

      <ActionSheet
        open={confirm}
        onClose={() => setConfirm(false)}
        title="Сбросить ключ?"
        message="Старый ключ сразу перестанет работать — внешний проект нужно будет перенастроить на новый."
        actions={[{ label: 'Сбросить ключ', destructive: true, onClick: reset }]}
      />
    </Page>
  )
}

function Docs({
  origin,
  base,
  apiKey,
  rateLimit,
  admin,
  reference,
  onCopy,
}: {
  origin: string
  base: string
  apiKey: string
  rateLimit: number
  admin: boolean
  reference: ApiReference | null
  onCopy: (text: string, what: string) => void
}) {
  const { fields, methods, errors } = docsFor(admin)
  return (
    <div className="docs">
      <section>
        <h3>Как обращаться</h3>
        <p>
          Ключ передаётся в заголовке <code>X-API-Key</code> (или <code>Authorization: Bearer …</code>). У API две
          части:
        </p>
        <ul className="docs-steps">
          <li>
            <b>Управление</b> — <code>/api/…</code>: всё, что можно делать на сайте, с теми же правами.
          </li>
          <li>
            <b>Данные</b> — <code>/api/v1/…</code>: посты, каналы и темы в удобном для аналитики виде, только чтение.
          </li>
        </ul>
        <CodeBlock code={`curl -H "X-API-Key: ${apiKey}" "${origin}/api/auth/me"`} onCopy={onCopy} />
        <p>
          Этот запрос скажет, чей это ключ и что ему можно. Не больше {rateLimit} запросов в минуту на все методы вместе.
        </p>
      </section>

      <section>
        <h3>Управление</h3>
        {MANAGE_INTRO.map((text) => (
          <p key={text}>{text}</p>
        ))}
      </section>
      {reference ? (
        reference.groups.map((g) => (
          <ManageGroup key={g.title} title={g.title} methods={g.methods} origin={origin} apiKey={apiKey} onCopy={onCopy} />
        ))
      ) : (
        <section className="docs-loading">
          <Loader2 size={18} className="spinner" />
        </section>
      )}

      <section>
        <h3>Данные</h3>
        <p>
          Базовый адрес — <code>{base}</code>. Все методы — <b>GET</b>, ответы в JSON, время — UTC в формате ISO 8601.
          Посты хранятся ограниченный срок, потом удаляются и из API.
        </p>
        <CodeBlock code={`curl -H "X-API-Key: ${apiKey}" "${base}/stats"`} onCopy={onCopy} />
      </section>

      {!admin && (
        <section>
          <h3>Канал проекта</h3>
          {MY_CHANNEL.intro.map((text) => (
            <p key={text}>{text}</p>
          ))}
          <FieldList rows={MY_CHANNEL.fields} />
          <p>
            <b>{MY_CHANNEL.scenarioTitle}</b>
          </p>
          <ol className="docs-steps">
            {MY_CHANNEL.scenario.map((step) => (
              <li key={step}>{step}</li>
            ))}
          </ol>
          <p>{MY_CHANNEL.forLlm}</p>
        </section>
      )}

      <section>
        <h3>Ошибки данных</h3>
        <p>
          Ответ с кодом 4xx и телом <code>{'{"error": {"code": "…", "message": "…"}}'}</code>.
        </p>
        <FieldList rows={errors} />
      </section>

      <section>
        <h3>Поля поста</h3>
        <FieldList rows={fields} />
      </section>

      {methods.map((m) => (
        <MethodDoc key={m.path} method={m} base={base} apiKey={apiKey} onCopy={onCopy} />
      ))}
    </div>
  )
}

function MethodDoc({
  method,
  base,
  apiKey,
  onCopy,
}: {
  method: ApiMethod
  base: string
  apiKey: string
  onCopy: (text: string, what: string) => void
}) {
  return (
    <section className="docs-method">
      <div className="docs-endpoint">
        <span className="method-badge">GET</span>
        <code>{method.path}</code>
      </div>
      <h4>{method.title}</h4>
      <p>{method.description}</p>
      {method.params && <FieldList rows={method.params} />}
      {method.example && (
        <>
          <div className="docs-label">Пример</div>
          <CodeBlock code={`curl -H "X-API-Key: ${apiKey}" "${base}${method.example}"`} onCopy={onCopy} />
        </>
      )}
      {method.response && (
        <>
          <div className="docs-label">Ответ</div>
          <CodeBlock code={method.response} onCopy={onCopy} />
        </>
      )}
    </section>
  )
}

/** A section of the site's methods, folded: a person sees the sections first, opens the one they need. */
function ManageGroup({
  title,
  methods,
  origin,
  apiKey,
  onCopy,
}: {
  title: string
  methods: ApiRefMethod[]
  origin: string
  apiKey: string
  onCopy: (text: string, what: string) => void
}) {
  const [open, setOpen] = useState(false)
  return (
    <section className="docs-group">
      <button className="docs-group-head" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <h3>{title}</h3>
        <span className="docs-group-count">{methods.length}</span>
        <motion.span animate={{ rotate: open ? 180 : 0 }} transition={spring} style={{ display: 'flex' }}>
          <ChevronDown size={18} color="var(--text-2)" />
        </motion.span>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={spring}
            style={{ overflow: 'hidden' }}
          >
            {methods.map((m) => (
              <div key={`${m.method} ${m.path}`} className="docs-manage">
                <div className="docs-endpoint">
                  <span className={`method-badge ${m.method.toLowerCase()}`}>{m.method}</span>
                  <code>{m.path}</code>
                </div>
                <p>{m.text}</p>
                {m.params.length > 0 && (
                  <>
                    <div className="docs-label">Параметры</div>
                    <FieldList rows={m.params.map((p) => [p.name, fieldText(p)])} />
                  </>
                )}
                {m.body && m.body.length > 0 && (
                  <>
                    <div className="docs-label">{m.body_kind === 'form-data' ? 'Форма (form-data)' : 'Тело (JSON)'}</div>
                    <FieldList rows={m.body.map((f) => [f.name, fieldText(f)])} />
                  </>
                )}
                <CodeBlock code={curlFor(origin, apiKey, m)} onCopy={onCopy} />
              </div>
            ))}
          </motion.div>
        )}
      </AnimatePresence>
    </section>
  )
}

function FieldList({ rows }: { rows: [string, string][] }) {
  return (
    <dl className="docs-fields">
      {rows.map(([name, text]) => (
        <div key={name}>
          <dt>
            <code>{name}</code>
          </dt>
          <dd>{text}</dd>
        </div>
      ))}
    </dl>
  )
}

function CodeBlock({ code, onCopy }: { code: string; onCopy: (text: string, what: string) => void }) {
  const [copied, setCopied] = useState(false)
  return (
    <div className="code-block">
      <pre>{code}</pre>
      <button
        className="code-copy"
        aria-label="Скопировать"
        title="Скопировать"
        onClick={async () => {
          onCopy(code, 'Пример')
          setCopied(true)
          window.setTimeout(() => setCopied(false), 1500)
        }}
      >
        {copied ? <Check size={15} /> : <Copy size={15} />}
      </button>
    </div>
  )
}
