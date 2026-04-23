import { Link } from 'react-router-dom'
import { useLinkedIn } from '../context/LinkedInContext'

function ProfileCard() {
  const { currentUser } = useLinkedIn()

  return (
    <div className="profile-card">
      <div className="profile-card-banner" style={{ backgroundImage: `url(${currentUser.banner})` }} />
      <Link to={`/profile/${currentUser.id}`} className="profile-card-avatar-link">
        <img src={currentUser.avatar} alt={currentUser.name} className="profile-card-avatar" />
      </Link>
      <div className="profile-card-info">
        <Link to={`/profile/${currentUser.id}`} className="profile-card-name">{currentUser.name}</Link>
        <p className="profile-card-headline">{currentUser.headline}</p>
      </div>
      <div className="profile-card-stats">
        <div className="profile-stat">
          <span className="profile-stat-label">Connections</span>
          <span className="profile-stat-value">{currentUser.connections}</span>
        </div>
        <div className="profile-stat">
          <span className="profile-stat-label">Profile views</span>
          <span className="profile-stat-value">124</span>
        </div>
      </div>
    </div>
  )
}

export default ProfileCard
