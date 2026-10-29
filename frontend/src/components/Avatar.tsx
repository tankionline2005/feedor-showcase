import { useState } from 'react'

// Telegram-style gradient avatars for entities without a photo
const PALETTE: [string, string][] = [
  ['#ff885e', '#ff516a'],
  ['#ffcd6a', '#ffa85c'],
  ['#82b1ff', '#665fff'],
  ['#a0de7e', '#54cb68'],
  ['#53edd6', '#28c9b7'],
  ['#72d5fd', '#2a9ef1'],
  ['#e0a2f3', '#d669ed'],
]

function initials(title: string): string {
  const words = title.replace(/[^\p{L}\p{N}\s]/gu, ' ').trim().split(/\s+/).filter(Boolean)
  if (words.length === 0) return '?'
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase()
  return (words[0][0] + words[1][0]).toUpperCase()
}

interface Props {
  title: string
  seed: number
  src?: string | null
  size?: number
}

export function Avatar({ title, seed, src, size = 42 }: Props) {
  const [failed, setFailed] = useState(false)
  const [from, to] = PALETTE[Math.abs(seed) % PALETTE.length]
  return (
    <div
      className="avatar"
      style={{
        width: size,
        height: size,
        fontSize: Math.round(size * 0.4),
        background: `linear-gradient(180deg, ${from}, ${to})`,
      }}
      aria-hidden
    >
      {src && !failed ? <img src={src} alt="" loading="lazy" onError={() => setFailed(true)} /> : initials(title)}
    </div>
  )
}
