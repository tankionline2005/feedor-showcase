import { useEffect } from 'react'
import { copyText } from '../lib/clipboard'
import { Alert } from './Alert'
import { useToast } from './Toast'

export interface ErrorInfo {
  /** what happened, for people */
  text: string
  /** what the service said itself: shown small, copied whole */
  detail?: string | null
}

/** An alert for a failure worth reading: «Скопировать текст ошибки» and «Понятно». */
export function ErrorAlert({
  error,
  title = 'Не удалось получить ответ',
  onClose,
}: {
  error: ErrorInfo | null
  title?: string
  onClose: () => void
}) {
  const toast = useToast()

  // Enter agrees too, as in a system alert
  useEffect(() => {
    if (!error) return
    const onKey = (e: KeyboardEvent) => e.key === 'Enter' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [error, onClose])

  const copy = async () => {
    if (!error) return
    const all = error.detail && error.detail !== error.text ? `${error.text}\n\n${error.detail}` : error.text
    toast((await copyText(all)) ? 'Текст ошибки скопирован' : all)
  }

  return (
    <Alert
      open={!!error}
      title={title}
      text={error?.text}
      buttons={[
        { label: 'Скопировать текст ошибки', onClick: copy },
        { label: 'Понятно', onClick: onClose, strong: true },
      ]}
      onClose={onClose}
    >
      {error?.detail && error.detail !== error.text && <div className="alert-detail">{error.detail}</div>}
    </Alert>
  )
}
