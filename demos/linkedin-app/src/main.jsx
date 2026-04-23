import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import { LinkedInProvider } from './context/LinkedInContext'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <LinkedInProvider>
        <App />
      </LinkedInProvider>
    </BrowserRouter>
  </React.StrictMode>,
)
