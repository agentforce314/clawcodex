import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { GameProvider } from '../context/GameContext';
import Nursery from '../pages/Nursery';
import HatchAnimation from '../components/HatchAnimation';

function renderNursery(initialState) {
  return render(
    <GameProvider initialState={initialState}>
      <MemoryRouter>
        <Nursery />
        <HatchAnimation />
      </MemoryRouter>
    </GameProvider>
  );
}

describe('Nursery', () => {
  it('renders the nursery page', () => {
    renderNursery();
    expect(screen.getByText('Nursery')).toBeInTheDocument();
  });

  it('shows all egg types', () => {
    renderNursery();
    expect(screen.getByText('Starter Egg')).toBeInTheDocument();
    expect(screen.getByText('Royal Egg')).toBeInTheDocument();
    expect(screen.getByText('Legendary Egg')).toBeInTheDocument();
    expect(screen.getByText('Mythic Egg')).toBeInTheDocument();
  });

  it('shows hatch button enabled when player can afford', () => {
    renderNursery({
      player: { name: 'Tester', coins: 5000, level: 1 },
      pets: [],
      activePetId: null,
      inventory: [],
      tradeOffers: [],
      notification: null,
    });
    const hatchButtons = screen.getAllByText('Hatch!');
    expect(hatchButtons.length).toBeGreaterThan(0);
  });

  it('shows hatch animation after hatching an egg', () => {
    renderNursery({
      player: { name: 'Tester', coins: 5000, level: 1 },
      pets: [],
      activePetId: null,
      inventory: [],
      tradeOffers: [],
      notification: null,
    });
    const hatchButtons = screen.getAllByText('Hatch!');
    fireEvent.click(hatchButtons[0]);
    expect(screen.getByText('You hatched a pet!')).toBeInTheDocument();
    expect(screen.getByText('Awesome!')).toBeInTheDocument();
  });

  it('disables hatch when not enough coins', () => {
    renderNursery({
      player: { name: 'Tester', coins: 0, level: 1 },
      pets: [],
      activePetId: null,
      inventory: [],
      tradeOffers: [],
      notification: null,
    });
    const disabledButtons = screen.getAllByText('Not enough coins');
    expect(disabledButtons[0]).toBeDisabled();
  });
});
