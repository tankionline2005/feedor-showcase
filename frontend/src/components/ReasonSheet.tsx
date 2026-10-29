import { Loader2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Sheet } from './Sheet'
import { useToast } from './Toast'

/**
 * A decision that teaches the AI — hiding a topic, hiding or showing a post of the smart feed — taken
 * only with why: the person's reason goes to the AI. Quick reasons fill the field; it may be edited.
 */
export function ReasonSheet({
  open,
  title,
  question,
  placeholder,
  quick,
  footer,
  action,
  destructive,
  onClose,
  onSave,
}: {
  open: boolean
  title: string
  question: string
  placeholder: string
  quick: string[]
  footer: string
  action: string
  destructive?: boolean
  onClose: () => void
  onSave: (reason: string) => Promise<void>
}) {
  const toast = useToast()
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    if (open) setReason('')
  }, [open])
  const valid = reason.trim().length >= 3

  const save = async () => {
    if (!valid || busy) return
    setBusy(true)
    try {
      await onSave(reason.trim())
    } catch (e) {
      toast((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Sheet
      open={open}
      onClose={onClose}
      title={title}
      left={
        <button className="nav-button" onClick={onClose}>
          Отмена
        </button>
      }
      right={
        <button
          className={`nav-button${destructive ? ' destructive-text' : ''}`}
          style={{ fontWeight: 600 }}
          disabled={!valid || busy}
          onClick={save}
        >
          {busy ? <Loader2 size={18} className="spinner" /> : action}
        </button>
      }
    >
      <div className="section">
        <div className="section-header">{question}</div>
        <div className="group">
          <textarea
            className="field prompt-field"
            rows={5}
            maxLength={500}
            autoFocus
            aria-label={question}
            placeholder={placeholder}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        </div>
        <div className="hide-reasons">
          {quick.map((r) => (
            <button
              key={r}
              className="chat-hint"
              onClick={() => setReason((v) => (v.trim() ? `${v.trim()}. ${r}` : r))}
            >
              {r}
            </button>
          ))}
        </div>
        <div className="section-footer">{footer}</div>
      </div>
    </Sheet>
  )
}
