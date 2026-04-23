import { useLinkedIn } from '../context/LinkedInContext'

function ConnectionCard({ person }) {
  const { connectedIds, toggleConnect } = useLinkedIn()
  const isConnected = connectedIds.has(person.id)

  return (
    <div className="connection-card">
      <img src={person.avatar} alt={person.name} className="connection-avatar" />
      <h4 className="connection-name">{person.name}</h4>
      <p className="connection-headline">{person.headline}</p>
      <p className="connection-mutual">{person.mutual} mutual connections</p>
      <button
        className={`connect-btn ${isConnected ? 'connected' : ''}`}
        onClick={() => toggleConnect(person.id)}
      >
        {isConnected ? 'Connected' : '+ Connect'}
      </button>
    </div>
  )
}

export default ConnectionCard
