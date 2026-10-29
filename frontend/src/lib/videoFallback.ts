import { api, type Me, type Post } from '../api'

/** What the person can do when the video does not play here: get the post from the bot, or open it in Telegram. */
export interface VideoFallback {
  send?: () => Promise<unknown>
  link?: string | null
}

/** The way to Telegram for a post's video, as the post card offers it: the bot sends it to the person's own
 * Telegram (a staff member once theirs is linked), or a plain link. */
export function videoFallback(post: Post, me: Me): VideoFallback {
  const member = me.role === 'member'
  const toMe = !member || Boolean(me.member?.telegram)
  return {
    send: post.sendable && toMe && me.role !== 'admin' ? () => api.sendOriginal(post.id) : undefined,
    link: member ? (post.source_url ?? post.url) : post.url,
  }
}
