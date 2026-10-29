import { useEffect, useRef, useState } from 'react'
import { Markdown } from '../lib/markdown'

/**
 * The AI's answer typed out character by character: however it arrives (a word, a paragraph at once),
 * it is revealed at a smooth pace that speeds up when far behind, so it neither jumps nor lags.
 * `typing`: it is still being written (shows the caret; a finished text that was never shown appears at once).
 */
export function TypedMarkdown({ text, typing }: { text: string; typing: boolean }) {
  const [shown, setShown] = useState(() => (typing ? 0 : text.length))
  const target = useRef(text.length)
  target.current = text.length

  useEffect(() => {
    if (shown >= text.length) return
    let frame = 0
    let last = performance.now()
    const step = (now: number) => {
      const dt = Math.min(64, now - last)
      last = now
      setShown((s) => {
        const behind = target.current - s
        if (behind <= 0) return s
        // about 70 characters a second, and much faster when far behind
        const speed = 0.07 + behind * 0.012
        return Math.min(target.current, s + Math.max(1, Math.round(speed * dt)))
      })
      frame = requestAnimationFrame(step)
    }
    frame = requestAnimationFrame(step)
    return () => cancelAnimationFrame(frame)
  }, [text, shown >= text.length]) // eslint-disable-line react-hooks/exhaustive-deps

  const done = shown >= text.length
  return (
    <div className={typing || !done ? 'typing' : undefined}>
      <Markdown text={tidy(text.slice(0, shown), !done)} />
    </div>
  )
}

/** A cut-off piece of markdown without its half-written marks: no stray "**", "`" or "[…](". */
function tidy(text: string, cut: boolean): string {
  if (!cut) return text
  let out = text
    .replace(/\[([^\]]*)\]\([^)]*$/, '$1') // a link whose address is still coming
    .replace(/\[([^\]]*)$/, '$1') // a link whose text is still coming
  if ((out.match(/\*\*/g) ?? []).length % 2) out = out.replace(/\*\*(?!.*\*\*)/s, '')
  if ((out.match(/`/g) ?? []).length % 2) out = out.replace(/`(?!.*`)/s, '')
  return out.replace(/\*+$/, '')
}
