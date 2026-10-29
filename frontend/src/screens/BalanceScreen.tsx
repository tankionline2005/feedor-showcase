import { motion } from 'framer-motion'
import { ChevronLeft, Loader2 } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { api, type BillingOp } from '../api'
import { Page } from '../components/Page'
import { useToast } from '../components/Toast'
import { longDate } from '../lib/format'
import { cents } from '../lib/money'

/** Settings → «Баланс»: the account's money (one for all projects), «Пополнить», and every move of it. */
export function BalanceScreen({ onBack, backLabel = 'Настройки' }: { onBack: () => void; backLabel?: string }) {
  const [balance, setBalance] = useState<number | null>(null)
  const [ops, setOps] = useState<BillingOp[] | null>(null)
  const [more, setMore] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const toast = useToast()

  const reload = useCallback(async () => {
    try {
      const [state, history] = await Promise.all([api.billing(), api.billingHistory()])
      setBalance(state.balance)
      setOps(history.items)
      setMore(history.more)
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])
  useEffect(() => {
    reload()
  }, [reload])

  const loadMore = async () => {
    if (!ops?.length) return
    setLoading(true)
    try {
      const next = await api.billingHistory(ops[ops.length - 1].id)
      setOps([...ops, ...next.items])
      setMore(next.more)
    } catch (e) {
      toast((e as Error).message)
    } finally {
      setLoading(false)
    }
  }
  const topup = async () => {
    try {
      await api.topup()
    } catch (e) {
      toast((e as Error).message)
    }
  }

  return (
    <Page
      title="Баланс"
      onRefresh={reload}
      left={
        <motion.button className="nav-button" onClick={onBack} whileTap={{ opacity: 0.4 }} style={{ paddingLeft: 0 }}>
          <ChevronLeft size={30} strokeWidth={2.2} style={{ marginRight: -4 }} />
          {backLabel}
        </motion.button>
      }
    >
      {error && <div className="error-banner">{error}</div>}
      <div className="money-card">
        <div className="money-label">На балансе</div>
        <div className="money-value">{balance === null ? '…' : cents(balance)}</div>
        <motion.button className="button" whileTap={{ scale: 0.98 }} onClick={topup}>
          Пополнить
        </motion.button>
      </div>
      <div className="section-footer" style={{ margin: '8px 16px 28px' }}>
        Один баланс на все ваши проекты: с него оплачиваются тарифы и ИИ сверх включённого в тариф.
      </div>

      <div className="section">
        <div className="section-header">История операций</div>
        {!ops && !error && (
          <div className="loader">
            <Loader2 size={22} className="spinner" />
          </div>
        )}
        {ops && ops.length === 0 && <div className="group"><div className="cell"><div className="cell-main" style={{ color: 'var(--text-2)' }}>Пока ничего не было</div></div></div>}
        {ops && ops.length > 0 && (
          <div className="group">
            {ops.map((op) => (
              <div key={op.id} className="cell">
                <div className="cell-main">
                  <div className="cell-title">{op.title}</div>
                  <div className="cell-sub">
                    {[op.kind === 'ai' && op.day ? longDate(op.day) : longDate(op.at), op.project, op.note]
                      .filter(Boolean)
                      .join(' · ')}
                  </div>
                </div>
                <span className={`op-amount${op.amount > 0 ? ' plus' : ''}`}>{cents(op.amount, true)}</span>
              </div>
            ))}
          </div>
        )}
        {more && (
          <div className="section" style={{ marginTop: 12 }}>
            <button className="button secondary" disabled={loading} onClick={loadMore}>
              {loading ? <Loader2 size={20} className="spinner" /> : 'Показать ещё'}
            </button>
          </div>
        )}
      </div>
    </Page>
  )
}
