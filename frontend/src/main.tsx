import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { startFreezeGuard } from './lib/freezeGuard'
import { watchKeyboard } from './lib/keyboard'
import './styles.css'
import './intro.css'
import './calendar.css'

startFreezeGuard()
watchKeyboard()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
