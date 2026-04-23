import { useState } from 'react'
import { useCRM } from '../context/CRMContext'
import ContactForm from '../components/ContactForm'

function Contacts() {
  const { state, dispatch } = useCRM()
  const [search, setSearch] = useState('')
  const [showForm, setShowForm] = useState(false)
  const [editingContact, setEditingContact] = useState(null)

  const filtered = state.contacts.filter(c =>
    c.name.toLowerCase().includes(search.toLowerCase()) ||
    c.email.toLowerCase().includes(search.toLowerCase()) ||
    c.company.toLowerCase().includes(search.toLowerCase())
  )

  function handleSave(contact) {
    if (editingContact) {
      dispatch({ type: 'UPDATE_CONTACT', payload: { ...contact, id: editingContact.id } })
    } else {
      dispatch({ type: 'ADD_CONTACT', payload: contact })
    }
    setShowForm(false)
    setEditingContact(null)
  }

  function handleEdit(contact) {
    setEditingContact(contact)
    setShowForm(true)
  }

  function handleDelete(id) {
    dispatch({ type: 'DELETE_CONTACT', payload: id })
  }

  function handleCancel() {
    setShowForm(false)
    setEditingContact(null)
  }

  return (
    <div className="page">
      <div className="page-header">
        <h2>Contacts</h2>
        <button className="btn btn-primary" onClick={() => setShowForm(true)}>
          Add Contact
        </button>
      </div>

      {showForm && (
        <ContactForm
          contact={editingContact}
          onSave={handleSave}
          onCancel={handleCancel}
        />
      )}

      <input
        type="text"
        className="search-input"
        placeholder="Search contacts..."
        value={search}
        onChange={e => setSearch(e.target.value)}
      />

      <table className="data-table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Email</th>
            <th>Phone</th>
            <th>Company</th>
            <th>Status</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {filtered.map(contact => (
            <tr key={contact.id}>
              <td>{contact.name}</td>
              <td>{contact.email}</td>
              <td>{contact.phone}</td>
              <td>{contact.company}</td>
              <td><span className={`status-badge status-${contact.status}`}>{contact.status}</span></td>
              <td>
                <button className="btn btn-sm" onClick={() => handleEdit(contact)}>Edit</button>
                <button className="btn btn-sm btn-danger" onClick={() => handleDelete(contact.id)}>Delete</button>
              </td>
            </tr>
          ))}
          {filtered.length === 0 && (
            <tr><td colSpan="6" className="empty-row">No contacts found</td></tr>
          )}
        </tbody>
      </table>
    </div>
  )
}

export default Contacts
