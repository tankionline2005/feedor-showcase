import { Loader2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../api'
import { ActionSheet, Sheet } from '../components/Sheet'
import { useToast } from '../components/Toast'
import { useMe } from '../hooks/useMe'
import { plural } from '../lib/format'

interface Props {
  open: boolean
  onClose: () => void
  /** channels were added or removed: the list loads again */
  onChanged: () => void
  /** channels in the list now: a user's plan limits them */
  count: number
}

const channels = (n: number) => `${n} ${plural(n, 'канал', 'канала', 'каналов')}`

/** «Новые каналы» by links — and, switched by the small link under it, «Удалить каналы» by links (a project's). */
export function AddChannelsSheet({ open, onClose, onChanged, count }: Props) {
  const { plan, role } = useMe()
  const admin = role === 'admin'
  const [removing, setRemoving] = useState(false)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // «Перестать отслеживать 12 каналов?»: how many the links found, asked before they go
  const [asking, setAsking] = useState<number | null>(null)
  const toast = useToast()

  useEffect(() => {
    if (open) {
      setError(null)
      setRemoving(false)
    }
  }, [open])

  const add = async () => {
    setBusy(true)
    setError(null)
    try {
      const res = await api.addChannels(text)
      const n = res.added.length
      const notes = []
      if (res.duplicates.length) notes.push(`уже в списке: ${res.duplicates.length}`)
      if (res.invalid.length) notes.push(`не распознано: ${res.invalid.join(', ')}`)
      if (res.over_limit.length)
        notes.push(`не хватило места по тарифу (до ${res.limit} каналов): ${res.over_limit.length}`)
      if (n === 0) {
        setError(notes.length ? `Ничего не добавлено — ${notes.join('; ')}` : 'Ничего не добавлено')
        return
      }
      toast(`Добавлено ${channels(n)}${notes.length ? ` (${notes.join('; ')})` : ''}`)
      setText('')
      onChanged()
      onClose()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  // what the links did not find, said the same way before and after
  const notesOf = (res: { not_found: string[]; invalid: string[]; own: string[] }) => {
    const notes = []
    if (res.not_found.length) notes.push(`нет в списке: ${res.not_found.length}`)
    if (res.invalid.length) notes.push(`не распознано: ${res.invalid.join(', ')}`)
    if (res.own.length) notes.push('канал проекта меняется в настройках')
    return notes
  }

  // first only how many the links find; they go once the person says so
  const check = async () => {
    setBusy(true)
    setError(null)
    try {
      const res = await api.removeChannels(text, true)
      if (res.removed === 0) {
        const notes = notesOf(res)
        setError(notes.length ? `Ничего не удалено — ${notes.join('; ')}` : 'Ничего не удалено')
        return
      }
      setAsking(res.removed)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const remove = async () => {
    setBusy(true)
    try {
      const res = await api.removeChannels(text)
      const notes = notesOf(res)
      toast(`Удалено ${channels(res.removed)}${notes.length ? ` (${notes.join('; ')})` : ''}`)
      setText('')
      onChanged()
      onClose()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const hint = admin
    ? 'Ссылки, @username или приглашения — каждый с новой строки или через запятую. Аккаунты вступают в каналы по очереди, чтобы не упереться в лимиты Telegram.'
    : removing
      ? 'Ссылки, @username или приглашения каналов, которые больше не нужно отслеживать, — каждый с новой строки или через запятую.'
      : !plan
        ? 'Нет действующего тарифа — введите промокод в настройках, чтобы добавлять каналы.'
        : `Ссылки, @username или приглашения — каждый с новой строки или через запятую. ${
            plan.channels === null
              ? 'По тарифу каналов можно сколько угодно.'
              : `По тарифу можно до ${plan.channels} ${plural(plan.channels, 'канала', 'каналов', 'каналов')}, сейчас у вас ${count}.`
          } Новые каналы подключаются по очереди.`

  return (
    <>
      <Sheet
        open={open}
        onClose={onClose}
        title={removing ? 'Удалить каналы' : 'Новые каналы'}
        left={
          <button className="nav-button" onClick={onClose}>
            Отмена
          </button>
        }
        right={
          <button
            className="nav-button"
            style={{ fontWeight: 600, ...(removing ? { color: 'var(--red)' } : null) }}
            disabled={busy || !text.trim()}
            onClick={removing ? check : add}
          >
            {busy ? <Loader2 size={20} className="spinner" /> : removing ? 'Удалить' : 'Добавить'}
          </button>
        }
      >
        <div className="section">
          <div className="group">
            <textarea
              className="field"
              autoFocus
              placeholder={'@channel\nhttps://t.me/another\nhttps://t.me/+invite'}
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
          </div>
          <div className="section-footer" style={error ? { color: 'var(--red)' } : undefined}>
            {error ?? hint}
          </div>
          {/* the admin removes channels from the whole system, one by one only */}
          {!admin && (
            <div className="channels-mode">
              <button
                className="link-inline"
                onClick={() => {
                  setRemoving(!removing)
                  setError(null)
                }}
              >
                {removing ? 'Добавить списком' : 'Удалить списком'}
              </button>
            </div>
          )}
        </div>
      </Sheet>
      <ActionSheet
        open={asking !== null}
        onClose={() => setAsking(null)}
        message={asking !== null ? `Перестать отслеживать ${channels(asking)}?` : undefined}
        actions={[{ label: `Удалить ${channels(asking ?? 0)}`, destructive: true, onClick: remove }]}
      />
    </>
  )
}
