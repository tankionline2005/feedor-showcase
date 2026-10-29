import { AnimatePresence, motion } from 'framer-motion'
import { ChevronLeft, Hand, Loader2, LogOut } from 'lucide-react'
import qrcode from 'qrcode-generator'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, type Device } from '../api'
import { DeviceIcon } from '../components/DeviceIcon'
import { Page } from '../components/Page'
import { QrIcon } from '../components/Qr'
import { QrConfirmSheet } from '../components/QrConfirmSheet'
import { QrScanner } from '../components/QrScanner'
import { ActionSheet } from '../components/Sheet'
import { SwipeRow } from '../components/SwipeRow'
import { useToast } from '../components/Toast'
import { useMouse } from '../hooks/useMedia'
import { longDate, relative, time } from '../lib/format'
import { spring } from '../lib/motion'
import { siteName } from '../lib/site'

/** The code of "#/qr/<code>", the address of a login page's QR code. */
const linkedToken = () => /^#\/qr\/([\w-]+)/.exec(location.hash)?.[1] ?? null

/** A phone or a tablet with a camera the site may open: it scans a computer's QR code. */
const canScan = () =>
  !window.matchMedia('(hover: hover) and (pointer: fine)').matches && Boolean(navigator.mediaDevices)

/**
 * Settings → «Устройства», as Telegram's: a computer signs in by a QR code this phone scans; this device and the
 * others the person is signed in on, any of them signed out from here.
 */
export function Devices({ onBack }: { onBack: () => void }) {
  const toast = useToast()
  const mouse = useMouse()
  const [state, setState] = useState<{ current: Device | null; others: Device[] } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [scanning, setScanning] = useState(false)
  // "#/qr/<code>": a login page's QR code opened by the phone's own camera (this page may be open already)
  const [token, setToken] = useState<string | null>(linkedToken)
  const [picked, setPicked] = useState<Device | null>(null)
  const [endingAll, setEndingAll] = useState(false)
  const scan = canScan()

  const load = useCallback(async () => {
    try {
      const next = await api.devices()
      setState(next)
      setError(null)
      return next
    } catch (e) {
      setError((e as Error).message)
      return null
    }
  }, [])

  // a computer confirmed by its QR code is a device once it picks its sign-in up (its next check, a second or
  // two later): the list looks again every second until it is there
  const waiting = useRef(0)
  const awaitNewDevice = useCallback(async () => {
    const known = new Set(state?.others.map((d) => d.id) ?? [])
    const turn = ++waiting.current
    for (let i = 0; i < 20 && turn === waiting.current; i++) {
      const next = await load()
      if (next?.others.some((d) => !known.has(d.id))) return
      await new Promise((r) => window.setTimeout(r, 1000))
    }
  }, [load, state])
  useEffect(() => () => void (waiting.current = -1), [])

  useEffect(() => {
    load()
    // «в сети» and «5 минут назад» stay true while the page is open
    const timer = window.setInterval(() => document.visibilityState === 'visible' && load(), 30_000)
    const onHash = () => {
      const linked = linkedToken()
      if (!linked) return
      setToken(linked)
      history.replaceState(null, '', '#/devices') // the address becomes the page's own
    }
    onHash()
    window.addEventListener('hashchange', onHash)
    return () => {
      window.clearInterval(timer)
      window.removeEventListener('hashchange', onHash)
    }
  }, [load])

  const end = async (d: Device) => {
    setState((s) => s && { ...s, others: s.others.filter((x) => x.id !== d.id) })
    try {
      await api.terminateDevice(d.id)
      toast(`Сеанс завершён: ${d.name}`)
    } catch (e) {
      toast((e as Error).message)
    }
    load()
  }

  const endAll = async () => {
    try {
      const { count } = await api.terminateOtherDevices()
      toast(count ? 'Все другие сеансы завершены' : 'Других сеансов нет')
    } catch (e) {
      toast((e as Error).message)
    }
    load()
  }

  const others = state?.others ?? []
  return (
    <Page
      title="Устройства"
      onRefresh={load}
      left={
        <motion.button className="nav-button" onClick={onBack} whileTap={{ opacity: 0.4 }} style={{ paddingLeft: 0 }}>
          <ChevronLeft size={30} strokeWidth={2.2} style={{ marginRight: -4 }} />
          Настройки
        </motion.button>
      }
    >
      <div className="devices-hero">
        <Laptop />
        {scan ? (
          <>
            <p>Вы можете зайти в {siteName()} на компьютере с помощью QR-кода.</p>
            <button className="button tg-button devices-connect" onClick={() => setScanning(true)}>
              <QrIcon size={22} />
              Подключить устройство
            </button>
          </>
        ) : (
          <p>
            Чтобы зайти в {siteName()} на другом компьютере, нажмите на странице входа кнопку с QR-кодом рядом с «Войти
            через Telegram» и отсканируйте код телефоном: «Настройки» → «Устройства» → «Подключить устройство».
          </p>
        )}
      </div>

      {error && !state ? (
        <div className="section">
          <div className="section-footer" style={{ color: 'var(--red)' }}>
            {error}
          </div>
        </div>
      ) : !state ? (
        <div className="devices-loading">
          <Loader2 size={22} className="spinner" />
        </div>
      ) : (
        <>
          <div className="section">
            <div className="section-header">Это устройство</div>
            <div className="group">
              {state.current && <DeviceRow device={state.current} />}
              <AnimatePresence initial={false}>
                {others.length > 0 && (
                  <motion.div key="end-all" exit={{ opacity: 0, height: 0 }} transition={spring}>
                    <button className="cell destructive devices-end-all" onClick={() => setEndingAll(true)}>
                      <Hand size={22} />
                      Завершить другие сеансы
                    </button>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
            <div className="section-footer">
              {others.length > 0 ? 'Выйти на всех устройствах, кроме текущего.' : 'Вы больше нигде не вошли.'}
            </div>
          </div>

          {others.length > 0 && (
            <div className="section">
              <div className="section-header">Активные сеансы</div>
              <div className="group">
                <AnimatePresence initial={false}>
                  {others.map((d) => (
                    <motion.div key={d.id} layout="position" exit={{ opacity: 0, height: 0 }} transition={spring}>
                      <SwipeRow
                        actions={[{ label: 'Завершить', icon: LogOut, color: 'var(--red)', onClick: () => end(d) }]}
                      >
                        <DeviceRow device={d} onClick={() => setPicked(d)} />
                      </SwipeRow>
                    </motion.div>
                  ))}
                </AnimatePresence>
              </div>
              <div className="section-footer">
                {mouse ? 'Нажмите на сеанс' : 'Нажмите на сеанс или смахните его влево'}, чтобы завершить его. Сеансы,
                которыми не пользовались 30 дней, завершаются сами.
              </div>
            </div>
          )}
        </>
      )}

      <ActionSheet
        open={picked !== null}
        onClose={() => setPicked(null)}
        title={picked ? `${picked.name} · ${picked.app}` : undefined}
        message={
          picked
            ? [
                [picked.place, picked.ip].filter(Boolean).join(' · '),
                `Вход ${longDate(picked.created_at)}, ${time(picked.created_at)}`,
              ]
                .filter(Boolean)
                .join('\n')
            : undefined
        }
        actions={picked ? [{ label: 'Завершить сеанс', destructive: true, onClick: () => end(picked) }] : []}
      />
      <ActionSheet
        open={endingAll}
        onClose={() => setEndingAll(false)}
        message="Выйти на всех устройствах, кроме этого?"
        actions={[{ label: 'Завершить другие сеансы', destructive: true, onClick: endAll }]}
      />
      <QrScanner
        open={scanning}
        onClose={() => setScanning(false)}
        onScan={(t) => {
          setScanning(false)
          setToken(t)
        }}
      />
      <QrConfirmSheet
        token={token}
        onClose={() => setToken(null)}
        onDone={() => {
          setToken(null)
          awaitNewDevice()
        }}
        onRescan={
          scan
            ? () => {
                setToken(null)
                setScanning(true)
              }
            : undefined
        }
      />
    </Page>
  )
}

function DeviceRow({ device, onClick }: { device: Device; onClick?: () => void }) {
  const seen = device.online ? 'в сети' : relative(device.last_seen_at)
  const body = (
    <>
      <DeviceIcon platform={device.platform} form={device.form} />
      <div className="cell-main">
        <div className="device-name">{device.name}</div>
        <div className="device-app">{device.app}</div>
        <div className="cell-sub">{[device.place ?? device.ip, seen].filter(Boolean).join(' • ')}</div>
      </div>
    </>
  )
  return onClick ? (
    <button className="cell device-cell" style={{ ['--inset' as string]: '62px' }} onClick={onClick}>
      {body}
    </button>
  ) : (
    <div className="cell device-cell" style={{ ['--inset' as string]: '62px' }}>
      {body}
    </div>
  )
}

/** The picture on top: a laptop showing the login page — a real little QR code and the blue button under it. */
function Laptop() {
  const { n, dots } = useMemo(() => {
    const qr = qrcode(1, 'L')
    qr.addData('feedor')
    qr.make()
    const n = qr.getModuleCount()
    const finder = (r: number, c: number) => (r < 7 && c < 7) || (r < 7 && c >= n - 7) || (r >= n - 7 && c < 7)
    const dots: [number, number][] = []
    for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) if (qr.isDark(r, c) && !finder(r, c)) dots.push([r, c])
    return { n, dots }
  }, [])
  const code = 40 // the code's side on the screen
  const m = code / n
  return (
    <svg className="devices-laptop" viewBox="0 0 240 160" aria-hidden="true">
      {/* the lid, its screen and the camera */}
      <rect x="44" y="6" width="152" height="108" rx="10" fill="var(--laptop-lid)" />
      <rect x="50" y="14" width="140" height="94" rx="3" fill="var(--laptop-screen)" />
      <circle cx="120" cy="10" r="1.3" fill="var(--laptop-notch)" />
      {/* the login page: the wordmark, the code, the button */}
      <rect x="104" y="22" width="32" height="4" rx="2" fill="var(--laptop-line)" />
      <rect x="96" y="31" width="48" height="48" rx="7" fill="#fff" />
      <g transform={`translate(100 35) scale(${m})`} fill="#0b0b0f">
        {dots.map(([r, c]) => (
          <rect key={`${r}:${c}`} x={c + 0.06} y={r + 0.06} width="0.88" height="0.88" rx="0.3" />
        ))}
        {[
          [0, 0],
          [0, n - 7],
          [n - 7, 0],
        ].map(([r, c]) => (
          <g key={`${r}:${c}`}>
            <rect x={c + 0.5} y={r + 0.5} width="6" height="6" rx="1.8" fill="none" stroke="#0b0b0f" strokeWidth="1" />
            <rect x={c + 2} y={r + 2} width="3" height="3" rx="0.9" />
          </g>
        ))}
      </g>
      <rect x="86" y="85" width="68" height="12" rx="4" fill="#008bff" />
      <rect x="93" y="89.5" width="36" height="3" rx="1.5" fill="#fff" opacity="0.9" />
      <rect x="140.5" y="88" width="0.7" height="6" fill="#fff" opacity="0.5" />
      <g fill="none" stroke="#fff" strokeWidth="0.8">
        <rect x="144" y="88" width="2.6" height="2.6" rx="0.7" />
        <rect x="148.4" y="88" width="2.6" height="2.6" rx="0.7" />
        <rect x="144" y="92.4" width="2.6" height="2.6" rx="0.7" />
      </g>
      <rect x="148.6" y="92.6" width="2.2" height="2.2" rx="0.5" fill="#fff" />
      {/* the base: the deck, the notch to open the lid, the front edge */}
      <path d="M26 116 H214 V119 Q214 126 206 127 L198 128 H42 L34 127 Q26 126 26 119 Z" fill="var(--laptop-base)" />
      <rect x="26" y="114" width="188" height="5" rx="2.5" fill="var(--laptop-hinge)" />
      <path d="M102 114 H138 V115.5 Q138 118 135 118 H105 Q102 118 102 115.5 Z" fill="var(--laptop-notch)" />
    </svg>
  )
}
