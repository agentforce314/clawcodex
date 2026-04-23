import { NavLink } from 'react-router-dom'
import { useLinkedIn } from '../context/LinkedInContext'

function Navbar() {
  const { currentUser, messages } = useLinkedIn()
  const unreadCount = messages.filter(m => m.unread).length

  return (
    <nav className="navbar">
      <div className="navbar-inner">
        <div className="navbar-left">
          <NavLink to="/" className="navbar-logo">in</NavLink>
          <div className="navbar-search">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
              <path d="M15.5 14h-.79l-.28-.27A6.471 6.471 0 0016 9.5 6.5 6.5 0 109.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/>
            </svg>
            <input type="text" placeholder="Search" />
          </div>
        </div>
        <div className="navbar-links">
          <NavLink to="/" className="nav-item" end>
            <svg viewBox="0 0 24 24" width="24" height="24" fill="currentColor">
              <path d="M23 9v2h-2v7a3 3 0 01-3 3h-4v-6h-4v6H6a3 3 0 01-3-3v-7H1V9l11-7 11 7z"/>
            </svg>
            <span>Home</span>
          </NavLink>
          <NavLink to="/network" className="nav-item">
            <svg viewBox="0 0 24 24" width="24" height="24" fill="currentColor">
              <path d="M12 16v6H3v-6a3 3 0 013-3h3a3 3 0 013 3zm5.5-3A3.5 3.5 0 1014 9.5a3.5 3.5 0 003.5 3.5zm1 2h-2a2.5 2.5 0 00-2.5 2.5V22h7v-4.5a2.5 2.5 0 00-2.5-2.5zM7.5 2A4.5 4.5 0 1012 6.5 4.49 4.49 0 007.5 2z"/>
            </svg>
            <span>Network</span>
          </NavLink>
          <NavLink to="/jobs" className="nav-item">
            <svg viewBox="0 0 24 24" width="24" height="24" fill="currentColor">
              <path d="M17 6V5a3 3 0 00-3-3h-4a3 3 0 00-3 3v1H2v4a3 3 0 003 3h14a3 3 0 003-3V6h-5zM9 5a1 1 0 011-1h4a1 1 0 011 1v1H9V5zm10 9a4 4 0 01-4 4h-6a4 4 0 01-4-4v-1h14v1z"/>
            </svg>
            <span>Jobs</span>
          </NavLink>
          <NavLink to="/messaging" className="nav-item">
            <svg viewBox="0 0 24 24" width="24" height="24" fill="currentColor">
              <path d="M16 4H8a7 7 0 000 14h4v4l8.16-5.39A6.78 6.78 0 0023 11a7 7 0 00-7-7zm-8 8.5A1.5 1.5 0 119.5 11 1.5 1.5 0 018 12.5zm4 0a1.5 1.5 0 111.5-1.5 1.5 1.5 0 01-1.5 1.5zm4 0a1.5 1.5 0 111.5-1.5 1.5 1.5 0 01-1.5 1.5z"/>
            </svg>
            <span>Messaging</span>
            {unreadCount > 0 && <span className="badge">{unreadCount}</span>}
          </NavLink>
          <NavLink to={`/profile/${currentUser.id}`} className="nav-item nav-profile">
            <img src={currentUser.avatar} alt={currentUser.name} className="nav-avatar" />
            <span>Me</span>
          </NavLink>
        </div>
      </div>
    </nav>
  )
}

export default Navbar
