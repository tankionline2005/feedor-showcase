import { AnimatePresence, motion } from 'framer-motion'
import { Check, ChevronRight, Loader2, Newspaper, Plus, Sparkles, Trash2 } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type Channel, type Rubric, type RubricsState } from '../api'
import { Alert } from '../components/Alert'
import { Switch } from '../components/Controls'
import { ImportanceSheet, importanceLabel } from '../components/ImportanceSheet'
import { Choice, ScheduleEditor } from '../components/ScheduleEditor'
import { Segmented } from '../components/Segmented'
import { Sheet } from '../components/Sheet'
import { TgText } from '../components/TgText'
import { useToast } from '../components/Toast'
import { CoverRows } from './CoversSheet'
import { plural, whenLabel } from '../lib/format'
import { spring } from '../lib/motion'

const PERIOD_TEXT = (h: number) =>
  h === 0
    ? 'С прошлого выпуска'
    : h < 24
      ? `За ${h} ${plural(h, 'час', 'часа', 'часов')}`
      : h === 24
        ? 'За сутки'
        : `За ${h / 24} ${plural(h / 24, 'день', 'дня', 'дней')}`

/** «Рубрики», the owner's: the project's rubrics, each with its switch; a new one from a preset. */
export function RubricsSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const toast = useToast()
  const [state, setState] = useState<RubricsState | null>(null)
  const [editing, setEditing] = useState<Rubric | null>(null)
  const [choosing, setChoosing] = useState(false)

  const load = useCallback(async () => {
    try {
      setState(await api.rubrics())
    } catch (e) {
      toast((e as Error).message)
    }
  }, [toast])

  useEffect(() => {
    if (open) load()
  }, [open, load])

  const put = (next: Rubric) =>
    setState((s) => s && { ...s, items: s.items.map((r) => (r.id === next.id ? next : r)) })

  const create = async (preset: string) => {
    setChoosing(false)
    try {
      const made = await api.createRubric(preset)
      setState((s) => s && { ...s, items: [...s.items, made] })
      setEditing(made)
    } catch (e) {
      toast((e as Error).message)
    }
  }

  return (
    <Sheet
      open={open}
      onClose={onClose}
      title="Рубрики"
      right={
        <button className="nav-button" style={{ fontWeight: 600 }} onClick={onClose}>
          Готово
        </button>
      }
    >
      <div className="section">
        <div className="section-footer" style={{ paddingTop: 0 }}>
          Рубрика — пост, который ИИ сам пишет по расписанию: «Итоги дня», утренний дайджест, главное за неделю. Вы
          решаете, из чего его собирать, как писать и выходит ли он сразу в канал или сначала в «Предложения ИИ».
        </div>
      </div>
      {state === null ? (
        <div className="loader">
          <Loader2 size={20} className="spinner" />
        </div>
      ) : (
        <div className="section">
          <div className="group">
            <AnimatePresence initial={false}>
              {state.items.map((r) => (
                <motion.div
                  key={r.id}
                  className="cell"
                  layout="position"
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: 'auto' }}
                  exit={{ opacity: 0, height: 0 }}
                  transition={spring}
                >
                  <button className="rubric-row" onClick={() => setEditing(r)}>
                    <div className="cell-icon" style={{ background: r.enabled ? 'var(--indigo)' : 'var(--text-3)' }}>
                      <Newspaper size={16} />
                    </div>
                    <div className="cell-main">
                      <div className="cell-title">{r.name}</div>
                      <div className="cell-sub">
                        {r.schedule_summary} · {r.mode === 'auto' ? 'сразу в канал' : 'в предложения'}
                      </div>
                    </div>
                  </button>
                  <Switch
                    label={`Рубрика «${r.name}»`}
                    on={r.enabled}
                    onChange={async (v) => {
                      try {
                        put(await api.saveRubric(r.id, { enabled: v }))
                      } catch (e) {
                        toast((e as Error).message)
                      }
                    }}
                  />
                </motion.div>
              ))}
              {state.items.length < state.most && (
                <motion.button key="add" layout="position" className="cell accent" onClick={() => setChoosing(true)}>
                  <Plus size={20} />
                  <div className="cell-main">Новая рубрика</div>
                </motion.button>
              )}
            </AnimatePresence>
          </div>
        </div>
      )}

      <Sheet open={choosing} onClose={() => setChoosing(false)} title="Новая рубрика">
        <div className="section">
          <div className="section-header">Начать с заготовки</div>
          <div className="group">
            {state?.presets.map((p) => (
              <button key={p.key} className="cell" onClick={() => create(p.key)}>
                <div className="cell-main">{p.name}</div>
                <ChevronRight size={18} color="var(--text-3)" />
              </button>
            ))}
          </div>
          <div className="section-footer">Заготовку можно поменять как угодно — это только начало.</div>
        </div>
      </Sheet>

      {state && editing && (
        <RubricEditor
          rubric={state.items.find((r) => r.id === editing.id) ?? editing}
          state={state}
          onClose={() => setEditing(null)}
          onChange={put}
          onDeleted={(id) => {
            setEditing(null)
            setState((s) => s && { ...s, items: s.items.filter((r) => r.id !== id) })
          }}
        />
      )}
    </Sheet>
  )
}

const zone = () => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'Europe/Moscow'
  } catch {
    return 'Europe/Moscow'
  }
}

/** One rubric's settings, saved as they change; «Сделать пример сейчас» writes an issue to look at. */
function RubricEditor({
  rubric: r,
  state,
  onClose,
  onChange,
  onDeleted,
}: {
  rubric: Rubric
  state: RubricsState
  onClose: () => void
  onChange: (r: Rubric) => void
  onDeleted: (id: number) => void
}) {
  const toast = useToast()
  const [texts, setTexts] = useState({ name: r.name, prompt: r.prompt, header: r.header, footer: r.footer, words: r.words.join(', ') })
  const [importance, setImportance] = useState(false)
  const [channels, setChannels] = useState(false)
  const [example, setExample] = useState<string | null>(null)
  const [writing, setWriting] = useState(false)
  const [confirm, setConfirm] = useState(false)
  const timer = useRef(0)

  useEffect(() => {
    setTexts({ name: r.name, prompt: r.prompt, header: r.header, footer: r.footer, words: r.words.join(', ') })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [r.id])
  useEffect(() => () => window.clearTimeout(timer.current), [])

  const save = async (values: Parameters<typeof api.saveRubric>[1]) => {
    try {
      onChange(await api.saveRubric(r.id, values))
    } catch (e) {
      toast((e as Error).message)
    }
  }
  const typed = (key: keyof typeof texts, value: string) => {
    setTexts((t) => ({ ...t, [key]: value }))
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => save({ [key]: value }), 700)
  }

  const makeExample = async () => {
    setWriting(true)
    try {
      setExample((await api.rubricExample(r.id)).text)
    } catch (e) {
      toast((e as Error).message)
    } finally {
      setWriting(false)
    }
  }

  const topics = r.source === 'topics'

  return (
    <Sheet
      open
      onClose={onClose}
      title={r.name}
      right={
        <button className="nav-button" style={{ fontWeight: 600 }} onClick={onClose}>
          Готово
        </button>
      }
    >
      <div className="section">
        <div className="group">
          <div className="cell">
            <input
              className="cell-input"
              style={{ flex: 1, textAlign: 'left', color: 'var(--text)', fontWeight: 600 }}
              aria-label="Название рубрики"
              value={texts.name}
              maxLength={64}
              onChange={(e) => typed('name', e.target.value)}
            />
          </div>
          <div className="cell">
            <div className="cell-main">Рубрика включена</div>
            <Switch label="Рубрика включена" on={r.enabled} onChange={(v) => save({ enabled: v })} />
          </div>
        </div>
        <div className="section-footer">
          {r.last_error
            ? `Последний выпуск не вышел: ${r.last_error}`
            : r.last_run_at
              ? `Последний выпуск — ${whenLabel(r.last_run_at)}.`
              : r.enabled
                ? 'Первый выпуск выйдет по расписанию.'
                : 'Пока выключена: настройте её и включите.'}
        </div>
      </div>

      <ScheduleEditor
        info={r}
        mode="rubric"
        postsWord="постов"
        onSave={async (schedule) => onChange(await api.saveRubric(r.id, { schedule: { ...schedule, tz: zone() } }))}
      />

      <div className="section">
        <div className="section-header">Из чего собирать</div>
        <div className="schedule-modes">
          <Segmented
            id={`rubric-source-${r.id}`}
            options={[
              { value: 'topics', label: 'Темы' },
              { value: 'top_posts', label: 'Популярные посты' },
            ]}
            value={r.source}
            onChange={(source) => save({ source })}
          />
        </div>
        <div className="group">
          <Choice
            label="Период"
            value={r.period_hours}
            options={state.periods}
            labels={Object.fromEntries(state.periods.map((h) => [h, PERIOD_TEXT(h)]))}
            onChange={(period_hours) => save({ period_hours })}
          />
          <Choice
            label={topics ? 'Сколько тем' : 'Сколько постов'}
            value={r.max_items}
            options={state.item_counts}
            labels={Object.fromEntries(state.item_counts.map((n) => [n, `до ${n}`]))}
            onChange={(max_items) => save({ max_items })}
          />
          {topics && (
            <button className="cell" onClick={() => setImportance(true)}>
              <div className="cell-main">Минимальная важность</div>
              <span className="cell-value">{importanceLabel(r.min_importance)}</span>
              <ChevronRight size={18} color="var(--text-3)" />
            </button>
          )}
          <button className="cell" onClick={() => setChannels(true)}>
            <div className="cell-main">Каналы</div>
            <span className="cell-value">
              {r.channel_ids.length
                ? `${r.channel_ids.length} ${plural(r.channel_ids.length, 'канал', 'канала', 'каналов')}`
                : 'Все'}
            </span>
            <ChevronRight size={18} color="var(--text-3)" />
          </button>
          <div className="cell">
            <input
              className="cell-input"
              style={{ flex: 1, textAlign: 'left', color: 'var(--text)' }}
              placeholder="Только со словами (через запятую)"
              aria-label="Только со словами"
              value={texts.words}
              onChange={(e) => typed('words', e.target.value)}
            />
          </div>
        </div>
        <div className="section-footer">
          {topics
            ? 'Темы за период — самые важные первыми. «С прошлого выпуска» — всё, что появилось или обновилось с прошлого раза.'
            : 'Самые просматриваемые посты каналов проекта за период.'}{' '}
          Если под условия ничего не подойдёт, выпуск не выйдет.
        </div>
      </div>

      <div className="section">
        <div className="section-header">Как писать</div>
        <div className="group">
          <textarea
            className="field prompt-field"
            rows={4}
            aria-label="Как писать выпуск"
            placeholder="Например: 5 главных новостей списком, каждая одной строкой, с эмодзи"
            value={texts.prompt}
            onChange={(e) => typed('prompt', e.target.value)}
          />
        </div>
        <div className="section-footer">ИИ пишет в стиле вашего канала и учитывает правки в проекте.</div>
      </div>

      <div className="section">
        <div className="section-header">Шапка и подпись</div>
        <div className="group">
          <textarea
            className="field"
            rows={2}
            style={{ minHeight: 0 }}
            aria-label="Шапка выпуска"
            placeholder="Шапка — например: Итоги дня"
            value={texts.header}
            onChange={(e) => typed('header', e.target.value)}
          />
          <textarea
            className="field"
            rows={2}
            style={{ minHeight: 0 }}
            aria-label="Подпись выпуска"
            placeholder="Подпись — например: @мой_канал"
            value={texts.footer}
            onChange={(e) => typed('footer', e.target.value)}
          />
        </div>
        <div className="section-footer">Одинаковые в каждом выпуске: шапка — жирным первой строкой, подпись — в конце.</div>
      </div>

      <div className="section">
        <div className="section-header">Выпуск</div>
        <div className="schedule-modes">
          <Segmented
            id={`rubric-mode-${r.id}`}
            options={[
              { value: 'suggest', label: 'В предложения' },
              { value: 'auto', label: 'Сразу в канал' },
            ]}
            value={r.mode}
            onChange={(mode) => save({ mode })}
          />
        </div>
        <div className="group">
          <div className="cell">
            <div className="cell-main">Фото из источников</div>
            <Switch
              label="Фото из источников"
              on={r.picture === 'source'}
              onChange={(v) => save({ picture: v ? 'source' : 'none' })}
            />
          </div>
          <CoverRows active />
        </div>
        <div className="section-footer">
          {r.mode === 'auto'
            ? 'Готовый выпуск бот сразу опубликует в канале проекта.'
            : 'Готовый выпуск появится в «Предложениях ИИ» — его можно поправить и опубликовать.'}{' '}
          Обложка и её стиль — общие для всех рубрик и автопостинга.
        </div>
        <div className="prompt-actions">
          <button className="button small" onClick={makeExample} disabled={writing}>
            {writing ? (
              <>
                <Loader2 size={16} className="spinner" /> ИИ пишет…
              </>
            ) : (
              <>
                <Sparkles size={16} /> Сделать пример сейчас
              </>
            )}
          </button>
        </div>
      </div>

      <div className="section">
        <div className="group">
          <button className="cell destructive" onClick={() => setConfirm(true)}>
            <Trash2 size={18} />
            Удалить рубрику
          </button>
        </div>
      </div>

      <ImportanceSheet
        open={importance}
        title="Минимальная важность"
        what="выпуск"
        value={r.min_importance}
        onClose={() => setImportance(false)}
        onChange={(min_importance) => save({ min_importance })}
      />
      <ChannelsPicker
        open={channels}
        chosen={r.channel_ids}
        onClose={() => setChannels(false)}
        onChange={(channel_ids) => save({ channel_ids })}
      />
      <Sheet open={example !== null} onClose={() => setExample(null)} title="Пример выпуска">
        {example && (
          <div className="section">
            <div className="group rubric-example">
              <TgText html={example} className="post-text" />
            </div>
            <div className="section-footer">
              Только пример: он нигде не сохранён и не опубликован. Каждый выпуск ИИ пишет заново по свежим данным.
            </div>
          </div>
        )}
      </Sheet>
      <Alert
        open={confirm}
        title={`Удалить рубрику «${r.name}»?`}
        text="Вышедшие и запланированные выпуски останутся, неопубликованные предложения удалятся."
        buttons={[
          {
            label: 'Удалить',
            destructive: true,
            onClick: async () => {
              setConfirm(false)
              try {
                await api.deleteRubric(r.id)
                onDeleted(r.id)
              } catch (e) {
                toast((e as Error).message)
              }
            },
          },
          { label: 'Отмена', onClick: () => setConfirm(false) },
        ]}
        onClose={() => setConfirm(false)}
      />
    </Sheet>
  )
}

/** Which channels a rubric takes from: all, or the ones ticked. */
function ChannelsPicker({
  open,
  chosen,
  onClose,
  onChange,
}: {
  open: boolean
  chosen: number[]
  onClose: () => void
  onChange: (ids: number[]) => void
}) {
  const [list, setList] = useState<Channel[] | null>(null)
  useEffect(() => {
    if (open) api.channels().then((r) => setList(r.items.filter((c) => !c.own)), () => setList([]))
  }, [open])
  const toggle = (id: number) => onChange(chosen.includes(id) ? chosen.filter((x) => x !== id) : [...chosen, id])
  return (
    <Sheet
      open={open}
      onClose={onClose}
      title="Каналы"
      right={
        <button className="nav-button" style={{ fontWeight: 600 }} onClick={onClose}>
          Готово
        </button>
      }
    >
      <div className="section">
        <div className="group">
          <button className="cell" onClick={() => onChange([])}>
            <div className="cell-main">Все каналы проекта</div>
            {!chosen.length && <Check size={19} strokeWidth={2.6} color="var(--accent)" />}
          </button>
        </div>
      </div>
      <div className="section">
        {list === null ? (
          <div className="loader">
            <Loader2 size={20} className="spinner" />
          </div>
        ) : (
          <div className="group">
            {list.map((c) => (
              <button key={c.id} className="cell" onClick={() => toggle(c.id)}>
                <div className="cell-main">{c.title || c.link}</div>
                {chosen.includes(c.id) && <Check size={19} strokeWidth={2.6} color="var(--accent)" />}
              </button>
            ))}
          </div>
        )}
        <div className="section-footer">Отметьте каналы, из которых брать материал для рубрики.</div>
      </div>
    </Sheet>
  )
}
