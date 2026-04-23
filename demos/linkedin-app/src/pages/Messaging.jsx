import { useLinkedIn } from '../context/LinkedInContext'

function Messaging() {
  const { messages } = useLinkedIn()

  return (
    <div className="messaging-page">
      <div className="messaging-header">
        <h2>Messaging</h2>
      </div>
      <div className="message-list">
        {messages.map(msg => (
          <div key={msg.id} className={`message-item ${msg.unread ? 'unread' : ''}`}>
            <img src={msg.contact.avatar} alt={msg.contact.name} className="message-avatar" />
            <div className="message-content">
              <div className="message-top">
                <h4 className="message-contact-name">{msg.contact.name}</h4>
                <span className="message-timestamp">{msg.timestamp}</span>
              </div>
              <p className="message-preview">{msg.lastMessage}</p>
            </div>
            {msg.unread && <span className="unread-dot" />}
          </div>
        ))}
      </div>
    </div>
  )
}

export default Messaging
