import { useLayoutEffect, useState } from 'react'

/**
 * Windows over windows: every sheet, action sheet and alert that opens takes the next layer, so its backdrop covers
 * the windows under it (they can't be tapped through) and Escape closes only the top one. The layers are counted
 * from 1 while anything is open and start again when all are closed.
 */
const open: number[] = []
let last = 0

/** The layer of a window while it is open (kept while it leaves, so it doesn't drop under the others). */
export function useLayer(isOpen: boolean): number {
  const [level, setLevel] = useState(0)
  useLayoutEffect(() => {
    if (!isOpen) return
    const mine = ++last
    open.push(mine)
    setLevel(mine)
    return () => {
      open.splice(open.indexOf(mine), 1)
      if (!open.length) last = 0
    }
  }, [isOpen])
  return level
}

/** The z-index of a layer's backdrop; its window goes one above. */
export const backdropZ = (level: number) => 50 + 2 * level

/** This window is the one on top (Escape is for it). */
export const isTop = (level: number) => open[open.length - 1] === level

/** Some window is open now: a section's welcome cards wait instead of jumping over it. */
export const anyOpen = () => open.length > 0
