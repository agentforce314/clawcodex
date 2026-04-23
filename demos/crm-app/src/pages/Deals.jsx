import { useState } from 'react'
import { useCRM } from '../context/CRMContext'
import DealForm from '../components/DealForm'

const STAGES = ['qualified', 'proposal', 'negotiation', 'closed-won', 'closed-lost']

function Deals() {
  const { state, dispatch } = useCRM()
  const [showForm, setShowForm] = useState(false)
  const [editingDeal, setEditingDeal] = useState(null)

  function handleSave(deal) {
    if (editingDeal) {
      dispatch({ type: 'UPDATE_DEAL', payload: { ...deal, id: editingDeal.id } })
    } else {
      dispatch({ type: 'ADD_DEAL', payload: deal })
    }
    setShowForm(false)
    setEditingDeal(null)
  }

  function handleEdit(deal) {
    setEditingDeal(deal)
    setShowForm(true)
  }

  function handleDelete(id) {
    dispatch({ type: 'DELETE_DEAL', payload: id })
  }

  function handleCancel() {
    setShowForm(false)
    setEditingDeal(null)
  }

  return (
    <div className="page">
      <div className="page-header">
        <h2>Deals</h2>
        <button className="btn btn-primary" onClick={() => setShowForm(true)}>
          Add Deal
        </button>
      </div>

      {showForm && (
        <DealForm
          deal={editingDeal}
          contacts={state.contacts}
          onSave={handleSave}
          onCancel={handleCancel}
        />
      )}

      <table className="data-table">
        <thead>
          <tr>
            <th>Title</th>
            <th>Contact</th>
            <th>Value</th>
            <th>Stage</th>
            <th>Created</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {state.deals.map(deal => {
            const contact = state.contacts.find(c => c.id === deal.contactId)
            return (
              <tr key={deal.id}>
                <td>{deal.title}</td>
                <td>{contact?.name || 'Unknown'}</td>
                <td>${deal.value.toLocaleString()}</td>
                <td><span className={`stage-badge stage-${deal.stage}`}>{deal.stage.replace('-', ' ')}</span></td>
                <td>{deal.createdAt}</td>
                <td>
                  <button className="btn btn-sm" onClick={() => handleEdit(deal)}>Edit</button>
                  <button className="btn btn-sm btn-danger" onClick={() => handleDelete(deal.id)}>Delete</button>
                </td>
              </tr>
            )
          })}
          {state.deals.length === 0 && (
            <tr><td colSpan="6" className="empty-row">No deals found</td></tr>
          )}
        </tbody>
      </table>
    </div>
  )
}

export default Deals
