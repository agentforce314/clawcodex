import { PET_RARITIES, PET_AGES, getXpForAge } from '../data/pets';

export default function PetCard({ pet, onClick, selected, compact }) {
  const rarity = PET_RARITIES[pet.rarity];
  const age = PET_AGES[pet.ageIndex];
  const xpNeeded = getXpForAge(pet.ageIndex);
  const xpPercent = Math.round((pet.xp / xpNeeded) * 100);

  const avgStats = pet.stats
    ? Math.round(Object.values(pet.stats).reduce((a, b) => a + b, 0) / 5)
    : 0;

  return (
    <div
      className={`pet-card ${selected ? 'pet-card--selected' : ''} ${compact ? 'pet-card--compact' : ''}`}
      onClick={onClick}
      style={{ borderColor: rarity.color }}
      role="button"
      tabIndex={0}
      aria-label={`${pet.nickname} the ${pet.name}`}
      onKeyDown={e => e.key === 'Enter' && onClick?.()}
    >
      <div className="pet-card__emoji">{pet.emoji}</div>
      <div className="pet-card__info">
        <h3 className="pet-card__name">{pet.nickname}</h3>
        <span className="pet-card__rarity" style={{ color: rarity.color }}>{rarity.name}</span>
        {!compact && (
          <>
            <div className="pet-card__age">Age: {age}</div>
            <div className="pet-card__xp-bar">
              <div className="pet-card__xp-fill" style={{ width: `${xpPercent}%` }} />
              <span className="pet-card__xp-text">XP {pet.xp}/{xpNeeded}</span>
            </div>
            <div className="pet-card__mood">
              Mood: {avgStats >= 70 ? '😊' : avgStats >= 40 ? '😐' : '😞'} {avgStats}%
            </div>
          </>
        )}
      </div>
    </div>
  );
}
