import { useGame } from '../context/GameContext';
import { Link } from 'react-router-dom';

export default function Home() {
  const { state } = useGame();
  const activePet = state.pets.find(p => p.instanceId === state.activePetId);

  return (
    <div className="page home-page">
      <div className="home-hero">
        <h1>Welcome to Adopt Me!</h1>
        <p className="home-hero__subtitle">Adopt, raise, and trade adorable pets!</p>
        <div className="home-hero__player">
          <span>👤 {state.player.name}</span>
          <span>🪙 {state.player.coins.toLocaleString()} coins</span>
          <span>🐾 {state.pets.length} pets</span>
        </div>
      </div>

      {activePet && (
        <div className="home-active-pet">
          <h2>Your Active Pet</h2>
          <div className="home-active-pet__display">
            <span className="home-active-pet__emoji">{activePet.emoji}</span>
            <div>
              <h3>{activePet.nickname}</h3>
              <p>{activePet.name}</p>
            </div>
          </div>
          <Link to="/my-pets" className="btn btn--primary">Care for {activePet.nickname}</Link>
        </div>
      )}

      <div className="home-actions">
        <Link to="/nursery" className="home-action-card">
          <span className="home-action-card__emoji">🥚</span>
          <h3>Nursery</h3>
          <p>Hatch eggs to discover new pets</p>
        </Link>
        <Link to="/shop" className="home-action-card">
          <span className="home-action-card__emoji">🛒</span>
          <h3>Pet Shop</h3>
          <p>Buy pets directly from the shop</p>
        </Link>
        <Link to="/trade" className="home-action-card">
          <span className="home-action-card__emoji">🤝</span>
          <h3>Trading Plaza</h3>
          <p>Trade pets with other players</p>
        </Link>
        <Link to="/my-pets" className="home-action-card">
          <span className="home-action-card__emoji">❤️</span>
          <h3>My Pets</h3>
          <p>Feed, play, and grow your pets</p>
        </Link>
      </div>
    </div>
  );
}
