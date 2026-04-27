import { useState, useMemo } from 'react';
import { useGame } from '../context/GameContext';
import PetCard from '../components/PetCard';
import { PET_CATALOG, PET_RARITIES } from '../data/pets';

const NPC_TRADERS = [
  { name: 'Friendly Farmer', emoji: '🧑‍\uD83C\uDF3E', wants: 'COMMON', offers: 'UNCOMMON' },
  { name: 'Explorer Emma', emoji: '🧑‍\uD83D\uDE80', wants: 'UNCOMMON', offers: 'RARE' },
  { name: 'Royal Knight', emoji: '🤴', wants: 'RARE', offers: 'ULTRA_RARE' },
  { name: 'Mystic Mage', emoji: '🧙', wants: 'ULTRA_RARE', offers: 'LEGENDARY' },
];

export default function Trade() {
  const { state, dispatch } = useGame();
  const [selectedPetId, setSelectedPetId] = useState(null);
  const [tradeResult, setTradeResult] = useState(null);

  const selectedPet = state.pets.find(p => p.instanceId === selectedPetId);

  const availableTraders = useMemo(() => {
    return NPC_TRADERS.map(trader => {
      const eligible = state.pets.filter(p => p.rarity === trader.wants);
      const offerPets = PET_CATALOG.filter(p => p.rarity === trader.offers);
      return { ...trader, eligible, offerPets };
    });
  }, [state.pets]);

  function executeTrade(trader) {
    if (!selectedPet || selectedPet.rarity !== trader.wants) return;
    const offerPets = PET_CATALOG.filter(p => p.rarity === trader.offers);
    const received = offerPets[Math.floor(Math.random() * offerPets.length)];

    dispatch({
      type: 'COMPLETE_TRADE',
      payload: {
        give: [selectedPet.instanceId],
        receive: [received],
      },
    });

    setTradeResult({ gave: selectedPet, received, trader });
    setSelectedPetId(null);
  }

  return (
    <div className="page trade-page">
      <h1>Trading Plaza</h1>
      <p className="page__subtitle">Trade your pets with NPC traders to get rarer ones!</p>

      {tradeResult && (
        <div className="trade-result" data-testid="trade-result">
          <h3>Trade Complete!</h3>
          <div className="trade-result__details">
            <div className="trade-result__gave">
              <span>{tradeResult.gave.emoji}</span>
              <p>{tradeResult.gave.nickname}</p>
            </div>
            <span className="trade-result__arrow">➡️</span>
            <div className="trade-result__received">
              <span>{tradeResult.received.emoji}</span>
              <p>{tradeResult.received.name}</p>
            </div>
          </div>
          <button className="btn btn--primary" onClick={() => setTradeResult(null)}>Continue</button>
        </div>
      )}

      {state.pets.length > 0 && (
        <div className="trade-select">
          <h2>Select a Pet to Trade</h2>
          <div className="trade-pet-list">
            {state.pets.map(pet => (
              <PetCard
                key={pet.instanceId}
                pet={pet}
                compact
                selected={pet.instanceId === selectedPetId}
                onClick={() => setSelectedPetId(pet.instanceId)}
              />
            ))}
          </div>
        </div>
      )}

      <div className="trader-list">
        <h2>NPC Traders</h2>
        {availableTraders.map(trader => {
          const canTrade = selectedPet && selectedPet.rarity === trader.wants;
          const wantsRarity = PET_RARITIES[trader.wants];
          const offersRarity = PET_RARITIES[trader.offers];
          return (
            <div key={trader.name} className="trader-card">
              <div className="trader-card__header">
                <span className="trader-card__emoji">{trader.emoji}</span>
                <h3>{trader.name}</h3>
              </div>
              <div className="trader-card__deal">
                <span>
                  Wants: <strong style={{ color: wantsRarity.color }}>{wantsRarity.name}</strong> pet
                </span>
                <span className="trader-card__arrow">➡️</span>
                <span>
                  Offers: <strong style={{ color: offersRarity.color }}>{offersRarity.name}</strong> pet
                </span>
              </div>
              <button
                className={`btn ${canTrade ? 'btn--primary' : 'btn--disabled'}`}
                disabled={!canTrade}
                onClick={() => executeTrade(trader)}
              >
                {canTrade ? 'Trade!' : `Select a ${wantsRarity.name} pet first`}
              </button>
            </div>
          );
        })}
      </div>

      {state.pets.length === 0 && (
        <div className="empty-state">
          <span className="empty-state__emoji">🤝</span>
          <h2>No pets to trade</h2>
          <p>Adopt some pets first, then come back to trade!</p>
        </div>
      )}
    </div>
  );
}
