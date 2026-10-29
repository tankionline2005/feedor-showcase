import { motion } from 'framer-motion'
import { Megaphone, Newspaper, Settings, Sparkles, TrendingUp, type LucideIcon } from 'lucide-react'
import type { Me } from '../api'
import { badgeText } from '../hooks/useBadges'

export type Tab = 'feed' | 'top' | 'ai' | 'channels' | 'settings'

export interface TabInfo {
  id: Tab
  label: string
  icon: LucideIcon
}

const FEED: TabInfo = { id: 'feed', label: 'Лента', icon: Newspaper }
const TOP: TabInfo = { id: 'top', label: 'Топ', icon: TrendingUp }
const AI: TabInfo = { id: 'ai', label: 'AI', icon: Sparkles }
const CHANNELS: TabInfo = { id: 'channels', label: 'Каналы', icon: Megaphone }
const SETTINGS: TabInfo = { id: 'settings', label: 'Настройки', icon: Settings }

/** A project owner's plan is in Settings → Тариф. «AI»: the owner always (it leads to linking
 * OpenRouter until it is), staff once the owner has linked it; the admin has none. */
export function tabsFor(me: Pick<Me, 'role' | 'ai_linked'>): TabInfo[] {
  const ai = me.role === 'user' || (me.role === 'member' && me.ai_linked)
  return ai ? [FEED, TOP, AI, CHANNELS, SETTINGS] : [FEED, TOP, CHANNELS, SETTINGS]
}

interface Props {
  tabs: TabInfo[]
  active: Tab
  badges?: Partial<Record<Tab, number>>
  onSelect: (tab: Tab) => void
}

export function TabBar({ tabs, active, badges = {}, onSelect }: Props) {
  return (
    <nav className="tabbar">
      {tabs.map(({ id, label, icon: Icon }) => {
        const isActive = id === active
        const badge = badges[id]
        return (
          <motion.button
            key={id}
            className={`tab${isActive ? ' active' : ''}`}
            onClick={() => onSelect(id)}
            whileTap={{ scale: 0.88 }}
            aria-current={isActive ? 'page' : undefined}
          >
            <Icon size={25} strokeWidth={isActive ? 2.1 : 1.7} />
            <span>{label}</span>
            {badge ? <span className="tab-badge">{badgeText(badge)}</span> : null}
          </motion.button>
        )
      })}
    </nav>
  )
}
