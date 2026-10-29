import { AnimatePresence, motion } from 'framer-motion'
import { createContext, type ReactNode, useCallback, useContext, useRef, useState } from 'react'
import { spring } from '../lib/motion'

const ToastContext = createContext<(text: string) => void>(() => {})

export function useToast() {
  return useContext(ToastContext)
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toast, setToast] = useState<{ id: number; text: string } | null>(null)
  const timer = useRef<number>(undefined)

  const show = useCallback((text: string) => {
    window.clearTimeout(timer.current)
    setToast({ id: Date.now(), text })
    timer.current = window.setTimeout(() => setToast(null), 2800)
  }, [])

  return (
    <ToastContext.Provider value={show}>
      {children}
      <AnimatePresence>
        {toast && (
          <motion.div
            key={toast.id}
            className="toast"
            role="status"
            initial={{ opacity: 0, y: 20, x: '-50%', scale: 0.96 }}
            animate={{ opacity: 1, y: 0, x: '-50%', scale: 1 }}
            exit={{ opacity: 0, y: 10, x: '-50%', scale: 0.98 }}
            transition={spring}
          >
            {toast.text}
          </motion.div>
        )}
      </AnimatePresence>
    </ToastContext.Provider>
  )
}
