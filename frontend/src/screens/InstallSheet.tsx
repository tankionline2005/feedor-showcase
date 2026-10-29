import { Check, Compass, EllipsisVertical, Share, SquarePlus } from 'lucide-react'
import type { ReactNode } from 'react'
import { Sheet } from '../components/Sheet'
import { useToast } from '../components/Toast'
import { isInAppBrowser, isIOS, usePwaInstall } from '../hooks/usePwa'
import { siteName } from '../lib/site'

/** How to put the app on the home screen, for the browser the user is in. */
export function InstallSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const pwa = usePwaInstall()
  const toast = useToast()
  const ios = isIOS()

  const install = async () => {
    if (await pwa.install()) {
      toast('Приложение установлено')
      onClose()
    }
  }

  return (
    <Sheet
      open={open}
      onClose={onClose}
      title="На экран «Домой»"
      right={
        <button className="nav-button" style={{ fontWeight: 600 }} onClick={onClose}>
          Готово
        </button>
      }
    >
      <div className="install-hero">
        <img src="/icons/icon-192.png" alt="" width={72} height={72} />
        <div className="hero-sub">
          {siteName()} откроется как отдельное приложение — на весь экран, без панелей браузера.
        </div>
      </div>

      {pwa.canPrompt ? (
        <div className="section">
          <button className="button" onClick={install}>
            Установить
          </button>
        </div>
      ) : (
        <div className="section">
          <div className="group">
            {isInAppBrowser() && (
              <Step icon={<Compass size={18} />} n={0}>
                Откройте эту страницу в {ios ? 'Safari' : 'Chrome'} — из встроенного браузера установить нельзя.
              </Step>
            )}
            {ios ? (
              <>
                <Step icon={<Share size={18} />} n={1}>
                  Нажмите «Поделиться» в панели браузера.
                </Step>
                <Step icon={<SquarePlus size={18} />} n={2}>
                  Выберите «На экран „Домой“» — пролистайте список, если пункта не видно.
                </Step>
                <Step icon={<Check size={18} />} n={3}>
                  Нажмите «Добавить». Иконка появится на экране «Домой».
                </Step>
              </>
            ) : (
              <>
                <Step icon={<EllipsisVertical size={18} />} n={1}>
                  Откройте меню браузера (три точки).
                </Step>
                <Step icon={<SquarePlus size={18} />} n={2}>
                  Выберите «Установить приложение» или «Добавить на главный экран».
                </Step>
              </>
            )}
          </div>
        </div>
      )}
    </Sheet>
  )
}

function Step({ icon, n, children }: { icon: ReactNode; n: number; children: ReactNode }) {
  return (
    <div
      className="cell"
      style={{ alignItems: 'flex-start', paddingTop: 12, paddingBottom: 12, ['--inset' as string]: '57px' }}
    >
      <div className="cell-icon" style={{ background: 'var(--accent)' }} aria-label={n ? `Шаг ${n}` : undefined}>
        {icon}
      </div>
      <div className="cell-main" style={{ fontSize: 15, lineHeight: 1.35, whiteSpace: 'normal' }}>
        {children}
      </div>
    </div>
  )
}
