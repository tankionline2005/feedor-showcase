import { Loader2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, type SmartState } from '../api'
import { Switch } from '../components/Controls'
import { Sheet } from '../components/Sheet'
import { useToast } from '../components/Toast'

interface Props {
  open: boolean
  state: SmartState | null
  onClose: () => void
  onChange: (state: SmartState) => void
}

/** «AI» in the feed: the smart feed on or off and its prompt (its model and what it costs: in the site's
 * settings, OpenRouter.ai; the text on pictures is read by OCR, always). */
export function AiFeedSheet({ open, state, onClose, onChange }: Props) {
  const toast = useToast()
  const [prompt, setPrompt] = useState('')
  const [busy, setBusy] = useState<string | null>(null)

  useEffect(() => {
    if (open && state) setPrompt(state.prompt)
  }, [open, state?.prompt]) // eslint-disable-line react-hooks/exhaustive-deps

  const run = async (what: string, call: () => Promise<SmartState>, done?: string) => {
    setBusy(what)
    try {
      onChange(await call())
      if (done) toast(done)
    } catch (e) {
      toast((e as Error).message)
    } finally {
      setBusy(null)
    }
  }

  const s = state
  const dirty = !!s && prompt.trim() !== s.prompt.trim()
  const isDefault = !!s && prompt.trim() === s.default_prompt.trim()

  return (
    <>
      <Sheet
        open={open}
        onClose={onClose}
        title="Умная лента"
        right={
          <button className="nav-button" style={{ fontWeight: 600 }} onClick={onClose}>
            Готово
          </button>
        }
      >
        {!s && (
          <div className="loader">
            <Loader2 size={22} className="spinner" />
          </div>
        )}
        {s?.paused && <div className="error-banner">{s.paused}</div>}

        {s && s.editable && (
          <>
            <div className="section">
              <div className="group">
                <div className="cell">
                  <div className="cell-main">Умная лента</div>
                  {busy === 'enabled' && <Loader2 size={18} className="spinner" />}
                  <Switch
                    label="Умная лента"
                    on={s.enabled}
                    onChange={(v) =>
                      run('enabled', () => api.saveSmart({ enabled: v }), v ? 'Умная лента включена' : undefined)
                    }
                  />
                </div>
              </div>
              <div className="section-footer">
                ИИ читает новые посты и оставляет в ленте только подходящие. Лента общая для проекта: пока она включена,
                сотрудники видят её же.
              </div>
            </div>

            <div className="section">
              <div className="section-header">Какие посты показывать</div>
              <div className="group">
                <textarea
                  className="field prompt-field"
                  rows={7}
                  spellCheck={false}
                  aria-label="Промпт умной ленты"
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                />
              </div>
              <div className="section-footer">Расскажите о том, какие посты хотите видеть, а какие отсеивать.</div>
              <div className="prompt-actions">
                {dirty && (
                  <button
                    className="button small"
                    disabled={!prompt.trim() || busy !== null}
                    onClick={() => run('prompt', () => api.saveSmart({ prompt }), 'Промпт сохранён')}
                  >
                    {busy === 'prompt' ? (
                      <Loader2 size={18} className="spinner" style={{ color: '#fff' }} />
                    ) : (
                      'Сохранить'
                    )}
                  </button>
                )}
                {!isDefault && (
                  <button
                    className="button small plain"
                    disabled={busy !== null}
                    onClick={() => run('reset', () => api.saveSmart({ reset_prompt: true }), 'Промпт по умолчанию')}
                  >
                    Вернуть по умолчанию
                  </button>
                )}
              </div>
            </div>
          </>
        )}

        {s && !s.editable && (
          <>
            <div className="section">
              <div className="group">
                <div className="cell">
                  <div className="cell-main">Умная лента проекта</div>
                  <span className="cell-value">{s.enabled ? 'включена' : 'выключена'}</span>
                </div>
              </div>
              <div className="section-footer">
                Её настраивает владелец проекта. Пока она включена, вы видите в ленте только подходящие посты; когда
                выключена — все посты проекта.
              </div>
            </div>
            <div className="section">
              <div className="section-header">Промпт проекта</div>
              <div className="group">
                <div className="prompt-readonly">{s.prompt}</div>
              </div>
            </div>
          </>
        )}
      </Sheet>
    </>
  )
}
