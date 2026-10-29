import { Loader2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../api'
import { Sheet } from '../components/Sheet'
import { useToast } from '../components/Toast'
import { useReloadMe } from '../hooks/useMe'
import { reloadProjects } from '../hooks/useProjects'
import { longDate } from '../lib/format'

/** "Ввести промокод": a code gives the current project a plan, extends the same one or upgrades to a better one. */
export function PromoSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const toast = useToast()
  const reloadMe = useReloadMe()

  useEffect(() => {
    if (open) {
      setCode('')
      setError(null)
    }
  }, [open])

  const submit = async () => {
    setBusy(true)
    setError(null)
    try {
      const { plan } = await api.redeemPromo(code)
      await Promise.all([reloadMe(), reloadProjects()]) // the switcher shows the plan, and whether a new project is allowed
      toast(`Тариф ${plan.title} ${plan.expires_at ? `до ${longDate(plan.expires_at)}` : '— бессрочно'}`)
      onClose()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Sheet
      open={open}
      onClose={onClose}
      title="Промокод"
      left={
        <button className="nav-button" onClick={onClose}>
          Отмена
        </button>
      }
      right={
        <button className="nav-button" style={{ fontWeight: 600 }} disabled={busy || !code.trim()} onClick={submit}>
          {busy ? <Loader2 size={20} className="spinner" /> : 'Активировать'}
        </button>
      }
    >
      <div className="section">
        <div className="group">
          <input
            className="field"
            autoFocus
            placeholder="FEED-XXXX-XXXX"
            autoCapitalize="characters"
            autoComplete="off"
            spellCheck={false}
            value={code}
            onChange={(e) => setCode(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && code.trim() && !busy && submit()}
            style={{ fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', letterSpacing: 1 }}
          />
        </div>
        <div className="section-footer" style={error ? { color: 'var(--red)' } : undefined}>
          {error ??
            'Промокод на тот же тариф продлевает его, на лучший — сразу заменяет текущий.'}
        </div>
      </div>
    </Sheet>
  )
}
