import type { JSONContent } from '@tiptap/core'

/**
 * A post's text between Telegram and the editor. Telegram's HTML has inline tags (b, i, u, s, code, a, tg-spoiler)
 * and two blocks (blockquote, pre); its lines are plain new lines. The editor has paragraphs: one per line, a
 * quote holds paragraphs, a code block its text. Telegram's length is the visible text in UTF-16 units — a
 * JavaScript string's length.
 */

const escape = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
const attr = (s: string) => escape(s).replace(/"/g, '&quot;')

const INLINE: Record<string, string> = { b: 'b', i: 'i', u: 'u', s: 's', code: 'code' }

/** Telegram's HTML as the editor's HTML. */
export function tgToEditor(tg: string): string {
  const root = new DOMParser().parseFromString(`<div>${tg}</div>`, 'text/html').body.firstElementChild!

  const inline = (node: Node): string => {
    if (node.nodeType === Node.TEXT_NODE) return escape(node.textContent ?? '').replace(/\n/g, '<br>')
    if (!(node instanceof Element)) return ''
    const inner = [...node.childNodes].map(inline).join('')
    const tag = node.tagName.toLowerCase()
    if (INLINE[tag]) return `<${INLINE[tag]}>${inner}</${INLINE[tag]}>`
    if (tag === 'a') return `<a href="${attr(node.getAttribute('href') ?? '')}">${inner}</a>`
    if (tag === 'tg-spoiler') return `<span data-spoiler="">${inner}</span>`
    return inner
  }

  // the lines of some nodes as paragraphs; a quote or code block among them is a block of its own, and the new
  // line that sets it apart in Telegram's text is not one more (empty) line
  const blocks = (nodes: NodeListOf<ChildNode> | ChildNode[], allowBlocks: boolean): string[] => {
    const out: string[] = []
    let line: string | null = null
    let afterBlock = false
    for (const node of nodes) {
      const tag = node instanceof Element ? node.tagName.toLowerCase() : ''
      if (allowBlocks && (tag === 'blockquote' || tag === 'pre')) {
        if (line) out.push(`<p>${line}</p>`)
        line = null
        out.push(
          tag === 'pre'
            ? `<pre><code>${escape(node.textContent ?? '')}</code></pre>`
            : `<blockquote>${blocks(node.childNodes, false).join('')}</blockquote>`,
        )
        afterBlock = true
        continue
      }
      if (node.nodeType === Node.TEXT_NODE) {
        let text = node.textContent ?? ''
        if (afterBlock && text.startsWith('\n')) text = text.slice(1)
        afterBlock = false
        text.split('\n').forEach((part, i) => {
          if (i > 0) {
            out.push(`<p>${line ?? ''}</p>`)
            line = ''
          }
          line = (line ?? '') + escape(part)
        })
        continue
      }
      afterBlock = false
      line = (line ?? '') + inline(node)
    }
    if (line !== null && (line !== '' || out.length === 0)) out.push(`<p>${line}</p>`)
    return out
  }

  return blocks(root.childNodes, true).join('') || '<p></p>'
}

const MARKS: [string, (m: { attrs?: Record<string, unknown> }) => [string, string]][] = [
  ['link', (m) => [`<a href="${attr(String(m.attrs?.href ?? ''))}">`, '</a>']],
  ['bold', () => ['<b>', '</b>']],
  ['italic', () => ['<i>', '</i>']],
  ['underline', () => ['<u>', '</u>']],
  ['strike', () => ['<s>', '</s>']],
  ['spoiler', () => ['<tg-spoiler>', '</tg-spoiler>']],
  ['code', () => ['<code>', '</code>']],
]

const inlineTg = (nodes: JSONContent[] = []): string =>
  nodes
    .map((n) => {
      if (n.type === 'hardBreak') return '\n'
      if (n.type !== 'text') return ''
      let out = escape(n.text ?? '')
      for (const [name, wrap] of [...MARKS].reverse()) {
        const mark = n.marks?.find((m) => m.type === name)
        if (mark) {
          const [open, close] = wrap(mark)
          out = open + out + close
        }
      }
      return out
    })
    .join('')

const plainOf = (nodes: JSONContent[] = []): string =>
  nodes.map((n) => (n.type === 'hardBreak' ? '\n' : n.type === 'text' ? (n.text ?? '') : plainOf(n.content))).join('')

/** The editor's document as Telegram's HTML. */
export function editorToTg(doc: JSONContent): string {
  return (doc.content ?? [])
    .map((block) => {
      if (block.type === 'blockquote')
        return `<blockquote>${(block.content ?? []).map((p) => inlineTg(p.content)).join('\n')}</blockquote>`
      if (block.type === 'codeBlock') return `<pre>${escape(plainOf(block.content))}</pre>`
      return inlineTg(block.content)
    })
    .join('\n')
    .replace(/\s+$/, '')
}

/** The document's length as Telegram counts it. */
export function tgLength(doc: JSONContent): number {
  return (doc.content ?? [])
    .map((block) =>
      block.type === 'blockquote'
        ? (block.content ?? []).map((p) => plainOf(p.content)).join('\n')
        : plainOf(block.content),
    )
    .join('\n')
    .replace(/\s+$/, '').length
}
