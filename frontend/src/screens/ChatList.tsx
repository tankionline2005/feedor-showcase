import { AnimatePresence, motion } from 'framer-motion'
import { ChevronRight, Ellipsis, Loader2, MessageCirclePlus, MessagesSquare } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { api, type ChatList as ChatListT } from '../api'
import { ChatRenameSheet } from '../components/ChatRenameSheet'
import { ActionSheet } from '../components/Sheet'
import { useToast } from '../components/Toast'
import { useMe } from '../hooks/useMe'
import { relative } from '../lib/format'
import { spring } from '../lib/motion'

const shortModel = (id: string) => id.split('/').pop() ?? id

/** AI → «Чат»: the person's chats, the newest first, and a new one. */
export function ChatList({
  onOpen,
  onReloadReady,
}: {
  onOpen: (id: number) => void
  onReloadReady: (fn: () => Promise<void>) => void
}) {
  const toast = useToast()
  const me = useMe()
  const [data, setData] = useState<ChatListT | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  // the chat whose «…» was tapped: kept after the menu closes, so its title stays while the sheet leaves
  const [target, setTarget] = useState<ChatListT['items'][number] | null>(null)
  const [menu, setMenu] = useState(false)
  const [renaming, setRenaming] = useState(false)

  const reload = useCallback(async () => {
    try {
      setData(await api.chats())
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])

  useEffect(() => {
    onReloadReady(reload)
    reload()
  }, [reload]) // eslint-disable-line react-hooks/exhaustive-deps

  const create = async () => {
    setCreating(true)
    try {
      onOpen((await api.newChat()).id)
    } catch (e) {
      toast((e as Error).message)
      setCreating(false)
    }
  }

  const remove = async (id: number) => {
    try {
      await api.deleteChat(id)
      setData((d) => (d ? { ...d, items: d.items.filter((c) => c.id !== id) } : d))
    } catch (e) {
      toast((e as Error).message)
    }
  }

  const rename = async (title: string) => {
    if (!target) return
    try {
      const res = await api.renameChat(target.id, title)
      setData((d) =>
        d ? { ...d, items: d.items.map((c) => (c.id === target.id ? { ...c, title: res.title } : c)) } : d,
      )
      setRenaming(false)
    } catch (e) {
      toast((e as Error).message)
    }
  }

  if (error) return <div className="error-banner">{error}</div>
  if (!data) {
    return (
      <div className="loader">
        <Loader2 size={22} className="spinner" />
      </div>
    )
  }

  return (
    <>
      {data.paused && <div className="error-banner">{data.paused}</div>}
      <div className="section">
        <div className="group">
          <button
            className="cell accent"
            style={{ ['--inset' as string]: '57px' }}
            onClick={create}
            disabled={creating || !data.linked}
          >
            <div className="cell-icon ai-icon">
              {creating ? <Loader2 size={17} className="spinner" /> : <MessageCirclePlus size={17} />}
            </div>
            <div className="cell-main">Новый чат</div>
            {data.model && <span className="cell-value mono-sub">{shortModel(data.model)}</span>}
          </button>
        </div>
        <div className="section-footer">
          {!data.linked
            ? me.ai_own
              ? 'OpenRouter.ai не подключён — новые чаты недоступны.'
              : 'ИИ временно недоступен — новые чаты появятся, как только он вернётся.'
            : 'ИИ видит посты отслеживаемых каналов, темы и статистику вашего канала — и сам ищет, что нужно для ответа.'}
          {data.owner && data.model
            ? ' Модель нового чата меняется в настройках OpenRouter.ai; начатый чат остаётся на своей модели.'
            : ''}
        </div>
      </div>

      {data.items.length === 0 ? (
        <motion.div
          className="empty"
          style={{ paddingTop: 8 }}
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={spring}
        >
          <div className="empty-icon">
            <MessagesSquare size={30} strokeWidth={1.6} />
          </div>
          <div className="empty-title">Чатов пока нет</div>
          <div>Спросите, что писали конкуренты за неделю, о чём сделать пост или как растёт ваш канал</div>
        </motion.div>
      ) : (
        <div className="section">
          <div className="group">
            <AnimatePresence initial={false}>
              {data.items.map((c) => (
                <motion.div
                  key={c.id}
                  className="cell chat-cell"
                  layout="position"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0, transition: { duration: 0.15 } }}
                  transition={spring}
                >
                  <button
                    className="chat-more"
                    onClick={() => {
                      setTarget(c)
                      setMenu(true)
                    }}
                    aria-label={`Действия с чатом «${c.title}»`}
                    title="Переименовать или удалить"
                  >
                    <Ellipsis size={20} />
                  </button>
                  <button className="chat-open" onClick={() => onOpen(c.id)}>
                    <div className="cell-main">
                      <div className="cell-title chat-title">{c.title}</div>
                      <div className="cell-sub chat-last">{c.last || 'ИИ ещё не ответил'}</div>
                    </div>
                    <span className="cell-value chat-when">{relative(c.updated_at)}</span>
                    {c.unread ? (
                      <span className="chat-unread" aria-label="Непрочитанный ответ" />
                    ) : (
                      <ChevronRight size={18} color="var(--text-3)" />
                    )}
                  </button>
                </motion.div>
              ))}
            </AnimatePresence>
          </div>
        </div>
      )}
      <div className="section-footer" style={{ padding: '0 32px 24px' }}>
        {data.owner
          ? 'Ваши чаты видны только вам. Если сотрудники ведут свои чаты — вы их не увидите, но расход на них будет виден в «Расход и остаток».'
          : 'Ваши чаты видны только вам — владелец проекта их не видит.'}
      </div>

      <ActionSheet
        open={menu}
        onClose={() => setMenu(false)}
        title={target?.title}
        actions={[
          { label: 'Переименовать', onClick: () => setRenaming(true) },
          { label: 'Удалить чат', destructive: true, onClick: () => target && remove(target.id) },
        ]}
      />
      <ChatRenameSheet open={renaming} title={target?.title ?? ''} onClose={() => setRenaming(false)} onSave={rename} />
    </>
  )
}
