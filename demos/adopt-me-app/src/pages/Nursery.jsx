import { useGame } from '../context/GameContext';
import { EGGS, PET_RARITIES } from '../data/pets';

export default function Nursery() {
  const { state, hatchEgg } = useGame();

  return (
    <div className="page nursery-page">
      <h1>Nursery</h1>
      <p className="page__subtitle">Buy and hatch eggs to discover new pets!</p>

      <div className="egg-grid">
        {EGGS.map(egg => {
          const canAfford = state.player.coins >= egg.price;
          return (
            <div key={egg.id} className="egg-card">
              <div className="egg-card__emoji">{egg.emoji}</div>
              <h3>{egg.name}</h3>
              <div className="egg-card__rarities">
                {egg.rarities.map(r => (
                  <span key={r} className="egg-card__rarity-tag" style={{ backgroundColor: PET_RARITIES[r].color }}>
                    {PET_RARITIES[r].name}
                  </span>
                ))}
              </div>
              <div className="egg-card__price">🪙 {egg.price.toLocaleString()}</div>
              <button
                className={`btn ${canAfford ? 'btn--primary' : 'btn--disabled'}`}
                disabled={!canAfford}
                onClick={() => hatchEgg(egg.id)}
              >
                {canAfford ? 'Hatch!' : 'Not enough coins'}
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}
