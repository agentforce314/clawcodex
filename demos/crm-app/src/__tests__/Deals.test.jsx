import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { BrowserRouter } from 'react-router-dom'
import { describe, it, expect } from 'vitest'
import { CRMProvider } from '../context/CRMContext'
import Deals from '../pages/Deals'

function renderWithProviders(ui) {
  return render(
    <BrowserRouter>
      <CRMProvider>{ui}</CRMProvider>
    </BrowserRouter>
  )
}

describe('Deals', () => {
  it('renders the deals page with initial data', () => {
    renderWithProviders(<Deals />)
    expect(screen.getByText('Deals')).toBeInTheDocument()
    expect(screen.getByText('Enterprise License')).toBeInTheDocument()
    expect(screen.getByText('Consulting Package')).toBeInTheDocument()
    expect(screen.getByText('Starter Plan')).toBeInTheDocument()
  })

  it('displays deal values', () => {
    renderWithProviders(<Deals />)
    expect(screen.getByText('$50,000')).toBeInTheDocument()
    expect(screen.getByText('$25,000')).toBeInTheDocument()
    expect(screen.getByText('$5,000')).toBeInTheDocument()
  })

  it('opens add deal form', async () => {
    const user = userEvent.setup()
    renderWithProviders(<Deals />)

    await user.click(screen.getByText('Add Deal'))
    expect(screen.getByText('New Deal')).toBeInTheDocument()
  })

  it('adds a new deal', async () => {
    const user = userEvent.setup()
    renderWithProviders(<Deals />)

    await user.click(screen.getByText('Add Deal'))
    await user.type(screen.getByLabelText(/Title/), 'Premium Support')
    await user.type(screen.getByLabelText(/Value/), '15000')
    await user.click(screen.getByText('Save'))

    expect(screen.getByText('Premium Support')).toBeInTheDocument()
  })

  it('deletes a deal', async () => {
    const user = userEvent.setup()
    renderWithProviders(<Deals />)

    const deleteButtons = screen.getAllByText('Delete')
    await user.click(deleteButtons[0])

    expect(screen.queryByText('Enterprise License')).not.toBeInTheDocument()
  })

  it('opens edit form for an existing deal', async () => {
    const user = userEvent.setup()
    renderWithProviders(<Deals />)

    const editButtons = screen.getAllByText('Edit')
    await user.click(editButtons[0])

    expect(screen.getByText('Edit Deal')).toBeInTheDocument()
    expect(screen.getByDisplayValue('Enterprise License')).toBeInTheDocument()
  })

  it('cancels adding a deal', async () => {
    const user = userEvent.setup()
    renderWithProviders(<Deals />)

    await user.click(screen.getByText('Add Deal'))
    expect(screen.getByText('New Deal')).toBeInTheDocument()

    await user.click(screen.getByText('Cancel'))
    expect(screen.queryByText('New Deal')).not.toBeInTheDocument()
  })
})
