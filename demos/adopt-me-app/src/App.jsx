import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { GameProvider } from './context/GameContext';
import Navbar from './components/Navbar';
import HatchAnimation from './components/HatchAnimation';
import Home from './pages/Home';
import Nursery from './pages/Nursery';
import MyPets from './pages/MyPets';
import Shop from './pages/Shop';
import Trade from './pages/Trade';

export default function App() {
  return (
    <GameProvider>
      <BrowserRouter>
        <div className="app">
          <Navbar />
          <main className="main-content">
            <Routes>
              <Route path="/" element={<Home />} />
              <Route path="/nursery" element={<Nursery />} />
              <Route path="/my-pets" element={<MyPets />} />
              <Route path="/shop" element={<Shop />} />
              <Route path="/trade" element={<Trade />} />
            </Routes>
          </main>
          <HatchAnimation />
        </div>
      </BrowserRouter>
    </GameProvider>
  );
}
