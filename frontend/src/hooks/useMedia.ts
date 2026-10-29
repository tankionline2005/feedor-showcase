import { useEffect, useState } from 'react'

function useMedia(query: string): boolean {
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches)
  useEffect(() => {
    const mql = window.matchMedia(query)
    const onChange = () => setMatches(mql.matches)
    onChange()
    mql.addEventListener('change', onChange)
    return () => mql.removeEventListener('change', onChange)
  }, [query])
  return matches
}

/** Wide screen: sidebar navigation and centered dialogs instead of a tab bar and bottom sheets. */
export function useWide() {
  return useMedia('(min-width: 900px)')
}

/** Mouse/trackpad: hover buttons and explicit refresh instead of swipes and pull-to-refresh. */
export function useMouse() {
  return useMedia('(hover: hover) and (pointer: fine)')
}
