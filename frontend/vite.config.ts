import { readFileSync } from 'node:fs'
import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'

// index.html and manifest.json call the site by its address: the server puts it in place of the mark when
// it serves them (backend/app/config.py, site_name); in development this plugin does, from DOMAIN
const SITE_NAME_MARK = '__SITE_NAME__'

function siteName(): string {
  const name = process.env.DOMAIN || 'localhost'
  return name.charAt(0).toUpperCase() + name.slice(1)
}

function siteNameInDev(): Plugin {
  return {
    name: 'site-name',
    apply: 'serve',
    transformIndexHtml: (html) => html.replaceAll(SITE_NAME_MARK, siteName()),
    configureServer(server) {
      server.middlewares.use('/manifest.json', (_req, res) => {
        res.setHeader('Content-Type', 'application/manifest+json')
        res.end(readFileSync('public/manifest.json', 'utf-8').replaceAll(SITE_NAME_MARK, siteName()))
      })
    },
  }
}

export default defineConfig({
  plugins: [react(), siteNameInDev()],
  server: {
    port: 5173,
    // the MCP connector and its sign-in live beside the API (backend web/mcp.py, mcp_oauth.py)
    proxy: Object.fromEntries(['/api', '/mcp', '/oauth', '/.well-known'].map((p) => [p, 'http://127.0.0.1:8000'])),
  },
})
