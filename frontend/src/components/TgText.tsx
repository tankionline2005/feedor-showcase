import DOMPurify from 'dompurify'
import { useMemo } from 'react'
import { copyText } from '../lib/clipboard'
import { useToast } from './Toast'

const TAGS = ['b', 'i', 'u', 's', 'code', 'pre', 'a', 'blockquote', 'tg-spoiler']

/** A post's text in Telegram's formatting, as Telegram shows it: a spoiler opens on a tap, monospace text is
 * copied on a tap (commands, codes), a link opens in a new tab. */
export function TgText({ html, className }: { html: string; className?: string }) {
  const toast = useToast()
  const clean = useMemo(
    () =>
      DOMPurify.sanitize(html, { ALLOWED_TAGS: TAGS, ALLOWED_ATTR: ['href', 'expandable'] })
        // a quote or a code block is a block already: the new line that sets it apart in Telegram's text is
        // not one more empty line here
        .replace(/<\/(blockquote|pre)>\n/g, '</$1>')
        .replace(/\n(<(?:blockquote|pre)[ >])/g, '$1'),
    [html],
  )

  const onClick = async (e: React.MouseEvent) => {
    const target = e.target as HTMLElement
    const spoiler = target.closest('tg-spoiler')
    if (spoiler && !spoiler.classList.contains('shown')) {
      e.preventDefault()
      spoiler.classList.add('shown')
      return
    }
    const link = target.closest('a')
    if (link) {
      e.preventDefault()
      window.open(link.getAttribute('href') ?? '', '_blank', 'noopener,noreferrer')
      return
    }
    const code = target.closest('code, pre')
    if (code) {
      toast((await copyText(code.textContent ?? '')) ? 'Скопировано' : 'Не удалось скопировать')
    }
  }

  return (
    <div
      className={`tg-text${className ? ` ${className}` : ''}`}
      onClick={onClick}
      dangerouslySetInnerHTML={{ __html: clean }}
    />
  )
}
