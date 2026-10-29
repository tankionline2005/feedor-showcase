import { useSyncExternalStore } from 'react'

/**
 * Dark or light, by the switch in the settings (dark unless chosen otherwise), kept in this browser. index.html puts it
 * on <html data-theme> before the page draws, so nothing flashes; the styles go by that attribute.
 */
export type Theme = 'dark' | 'light'

const KEY = 'theme'
const BARS: Record<Theme, string> = { dark: '#000000', light: '#f2f2f7' }
const listeners = new Set<() => void>()

function current(): Theme {
  return document.documentElement.dataset.theme === 'light' ? 'light' : 'dark'
}

export function setTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme
  document.querySelector('meta[name=theme-color]')?.setAttribute('content', BARS[theme])
  try {
    localStorage.setItem(KEY, theme)
  } catch {
    /* private mode: it lasts till the page is closed */
  }
  listeners.forEach((l) => l())
}

export function useTheme(): Theme {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l)
      return () => listeners.delete(l)
    },
    current,
  )
}
