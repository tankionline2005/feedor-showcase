import { Extension, Mark, mergeAttributes, type Editor } from '@tiptap/core'
import { Blockquote } from '@tiptap/extension-blockquote'
import { Link } from '@tiptap/extension-link'
import { Plugin } from '@tiptap/pm/state'
import { EditorContent, useEditor, useEditorState } from '@tiptap/react'
import StarterKit from '@tiptap/starter-kit'
import {
  Bold,
  Check,
  Code,
  EyeOff,
  Italic,
  Link2,
  RemoveFormatting,
  Strikethrough,
  TextQuote,
  Underline,
  X,
  type LucideIcon,
} from 'lucide-react'
import { type ReactNode, useEffect, useRef, useState } from 'react'
import { editorToTg, tgLength, tgToEditor } from '../lib/tgPost'

declare module '@tiptap/core' {
  interface Commands<ReturnType> {
    spoiler: { toggleSpoiler: () => ReturnType }
  }
}

/** Telegram's spoiler: hidden text a reader taps to see. */
const Spoiler = Mark.create({
  name: 'spoiler',
  parseHTML: () => [{ tag: 'tg-spoiler' }, { tag: 'span[data-spoiler]' }],
  renderHTML: ({ HTMLAttributes }) => ['span', mergeAttributes(HTMLAttributes, { 'data-spoiler': '' }), 0],
  addCommands() {
    return {
      toggleSpoiler:
        () =>
        ({ commands }) =>
          commands.toggleMark(this.name),
    }
  },
})

/** A link ends where it ends: what is typed after it is not the link's. */
const TgLink = Link.extend({ inclusive: () => false })

/** A quote of Telegram holds lines, not other quotes. */
const Quote = Blockquote.extend({ content: 'paragraph+' })

/** Telegram's limit: no change that makes the text longer than it goes in (typing, pasting, «вернуть»); what
 * makes it shorter always does. */
const Limit = Extension.create<{ limit: () => number; onRefused: () => void }>({
  name: 'telegramLimit',
  addOptions: () => ({ limit: () => 4096, onRefused: () => undefined }),
  addProseMirrorPlugins() {
    const options = this.options
    const size = (doc: {
      textBetween: (a: number, b: number, s: string, l: string) => string
      content: { size: number }
    }) => doc.textBetween(0, doc.content.size, '\n', '\n').replace(/\s+$/, '').length
    return [
      new Plugin({
        filterTransaction: (tr, state) => {
          if (!tr.docChanged) return true
          const after = size(tr.doc)
          if (after <= options.limit() || after <= size(state.doc)) return true
          options.onRefused()
          return false
        },
      }),
    ]
  },
})

const LINK = /^(https?:\/\/|tg:\/\/|mailto:)/i

export interface PostEditorApi {
  /** the text now, in Telegram's HTML */
  html: () => string
  /** puts a text in (Telegram's HTML): the AI's original back */
  reset: (tg: string) => boolean
}

/** The post's text, edited the way Word edits: what Telegram can show, with its buttons above — bold, italic,
 * underline, strikethrough, monospace, spoiler, link, quote, and taking the formatting off. The text never gets
 * longer than Telegram's limit (`limit`): what would make it so is not let in, and said (`onRefused`). */
export function PostEditor({
  initial,
  limit,
  onLength,
  onRefused,
  apiRef,
  toolbarEnd,
  between,
}: {
  initial: string
  limit: number
  onLength: (n: number) => void
  onRefused: () => void
  apiRef: React.MutableRefObject<PostEditorApi | null>
  /** at the end of the buttons' row (the phone's window: «отменить», «сохранить») */
  toolbarEnd?: ReactNode
  /** between the buttons and the text (the phone's window: the pictures) */
  between?: ReactNode
}) {
  // the extensions read the limit and the callbacks as they are now (they are made once)
  const limitRef = useRef(limit)
  limitRef.current = limit
  const refusedRef = useRef(onRefused)
  refusedRef.current = onRefused
  const lengthRef = useRef(onLength)
  lengthRef.current = onLength

  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        heading: false,
        bulletList: false,
        orderedList: false,
        listItem: false,
        listKeymap: false,
        horizontalRule: false,
        trailingNode: false,
        blockquote: false,
        link: false,
      }),
      Quote,
      TgLink.configure({
        openOnClick: false,
        autolink: true,
        linkOnPaste: true,
        defaultProtocol: 'https',
        HTMLAttributes: { target: null, rel: null },
        validate: (url) => LINK.test(url),
      }),
      Spoiler,
      Limit.configure({ limit: () => limitRef.current, onRefused: () => refusedRef.current() }),
    ],
    content: tgToEditor(initial),
    autofocus: 'end',
    editorProps: { attributes: { class: 'post-editor-content tg-text post-text' } },
    onCreate: ({ editor }) => lengthRef.current(tgLength(editor.getJSON())),
    onUpdate: ({ editor }) => lengthRef.current(tgLength(editor.getJSON())),
  })

  useEffect(() => {
    apiRef.current = {
      html: () => editorToTg(editor.getJSON()),
      reset: (tg) => editor.commands.setContent(tgToEditor(tg), { emitUpdate: true }),
    }
    return () => {
      apiRef.current = null
    }
  }, [editor, apiRef])

  return (
    <div className="post-editor">
      <Toolbar editor={editor} end={toolbarEnd} />
      {between}
      <EditorContent editor={editor} className="post-editor-body" />
    </div>
  )
}

function Toolbar({ editor, end }: { editor: Editor; end?: ReactNode }) {
  const active = useEditorState({
    editor,
    selector: ({ editor: e }) => ({
      bold: e.isActive('bold'),
      italic: e.isActive('italic'),
      underline: e.isActive('underline'),
      strike: e.isActive('strike'),
      code: e.isActive('code'),
      spoiler: e.isActive('spoiler'),
      link: e.isActive('link'),
      quote: e.isActive('blockquote'),
    }),
  })
  const [linking, setLinking] = useState<string | null>(null)
  const input = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (linking !== null) requestAnimationFrame(() => input.current?.focus())
  }, [linking])

  const chain = () => editor.chain().focus()
  const buttons: [LucideIcon, string, boolean, () => void][] = [
    [Bold, 'Жирный', active.bold, () => chain().toggleBold().run()],
    [Italic, 'Курсив', active.italic, () => chain().toggleItalic().run()],
    [Underline, 'Подчёркнутый', active.underline, () => chain().toggleUnderline().run()],
    [Strikethrough, 'Зачёркнутый', active.strike, () => chain().toggleStrike().run()],
    [Code, 'Моноширинный', active.code, () => chain().toggleCode().run()],
    [EyeOff, 'Спойлер', active.spoiler, () => chain().toggleSpoiler().run()],
    [
      Link2,
      'Ссылка',
      active.link,
      () =>
        active.link
          ? chain().extendMarkRange('link').unsetLink().run()
          : setLinking(String(editor.getAttributes('link').href ?? '')),
    ],
    [TextQuote, 'Цитата', active.quote, () => chain().toggleBlockquote().run()],
    [RemoveFormatting, 'Убрать форматирование', false, () => chain().unsetAllMarks().clearNodes().run()],
  ]

  const putLink = () => {
    let href = (linking ?? '').trim()
    if (href && !LINK.test(href)) href = `https://${href}`
    if (href) chain().extendMarkRange('link').setLink({ href }).run()
    setLinking(null)
  }

  return (
    <div className="post-editor-toolbar">
      {linking === null ? (
        buttons.map(([Icon, label, on, act]) => (
          <button
            key={label}
            type="button"
            className={`post-editor-button${on ? ' on' : ''}`}
            aria-label={label}
            aria-pressed={on}
            title={label}
            // the text keeps its selection: the button does not take the focus
            onPointerDown={(e) => e.preventDefault()}
            onClick={act}
          >
            <Icon size={18} strokeWidth={2.2} />
          </button>
        ))
      ) : (
        <form
          className="post-editor-link"
          onSubmit={(e) => {
            e.preventDefault()
            putLink()
          }}
        >
          <input
            ref={input}
            value={linking}
            onChange={(e) => setLinking(e.target.value)}
            placeholder="https://…"
            inputMode="url"
            autoCapitalize="off"
            autoCorrect="off"
            onKeyDown={(e) => e.key === 'Escape' && setLinking(null)}
          />
          <button type="submit" className="post-editor-button on" aria-label="Поставить ссылку">
            <Check size={18} />
          </button>
          <button type="button" className="post-editor-button" aria-label="Отмена" onClick={() => setLinking(null)}>
            <X size={18} />
          </button>
        </form>
      )}
      {end && linking === null && <div className="post-editor-toolbar-end">{end}</div>}
    </div>
  )
}
