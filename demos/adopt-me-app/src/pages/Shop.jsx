import { useGame } from '../context/GameContext';
import { PET_CATALOG, PET_RARITIES } from '../data/pets';

export default function Shop() {
  const { state, adoptPet, spendCoins } = useGame();

  function handleBuy(pet) {
    if (state.player.coins < pet.basePrice) return;
    spendCoins(pet.basePrice);
    adoptPet(pet);
  }

  const grouped = {};
  PET_CATALOG.forEach(pet => {
    if (!grouped[pet.rarity]) grouped[pet.rarity] = [];
    grouped[pet.rarity].push(pet);
  });

  return (
    <div className="page shop-page">
      <h1>Pet Shop</h1>
      <p className="page__subtitle">Browse and buy pets directly. Your coins: 🪙 {state.player.coins.toLocaleString()}</p>

      {Object.entries(grouped).map(([rarity, pets]) => {
        const rarityInfo = PET_RARITIES[rarity];
        return (
          <div key={rarity} className="shop-section">
            <h2 style={{ color: rarityInfo.color }}>{rarityInfo.name}</h2>
            <div className="shop-grid">
              {pets.map(pet => {
                const canAfford = state.player.coins >= pet.basePrice;
                return (
                  <div key={pet.id} className="shop-card" style={{ borderColor: rarityInfo.color }}>
                    <span className="shop-card__emoji">{pet.emoji}</span>
                    <h3>{pet.name}</h3>
                    <div className="shop-card__price">🪙 {pet.basePrice.toLocaleString()}</div>
                    <button
                      className={`btn ${canAfford ? 'btn--primary' : 'btn--disabled'}`}
                      disabled={!canAfford}
                      onClick={() => handleBuy(pet)}
                    >
                      {canAfford ? 'Adopt!' : 'Too expensive'}
                    </button>
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}
