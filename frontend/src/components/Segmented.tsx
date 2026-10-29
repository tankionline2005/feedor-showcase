import { AnimatePresence, motion } from 'framer-motion'
import { Check, Plus, Settings } from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'
import { setHiddenSorts, useHiddenSorts } from '../hooks/useHiddenSorts'
import { spring } from '../lib/motion'

interface Option<T> {
  value: T
  label: ReactNode
}

interface Props<T extends string> {
  id: string
  options: Option<T>[]
  value: T
  onChange: (value: T) => void
}

/** iOS segmented control with a sliding thumb. */
export function Segmented<T extends string>({ id, options, value, onChange }: Props<T>) {
  return (
    <div className="segmented" role="tablist">
      {options.map((o) => (
        <button
          key={o.value}
          role="tab"
          aria-selected={o.value === value}
          className={`segment${o.value === value ? ' active' : ''}`}
          onClick={() => onChange(o.value)}
        >
          {o.value === value && <motion.div layoutId={`seg-${id}`} className="segment-thumb" transition={spring} />}
          {o.label}
        </button>
      ))}
    </div>
  )
}

/** Horizontally scrolling filter chips with a sliding selection. */
export function Chips<T extends string>({ id, options, value, onChange }: Props<T>) {
  return (
    <div className="chips" role="tablist">
      {options.map((o) => (
        <motion.button
          key={o.value}
          role="tab"
          aria-selected={o.value === value}
          className={`chip${o.value === value ? ' active' : ''}`}
          onClick={() => onChange(o.value)}
          whileTap={{ scale: 0.94 }}
          style={{ isolation: 'isolate' }}
        >
          {o.value === value && <motion.div layoutId={`chip-${id}`} className="chip-thumb" transition={spring} />}
          <span>{o.label}</span>
        </motion.button>
      ))}
    </div>
  )
}

/** Sort chips the user can trim: the gear shows every sort with a cross or a plus, the check saves. */
export function SortChips<T extends string>({ id, options, value, onChange }: Props<T>) {
  const hidden = useHiddenSorts()
  const [draft, setDraft] = useState<string[] | null>(null)
  const editing = draft !== null
  const left = options.filter((o) => !hidden.includes(o.value))
  const visible = left.length ? left : options.slice(0, 1)

  // the chosen sort was put away (here or on the other screen): take the first one left
  useEffect(() => {
    if (!visible.some((o) => o.value === value)) onChange(visible[0].value)
  }, [visible, value, onChange])

  const toggle = (v: string) => {
    if (!draft) return
    if (draft.includes(v)) setDraft(draft.filter((d) => d !== v))
    else if (options.some((o) => o.value !== v && !draft.includes(o.value))) setDraft([...draft, v])
    // the last one stays: the list can't be left without a sort
  }

  const save = () => {
    if (draft) setHiddenSorts(draft)
    setDraft(null)
  }

  const list = editing ? options : visible
  return (
    <div className="sort-chips">
      <div className={`chips${editing ? ' wrap' : ''}`} role="tablist">
        <AnimatePresence mode="popLayout" initial={false}>
          {list.map((o) => {
            const off = editing && draft.includes(o.value)
            const active = !editing && o.value === value
            return (
              <motion.button
                key={o.value}
                layout="position"
                role="tab"
                aria-selected={active}
                className={`chip${active ? ' active' : ''}${off ? ' off' : ''}`}
                onClick={() => (editing ? toggle(o.value) : onChange(o.value))}
                initial={{ opacity: 0, scale: 0.85 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.85 }}
                transition={spring}
                whileTap={{ scale: 0.94 }}
                style={{ isolation: 'isolate' }}
              >
                {active && <motion.div layoutId={`chip-${id}`} className="chip-thumb" transition={spring} />}
                <span>{o.label}</span>
                <AnimatePresence initial={false}>
                  {editing && (
                    <motion.span
                      className="chip-toggle"
                      aria-label={off ? 'Показать' : 'Скрыть'}
                      initial={{ width: 0, opacity: 0 }}
                      animate={{ width: 18, opacity: 1, rotate: off ? 0 : 45 }}
                      exit={{ width: 0, opacity: 0 }}
                      transition={spring}
                    >
                      <Plus size={16} strokeWidth={2.4} />
                    </motion.span>
                  )}
                </AnimatePresence>
              </motion.button>
            )
          })}
        </AnimatePresence>
      </div>
      <motion.button
        layout="position"
        className={`chip-gear${editing ? ' save' : ''}`}
        aria-label={editing ? 'Сохранить категории' : 'Настроить категории'}
        onClick={() => (editing ? save() : setDraft(hidden))}
        whileTap={{ scale: 0.88 }}
        transition={spring}
      >
        <AnimatePresence mode="popLayout" initial={false}>
          <motion.span
            key={editing ? 'check' : 'gear'}
            initial={{ opacity: 0, rotate: -90, scale: 0.6 }}
            animate={{ opacity: 1, rotate: 0, scale: 1 }}
            exit={{ opacity: 0, rotate: 90, scale: 0.6 }}
            transition={spring}
          >
            {editing ? <Check size={20} strokeWidth={2.6} /> : <Settings size={22} />}
          </motion.span>
        </AnimatePresence>
      </motion.button>
    </div>
  )
}
