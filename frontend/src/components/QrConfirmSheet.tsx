import { Loader2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { api, type QrAsking } from '../api'
import { siteName } from '../lib/site'
import { DeviceIcon } from './DeviceIcon'
import { Sheet } from './Sheet'
import { useToast } from './Toast'

/**
 * A login page's QR code scanned: which browser asks to sign in, and the person confirms it. Closed without it,
 * the page hears «Вход отменён».
 */
export function QrConfirmSheet({
  token,
  onClose,
  onDone,
  onRescan,
}: {
  token: string | null
  onClose: () => void
  onDone: () => void
  /** the code was old: scan the page's new one */
  onRescan?: () => void
}) {
  const toast = useToast()
  const [asking, setAsking] = useState<QrAsking | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const answered = useRef(false)

  useEffect(() => {
    if (!token) return
    setAsking(null)
    setError(null)
    answered.current = false
    api.qrScanned(token).then(setAsking, (e) => setError((e as Error).message))
  }, [token])

  const close = () => {
    if (token && asking && !answered.current) api.qrDecline(token).catch(() => {})
    answered.current = true
    onClose()
  }

  const confirm = async () => {
    if (!token) return
    setBusy(true)
    try {
      await api.qrConfirm(token)
      answered.current = true
      toast(`Вход подтверждён: ${asking?.name ?? 'устройство'}`)
      onDone()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Sheet
      open={token !== null}
      onClose={close}
      title="Вход по QR-коду"
      left={
        <button className="nav-button" onClick={close}>
          Отмена
        </button>
      }
    >
      <div className="qr-confirm">
        {error ? (
          <>
            <div className="qr-confirm-text" style={{ color: 'var(--red)' }}>
              {error}
            </div>
            {onRescan && (
              <button className="button" onClick={onRescan}>
                Сканировать снова
              </button>
            )}
          </>
        ) : !asking ? (
          <div className="qr-confirm-loading">
            <Loader2 size={24} className="spinner" />
          </div>
        ) : (
          <>
            <DeviceIcon platform={asking.platform} form={asking.form} size={64} />
            <div className="qr-confirm-name">{asking.name}</div>
            <div className="qr-confirm-sub">{asking.app}</div>
            <div className="qr-confirm-sub">{[asking.place, asking.ip].filter(Boolean).join(' · ')}</div>
            <div className="qr-confirm-text">
              Войти в ваш аккаунт {siteName()} на этом устройстве? Подтверждайте, только если сами открыли там сайт:
              тот, у кого это устройство, получит доступ к аккаунту.
            </div>
            <button className="button" onClick={confirm} disabled={busy}>
              {busy ? <Loader2 size={20} className="spinner" style={{ color: '#fff' }} /> : 'Подтвердить вход'}
            </button>
          </>
        )}
      </div>
    </Sheet>
  )
}
