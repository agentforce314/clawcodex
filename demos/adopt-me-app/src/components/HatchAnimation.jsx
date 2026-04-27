import { PET_RARITIES } from '../data/pets';
import { useGame } from '../context/GameContext';

export default function HatchAnimation() {
  const { state, clearNotification } = useGame();
  const notif = state.notification;
  if (!notif || notif.type !== 'hatch') return null;

  const rarity = PET_RARITIES[notif.pet.rarity];

  return (
    <div className="hatch-overlay" onClick={clearNotification} data-testid="hatch-overlay">
      <div className="hatch-modal">
        <h2>You hatched a pet!</h2>
        <div className="hatch-modal__egg">{notif.egg.emoji}</div>
        <div className="hatch-modal__arrow">⬇️</div>
        <div className="hatch-modal__pet">{notif.pet.emoji}</div>
        <h3 style={{ color: rarity.color }}>{notif.pet.name}</h3>
        <span className="hatch-modal__rarity" style={{ color: rarity.color }}>{rarity.name}</span>
        <button className="btn btn--primary" onClick={clearNotification}>Awesome!</button>
      </div>
    </div>
  );
}
