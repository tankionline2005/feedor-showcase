/** Dollars as the AI spends them: "$12", "$2,50", "$0,012", "$0,0004", "<$0,0001". */
export function usd(n: number): string {
  if (n === 0) return '$0'
  const abs = Math.abs(n)
  if (abs < 0.0001) return '<$0,0001'
  const digits = abs >= 100 ? 0 : abs >= 1 ? 2 : abs >= 0.01 ? 3 : 4
  let text = n.toFixed(digits)
  // fractions of a cent keep their digits, but not trailing zeros past the cents
  if (digits > 2) text = text.replace(/0+$/, '').replace(/\.(\d)$/, '.$10')
  return '$' + text.replace('.', ',')
}

/** A price: "$29", "$0,08" (`digits` after the comma when it has a fraction). */
export function price(n: number, digits = 2): string {
  const text = Number.isInteger(n) ? String(n) : n.toFixed(digits)
  return '$' + text.replace('.', ',')
}

/** The balance and its moves: always cents, "−$1,25" for money off it. */
export function cents(n: number, signed = false): string {
  const text = Math.abs(n).toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  const sign = n < 0 ? '−' : signed && n > 0 ? '+' : ''
  return `${sign}$${text}`
}
