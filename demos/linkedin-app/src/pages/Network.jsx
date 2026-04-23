import { useLinkedIn } from '../context/LinkedInContext'
import ConnectionCard from '../components/ConnectionCard'

function Network() {
  const { people } = useLinkedIn()

  return (
    <div className="network-page">
      <div className="network-header">
        <h2>Grow your network</h2>
        <p>People you may know based on your profile and connections</p>
      </div>
      <div className="network-grid">
        {people.map(person => (
          <ConnectionCard key={person.id} person={person} />
        ))}
      </div>
    </div>
  )
}

export default Network
