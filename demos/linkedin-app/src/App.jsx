import { Routes, Route } from 'react-router-dom'
import Navbar from './components/Navbar'
import Feed from './pages/Feed'
import Profile from './pages/Profile'
import Network from './pages/Network'
import Jobs from './pages/Jobs'
import Messaging from './pages/Messaging'

function App() {
  return (
    <div className="app">
      <Navbar />
      <main className="main-content">
        <Routes>
          <Route path="/" element={<Feed />} />
          <Route path="/profile/:userId" element={<Profile />} />
          <Route path="/network" element={<Network />} />
          <Route path="/jobs" element={<Jobs />} />
          <Route path="/messaging" element={<Messaging />} />
        </Routes>
      </main>
    </div>
  )
}

export default App
