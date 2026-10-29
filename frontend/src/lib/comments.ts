import type { Post } from '../api'

// posts whose comments were opened from a card: the page shows the card at once, then asks for it anew
const opened = new Map<number, Post>()

export function openedPost(id: number): Post | undefined {
  return opened.get(id)
}

/** The section the page is in, for the comments page to stay in it (the tab bar keeps it lit). */
function currentTab(): string {
  const [first, , third] = location.hash.replace(/^#\/?/, '').split('?')[0].split('/')
  if (first === 'channel' || first === 'channels') return 'channels'
  if (first === 'top' || first === 'ai') return first
  if (first === 'comments' && third) return third
  return 'feed'
}

/** «комменты» on a post card: the post's comments page, #/comments/<post>/<section>. */
export function openComments(post: Post) {
  opened.set(post.id, post)
  location.hash = `/comments/${post.id}/${currentTab()}`
}

/** A comment in Telegram: its own page in a public discussion group, or the post's comments. */
export function commentLink(post: Post, group: string | null, commentId: number): string | null {
  if (group) return `https://t.me/${group}/${commentId}`
  const source = post.source_url ?? post.url
  return source ? `${source}?comment=${commentId}` : null
}
