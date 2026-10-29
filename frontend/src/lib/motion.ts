import type { Transition } from 'framer-motion'

/** iOS-like springs. */
export const spring: Transition = { type: 'spring', stiffness: 420, damping: 38, mass: 0.9 }
export const softSpring: Transition = { type: 'spring', stiffness: 260, damping: 30 }
export const sheetSpring: Transition = { type: 'spring', stiffness: 380, damping: 40 }
