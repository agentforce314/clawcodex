import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { BrowserRouter } from 'react-router-dom'
import { describe, it, expect } from 'vitest'
import { CRMProvider } from '../context/CRMContext'
import App from '../App'

function renderApp() {
  return render(
    <BrowserRouter>
      <CRMProvider>
        <App />
      </CRMProvider>
    </BrowserRouter>
  )
}

describe('App', () => {
  it('renders the sidebar with navigation links', () => {
    renderApp()
    expect(screen.getByText('SimpleCRM')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Dashboard' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Contacts' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Deals' })).toBeInTheDocument()
  })

  it('navigates to contacts page', async () => {
    const user = userEvent.setup()
    renderApp()

    await user.click(screen.getByText('Contacts'))
    expect(screen.getByText('Add Contact')).toBeInTheDocument()
  })

  it('navigates to deals page', async () => {
    const user = userEvent.setup()
    renderApp()

    await user.click(screen.getByText('Deals'))
    expect(screen.getByText('Add Deal')).toBeInTheDocument()
  })
})
