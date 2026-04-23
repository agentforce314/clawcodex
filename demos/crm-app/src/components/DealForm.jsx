import { useState } from 'react'

const STAGES = ['qualified', 'proposal', 'negotiation', 'closed-won', 'closed-lost']

function DealForm({ deal, contacts, onSave, onCancel }) {
  const [form, setForm] = useState({
    title: deal?.title || '',
    contactId: deal?.contactId || (contacts[0]?.id ?? ''),
    value: deal?.value || '',
    stage: deal?.stage || 'qualified',
    createdAt: deal?.createdAt || new Date().toISOString().slice(0, 10),
  })

  function handleChange(e) {
    const value = e.target.name === 'value' ? Number(e.target.value) || ''
      : e.target.name === 'contactId' ? Number(e.target.value)
      : e.target.value
    setForm({ ...form, [e.target.name]: value })
  }

  function handleSubmit(e) {
    e.preventDefault()
    if (!form.title.trim() || !form.value) return
    onSave(form)
  }

  return (
    <form className="form-card" onSubmit={handleSubmit}>
      <h3>{deal ? 'Edit Deal' : 'New Deal'}</h3>
      <div className="form-grid">
        <label>
          Title *
          <input name="title" value={form.title} onChange={handleChange} required />
        </label>
        <label>
          Contact
          <select name="contactId" value={form.contactId} onChange={handleChange}>
            {contacts.map(c => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </select>
        </label>
        <label>
          Value ($) *
          <input name="value" type="number" min="0" value={form.value} onChange={handleChange} required />
        </label>
        <label>
          Stage
          <select name="stage" value={form.stage} onChange={handleChange}>
            {STAGES.map(s => (
              <option key={s} value={s}>{s.replace('-', ' ')}</option>
            ))}
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

export default DealForm
