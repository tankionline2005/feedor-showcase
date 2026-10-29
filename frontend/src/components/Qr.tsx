import qrcode from 'qrcode-generator'
import { useMemo } from 'react'

/** The icon of signing in by a QR code: three corner squares and the dots of the code. */
export function QrIcon({ size = 24 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <g stroke="currentColor" strokeWidth={2}>
        <rect x="3" y="3" width="7" height="7" rx="2.2" />
        <rect x="14" y="3" width="7" height="7" rx="2.2" />
        <rect x="3" y="14" width="7" height="7" rx="2.2" />
      </g>
      <g fill="currentColor">
        <rect x="13.4" y="13.4" width="3.2" height="3.2" rx="1" />
        <rect x="18.4" y="13.4" width="3.2" height="3.2" rx="1" />
        <rect x="13.4" y="18.4" width="3.2" height="3.2" rx="1" />
        <rect x="18.4" y="18.4" width="3.2" height="3.2" rx="1" />
      </g>
    </svg>
  )
}

const QUIET = 2 // modules of white around the code

/**
 * A QR code drawn the way apps draw theirs: rounded dots, rounded corner squares and the logo in the middle
 * (the high error correction reads it all the same). Dark on white in both themes: cameras want it so.
 */
export function QrCode({ value, size }: { value: string; size: number }) {
  const { count, dots, logo } = useMemo(() => {
    const qr = qrcode(0, 'H')
    qr.addData(value)
    qr.make()
    const count = qr.getModuleCount()
    // the middle left free for the logo: an odd number of modules, about a fifth of the code
    let logo = Math.floor(count * 0.22)
    if (logo % 2 === 0) logo += 1
    const from = (count - logo) / 2
    const finder = (r: number, c: number) => (r < 8 && c < 8) || (r < 8 && c >= count - 8) || (r >= count - 8 && c < 8)
    const middle = (r: number, c: number) => r >= from - 0.5 && r < from + logo && c >= from - 0.5 && c < from + logo
    const dots: [number, number][] = []
    for (let r = 0; r < count; r++)
      for (let c = 0; c < count; c++) if (qr.isDark(r, c) && !finder(r, c) && !middle(r, c)) dots.push([r, c])
    return { count, dots, logo: { at: from, size: logo } }
  }, [value])

  const whole = count + QUIET * 2
  const corners = [
    [0, 0],
    [0, count - 7],
    [count - 7, 0],
  ]
  return (
    <svg
      className="qr-code"
      width={size}
      height={size}
      viewBox={`${-QUIET} ${-QUIET} ${whole} ${whole}`}
      role="img"
      aria-label="QR-код для входа"
      shapeRendering="geometricPrecision"
    >
      <rect x={-QUIET} y={-QUIET} width={whole} height={whole} fill="#fff" />
      <g fill="#0b0b0f">
        {dots.map(([r, c]) => (
          <rect key={`${r}:${c}`} x={c + 0.04} y={r + 0.04} width={0.92} height={0.92} rx={0.3} />
        ))}
        {corners.map(([r, c]) => (
          <g key={`${r}:${c}`}>
            <rect x={c + 0.5} y={r + 0.5} width={6} height={6} rx={1.9} fill="none" stroke="#0b0b0f" strokeWidth={1} />
            <rect x={c + 2} y={r + 2} width={3} height={3} rx={0.95} />
          </g>
        ))}
      </g>
      {/* the logo: the blue square with the pixel «F» */}
      <svg x={logo.at + 0.5} y={logo.at + 0.5} width={logo.size - 1} height={logo.size - 1} viewBox="0 0 1080 1080">
        <rect width="1080" height="1080" rx="240" fill="#008bff" />
        <path
          fill="#fff"
          d="M504 288 H864 V360 H792 V432 H504 V504 H648 V576 H504 V648 H432 V792 H216 V720 H288 V576 H432 V360 H504 Z"
        />
      </svg>
    </svg>
  )
}

/** The address a login page's QR code holds: a phone's camera opens it, «Подключить устройство» reads it. */
export function qrLink(token: string) {
  return `${location.origin}/#/qr/${token}`
}

/** The code of a login page's QR code, if it is one of ours. */
export function qrToken(text: string): string | null {
  try {
    const url = new URL(text.trim())
    if (url.host !== location.host) return null
    return /^#\/qr\/([\w-]{16,64})$/.exec(url.hash)?.[1] ?? null
  } catch {
    return null
  }
}
