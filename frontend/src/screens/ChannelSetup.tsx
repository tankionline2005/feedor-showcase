import { motion } from 'framer-motion'
import { Megaphone } from 'lucide-react'
import { useEffect } from 'react'
import { ChannelChoice } from '../components/ProjectSwitcher'
import { spring } from '../lib/motion'

/**
 * A new owner's first step, before anything else: the channel the project works for. Everything on the site serves
 * it — the topics are picked for it, the AI writes in its style and publishes to it — so there is no going on
 * without one. Shown while none of the owner's projects has its channel (a project made later may go without).
 */
export function ChannelSetup({ projectId, onLogout }: { projectId: number; onLogout: () => void }) {
  // the channel chosen, the site opens on «Каналы»: the easiest place to start — add the channels to watch
  useEffect(() => history.replaceState(null, '', '#/channels'), [])
  return (
    <motion.div
      className="login channel-setup"
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={spring}
    >
      <motion.div
        className="channel-setup-icon"
        initial={{ scale: 0.6, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        transition={{ ...spring, delay: 0.08 }}
      >
        <Megaphone size={34} strokeWidth={2} />
      </motion.div>
      <h1>Какой канал вы развиваете?</h1>
      <p>
        Укажите свой Telegram-канал — всё в сервисе будет работать на него: мониторинг подскажет, о чём писать, а
        ИИ напишет посты в его стиле. Сменить канал можно в любой момент.
      </p>
      <ChannelChoice projectId={projectId} onChosen={() => {}} />
      <div className="login-switch quiet">
        <button onClick={onLogout}>Выйти</button>
      </div>
    </motion.div>
  )
}
