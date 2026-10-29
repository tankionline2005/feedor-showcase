/** The site's name: its address, the first letter a capital (Feedor.xyz), put into index.html by the server. */
export function siteName(): string {
  const meta = document.querySelector<HTMLMetaElement>('meta[name="apple-mobile-web-app-title"]')
  const host = location.hostname
  return meta?.content || host.charAt(0).toUpperCase() + host.slice(1)
}
