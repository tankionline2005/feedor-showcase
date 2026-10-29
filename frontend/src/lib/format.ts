export function compact(n: number): string {
  if (n < 1000) return String(n)
  if (n < 10_000) return (n / 1000).toFixed(1).replace('.0', '').replace('.', ',') + 'K'
  if (n < 1_000_000) return Math.round(n / 1000) + 'K'
  return (n / 1_000_000).toFixed(1).replace('.0', '').replace('.', ',') + 'M'
}

const exactFmt = new Intl.NumberFormat('ru-RU')

/**
 * Card metrics: exact below a million ("21 615") so every refresh shows, "1,23M" above.
 * (compact() rounds to thousands — fine for lists, but there 19 000…19 999 all read "19K".)
 */
export function exact(n: number): string {
  if (n < 1_000_000) return exactFmt.format(n)
  return (n / 1_000_000).toFixed(2).replace('.', ',') + 'M'
}

export function bytes(n: number): string {
  const units: [string, number][] = [
    ['ТБ', 1024 ** 4],
    ['ГБ', 1024 ** 3],
    ['МБ', 1024 ** 2],
  ]
  for (const [unit, scale] of units) {
    if (n >= scale) return `${(n / scale).toFixed(1).replace('.', ',')} ${unit}`
  }
  return n > 0 ? `${Math.max(1, Math.round(n / 1024))} КБ` : '0 КБ'
}

/** FR+ style ratio: 1.5 → "×1,5", 12.3 → "×12" */
export function multiplier(n: number): string {
  return '×' + (n >= 10 ? String(Math.round(n)) : n.toFixed(1).replace('.', ','))
}

export function percent(n: number): string {
  return n.toFixed(n >= 10 ? 1 : 2).replace('.', ',') + '%'
}

export function plural(n: number, one: string, few: string, many: string): string {
  const mod10 = n % 10
  const mod100 = n % 100
  if (mod10 === 1 && mod100 !== 11) return one
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few
  return many
}

const timeFmt = new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit' })
const dayFmt = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short' })
const weekdayFmt = new Intl.DateTimeFormat('ru-RU', { weekday: 'short' })

/** "14:05" for today, "пн" within a week, "7 окт." otherwise — like the Telegram chat list. */
export function shortDate(iso: string): string {
  const d = new Date(iso)
  const now = new Date()
  if (d.toDateString() === now.toDateString()) return timeFmt.format(d)
  if (now.getTime() - d.getTime() < 6 * 86_400_000) return weekdayFmt.format(d)
  return dayFmt.format(d)
}

export function time(iso: string): string {
  return timeFmt.format(new Date(iso))
}

export function relative(iso: string): string {
  const diff = Math.max(0, Date.now() - new Date(iso).getTime())
  const min = Math.floor(diff / 60_000)
  if (min < 1) return 'только что'
  if (min < 60) return `${min} мин назад`
  const h = Math.floor(min / 60)
  if (h < 24) return `${h} ч назад`
  return `${shortDate(iso)}, ${time(iso)}`
}

/** Day separator text in a feed: "Сегодня", "Вчера", "5 октября". */
export function dayLabel(iso: string): string {
  const d = new Date(iso)
  const today = new Date()
  const yesterday = new Date(Date.now() - 86_400_000)
  if (d.toDateString() === today.toDateString()) return 'Сегодня'
  if (d.toDateString() === yesterday.toDateString()) return 'Вчера'
  return new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long' }).format(d)
}

/** "сегодня в 10:00", "завтра в 10:00", "вчера в 10:00", "25 октября в 10:00" (with the year when not this one). */
export function whenLabel(iso: string): string {
  const d = new Date(iso)
  const day = (offset: number) => new Date(Date.now() + offset * 86_400_000).toDateString()
  const name =
    d.toDateString() === day(0)
      ? 'сегодня'
      : d.toDateString() === day(1)
        ? 'завтра'
        : d.toDateString() === day(-1)
          ? 'вчера'
          : longDate(iso)
  return `${name} в ${time(iso)}`
}

/** "26 октября", with the year when it is not this one. */
export function longDate(iso: string): string {
  const d = new Date(iso)
  const opts: Intl.DateTimeFormatOptions = { day: 'numeric', month: 'long' }
  if (d.getFullYear() !== new Date().getFullYear()) opts.year = 'numeric'
  return new Intl.DateTimeFormat('ru-RU', opts).format(d)
}

/** "осталось 12 дней · до 26 октября"; null for a plan without an end. */
export function planTerm(expiresAt: string | null): string | null {
  if (!expiresAt) return null
  const ms = new Date(expiresAt).getTime() - Date.now()
  if (ms <= 0) return `закончился ${longDate(expiresAt)}`
  const days = Math.ceil(ms / 86_400_000)
  const left = days <= 1 && ms < 86_400_000 ? 'меньше суток' : `${days} ${plural(days, 'день', 'дня', 'дней')}`
  return `осталось ${left} · до ${longDate(expiresAt)}`
}
