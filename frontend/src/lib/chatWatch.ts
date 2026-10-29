/**
 * The chats whose answer the browser waits on, so that «ИИ ответил» shows wherever the person is on the
 * site (see ChatNotice). Kept in localStorage, for every tab: a chat may be started in a new tab (a post
 * for the channel, from a topic) while the person goes on reading in the first one.
 */
const KEY = 'chat-watch'
const KEEP = 30 * 60_000 // an answer is not waited on longer than this

export type Watch = { at: number; done?: number }

export function watched(): Record<string, Watch> {
  try {
    const all = JSON.parse(localStorage.getItem(KEY) || '{}') as Record<string, Watch>
    const now = Date.now()
    return Object.fromEntries(Object.entries(all).filter(([, w]) => w && now - w.at < KEEP))
  } catch {
    return {}
  }
}

function save(all: Record<string, Watch>) {
  try {
    localStorage.setItem(KEY, JSON.stringify(all))
  } catch {
    /* storage unavailable: only this page's answer is lost to the notice */
  }
}

/** The AI began an answer in this chat: tell when it is done. */
export function watchChat(id: number) {
  const all = watched()
  if (all[id] && !all[id].done) return
  all[id] = { at: Date.now() }
  save(all)
  window.dispatchEvent(new Event('chat-watch'))
}

export function markDone(id: number) {
  const all = watched()
  if (all[id] && !all[id].done) {
    all[id].done = Date.now()
    save(all)
  }
}
