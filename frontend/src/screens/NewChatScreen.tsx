import { motion } from 'framer-motion'
import { ChevronLeft, Loader2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../api'
import { Page } from '../components/Page'
import { firstMessage } from './ChatScreen'

/**
 * What the AI is asked from a topic's «Создать об этом пост для моего канала»: the person's template with
 * {тема} and {номер} filled in; a template naming neither gets the topic at its end.
 */
export const postAbout = (template: string, topic: { id: number; title: string }) => {
  if (!template.includes('{тема}') && !template.includes('{номер}'))
    template = `${template.trimEnd()}\n\nТема №{номер}: «{тема}».`
  return template.replaceAll('{тема}', topic.title).replaceAll('{номер}', String(topic.id))
}

let making: Promise<number> | null = null

async function make(): Promise<number> {
  const topicId = Number(new URLSearchParams(location.hash.split('?')[1] ?? '').get('topic'))
  if (!topicId) throw new Error('Не указана тема')
  const [topic, prompt] = await Promise.all([api.topic(topicId), api.postPrompt()])
  const { id } = await api.newChat(topicId)
  await api.renameChat(id, `Пост: ${topic.title}`.slice(0, 80))
  firstMessage(id, postAbout(prompt.template, topic))
  return id
}

/**
 * #/ai/chat/new?topic=<id>, opened from a topic (in the same tab, the topic is a step back):
 * a new chat, named after the topic, whose
 * first message (asking for a post on it) is already written in the field when the chat opens.
 */
export function NewChatScreen({ onBack }: { onBack: () => void }) {
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    // one chat per opening, though the effect may run twice
    making ??= make().finally(() => {
      making = null
    })
    making.then(
      (id) => location.replace(`#/ai/chat/${id}`), // the chat's own address: «Назад» makes no other one
      (e) => setError((e as Error).message),
    )
  }, [])

  return (
    <Page
      title="Чат"
      left={
        <motion.button className="nav-button" onClick={onBack} whileTap={{ opacity: 0.4 }} style={{ paddingLeft: 0 }}>
          <ChevronLeft size={30} strokeWidth={2.2} style={{ marginRight: -4 }} />
          AI
        </motion.button>
      }
    >
      {error ? (
        <div className="error-banner">{error}</div>
      ) : (
        <div className="loader">
          <Loader2 size={22} className="spinner" />
        </div>
      )}
    </Page>
  )
}
