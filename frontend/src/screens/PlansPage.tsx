import { motion } from 'framer-motion'
import { ChevronLeft, Gift, Loader2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, type Plan } from '../api'
import { LegalLinks, LegalSheet } from '../components/LegalSheet'
import { PlanCard } from '../components/PlanCard'
import type { LegalDoc } from '../lib/legal'
import { spring } from '../lib/motion'

/** «Посмотреть тарифы» on the login page: every plan and what it costs, before signing in. */
export function PlansPage({ onBack }: { onBack: () => void }) {
  const [plans, setPlans] = useState<Plan[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [legal, setLegal] = useState<LegalDoc | null>(null)
  useEffect(() => {
    api.plans().then(
      (r) => setPlans(r.items),
      (e) => setError((e as Error).message),
    )
  }, [])
  const trial = plans?.find((p) => p.id === 'trial')
  const paid = plans?.filter((p) => p.buyable) ?? []

  return (
    <motion.div className="plans-page" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={spring}>
      <div className="plans-page-head">
        <motion.button className="nav-button" onClick={onBack} whileTap={{ opacity: 0.4 }}>
          <ChevronLeft size={30} strokeWidth={2.2} style={{ marginRight: -4 }} />
          Вход
        </motion.button>
      </div>
      <h1>Тарифы</h1>
      <p className="plans-page-lead">
        Тариф — на проект: свои каналы, сотрудники и ИИ. Оплата с баланса аккаунта, на 30 дней.
      </p>
      {error && <div className="error-banner">{error}</div>}
      {!plans && !error && (
        <div className="loader">
          <Loader2 size={22} className="spinner" />
        </div>
      )}
      {trial && (
        <div className="plans-trial">
          <Gift size={26} />
          <div>
            <b>30 дней бесплатно</b>
            При регистрации первый проект получает Free Trial: до {trial.channels} каналов и ИИ на пробу.
          </div>
        </div>
      )}
      {plans && (
        <div className="tariffs wide">
          {paid.map((plan, i) => (
            <motion.div
              key={plan.id}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ ...spring, delay: 0.04 * i }}
            >
              <PlanCard plan={plan} all={plans} />
            </motion.div>
          ))}
        </div>
      )}
      {plans && (
        <p className="plans-note">
          ИИ сверх включённого в тариф оплачивается с баланса по цене запросов — например, пост от ИИ стоит меньше
          цента. Докупку можно выключить или ограничить в настройках тарифа. Переход на тариф выше — сразу,
          неиспользованные дни текущего возвращаются на баланс.
        </p>
      )}
      <footer className="login-footer">
        <LegalLinks onOpen={setLegal} />
      </footer>
      <LegalSheet doc={legal} onClose={() => setLegal(null)} />
    </motion.div>
  )
}
