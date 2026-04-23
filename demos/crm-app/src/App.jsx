import { Routes, Route, NavLink } from 'react-router-dom'
import Dashboard from './pages/Dashboard'
import Contacts from './pages/Contacts'
import Deals from './pages/Deals'

function App() {
  return (
    <div className="app">
      <nav className="sidebar">
        <h1 className="logo">SimpleCRM</h1>
        <ul>
          <li><NavLink to="/" end>Dashboard</NavLink></li>
          <li><NavLink to="/contacts">Contacts</NavLink></li>
          <li><NavLink to="/deals">Deals</NavLink></li>
        </ul>
      </nav>
      <main className="main-content">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/contacts" element={<Contacts />} />
          <Route path="/deals" element={<Deals />} />
        </Routes>
      </main>
    </div>
  )
}

export default App
