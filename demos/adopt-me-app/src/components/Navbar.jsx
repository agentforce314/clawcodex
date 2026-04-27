import { NavLink } from 'react-router-dom';
import { useGame } from '../context/GameContext';

export default function Navbar() {
  const { state } = useGame();

  return (
    <nav className="navbar">
      <div className="navbar__brand">
        <span className="navbar__logo">🐾</span>
        <span className="navbar__title">Adopt Me!</span>
      </div>
      <div className="navbar__links">
        <NavLink to="/" end>Home</NavLink>
        <NavLink to="/nursery">Nursery</NavLink>
        <NavLink to="/my-pets">My Pets ({state.pets.length})</NavLink>
        <NavLink to="/shop">Shop</NavLink>
        <NavLink to="/trade">Trade</NavLink>
      </div>
      <div className="navbar__coins">
        🪙 {state.player.coins.toLocaleString()}
      </div>
    </nav>
  );
}
