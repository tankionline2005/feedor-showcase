import { Loader2, RotateCcw } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, type PostPrompt } from '../api'
import { Sheet } from './Sheet'
import { useToast } from './Toast'

/** «Шаблон запроса»: how «Создать об этом пост для моего канала» asks the AI — the person's own, or the default. */
export function PostPromptSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const toast = useToast()
  const [state, setState] = useState<PostPrompt | null>(null)
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!open) return
    setState(null)
    api.postPrompt().then(
      (p) => {
        setState(p)
        setValue(p.template)
      },
      (e) => toast((e as Error).message),
    )
  }, [open, toast])

  const changed = !!state && value.trim() !== state.template
  const save = async (template: string | null) => {
    setBusy(true)
    try {
      const p = await api.setPostPrompt(template)
      setState(p)
      setValue(p.template)
      toast(p.custom ? 'Шаблон сохранён' : 'Вернули шаблон по умолчанию')
      onClose()
    } catch (e) {
      toast((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Sheet
      open={open}
      onClose={onClose}
      title="Шаблон запроса"
      left={
        <button className="nav-button" onClick={onClose}>
          Отмена
        </button>
      }
      right={
        <button
          className="nav-button"
          style={{ fontWeight: 600 }}
          disabled={!changed || busy || !value.trim()}
          onClick={() => save(value)}
        >
          {busy ? <Loader2 size={18} className="spinner" /> : 'Готово'}
        </button>
      }
    >
      {!state ? (
        <div className="loader">
          <Loader2 size={22} className="spinner" />
        </div>
      ) : (
        <>
          <div className="section">
            <div className="section-header">Что ИИ попросят написать</div>
            <div className="group">
              <textarea
                className="field prompt-field"
                aria-label="Шаблон запроса"
                maxLength={4000}
                rows={7}
                value={value}
                onChange={(e) => setValue(e.target.value)}
              />
            </div>
            <div className="section-footer">
              {'{тема}'} — название темы, {'{номер}'} — её номер: по нему ИИ найдёт пересказ, хронологию и посты темы.
              Если в шаблоне нет ни того, ни другого, тема добавится в конце. Сообщение не отправится само — его можно
              поправить в чате перед отправкой. Шаблон ваш личный и действует в этом проекте.
            </div>
          </div>
          {(state.custom || value.trim() !== state.default) && (
            <div className="section">
              <div className="group">
                <button className="cell accent post-prompt-reset" disabled={busy} onClick={() => save(null)}>
                  <RotateCcw size={17} />
                  Вернуть шаблон по умолчанию
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </Sheet>
  )
}
