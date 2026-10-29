import { AnimatePresence, motion } from 'framer-motion'
import { ChevronLeft, Loader2, Plus, Trash2, UsersRound } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../api'
import { Avatar } from '../components/Avatar'
import { Page } from '../components/Page'
import { SwipeRow } from '../components/SwipeRow'
import { useToast } from '../components/Toast'
import { reloadGroups, setGroups, useGroups } from '../hooks/useGroups'
import { useMouse } from '../hooks/useMedia'
import { plural } from '../lib/format'
import { spring } from '../lib/motion'
import { openIntro, useFirstVisit } from '../lib/intros'

const groupName = (g: { title: string | null; username: string | null; chat_id: number }) =>
  g.title || (g.username ? `@${g.username}` : `Группа ${g.chat_id}`)

/** «Группы проекта»: the project's groups the bot sends posts to, besides the private chat. */
export function MyGroups({ onBack }: { onBack: () => void }) {
  useFirstVisit('groups')
  const state = useGroups()
  const [link, setLink] = useState('')
  const [busy, setBusy] = useState<string | number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const toast = useToast()
  const mouse = useMouse()

  // fresh on every visit: the user may have just added the bot to a group
  useEffect(() => {
    reloadGroups()
  }, [])

  const add = async (body: { link?: string; chat_id?: number }, key: string | number) => {
    setBusy(key)
    setError(null)
    try {
      const next = await api.addGroup(body)
      setGroups(next)
      setLink('')
      toast('Группа добавлена')
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(null)
    }
  }

  const remove = async (chatId: number) => {
    try {
      setGroups(await api.deleteGroup(chatId))
    } catch (e) {
      toast((e as Error).message)
    }
  }

  const bot = state?.bot_username ? `@${state.bot_username}` : 'наш бот'
  const items = state?.items ?? []
  const full = state ? items.length >= state.limit : false

  return (
    <Page
      onHelp={() => openIntro('groups')}
      title="Группы проекта"
      onRefresh={reloadGroups}
      left={
        <motion.button className="nav-button" onClick={onBack} whileTap={{ opacity: 0.4 }} style={{ paddingLeft: 0 }}>
          <ChevronLeft size={30} strokeWidth={2.2} style={{ marginRight: -4 }} />
          Настройки
        </motion.button>
      }
    >
      <div className="section">
        <div className="section-footer" style={{ paddingTop: 0 }}>
          Бот может пересылать посты не только вам, но и в группы проекта — например, в чат с редакторами. У каждого
          проекта свои группы, до {state?.limit ?? 3}.
          <ol style={{ margin: '8px 0 0', paddingLeft: 20 }}>
            <li>
              Добавьте{' '}
              {state?.bot_username ? (
                <a href={`https://t.me/${state.bot_username}?startgroup=feedor`} target="_blank" rel="noreferrer">
                  {bot}
                </a>
              ) : (
                bot
              )}{' '}
              в свою группу.
            </li>
            <li>Добавьте группу здесь: публичную — по ссылке, закрытую — из списка «Группы с ботом» ниже.</li>
          </ol>
        </div>
      </div>

      {items.length > 0 && (
        <>
          <div className="section-header" style={{ marginLeft: 16 }}>
            {items.length} из {state?.limit}
          </div>
          <div className="section">
            <div className="group">
              <AnimatePresence initial={false}>
                {items.map((g) => (
                  <motion.div key={g.chat_id} layout="position" exit={{ opacity: 0, height: 0 }} transition={spring}>
                    <SwipeRow
                      actions={[
                        { label: 'Удалить', icon: Trash2, color: 'var(--red)', onClick: () => remove(g.chat_id) },
                      ]}
                    >
                      <div className="cell" style={{ ['--inset' as string]: '62px' }}>
                        <Avatar title={groupName(g)} seed={g.chat_id} size={34} />
                        <div className="cell-main">
                          <div className="cell-title">{groupName(g)}</div>
                          <div className="cell-sub" style={g.bot_removed ? { color: 'var(--red)' } : undefined}>
                            {g.bot_removed
                              ? `${bot} удалён из группы — добавьте его снова`
                              : g.username
                                ? `@${g.username}`
                                : 'закрытая группа'}
                          </div>
                        </div>
                      </div>
                    </SwipeRow>
                  </motion.div>
                ))}
              </AnimatePresence>
            </div>
            <div className="section-footer">
              {mouse ? 'Наведите курсор на группу, чтобы удалить её.' : 'Смахните группу влево, чтобы удалить её.'} В
              ленте и топе кнопка отправки поста предложит выбрать: личные сообщения или одна из групп.
            </div>
          </div>
        </>
      )}

      {full ? (
        <div className="section">
          <div className="section-footer">
            В проекте можно добавить не больше {state?.limit} {plural(state?.limit ?? 0, 'группы', 'групп', 'групп')}.
          </div>
        </div>
      ) : (
        <>
          <div className="section-header" style={{ marginLeft: 16 }}>
            Добавить по ссылке
          </div>
          <div className="section">
            <div className="group">
              <input
                className="field"
                placeholder="t.me/my_group или @my_group"
                autoCapitalize="none"
                autoComplete="off"
                value={link}
                onChange={(e) => setLink(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && link.trim() && add({ link }, 'link')}
              />
              <button
                className="cell accent"
                disabled={!link.trim() || busy !== null}
                onClick={() => add({ link }, 'link')}
              >
                {busy === 'link' ? <Loader2 size={18} className="spinner" /> : <Plus size={18} />}
                Добавить
              </button>
            </div>
            <div className="section-footer" style={error ? { color: 'var(--red)' } : undefined}>
              {error ?? 'Для публичной группы. Добавить можно только группу, в которой вы состоите.'}
            </div>
          </div>

          <div className="section-header" style={{ marginLeft: 16 }}>
            Группы с ботом
          </div>
          <div className="section">
            {state && state.found.length > 0 ? (
              <div className="group">
                {state.found.map((g) => (
                  <div className="cell" key={g.chat_id} style={{ ['--inset' as string]: '62px' }}>
                    <Avatar title={groupName(g)} seed={g.chat_id} size={34} />
                    <div className="cell-main">
                      <div className="cell-title">{groupName(g)}</div>
                      <div className="cell-sub">{g.username ? `@${g.username}` : 'закрытая группа'}</div>
                    </div>
                    <button
                      className="pill-button"
                      disabled={busy !== null}
                      onClick={() => add({ chat_id: g.chat_id }, g.chat_id)}
                    >
                      {busy === g.chat_id ? <Loader2 size={16} className="spinner" /> : 'Добавить'}
                    </button>
                  </div>
                ))}
              </div>
            ) : (
              <div className="empty" style={{ padding: '16px 16px 8px' }}>
                <div className="empty-icon">
                  <UsersRound size={28} strokeWidth={1.6} />
                </div>
                <div>Здесь появятся группы, в которые вы добавите {bot}</div>
              </div>
            )}
          </div>
        </>
      )}
    </Page>
  )
}
