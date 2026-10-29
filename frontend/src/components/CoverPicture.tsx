import { type CSSProperties, useLayoutEffect, useRef, useState } from 'react'

/**
 * A cover the server draws (a moment's work): a pulsing placeholder of its shape until the picture has come, then
 * the picture fading in — the same when another template, colour or title asks for a new one. `ratio` "4:5"; without
 * it the shape is the last picture's (a square before the first).
 */
export function CoverPicture({
  src,
  ratio,
  alt = '',
  lazy = false,
  className = '',
  style,
}: {
  src: string
  ratio?: string
  alt?: string
  lazy?: boolean
  className?: string
  style?: CSSProperties
}) {
  const img = useRef<HTMLImageElement>(null)
  const [ready, setReady] = useState<string | null>(null)
  const [natural, setNatural] = useState<number | null>(null)
  const loaded = () => {
    const el = img.current
    if (el?.naturalWidth) setNatural(el.naturalWidth / el.naturalHeight)
    setReady(src)
  }
  // already in the browser's cache: complete before the handler was there
  useLayoutEffect(() => {
    if (img.current?.complete && img.current.naturalWidth) loaded()
  }, [src]) // eslint-disable-line react-hooks/exhaustive-deps
  const [w, h] = (ratio ?? '').split(':').map(Number)
  const shape = w && h ? w / h : (natural ?? 1)
  return (
    <div
      className={`cover-picture${ready === src ? ' ready' : ''} ${className}`}
      style={{ aspectRatio: String(shape), ...style }}
    >
      <img ref={img} src={src} alt={alt} loading={lazy ? 'lazy' : undefined} onLoad={loaded} />
    </div>
  )
}
