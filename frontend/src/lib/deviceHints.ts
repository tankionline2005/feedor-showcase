import { api } from '../api'

/** As the app opens signed in: what the browser tells about itself beyond its user agent — an Android phone's
 * model, the version of Windows (Chrome hides both), an iPad passing for a Mac, an app on the home screen — so
 * that «Устройства» names this device the way its person knows it. */
export async function sendDeviceHints() {
  const data = (
    navigator as Navigator & {
      userAgentData?: { getHighEntropyValues(hints: string[]): Promise<{ model?: string; platformVersion?: string }> }
    }
  ).userAgentData
  const high = await data?.getHighEntropyValues(['model', 'platformVersion']).catch(() => undefined)
  const iPad = /Macintosh/.test(navigator.userAgent) && navigator.maxTouchPoints > 1
  const standalone =
    window.matchMedia('(display-mode: standalone)').matches ||
    (navigator as Navigator & { standalone?: boolean }).standalone === true
  api
    .deviceHints({
      model: iPad ? 'iPad' : high?.model || null,
      os_version: /Windows/.test(navigator.userAgent) ? high?.platformVersion || null : null,
      standalone,
    })
    .catch(() => {})
}
