import { Fragment, type ReactNode } from 'react'

/**
 * The little markdown the AI writes, as React elements (never as HTML): paragraphs, headings, lists,
 * **bold**, *italic*, `code` and [links](https://…). Only http(s) links become links.
 */
export function Markdown({ text }: { text: string }) {
  const blocks: ReactNode[] = []
  let list: { ordered: boolean; items: string[] } | null = null
  let para: string[] = []

  const flushPara = () => {
    if (para.length) blocks.push(<p key={blocks.length}>{inline(para.join('\n'))}</p>)
    para = []
  }
  const flushList = () => {
    if (!list) return
    const items = list.items.map((it, i) => <li key={i}>{inline(it)}</li>)
    blocks.push(list.ordered ? <ol key={blocks.length}>{items}</ol> : <ul key={blocks.length}>{items}</ul>)
    list = null
  }

  for (const raw of text.split('\n')) {
    const line = raw.trimEnd()
    const heading = line.match(/^#{1,6}\s+(.*)$/)
    const bullet = line.match(/^\s*[-*•]\s+(.*)$/)
    const numbered = line.match(/^\s*\d+[.)]\s+(.*)$/)
    if (!line.trim()) {
      flushPara()
      flushList()
    } else if (heading) {
      flushPara()
      flushList()
      blocks.push(<h4 key={blocks.length}>{inline(heading[1])}</h4>)
    } else if (bullet || numbered) {
      flushPara()
      const ordered = !bullet
      if (list && list.ordered !== ordered) flushList()
      list ??= { ordered, items: [] }
      list.items.push((bullet ?? numbered)![1])
    } else if (list && /^\s{2,}/.test(raw)) {
      list.items[list.items.length - 1] += ' ' + line.trim()
    } else {
      flushList()
      para.push(line)
    }
  }
  flushPara()
  flushList()
  return <div className="markdown">{blocks}</div>
}

const TOKEN = /(\*\*[^*]+\*\*|__[^_]+__|`[^`]+`|\[[^\]]+\]\([^)\s]+\)|\*[^*\s][^*]*\*|_[^_\s][^_]*_)/g

function inline(text: string): ReactNode {
  const parts = text.split(TOKEN)
  return parts.map((part, i) => {
    if (!part) return null
    if ((part.startsWith('**') && part.endsWith('**')) || (part.startsWith('__') && part.endsWith('__')))
      return <b key={i}>{inline(part.slice(2, -2))}</b>
    if (part.startsWith('`') && part.endsWith('`')) return <code key={i}>{part.slice(1, -1)}</code>
    const link = part.match(/^\[([^\]]+)\]\(([^)\s]+)\)$/)
    if (link) {
      const url = link[2]
      return /^https?:\/\//.test(url) ? (
        <a key={i} href={url} target="_blank" rel="noopener noreferrer">
          {link[1]}
        </a>
      ) : (
        <Fragment key={i}>{link[1]}</Fragment>
      )
    }
    if ((part.startsWith('*') && part.endsWith('*')) || (part.startsWith('_') && part.endsWith('_')))
      return <i key={i}>{part.slice(1, -1)}</i>
    return (
      <Fragment key={i}>
        {part.split('\n').map((line, j) => (
          <Fragment key={j}>
            {j > 0 && <br />}
            {line}
          </Fragment>
        ))}
      </Fragment>
    )
  })
}

/** The same markdown as plain text, to copy: no markup, «•» for bullets, links as «text (address)». */
export function plainText(text: string): string {
  const flat = (s: string): string =>
    s
      .split(TOKEN)
      .map((part) => {
        if ((part.startsWith('**') && part.endsWith('**')) || (part.startsWith('__') && part.endsWith('__')))
          return flat(part.slice(2, -2))
        if (part.startsWith('`') && part.endsWith('`')) return part.slice(1, -1)
        const link = part.match(/^\[([^\]]+)\]\(([^)\s]+)\)$/)
        if (link) return link[1] === link[2] || !/^https?:\/\//.test(link[2]) ? link[1] : `${link[1]} (${link[2]})`
        if ((part.startsWith('*') && part.endsWith('*')) || (part.startsWith('_') && part.endsWith('_')))
          return part.slice(1, -1)
        return part
      })
      .join('')
  return text
    .split('\n')
    .map((raw) => {
      const line = raw.trimEnd()
      const heading = line.match(/^#{1,6}\s+(.*)$/)
      const bullet = line.match(/^(\s*)[-*•]\s+(.*)$/)
      if (heading) return flat(heading[1])
      if (bullet) return `${bullet[1]}• ${flat(bullet[2])}`
      return flat(line)
    })
    .join('\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}
