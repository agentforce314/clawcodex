import { useLinkedIn } from '../context/LinkedInContext'

function Profile() {
  const { currentUser } = useLinkedIn()

  return (
    <div className="profile-page">
      <div className="profile-header-card">
        <div className="profile-banner" style={{ backgroundImage: `url(${currentUser.banner})` }} />
        <div className="profile-header-content">
          <img src={currentUser.avatar} alt={currentUser.name} className="profile-page-avatar" />
          <div className="profile-header-info">
            <h1>{currentUser.name}</h1>
            <p className="profile-headline">{currentUser.headline}</p>
            <p className="profile-location">{currentUser.location} &middot; {currentUser.connections}+ connections</p>
            <div className="profile-header-actions">
              <button className="btn-primary">Open to</button>
              <button className="btn-secondary">Add profile section</button>
              <button className="btn-secondary">More</button>
            </div>
          </div>
        </div>
      </div>

      <div className="profile-section">
        <h2>About</h2>
        <p>{currentUser.about}</p>
      </div>

      <div className="profile-section">
        <h2>Experience</h2>
        {currentUser.experience.map((exp, index) => (
          <div key={index} className="experience-item">
            <img src={exp.logo} alt={exp.company} className="experience-logo" />
            <div className="experience-info">
              <h4>{exp.title}</h4>
              <p className="experience-company">{exp.company}</p>
              <p className="experience-duration">{exp.duration}</p>
            </div>
          </div>
        ))}
      </div>

      <div className="profile-section">
        <h2>Skills</h2>
        <div className="skills-list">
          {['React', 'JavaScript', 'TypeScript', 'Node.js', 'Python', 'AWS', 'Docker', 'GraphQL', 'PostgreSQL'].map(skill => (
            <span key={skill} className="skill-tag">{skill}</span>
          ))}
        </div>
      </div>
    </div>
  )
}

export default Profile
