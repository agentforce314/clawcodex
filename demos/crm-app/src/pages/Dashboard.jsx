import { useCRM } from '../context/CRMContext'

const STAGES = ['qualified', 'proposal', 'negotiation', 'closed-won', 'closed-lost']

function Dashboard() {
  const { state } = useCRM()
  const { contacts, deals } = state

  const totalValue = deals.reduce((sum, d) => sum + d.value, 0)
  const wonDeals = deals.filter(d => d.stage === 'closed-won')
  const wonValue = wonDeals.reduce((sum, d) => sum + d.value, 0)
  const activeContacts = contacts.filter(c => c.status === 'active').length
  const leads = contacts.filter(c => c.status === 'lead').length

  const dealsByStage = STAGES.reduce((acc, stage) => {
    acc[stage] = deals.filter(d => d.stage === stage)
    return acc
  }, {})

  return (
    <div className="page">
      <h2>Dashboard</h2>
      <div className="stats-grid">
        <div className="stat-card">
          <span className="stat-label">Total Contacts</span>
          <span className="stat-value">{contacts.length}</span>
        </div>
        <div className="stat-card">
          <span className="stat-label">Active Contacts</span>
          <span className="stat-value">{activeContacts}</span>
        </div>
        <div className="stat-card">
          <span className="stat-label">Leads</span>
          <span className="stat-value">{leads}</span>
        </div>
        <div className="stat-card">
          <span className="stat-label">Total Deals</span>
          <span className="stat-value">{deals.length}</span>
        </div>
        <div className="stat-card">
          <span className="stat-label">Pipeline Value</span>
          <span className="stat-value">${totalValue.toLocaleString()}</span>
        </div>
        <div className="stat-card">
          <span className="stat-label">Won Value</span>
          <span className="stat-value">${wonValue.toLocaleString()}</span>
        </div>
      </div>

      <h3>Deal Pipeline</h3>
      <div className="pipeline">
        {STAGES.map(stage => (
          <div key={stage} className="pipeline-stage">
            <h4>{stage.replace('-', ' ')}</h4>
            <span className="badge">{dealsByStage[stage].length}</span>
            {dealsByStage[stage].map(deal => {
              const contact = contacts.find(c => c.id === deal.contactId)
              return (
                <div key={deal.id} className="pipeline-card">
                  <strong>{deal.title}</strong>
                  <span>${deal.value.toLocaleString()}</span>
                  <small>{contact?.name || 'Unknown'}</small>
                </div>
              )
            })}
          </div>
        ))}
      </div>
    </div>
  )
}

export default Dashboard
