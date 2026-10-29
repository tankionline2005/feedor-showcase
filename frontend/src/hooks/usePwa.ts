import { useEffect, useState } from 'react'
import { useMouse } from './useMedia'

interface InstallPromptEvent extends Event {
  prompt: () => Promise<void>
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }>
}

// Chrome/Android fire this once, early; keep it for the install button
let deferredPrompt: InstallPromptEvent | null = null
const listeners = new Set<() => void>()
window.addEventListener('beforeinstallprompt', (e) => {
  e.preventDefault()
  deferredPrompt = e as InstallPromptEvent
  listeners.forEach((fn) => fn())
})
window.addEventListener('appinstalled', () => {
  deferredPrompt = null
  listeners.forEach((fn) => fn())
})

export function isStandalone(): boolean {
  return (
    window.matchMedia('(display-mode: standalone)').matches ||
    (navigator as Navigator & { standalone?: boolean }).standalone === true
  )
}

export function isIOS(): boolean {
  return /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1)
}

/** In-app browsers (Telegram, Instagram…) cannot add to the home screen. */
export function isInAppBrowser(): boolean {
  return /Telegram|Instagram|FBAN|FBAV|Line\//i.test(navigator.userAgent)
}

/**
 * Install offer for the home screen: only in a mobile browser — not on a desktop
 * (mouse) and not when the app already runs from the home screen.
 */
export function usePwaInstall() {
  const mouse = useMouse()
  const [, rerender] = useState(0)
  useEffect(() => {
    const fn = () => rerender((n) => n + 1)
    listeners.add(fn)
    return () => {
      listeners.delete(fn)
    }
  }, [])

  const install = async () => {
    if (!deferredPrompt) return false
    await deferredPrompt.prompt()
    const { outcome } = await deferredPrompt.userChoice
    deferredPrompt = null
    rerender((n) => n + 1)
    return outcome === 'accepted'
  }

  return {
    available: !mouse && !isStandalone(),
    canPrompt: deferredPrompt !== null,
    install,
  }
}
