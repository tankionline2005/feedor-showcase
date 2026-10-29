import { AnimatePresence, motion } from 'framer-motion'
import { CreditCard, X } from 'lucide-react'
import { useMe } from '../hooks/useMe'
import { useTip } from '../lib/intros'
import { spring } from '../lib/motion'

/** Once, in the feed of an owner: plans and the balance came, and the AI is in the plan now — no OpenRouter to link. */
export function BillingNews() {
  const me = useMe()
  const [shown, close] = useTip('billing-news')
  const show = shown && me.role === 'user' && !me.ai_own
  return (
    <AnimatePresence initial={false}>
      {show && (
        <motion.div
          key="billing-news"
          className="covers-tip"
          initial={{ opacity: 0, y: -8 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, height: 0, marginBottom: 0, paddingTop: 0, paddingBottom: 0 }}
          transition={spring}
        >
          <div className="covers-tip-picture billing-news-icon">
            <CreditCard size={30} strokeWidth={1.7} />
          </div>
          <div className="covers-tip-text">
            <div className="covers-tip-title">Новое: тарифы и баланс</div>
            <div>
              ИИ теперь входит в тариф — подключать OpenRouter больше не нужно.{' '}
              {me.plan?.id === 'trial' ? 'Сейчас у проекта Free Trial.' : ''}
            </div>
            <button
              className="covers-tip-action"
              onClick={() => {
                close()
                location.hash = '/plan'
              }}
            >
              Посмотреть тарифы
            </button>
          </div>
          <button className="covers-tip-close" onClick={close} aria-label="Закрыть">
            <X size={16} />
          </button>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
