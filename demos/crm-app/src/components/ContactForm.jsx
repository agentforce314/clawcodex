import { useState } from 'react'

function ContactForm({ contact, onSave, onCancel }) {
  const [form, setForm] = useState({
    name: contact?.name || '',
    email: contact?.email || '',
    phone: contact?.phone || '',
    company: contact?.company || '',
    status: contact?.status || 'lead',
  })

  function handleChange(e) {
    setForm({ ...form, [e.target.name]: e.target.value })
  }

  function handleSubmit(e) {
    e.preventDefault()
    if (!form.name.trim() || !form.email.trim()) return
    onSave(form)
  }

  return (
    <form className="form-card" onSubmit={handleSubmit}>
      <h3>{contact ? 'Edit Contact' : 'New Contact'}</h3>
      <div className="form-grid">
        <label>
          Name *
          <input name="name" value={form.name} onChange={handleChange} required />
        </label>
        <label>
          Email *
          <input name="email" type="email" value={form.email} onChange={handleChange} required />
        </label>
        <label>
          Phone
          <input name="phone" value={form.phone} onChange={handleChange} />
        </label>
        <label>
          Company
          <input name="company" value={form.company} onChange={handleChange} />
        </label>
        <label>
          Status
          <select name="status" value={form.status} onChange={handleChange}>
            <option value="lead">Lead</option>
            <option value="active">Active</option>
            <option value="inactive">Inactive</option>
          </select>
        </label>
      </div>
      <div className="form-actions">
        <button type="submit" className="btn btn-primary">Save</button>
        <button type="button" className="btn" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  )
}

export default ContactForm
