import { motion } from 'framer-motion'
import { Check, ChevronRight, ChevronsUpDown, ImagePlus, Loader2, Trash2, X } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type CoverSettings } from '../api'
import { Switch } from '../components/Controls'
import { CoverPicture } from '../components/CoverPicture'
import { Choice } from '../components/ScheduleEditor'
import { Segmented } from '../components/Segmented'
import { Sheet } from '../components/Sheet'
import { useToast } from '../components/Toast'
import { useCurrentProject } from '../hooks/useProjects'
import { useWheelScrollX } from '../hooks/useWheelScrollX'
import { spring } from '../lib/motion'

const MODE_TEXT: Record<CoverSettings['mode'], string> = {
  off: 'Не добавлять',
  always: 'Всегда',
  no_photo: 'Если нет фото',
}

export const coverModeLabel = (mode: CoverSettings['mode']) => MODE_TEXT[mode]

const TILE_HEIGHT = 108 // a template's tile in the gallery

/** A cover of the project's style with a sample title: the big preview, and the small one on the rows below. */
export const coverPreviewUrl = (s: CoverSettings, extra: Record<string, string> = {}) =>
  `/api/covers/preview.jpg?${new URLSearchParams({ template: s.template, size: s.size, accent: s.accent, font: s.font, position: s.position, show_channel: String(s.show_channel), v: String(s.version), ...extra })}`

const THUMB = { size: '1:1', title: 'Аа', width: '96' } // the small preview on the «Стиль обложек» row

// the style last seen of each project: its rows show it at once, and the server's answer then takes its place
const styles = new Map<number, CoverSettings>()

/** The project's cover style asked ahead, with its small preview (the owner's «Публикации»): the settings' rows are
 * there at once when they open, not a moment after. */
export function preloadCoverStyle(projectId: number) {
  api.covers().then((s) => {
    styles.set(projectId, s)
    new Image().src = coverPreviewUrl(s, THUMB)
  }, () => {})
}

/** The project's cover style, for the rows that show it elsewhere: asked when `active`, and again by `reload`. */
export function useCoverStyle(active: boolean) {
  const project = useCurrentProject()?.id
  const [s, setS] = useState<CoverSettings | null>(() => (project ? (styles.get(project) ?? null) : null))
  const keep = useCallback(
    (next: CoverSettings) => {
      if (project) styles.set(project, next)
      setS(next)
    },
    [project],
  )
  const reload = useCallback(() => {
    api.covers().then(keep, () => {})
  }, [keep])
  useEffect(() => {
    if (!active) return
    const known = project ? styles.get(project) : undefined
    if (known) setS(known)
    reload()
  }, [active, project, reload])
  return [s, keep, reload] as const
}

/** «Новое: обложки» over the owner's «Публикации», until it is closed or the style is opened from it. */
export function CoversTip({ onClose }: { onClose: () => void }) {
  const [s] = useCoverStyle(true)
  const [open, setOpen] = useState(false)
  return (
    <motion.div
      className="covers-tip"
      initial={{ opacity: 0, y: -8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, height: 0, marginBottom: 0, paddingTop: 0, paddingBottom: 0 }}
      transition={spring}
    >
      <div className="covers-tip-picture">
        {s && <CoverPicture src={coverPreviewUrl(s, { size: '1:1', title: 'Заголовок поста', width: '160' })} ratio="1:1" />}
      </div>
      <div className="covers-tip-text">
        <div className="covers-tip-title">Новое: обложки</div>
        <div>Картинка с заголовком поста в стиле вашего канала — для любого поста, автопостов и рубрик.</div>
        <button className="covers-tip-action" onClick={() => setOpen(true)}>
          Настроить стиль
        </button>
      </div>
      <button className="covers-tip-close" onClick={onClose} aria-label="Закрыть">
        <X size={16} />
      </button>
      <CoversSheet
        open={open}
        onClose={() => {
          setOpen(false)
          onClose()
        }}
      />
    </motion.div>
  )
}

/**
 * «Обложки» where the owner sets up the AI's own posts — «Публикации» and a rubric: whether those posts take a cover
 * (one setting for autoposts and rubrics) and the way to the style, with a small cover of it. For a `.group`.
 */
export function CoverRows({ active, modeLabel = 'Обложка' }: { active: boolean; modeLabel?: string }) {
  const toast = useToast()
  const [s, setS, reload] = useCoverStyle(active)
  const [open, setOpen] = useState(false)
  if (!s) {
    // not come yet: the rows as they will be, their values pulsing — nothing below jumps when it comes
    return (
      <>
        <div className="cell">
          <div className="cell-icon cover-thumb">
            <div className="cover-picture" style={{ aspectRatio: '1' }} />
          </div>
          <div className="cell-main">Стиль обложек</div>
          <span className="cell-value">
            <span className="skeleton-line" style={{ width: 72 }} />
          </span>
          <ChevronRight size={18} color="var(--text-3)" />
        </div>
        <div className="cell">
          <div className="cell-main">{modeLabel}</div>
          <span className="cell-value">
            <span className="skeleton-line" style={{ width: 100 }} />
            <ChevronsUpDown size={15} />
          </span>
        </div>
      </>
    )
  }
  return (
    <>
      <button className="cell" onClick={() => setOpen(true)}>
        <div className="cell-icon cover-thumb">
          <CoverPicture src={coverPreviewUrl(s, THUMB)} ratio="1:1" />
        </div>
        <div className="cell-main">Стиль обложек</div>
        <span className="cell-value">{s.templates.find((t) => t.key === s.template)?.name}</span>
        <ChevronRight size={18} color="var(--text-3)" />
      </button>
      <Choice
        label={modeLabel}
        value={s.modes.indexOf(s.mode)}
        options={s.modes.map((_, i) => i)}
        labels={Object.fromEntries(s.modes.map((m, i) => [i, MODE_TEXT[m]]))}
        onChange={async (i) => {
          setS({ ...s, mode: s.modes[i] })
          try {
            setS(await api.saveCovers({ mode: s.modes[i] }))
          } catch (e) {
            toast((e as Error).message)
          }
        }}
      />
      <CoversSheet
        open={open}
        onClose={() => {
          setOpen(false)
          reload()
        }}
      />
    </>
  )
}

/** «Обложки», the owner's: the project's one cover style — a layout, a size, a colour, a font, where the title
 * goes, the channel's name, a logo — with a live preview; and whether autoposts and rubrics take one. */
export function CoversSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const toast = useToast()
  const [s, setS] = useState<CoverSettings | null>(null)
  const [busy, setBusy] = useState(false)
  const file = useRef<HTMLInputElement>(null)
  const templates = useWheelScrollX<HTMLDivElement>()

  useEffect(() => {
    if (open) api.covers().then(setS, (e) => toast((e as Error).message))
  }, [open, toast])

  const save = async (values: Partial<Pick<CoverSettings, 'mode' | 'template' | 'size' | 'accent' | 'font' | 'position' | 'show_channel'>>) => {
    if (!s) return
    setS({ ...s, ...values })
    try {
      setS(await api.saveCovers(values))
    } catch (e) {
      toast((e as Error).message)
    }
  }

  const upload = async (f: File) => {
    setBusy(true)
    try {
      setS(await api.uploadCoverLogo(f))
    } catch (e) {
      toast((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const preview = s ? coverPreviewUrl(s) : null
  const [rw, rh] = (s?.size ?? '1:1').split(':').map(Number)
  const tileWidth = Math.round((TILE_HEIGHT * rw) / rh)

  return (
    <Sheet
      open={open}
      onClose={onClose}
      title="Обложки"
      right={
        <button className="nav-button" style={{ fontWeight: 600 }} onClick={onClose}>
          Готово
        </button>
      }
    >
      {!s ? (
        <div className="loader">
          <Loader2 size={20} className="spinner" />
        </div>
      ) : (
        <>
          <div className="section">
            <div className="cover-preview">
              <CoverPicture
                // a new frame for another format: Safari on a phone keeps the old frame's shape when only its
                // aspect-ratio changes, and the new picture was cut to it
                key={s.size}
                src={preview!}
                ratio={s.size}
                alt="Пример обложки"
                // as tall as 340 points at most, as wide as the screen at most
                style={{ width: `min(100%, ${(340 * Number(s.size.split(':')[0])) / Number(s.size.split(':')[1])}px)` }}
              />
            </div>
            <div className="section-footer" style={{ textAlign: 'center' }}>
              Обложка — картинка с заголовком поста в стиле вашего канала. Включается на каждом посте отдельно, а для
              автопостинга и рубрик — в их настройках.
            </div>
          </div>

          <div className="section">
            <div className="section-header">Шаблон</div>
            <div className="cover-templates" ref={templates}>
              {s.templates.map((t) => (
                <button
                  key={t.key}
                  className={`cover-template${t.key === s.template ? ' on' : ''}`}
                  onClick={() => save({ template: t.key })}
                  aria-pressed={t.key === s.template}
                  // the tiles in the chosen format, all as tall
                  style={{ width: tileWidth }}
                >
                  <CoverPicture
                    key={s.size}
                    style={{ height: TILE_HEIGHT }}
                    src={`/api/covers/preview.jpg?${new URLSearchParams({ template: t.key, size: s.size, v: String(s.version), title: 'Заголовок поста', width: String(Math.round(tileWidth * 2.2)) })}`}
                    ratio={s.size}
                    lazy
                  />
                  <span>{t.name}</span>
                  {t.key === s.template && (
                    <motion.i className="cover-check" layoutId="cover-check" transition={spring}>
                      <Check size={13} strokeWidth={3} />
                    </motion.i>
                  )}
                </button>
              ))}
            </div>
            <div className="section-footer">Для шаблонов с фото берётся первое фото из источников поста; если фото нет, его место займут тона выбранного цвета.</div>
          </div>

          <div className="section">
            <div className="section-header">Формат</div>
            <div className="schedule-modes">
              <Segmented
                id="cover-size"
                options={s.sizes.map((x) => ({ value: x, label: x === '1:1' ? 'Квадрат' : x === '4:5' ? 'Вертикаль' : 'Широкий' }))}
                value={s.size}
                onChange={(size) => save({ size })}
              />
            </div>
          </div>

          <div className="section">
            <div className="section-header">Цвет</div>
            <div className="cover-swatches">
              {s.accents.map((c) => (
                <button
                  key={c}
                  className={`cover-swatch${c === s.accent ? ' on' : ''}`}
                  style={{ background: c }}
                  aria-label={`Цвет ${c}`}
                  onClick={() => save({ accent: c })}
                />
              ))}
              <label className="cover-swatch custom" aria-label="Свой цвет" style={{ background: s.accents.includes(s.accent) ? undefined : s.accent }}>
                <input type="color" value={s.accent} onChange={(e) => save({ accent: e.target.value })} />
                +
              </label>
            </div>
          </div>

          <div className="section">
            <div className="section-header">Текст</div>
            <div className="schedule-modes">
              <Segmented
                id="cover-position"
                options={[
                  { value: 'top', label: 'Сверху' },
                  { value: 'center', label: 'По центру' },
                  { value: 'bottom', label: 'Снизу' },
                ]}
                value={s.position}
                onChange={(position) => save({ position })}
              />
            </div>
            <div className="group">
              <Choice
                label="Шрифт"
                value={s.fonts.findIndex((f) => f.key === s.font)}
                options={s.fonts.map((_, i) => i)}
                labels={Object.fromEntries(s.fonts.map((f, i) => [i, f.name]))}
                onChange={(i) => save({ font: s.fonts[i].key })}
              />
              <div className="cell">
                <div className="cell-main">Название канала</div>
                <Switch label="Название канала" on={s.show_channel} onChange={(v) => save({ show_channel: v })} />
              </div>
            </div>
          </div>

          <div className="section">
            <div className="section-header">Логотип</div>
            <div className="group">
              <button className="cell accent" onClick={() => file.current?.click()} disabled={busy}>
                {busy ? <Loader2 size={18} className="spinner" /> : <ImagePlus size={19} />}
                <div className="cell-main">{s.has_logo ? 'Заменить логотип' : 'Загрузить логотип'}</div>
              </button>
              {s.has_logo && (
                <button
                  className="cell destructive"
                  onClick={async () => {
                    try {
                      setS(await api.deleteCoverLogo())
                    } catch (e) {
                      toast((e as Error).message)
                    }
                  }}
                >
                  <Trash2 size={18} />
                  Убрать логотип
                </button>
              )}
            </div>
            <input
              ref={file}
              type="file"
              accept="image/png,image/jpeg,image/webp"
              hidden
              onChange={(e) => {
                const f = e.target.files?.[0]
                e.target.value = ''
                if (f) upload(f)
              }}
            />
            <div className="section-footer">PNG с прозрачным фоном смотрится лучше всего. Ставится в правый верхний угол.</div>
          </div>

        </>
      )}
    </Sheet>
  )
}
