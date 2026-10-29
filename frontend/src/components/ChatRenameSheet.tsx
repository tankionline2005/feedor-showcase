import { Loader2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Sheet } from './Sheet'

/** «Название чата»: renaming a chat with the AI, from the chat itself or from the list of chats. */
export function ChatRenameSheet({
  open,
  title,
  onClose,
  onSave,
}: {
  open: boolean
  title: string
  onClose: () => void
  onSave: (title: string) => Promise<void>
}) {
  const [value, setValue] = useState(title)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    if (open) setValue(title)
  }, [open, title])
  const valid = value.trim().length > 0 && value.trim() !== title

  const save = async () => {
    if (!valid || busy) return
    setBusy(true)
    try {
      await onSave(value.trim())
    } finally {
      setBusy(false)
    }
  }

  return (
    <Sheet
      open={open}
      onClose={onClose}
      title="Название чата"
      left={
        <button className="nav-button" onClick={onClose}>
          Отмена
        </button>
      }
      right={
        <button className="nav-button" style={{ fontWeight: 600 }} disabled={!valid || busy} onClick={save}>
          {busy ? <Loader2 size={18} className="spinner" /> : 'Готово'}
        </button>
      }
    >
      <form
        onSubmit={(e) => {
          e.preventDefault()
          save()
        }}
      >
        <div className="section">
          <div className="group">
            <input
              className="field"
              aria-label="Название чата"
              maxLength={80}
              autoFocus
              value={value}
              onChange={(e) => setValue(e.target.value)}
            />
          </div>
        </div>
      </form>
    </Sheet>
  )
}
