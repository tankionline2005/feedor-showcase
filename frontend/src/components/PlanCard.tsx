import { Check } from 'lucide-react'
import { type ReactNode, useEffect, useRef } from 'react'
import type { Plan } from '../api'
import { plural } from '../lib/format'
import { price } from '../lib/money'

/** What a plan gives, in a line each — the same on the plans page, in «Тариф» and when buying. */
export function planLines(plan: Plan): string[] {
  if (plan.id === 'dev') return []
  const count = (n: number | null, one: string, few: string, many: string, none: string, any: string) =>
    n === null ? any : n === 0 ? none : `${n.toLocaleString('ru-RU')} ${plural(n, one, few, many)}`
  return [
    count(plan.channels, 'канал', 'канала', 'каналов', 'Без каналов', 'Каналы без ограничений'),
    count(plan.members, 'сотрудник', 'сотрудника', 'сотрудников', 'Без сотрудников', 'Сотрудники без ограничений'),
    count(plan.triggers, 'триггер-слово', 'триггер-слова', 'триггер-слов', 'Без триггер-слов', 'Триггер-слова без ограничений'),
    count(plan.groups, 'группа', 'группы', 'групп', 'Без групп', 'Группы без ограничений'),
    plan.api ? 'API и MCP-коннектор' : 'Без API и MCP',
    plan.ai === null ? '' : plan.id === 'trial' ? `ИИ на ${price(plan.ai)} на пробу` : `ИИ на ${price(plan.ai)} в месяц`,
  ].filter(Boolean)
}

/** Per channel, for Agency's line against Studio's: the decoy's job is to make this plain. */
function perChannel(plan: Plan): number | null {
  return plan.price && plan.channels ? plan.price / plan.channels : null
}

/**
 * A plan's card: each has its own look — the trial a ticket, Starter Telegram's blue, Pro indigo with a sheen,
 * Studio quiet, Agency graphite and gold, dev:service:plan a terminal that should not exist.
 */
export function PlanCard({
  plan,
  all,
  state,
  footer,
  onClick,
}: {
  plan: Plan
  /** the other plans, for Agency's price per channel against Studio's */
  all?: Plan[]
  /** «Ваш тариф», or the cheaper one coming at the end of the period */
  state?: 'current' | 'next' | null
  footer?: ReactNode
  onClick?: () => void
}) {
  if (plan.id === 'dev') return <DevCard state={state} footer={footer} />
  const studio = all?.find((p) => p.id === 'studio')
  const value = plan.id === 'agency' && studio ? [perChannel(plan), perChannel(studio)] : null
  const badge = state === 'current' ? 'Ваш тариф' : state === 'next' ? 'Со следующего периода' : plan.badge
  const Tag = onClick ? 'button' : 'div'
  return (
    <Tag className={`tariff tariff-${plan.id}${state === 'current' ? ' current' : ''}`} onClick={onClick}>
      {plan.id === 'starter' && <Spark />}
      <div className="tariff-head">
        <div className="tariff-name">{plan.title}</div>
        {badge && (
          <span className="tariff-badge">
            {state === 'current' && <Check size={12} strokeWidth={3} />}
            {badge}
          </span>
        )}
      </div>
      <div className="tariff-price">
        {plan.price === null ? (
          <b>Бесплатно</b>
        ) : (
          <>
            <b>{price(plan.price)}</b>
            <span>за 30 дней</span>
          </>
        )}
      </div>
      <ul className="tariff-lines">
        {planLines(plan).map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
      {value && value[0] !== null && value[1] !== null && (
        <div className="tariff-value">
          {price(value[0], 2)} за канал — против {price(value[1], 2)} у Studio
        </div>
      )}
      {footer && <div className="tariff-footer">{footer}</div>}
    </Tag>
  )
}

function Spark() {
  return (
    <svg className="tariff-spark" viewBox="0 0 64 64" aria-hidden="true">
      <path d="M32 4c2 14 6 20 14 24 4 2 9 3 14 4-14 2-20 6-24 14-2 4-3 9-4 14-2-14-6-20-14-24-4-2-9-3-14-4 14-2 20-6 24-14 2-4 3-9 4-14z" />
    </svg>
  )
}

/** dev:service:plan: a plan that is not on any list. */
function DevCard({ state, footer }: { state?: 'current' | 'next' | null; footer?: ReactNode }) {
  return (
    <div className="tariff tariff-dev">
      <MatrixRain />
      <div className="tariff-dev-body">
        <div className="tariff-head">
          <div className="tariff-name glitch" data-text="dev:service:plan">
            dev:service:plan
          </div>
          {state === 'current' && <span className="tariff-badge">active</span>}
        </div>
        <ul className="tariff-lines" style={{ marginTop: 12 }}>
          <li>channels: undefined</li>
          <li>staff: undefined</li>
          <li>triggers: undefined</li>
          <li>ai: own_openrouter_key</li>
          <li>expires: null</li>
        </ul>
        {footer && <div className="tariff-footer">{footer}</div>}
      </div>
    </div>
  )
}

const GLYPHS = 'アイウエオカキクケコサシスセソタチツテトナニヌネノ0123456789{}[]<>/$#'

/** Green glyphs falling behind dev:service:plan's card: slow, light on the CPU, still for reduced motion. */
function MatrixRain() {
  const ref = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const canvas = ref.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const size = 13
    let drops: number[] = []
    let visible = true
    let timer = 0
    const fit = () => {
      const { width, height } = canvas.getBoundingClientRect()
      const ratio = window.devicePixelRatio || 1
      canvas.width = Math.max(1, Math.round(width * ratio))
      canvas.height = Math.max(1, Math.round(height * ratio))
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0)
      drops = Array.from({ length: Math.ceil(width / size) }, () => Math.random() * (height / size))
      ctx.fillStyle = '#030703'
      ctx.fillRect(0, 0, width, height)
    }
    const frame = () => {
      const { width, height } = canvas.getBoundingClientRect()
      ctx.fillStyle = 'rgba(3, 7, 3, 0.16)'
      ctx.fillRect(0, 0, width, height)
      ctx.font = `${size}px ui-monospace, Menlo, Consolas, monospace`
      drops.forEach((y, i) => {
        const glyph = GLYPHS[Math.floor(Math.random() * GLYPHS.length)]
        ctx.fillStyle = Math.random() < 0.06 ? '#d4ffe0' : '#1fd65f'
        ctx.fillText(glyph, i * size, y * size)
        drops[i] = y * size > height && Math.random() > 0.96 ? 0 : y + 1
      })
    }
    const loop = () => {
      if (visible) frame()
      timer = window.setTimeout(loop, 85)
    }
    fit()
    if (still) {
      for (let i = 0; i < 40; i++) frame()
    } else {
      loop()
    }
    const watcher = new IntersectionObserver(([entry]) => (visible = entry.isIntersecting))
    watcher.observe(canvas)
    const resize = new ResizeObserver(fit)
    resize.observe(canvas)
    return () => {
      window.clearTimeout(timer)
      watcher.disconnect()
      resize.disconnect()
    }
  }, [])
  return <canvas ref={ref} className="tariff-rain" aria-hidden="true" />
}
