import { render, screen } from '@testing-library/react'
import { BrowserRouter } from 'react-router-dom'
import { describe, it, expect } from 'vitest'
import { CRMProvider } from '../context/CRMContext'
import Dashboard from '../pages/Dashboard'

function renderWithProviders(ui) {
  return render(
    <BrowserRouter>
      <CRMProvider>{ui}</CRMProvider>
    </BrowserRouter>
  )
}

describe('Dashboard', () => {
  it('renders the dashboard heading', () => {
    renderWithProviders(<Dashboard />)
    expect(screen.getByText('Dashboard')).toBeInTheDocument()
  })

  it('displays stat cards with correct counts', () => {
    renderWithProviders(<Dashboard />)
    const statCards = document.querySelectorAll('.stat-card')
    expect(statCards.length).toBe(6)
    expect(screen.getByText('Total Contacts')).toBeInTheDocument()
    expect(screen.getByText('Total Deals')).toBeInTheDocument()
    expect(screen.getByText('Active Contacts')).toBeInTheDocument()
    expect(screen.getByText('Leads')).toBeInTheDocument()
  })

  it('displays pipeline value', () => {
    renderWithProviders(<Dashboard />)
    expect(screen.getByText('Pipeline Value')).toBeInTheDocument()
    expect(screen.getByText('$80,000')).toBeInTheDocument()
  })

  it('renders pipeline stages', () => {
    renderWithProviders(<Dashboard />)
    expect(screen.getByText('qualified')).toBeInTheDocument()
    expect(screen.getByText('proposal')).toBeInTheDocument()
    expect(screen.getByText('negotiation')).toBeInTheDocument()
    expect(screen.getByText('closed won')).toBeInTheDocument()
  })

  it('shows deal cards in the pipeline', () => {
    renderWithProviders(<Dashboard />)
    expect(screen.getByText('Enterprise License')).toBeInTheDocument()
    expect(screen.getByText('Consulting Package')).toBeInTheDocument()
    expect(screen.getByText('Starter Plan')).toBeInTheDocument()
  })
})
