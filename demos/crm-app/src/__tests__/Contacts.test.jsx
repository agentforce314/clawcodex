import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { BrowserRouter } from 'react-router-dom'
import { describe, it, expect } from 'vitest'
import { CRMProvider } from '../context/CRMContext'
import Contacts from '../pages/Contacts'

function renderWithProviders(ui) {
  return render(
    <BrowserRouter>
      <CRMProvider>{ui}</CRMProvider>
    </BrowserRouter>
  )
}

describe('Contacts', () => {
  it('renders the contacts page with initial data', () => {
    renderWithProviders(<Contacts />)
    expect(screen.getByText('Contacts')).toBeInTheDocument()
    expect(screen.getByText('Alice Johnson')).toBeInTheDocument()
    expect(screen.getByText('Bob Smith')).toBeInTheDocument()
    expect(screen.getByText('Carol White')).toBeInTheDocument()
  })

  it('filters contacts by search input', async () => {
    const user = userEvent.setup()
    renderWithProviders(<Contacts />)

    const searchInput = screen.getByPlaceholderText('Search contacts...')
    await user.type(searchInput, 'alice')

    expect(screen.getByText('Alice Johnson')).toBeInTheDocument()
    expect(screen.queryByText('Bob Smith')).not.toBeInTheDocument()
    expect(screen.queryByText('Carol White')).not.toBeInTheDocument()
  })

  it('shows empty message when no contacts match', async () => {
    const user = userEvent.setup()
    renderWithProviders(<Contacts />)

    const searchInput = screen.getByPlaceholderText('Search contacts...')
    await user.type(searchInput, 'zzzzz')

    expect(screen.getByText('No contacts found')).toBeInTheDocument()
  })

  it('opens add contact form when clicking Add Contact', async () => {
    const user = userEvent.setup()
    renderWithProviders(<Contacts />)

    await user.click(screen.getByText('Add Contact'))
    expect(screen.getByText('New Contact')).toBeInTheDocument()
  })

  it('adds a new contact via the form', async () => {
    const user = userEvent.setup()
    renderWithProviders(<Contacts />)

    await user.click(screen.getByText('Add Contact'))

    await user.type(screen.getByLabelText(/Name/), 'Dave Brown')
    await user.type(screen.getByLabelText(/Email/), 'dave@example.com')
    await user.type(screen.getByLabelText(/Phone/), '555-0104')
    await user.type(screen.getByLabelText(/Company/), 'Wayne Enterprises')
    await user.click(screen.getByText('Save'))

    expect(screen.getByText('Dave Brown')).toBeInTheDocument()
    expect(screen.getByText('dave@example.com')).toBeInTheDocument()
  })

  it('cancels adding a contact', async () => {
    const user = userEvent.setup()
    renderWithProviders(<Contacts />)

    await user.click(screen.getByText('Add Contact'))
    expect(screen.getByText('New Contact')).toBeInTheDocument()

    await user.click(screen.getByText('Cancel'))
    expect(screen.queryByText('New Contact')).not.toBeInTheDocument()
  })

  it('deletes a contact', async () => {
    const user = userEvent.setup()
    renderWithProviders(<Contacts />)

    const deleteButtons = screen.getAllByText('Delete')
    await user.click(deleteButtons[0])

    expect(screen.queryByText('Alice Johnson')).not.toBeInTheDocument()
  })

  it('opens edit form with contact data', async () => {
    const user = userEvent.setup()
    renderWithProviders(<Contacts />)

    const editButtons = screen.getAllByText('Edit')
    await user.click(editButtons[0])

    expect(screen.getByText('Edit Contact')).toBeInTheDocument()
    expect(screen.getByDisplayValue('Alice Johnson')).toBeInTheDocument()
    expect(screen.getByDisplayValue('alice@example.com')).toBeInTheDocument()
  })
})
