/**
 * Copies text to the clipboard. navigator.clipboard exists only on HTTPS, and the site runs
 * on plain HTTP, so there is a fallback through a temporary textarea (works in iOS Safari too).
 */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    /* fall through */
  }
  const area = document.createElement('textarea')
  area.value = text
  area.setAttribute('readonly', '')
  // 16px keeps iOS from zooming in; off-screen so nothing flashes
  Object.assign(area.style, { position: 'fixed', top: '-1000px', left: '0', fontSize: '16px', opacity: '0' })
  document.body.appendChild(area)
  area.focus()
  area.select()
  area.setSelectionRange(0, text.length)
  let ok = false
  try {
    ok = document.execCommand('copy')
  } catch {
    ok = false
  }
  document.body.removeChild(area)
  return ok
}
