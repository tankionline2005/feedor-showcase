import { motion } from 'framer-motion'
import { badgeText } from '../hooks/useBadges'
import { spring } from '../lib/motion'
import { Wordmark } from './Logo'
import { ProjectButton } from './ProjectSwitcher'
import type { Tab, TabInfo } from './TabBar'

interface Props {
  tabs: TabInfo[]
  active: Tab
  /** the blue badges: what is new there */
  badges?: Partial<Record<Tab, number>>
  onSelect: (tab: Tab) => void
}

/** Desktop navigation: a sidebar instead of the bottom tab bar. */
export function Sidebar({ tabs, active, badges = {}, onSelect }: Props) {
  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <Wordmark width={112} />
        <ProjectButton size={40} />
      </div>
      <nav className="sidebar-nav">
        {tabs.map(({ id, label, icon: Icon }, i) => {
          const isActive = id === active
          return (
            <button
              key={id}
              className={`sidebar-item${isActive ? ' active' : ''}`}
              onClick={() => onSelect(id)}
              aria-current={isActive ? 'page' : undefined}
              title={`${label} (${i + 1})`}
            >
              {isActive && <motion.div layoutId="sidebar-active" className="sidebar-highlight" transition={spring} />}
              <Icon size={20} strokeWidth={isActive ? 2.1 : 1.8} />
              <span>{label}</span>
              {badges[id] ? <span className="sidebar-badge">{badgeText(badges[id]!)}</span> : null}
              <kbd>{i + 1}</kbd>
            </button>
          )
        })}
      </nav>
    </aside>
  )
}
