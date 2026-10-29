import { Laptop, Monitor, Smartphone, Tablet } from 'lucide-react'
import type { DeviceKind } from '../api'

// as Telegram colours its devices: an iPhone blue, an Android green, a computer by its system
const COLORS: Record<DeviceKind['platform'], string> = {
  ios: 'var(--accent)',
  android: 'var(--green)',
  windows: '#0a84ff',
  mac: 'var(--text-2)',
  linux: 'var(--orange)',
  other: 'var(--indigo)',
}

/** A device's icon: a coloured square with a phone, a tablet or a computer. */
export function DeviceIcon({ platform, form, size = 30 }: Pick<DeviceKind, 'platform' | 'form'> & { size?: number }) {
  const Icon = form === 'phone' ? Smartphone : form === 'tablet' ? Tablet : platform === 'mac' ? Laptop : Monitor
  return (
    <div
      className="device-icon"
      style={{ background: COLORS[platform], width: size, height: size, borderRadius: size * 0.24 }}
    >
      <Icon size={size * 0.6} strokeWidth={2} />
    </div>
  )
}
