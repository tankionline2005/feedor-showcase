import { motion } from 'framer-motion'
import { Ticket } from 'lucide-react'
import { useState } from 'react'
import { useMe } from '../hooks/useMe'
import { spring } from '../lib/motion'
import { PromoSheet } from '../screens/PromoSheet'

/** A user without a plan (or with an expired one): no posts in the feed and the top. */
export function useNoPlan() {
  const me = useMe()
  return me.role === 'user' && !me.plan
}

export function NoPlan() {
  const [promo, setPromo] = useState(false)
  return (
    <motion.div className="empty" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={spring}>
      <div className="empty-icon">
        <Ticket size={32} strokeWidth={1.6} />
      </div>
      <div className="empty-title">Нет тарифа</div>
      <div>Посты появятся здесь, когда у проекта будет тариф</div>
      <button className="button" style={{ width: 'auto', padding: '0 24px', marginTop: 16 }} onClick={() => (location.hash = '/plan')}>
        Выбрать тариф
      </button>
      <button className="link-inline" style={{ marginTop: 12 }} onClick={() => setPromo(true)}>
        Ввести промокод
      </button>
      <PromoSheet open={promo} onClose={() => setPromo(false)} />
    </motion.div>
  )
}
