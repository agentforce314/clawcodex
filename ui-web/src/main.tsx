import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import { App } from './App.tsx'
import './styles/base.css'

const root = document.getElementById('root')

if (root === null) throw new Error('web app: missing #root')

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
