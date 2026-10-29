import { useCallback, useRef } from 'react'

/**
 * A row that scrolls sideways (the cover templates) scrolled by the mouse wheel too: turning it over the row moves
 * the row left and right; at the row's end the page scrolls on as usual. For the row's `ref`.
 */
export function useWheelScrollX<T extends HTMLElement>() {
  const off = useRef<() => void>(undefined)
  return useCallback((el: T | null) => {
    off.current?.()
    off.current = undefined
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      if (e.ctrlKey || Math.abs(e.deltaX) >= Math.abs(e.deltaY)) return // a touchpad's own sideways swipe, a zoom
      const delta = e.deltaMode === 1 ? e.deltaY * 32 : e.deltaY
      const room = delta > 0 ? el.scrollWidth - el.clientWidth - el.scrollLeft : el.scrollLeft
      if (room <= 1) return
      e.preventDefault()
      el.scrollLeft += delta
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    off.current = () => el.removeEventListener('wheel', onWheel)
  }, [])
}
